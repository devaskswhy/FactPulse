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
