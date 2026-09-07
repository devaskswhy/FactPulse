"""Page-aware chunking.

Two properties matter downstream:

1. Every chunk knows its page range, so a fact extracted from it starts with a
   page hint before grounding narrows that to a rectangle.
2. Chunks do not straddle more text than the extractor can hold at once.

Chunks are built by accumulating whole pages until the token budget is hit. A
page too large to fit alone is split on paragraph boundaries, and in that case
page_start == page_end.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.pdf import ParsedPage

# PDF text has no tokenizer attached and pulling one in for a page-range
# heuristic is not worth the dependency. ~4 chars/token is the usual English
# approximation; it is only used to decide where to cut, and the resulting
# estimate is what lands in chunks.token_count.
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Approximate token count. Not tokenizer-exact -- see CHARS_PER_TOKEN."""
    return max(1, len(text) // CHARS_PER_TOKEN) if text else 0


@dataclass(frozen=True)
class ChunkDraft:
    """A chunk before it has a database id."""

    page_start: int
    page_end: int
    text: str
    token_count: int


def _split_page(text: str, max_tokens: int) -> list[str]:
    """Split one oversized page into paragraph-aligned pieces.

    Falls back to line boundaries, then to a hard character cut, so a page with
    no paragraph breaks at all (a dense table, say) still terminates.
    """
    budget = max_tokens * CHARS_PER_TOKEN
    units = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(units) <= 1:
        units = [ln for ln in text.split("\n") if ln.strip()] or [text]

    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if current and len(candidate) > budget:
            pieces.append(current)
            current = unit
        else:
            current = candidate

        # A single unit can still exceed the budget; cut it hard.
        while len(current) > budget:
            pieces.append(current[:budget])
            current = current[budget:]

    if current.strip():
        pieces.append(current)
    return pieces


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """Trailing slice of a chunk, repeated into the next one.

    Overlap keeps a fact whose sentence spans a chunk boundary from being lost
    by both chunks. Cut on a paragraph break where possible so the repeated
    text still reads as prose.
    """
    if overlap_tokens <= 0:
        return ""
    budget = overlap_tokens * CHARS_PER_TOKEN
    if len(text) <= budget:
        return text
    tail = text[-budget:]
    break_at = tail.find("\n\n")
    return tail[break_at + 2 :] if break_at != -1 else tail


def chunk_pages(
    pages: list[ParsedPage],
    max_tokens: int = 900,
    overlap_tokens: int = 120,
) -> list[ChunkDraft]:
    """Group pages into chunks of at most ~max_tokens, with overlap between them.

    Empty pages are skipped entirely -- chunks.text is NOT NULL, and a blank
    chunk would only give the extractor nothing to do.
    """
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")

    drafts: list[ChunkDraft] = []
    buffer = ""
    buf_start: int | None = None
    buf_end: int | None = None

    def flush() -> None:
        nonlocal buffer, buf_start, buf_end
        if buffer.strip() and buf_start is not None and buf_end is not None:
            drafts.append(
                ChunkDraft(
                    page_start=buf_start,
                    page_end=buf_end,
                    text=buffer.strip(),
                    token_count=estimate_tokens(buffer.strip()),
                )
            )
        buffer, buf_start, buf_end = "", None, None

    for page in pages:
        text = page.text.strip()
        if not text:
            continue

        # A page that cannot fit in a chunk by itself is split in place. Flush
        # whatever preceded it first so page ranges stay contiguous.
        if estimate_tokens(text) > max_tokens:
            flush()
            for piece in _split_page(text, max_tokens):
                piece = piece.strip()
                if piece:
                    drafts.append(
                        ChunkDraft(
                            page_start=page.page_number,
                            page_end=page.page_number,
                            text=piece,
                            token_count=estimate_tokens(piece),
                        )
                    )
            continue

        candidate = f"{buffer}\n\n{text}" if buffer else text
        if buffer and estimate_tokens(candidate) > max_tokens:
            tail = _overlap_tail(buffer, overlap_tokens)
            # The tail comes from the page we are about to flush, so the new
            # chunk genuinely starts there. Carrying that page forward keeps a
            # fact found in the repeated text pointing at the right page;
            # setting page_start to the current page would misattribute it.
            carried_from = buf_end
            flush()
            if tail and carried_from is not None:
                buffer = f"{tail}\n\n{text}"
                buf_start = carried_from
            else:
                buffer = text
                buf_start = page.page_number
            buf_end = page.page_number
        else:
            buffer = candidate
            buf_start = page.page_number if buf_start is None else buf_start
            buf_end = page.page_number

    flush()
    return drafts
