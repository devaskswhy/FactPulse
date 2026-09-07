"""Pipeline steps 1-8: ingest, parse, chunk, extract, ground, embed, link.

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

import logging
import sqlite3
import time
from dataclasses import dataclass, field

from app.core.config import settings
from app.db import repository as repo
from app.schemas.document import Document
from app.services.chunker import chunk_pages
from app.services.embed import EmbeddingError
from app.services.extract import ExtractionError
from app.services.pdf import PdfParseError, parse_pdf, sha256_bytes, store_pdf
from app.services.link import LinkingSummary, link_document_facts
from app.services.pipeline import ExtractionSummary, extract_document_facts
from app.services.progress import ProgressTracker, registry
from app.services.selfcheck import run_self_check
from app.services import ingest_log

logger = logging.getLogger(__name__)


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
    linking: LinkingSummary | None = None        # None when linking was skipped
    self_check: dict[str, int] = field(default_factory=dict)  # queued by issue type


def ingest_pdf(
    conn: sqlite3.Connection,
    *,
    filename: str,
    data: bytes,
    extract: bool | None = None,
    progress: ProgressTracker | None = None,
) -> IngestResult:
    """Ingest one PDF. Idempotent on file content via the sha256 unique index.

    `extract` overrides the EXTRACT_ON_UPLOAD setting for this call.
    `progress` receives phase-by-phase updates; one is created if not supplied,
    so an SSE client can follow any ingest however it was started.

    Raises PdfParseError / EmptyPdfError for input the pipeline cannot use.
    """
    if not data:
        raise PdfParseError("uploaded file is empty")

    tracker = progress or registry.start(filename)
    started = time.perf_counter()
    timings: dict[str, float] = {}

    def phase_done(name: str, since: float) -> float:
        timings[name] = round(time.perf_counter() - since, 2)
        return time.perf_counter()

    digest = sha256_bytes(data)

    # Content-addressed dedupe. Re-uploading a file we have already processed
    # returns the existing document rather than duplicating every fact in it.
    existing = repo.get_document_by_sha256(conn, digest)
    if existing is not None:
        registry.rekey(tracker, existing.id)
        tracker.phase("done", "already ingested; nothing to do")
        return IngestResult(
            document=existing,
            chunk_count=repo.count_chunks(conn, existing.id),
            deduplicated=True,
        )

    mark = time.perf_counter()
    tracker.phase("parsing", "reading pages")
    parsed = parse_pdf(data)  # raises PdfParseError before anything is written
    if not parsed.has_text:
        tracker.update(phase="failed", error="no extractable text")
        raise EmptyPdfError(
            "no extractable text -- the PDF appears to be scanned images, "
            "which needs OCR that is not implemented yet"
        )
    tracker.update(pages=parsed.page_count, current=parsed.page_count,
                   total=parsed.page_count, message=f"read {parsed.page_count} pages")
    mark = phase_done("parse", mark)

    tracker.phase("chunking", "splitting into chunks")
    drafts = chunk_pages(
        parsed.pages,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
    )
    tracker.update(chunks=len(drafts), current=len(drafts), total=len(drafts),
                   message=f"{len(drafts)} chunks from {parsed.page_count} pages")
    mark = phase_done("chunk", mark)

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
    registry.rekey(tracker, document_id)

    document = repo.get_document(conn, document_id)
    assert document is not None  # just inserted, inside the same transaction

    mark = time.perf_counter()
    extraction = _maybe_extract(conn, document, extract=extract, progress=tracker)
    mark = phase_done("extract", mark)

    # Linking is a batch step over the document's whole fact set, so it runs
    # after extraction rather than per fact: a fact needs its siblings embedded
    # before it is worth comparing anything against the corpus.
    linking = _maybe_link(conn, document, extraction=extraction, progress=tracker)
    mark = phase_done("link", mark)

    tracker.phase("checking", "running post-ingestion checks")
    # Systematic pass over what was written, once the document is complete.
    # Pure queries, no model calls, so it costs nothing and runs unconditionally
    # -- including when extraction was skipped, where it simply finds nothing.
    self_check = run_self_check(conn, document_id)
    phase_done("self_check", mark)

    # Re-read: extraction and linking advance the document's status.
    document = repo.get_document(conn, document_id) or document
    total = round(time.perf_counter() - started, 2)

    tracker.phase("done", f"ingested in {total:.1f}s")
    tracker.update(
        facts=extraction.facts_inserted if extraction else 0,
        relationships=linking.relationships_created if linking else 0,
    )

    ingest_log.record(
        {
            "document_id": document_id,
            "filename": filename,
            "pages": parsed.page_count,
            "chunks": len(drafts),
            "facts": extraction.facts_inserted if extraction else 0,
            "facts_grounded": extraction.facts_grounded if extraction else 0,
            "chunks_failed": extraction.chunks_failed if extraction else 0,
            "quota_exhausted": bool(extraction and extraction.quota_exhausted),
            "relationships": linking.relationships_created if linking else 0,
            "pairs_evaluated": linking.pairs_evaluated if linking else 0,
            "pool_size": linking.pool_size if linking else 0,
            "review_items": sum(self_check.values()) + (extraction.review_items if extraction else 0),
            "seconds": total,
            "phase_seconds": timings,
            "model": settings.gemini_model,
            "extraction_concurrency": settings.extraction_concurrency,
            "status": document.status,
        }
    )

    return IngestResult(
        document=document,
        chunk_count=len(drafts),
        deduplicated=False,
        extraction=extraction,
        linking=linking,
        self_check=self_check,
    )


def _maybe_extract(
    conn: sqlite3.Connection,
    document: Document,
    *,
    extract: bool | None,
    progress: ProgressTracker | None = None,
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
        return extract_document_facts(conn, document, progress=progress)
    except ExtractionError:
        # The document and its chunks are already durable and worth keeping;
        # only the model step failed. Surface it as status, not as a 500.
        repo.set_document_status(conn, document.id, "extraction_failed")
        return None


def _maybe_link(
    conn: sqlite3.Connection,
    document: Document,
    *,
    extraction: ExtractionSummary | None,
    progress: ProgressTracker | None = None,
) -> LinkingSummary | None:
    """Embed and link, if extraction actually produced anything.

    Skipped silently when extraction was skipped or found nothing -- there is
    nothing to compare. A failure here leaves the facts intact; relationships
    are derived data and can be rebuilt with POST /documents/{id}/link.
    """
    if not settings.link_on_upload or extraction is None:
        return None
    if extraction.facts_inserted == 0:
        return None
    try:
        summary = link_document_facts(conn, document.id, progress=progress)
    except (ExtractionError, EmbeddingError) as exc:
        logger.warning("linking failed for document %s: %s", document.id, exc)
        return None
    repo.set_document_status(conn, document.id, "linked")
    return summary


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
