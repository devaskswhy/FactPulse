"""PDF parsing via PyMuPDF.

Text only at this stage. Bounding boxes are deliberately *not* stored here:
grounding (pipeline step 5) re-opens the saved PDF and uses `page.search_for()`
to locate a quote, which is both more accurate than reassembling spans and
keeps `chunks` to the columns the schema declares. That is why upload keeps the
original file on disk -- see `store_pdf()`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF


class PdfParseError(ValueError):
    """Raised when bytes are not a readable PDF."""


@dataclass(frozen=True)
class ParsedPage:
    page_number: int  # 1-based, matching what a reader sees
    text: str


@dataclass(frozen=True)
class ParsedPdf:
    title: str | None
    page_count: int
    pages: list[ParsedPage]

    @property
    def has_text(self) -> bool:
        """False for scanned/image-only PDFs, which need OCR we do not do yet."""
        return any(p.text.strip() for p in self.pages)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# PDF text extraction leaves artifacts that hurt both chunking and, later, the
# extractor's ability to quote verbatim. Normalise conservatively: fix encoding
# quirks and collapse whitespace, but never drop or reorder characters, because
# `page.search_for()` has to find the quote in the *original* page later.
#
# Keyed by codepoint so this file stays pure ASCII -- the literal glyphs are
# invisible in most editors and several are trivially confusable with the
# plain characters they map to.
_SUBSTITUTIONS: dict[int, str] = {
    0xFB00: "ff",   # LATIN SMALL LIGATURE FF
    0xFB01: "fi",   # LATIN SMALL LIGATURE FI
    0xFB02: "fl",   # LATIN SMALL LIGATURE FL
    0xFB03: "ffi",  # LATIN SMALL LIGATURE FFI
    0xFB04: "ffl",  # LATIN SMALL LIGATURE FFL
    0x2018: "'",    # LEFT SINGLE QUOTATION MARK
    0x2019: "'",    # RIGHT SINGLE QUOTATION MARK
    0x201C: '"',    # LEFT DOUBLE QUOTATION MARK
    0x201D: '"',    # RIGHT DOUBLE QUOTATION MARK
    0x00A0: " ",    # NO-BREAK SPACE
    0x2013: "-",    # EN DASH
    0x2014: "-",    # EM DASH
}


def clean_text(raw: str) -> str:
    text = unicodedata.normalize("NFKC", raw)
    text = text.translate(_SUBSTITUTIONS)
    # de-hyphenate words broken across a line end: "settle-\nment" -> "settlement"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    # collapse runs of blank lines to a single paragraph break
    text = re.sub(r"\n{3,}", "\n\n", text)
    # trim trailing spaces on each line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip()


def _infer_title(doc: fitz.Document, pages: list[ParsedPage]) -> str | None:
    """PDF metadata title if it is meaningful, else the first substantial line."""
    meta_title = (doc.metadata or {}).get("title") or ""
    meta_title = meta_title.strip()
    # Producers often leave junk here: a temp filename, or the path it was
    # printed from. Reject anything that looks like a filename.
    if meta_title and not re.fullmatch(r".*\.(pdf|docx?|pptx?|indd)", meta_title, re.I):
        return meta_title[:300]

    if pages:
        for line in pages[0].text.split("\n"):
            line = line.strip()
            if len(line) >= 8:
                return line[:300]
    return None


def parse_pdf(source: bytes | Path | str) -> ParsedPdf:
    """Parse a PDF from bytes or a path. Raises PdfParseError on unreadable input."""
    try:
        if isinstance(source, bytes):
            doc = fitz.open(stream=source, filetype="pdf")
        else:
            doc = fitz.open(Path(source))
    except Exception as exc:
        raise PdfParseError(f"could not open PDF: {exc}") from exc

    try:
        if doc.is_encrypted and not doc.authenticate(""):
            raise PdfParseError("PDF is password protected")

        pages: list[ParsedPage] = []
        for index in range(doc.page_count):
            try:
                raw = doc.load_page(index).get_text("text")
            except Exception as exc:  # one bad page should not lose the document
                raise PdfParseError(f"failed to read page {index + 1}: {exc}") from exc
            pages.append(ParsedPage(page_number=index + 1, text=clean_text(raw)))

        return ParsedPdf(
            title=_infer_title(doc, pages),
            page_count=doc.page_count,
            pages=pages,
        )
    finally:
        doc.close()


def store_pdf(data: bytes, sha256: str, upload_dir: Path) -> Path:
    """Save the original bytes under their hash so grounding can reopen them.

    Content-addressed, so re-uploading an identical file is a no-op rather than
    a second copy.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    path = upload_dir / f"{sha256}.pdf"
    if not path.exists():
        path.write_bytes(data)
    return path


# --------------------------------------------------------------- grounding
#
# Two separate jobs, deliberately kept apart:
#
#   verify_quote()      -- is this quote really in the chunk we sent the model?
#                          Guards against a plausible-sounding invention.
#   locate_quote_bbox() -- where on the page does it sit?
#
# A quote can pass the first and fail the second: chunk text has been cleaned
# (ligatures folded, hyphenation joined, whitespace collapsed), so it does not
# always match the raw glyph stream PyMuPDF searches. That is why a missing box
# is reported separately rather than treated as a failed verification.

_WS = re.compile(r"\s+")


def _normalize_for_match(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def verify_quote(chunk_text: str, quote: str) -> str | None:
    """Return the quote as it actually appears in `chunk_text`, or None.

    Matching is case-insensitive and whitespace-normalized, because models
    reflow and re-case text they are quoting. The *returned* string is the
    original span, so what gets stored is genuinely verbatim rather than the
    model's paraphrase of it.
    """
    if not quote or not quote.strip():
        return None

    needle = _normalize_for_match(quote)
    if not needle:
        return None

    # Walk the original text building a normalized copy, remembering which
    # original index each normalized character came from. That index map is
    # what lets us slice the original span back out after matching.
    normalized_chars: list[str] = []
    origin: list[int] = []
    previous_was_space = True  # leading whitespace is dropped, as in strip()
    for index, char in enumerate(chunk_text):
        if char.isspace():
            if not previous_was_space:
                normalized_chars.append(" ")
                origin.append(index)
                previous_was_space = True
        else:
            normalized_chars.append(char.lower())
            origin.append(index)
            previous_was_space = False

    haystack = "".join(normalized_chars).strip()
    # strip() above may have removed a trailing space; keep origin aligned.
    origin = origin[: len(haystack)]

    position = haystack.find(needle)
    if position == -1:
        return None

    start = origin[position]
    end_index = position + len(needle) - 1
    end = origin[end_index] + 1
    return chunk_text[start:end]


def locate_quote_bbox(
    pdf_path: Path | str,
    quote: str,
    page_start: int | None = None,
    page_end: int | None = None,
) -> tuple[int, tuple[float, float, float, float]] | None:
    """Find `quote` in the PDF and return (page_number, (x0, y0, x1, y1)).

    Searches only the chunk's page range when given one, then the whole
    document as a fallback. Returns None if the text cannot be located, which
    is a grounding gap rather than a verification failure -- see the note above.

    Page numbers are 1-based. The box is the union of every rectangle the match
    spans, so a quote wrapping across lines yields one box covering all of them.
    """
    cleaned = _WS.sub(" ", quote or "").strip()
    if not cleaned:
        return None

    try:
        doc = fitz.open(Path(pdf_path))
    except Exception:
        return None

    try:
        if page_start and page_end:
            ordered = list(range(page_start - 1, min(page_end, doc.page_count)))
            ordered += [i for i in range(doc.page_count) if i not in ordered]
        else:
            ordered = list(range(doc.page_count))

        # Candidates OUTER, pages INNER. This ordering is load-bearing: a
        # shortened fallback is far less distinctive than the full quote, and a
        # report that repeats boilerplate ("Gross foreign exchange reserves
        # were ...") will match it on dozens of pages. Iterating pages outer
        # let a five-word prefix match on an early page and return, while the
        # full quote -- unique to one page -- was never tried there. The result
        # was a highlight over the right-looking sentence on the wrong page,
        # showing a different number than the fact claimed.
        #
        # So: exhaust the most specific candidate across every page before
        # falling back to a shorter one.
        for candidate in _search_candidates(cleaned):
            for index in ordered:
                if index < 0 or index >= doc.page_count:
                    continue
                try:
                    rects = doc.load_page(index).search_for(candidate)
                except Exception:
                    continue
                if rects:
                    box = rects[0]
                    for rect in rects[1:]:
                        box = box | rect  # union, so multi-line quotes stay whole
                    return index + 1, (
                        round(box.x0, 2),
                        round(box.y0, 2),
                        round(box.x1, 2),
                        round(box.y1, 2),
                    )
        return None
    finally:
        doc.close()


def _search_candidates(quote: str) -> list[str]:
    """The quote, then progressively shorter leading word runs to fall back on."""
    words = quote.split()
    candidates = [quote]
    for count in (12, 8, 5):
        if len(words) > count:
            candidates.append(" ".join(words[:count]))
    # De-duplicate while keeping order.
    seen: set[str] = set()
    return [c for c in candidates if not (c in seen or seen.add(c))]
