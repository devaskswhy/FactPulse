"""Pipeline steps 1-5: ingest, parse, chunk, extract, ground.

Ordering here is deliberate. The PDF is parsed *before* any row is written, so
an unreadable upload never leaves a half-created document behind. The write
that follows is a single transaction: document + chunks + final status land
together or not at all.

Extraction (steps 4-5, in pipeline.py) runs right after chunking, in the same
request. That keeps upload-to-facts a single call, at the cost of a slow
upload for a large document -- a background job is the obvious later move. If
no Gemini key is configured the document still ingests and simply stops at
'chunked'; ingestion does not depend on the model.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.config import settings
from app.db import repository as repo
from app.schemas.document import Document
from app.services.chunker import chunk_pages
from app.services.extract import ExtractionError
from app.services.pdf import PdfParseError, parse_pdf, sha256_bytes, store_pdf
from app.services.pipeline import ExtractionSummary, extract_document_facts


class EmptyPdfError(PdfParseError):
    """PDF opened fine but has no extractable text -- almost always a scan.

    Distinguished from a parse failure because the fix is different: this one
    needs OCR, not a different file.
    """


@dataclass(frozen=True)
class IngestResult:
    document: Document
    chunk_count: int
    deduplicated: bool  # True when this file was already ingested
    extraction: ExtractionSummary | None = None  # None when extraction was skipped


def ingest_pdf(
    conn: sqlite3.Connection, *, filename: str, data: bytes, extract: bool | None = None
) -> IngestResult:
    """Ingest one PDF. Idempotent on file content via the sha256 unique index.

    `extract` overrides the EXTRACT_ON_UPLOAD setting for this call.

    Raises PdfParseError / EmptyPdfError for input the pipeline cannot use.
    """
    if not data:
        raise PdfParseError("uploaded file is empty")

    digest = sha256_bytes(data)

    # Content-addressed dedupe. Re-uploading a file we have already processed
    # returns the existing document rather than duplicating every fact in it.
    existing = repo.get_document_by_sha256(conn, digest)
    if existing is not None:
        return IngestResult(
            document=existing,
            chunk_count=repo.count_chunks(conn, existing.id),
            deduplicated=True,
        )

    parsed = parse_pdf(data)  # raises PdfParseError before anything is written
    if not parsed.has_text:
        raise EmptyPdfError(
            "no extractable text -- the PDF appears to be scanned images, "
            "which needs OCR that is not implemented yet"
        )

    drafts = chunk_pages(
        parsed.pages,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )

    # Keep the original bytes: grounding reopens this file to turn a quote into
    # a bounding box, and the API can serve pages to the viewer.
    store_pdf(data, digest, settings.upload_path)

    document_id = repo.insert_document(
        conn,
        filename=filename,
        title=parsed.title,
        sha256=digest,
        page_count=parsed.page_count,
        status="chunked",
    )
    repo.insert_chunks(conn, document_id, drafts)

    document = repo.get_document(conn, document_id)
    assert document is not None  # just inserted, inside the same transaction

    extraction = _maybe_extract(conn, document, extract=extract)

    # Re-read: extraction advances the document's status.
    document = repo.get_document(conn, document_id) or document
    return IngestResult(
        document=document,
        chunk_count=len(drafts),
        deduplicated=False,
        extraction=extraction,
    )


def _maybe_extract(
    conn: sqlite3.Connection, document: Document, *, extract: bool | None
) -> ExtractionSummary | None:
    """Run extraction if it is switched on and a key is configured.

    Returns None when the step was skipped. A missing key is not an error here:
    ingestion is complete and useful without it, and the document simply stays
    at 'chunked' until extraction is run later.
    """
    wanted = settings.extract_on_upload if extract is None else extract
    if not wanted:
        return None
    if not settings.gemini_configured:
        return None
    try:
        return extract_document_facts(conn, document)
    except ExtractionError:
        # The document and its chunks are already durable and worth keeping;
        # only the model step failed. Surface it as status, not as a 500.
        repo.set_document_status(conn, document.id, "extraction_failed")
        return None


def rechunk_document(conn: sqlite3.Connection, document: Document) -> int:
    """Re-parse a stored PDF and replace its chunks.

    Useful after changing the chunk settings. Facts already extracted keep
    their own copy of quote/page/bbox, so they survive rechunking; their
    chunk_id is nulled by ON DELETE SET NULL rather than cascading.
    """
    path = settings.upload_path / f"{document.sha256}.pdf"
    if not path.exists():
        raise PdfParseError(f"stored PDF is missing for document {document.id}")

    parsed = parse_pdf(path)
    drafts = chunk_pages(
        parsed.pages,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )

    repo.delete_chunks(conn, document.id)
    repo.insert_chunks(conn, document.id, drafts)
    repo.set_document_status(conn, document.id, "chunked")
    return len(drafts)
