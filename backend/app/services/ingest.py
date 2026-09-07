"""Pipeline steps 1-3: ingest, parse, chunk.

Ordering here is deliberate. The PDF is parsed *before* any row is written, so
an unreadable upload never leaves a half-created document behind. The write
that follows is a single transaction: document + chunks + final status land
together or not at all.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.config import settings
from app.db import repository as repo
from app.schemas.document import Document
from app.services.chunker import chunk_pages
from app.services.pdf import PdfParseError, parse_pdf, sha256_bytes, store_pdf


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


def ingest_pdf(conn: sqlite3.Connection, *, filename: str, data: bytes) -> IngestResult:
    """Ingest one PDF. Idempotent on file content via the sha256 unique index.

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
    return IngestResult(document=document, chunk_count=len(drafts), deduplicated=False)


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
