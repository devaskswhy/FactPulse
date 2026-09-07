"""Document ingestion and inspection: pipeline steps 1-3."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.db import repository as repo
from app.db.database import db_dependency
from app.schemas.document import (
    ChunkList,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    DocumentUploadResponse,
    KnowledgeLayerTotals,
    RechunkResponse,
)
from app.schemas.fact import ExtractionSummaryOut
from app.schemas.relationship import LinkingSummaryOut
from app.services.extract import ExtractionError
from app.services.ingest import EmptyPdfError, ingest_pdf, rechunk_document
from app.services.pdf import PdfParseError
from app.services.embed import EmbeddingError
from app.services.link import link_document_facts
from app.services.pipeline import extract_document_facts, reground_document_facts
from app.services.render import PageRenderError, render_page
from app.services.selfcheck import run_self_check

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a PDF, parse it, and chunk it",
)
async def upload_document(
    response: Response,
    file: UploadFile = File(..., description="The PDF to ingest."),
    extract: bool | None = Query(
        None,
        description=(
            "Run fact extraction after chunking. Defaults to the "
            "EXTRACT_ON_UPLOAD setting. Skipped silently when no Gemini key "
            "is configured."
        ),
    ),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> DocumentUploadResponse:
    """Ingest one PDF.

    Deduplicated by SHA-256 of the file bytes: uploading the same file twice
    returns the existing document with `deduplicated: true` and re-parses
    nothing. That case answers 200, not 201, because nothing was created.
    """
    data = await file.read()

    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"file is {len(data) / 1_048_576:.1f} MB, limit is {settings.max_upload_mb} MB",
        )

    # Off the event loop, deliberately.
    #
    # ingest_pdf is fully synchronous and can run for minutes -- model calls,
    # PyMuPDF parsing, SQLite writes. Calling it directly from an `async def`
    # route blocks the ONLY event loop for that entire time, which makes every
    # other endpoint unresponsive, the SSE progress stream included. The
    # progress endpoint exists precisely to report on this work, so blocking it
    # with that work made the feature impossible: the client saw one frozen
    # frame until the upload finished.
    #
    # run_in_threadpool moves it to a worker thread. The SQLite connection is
    # opened with check_same_thread=False, so handing it across is safe, and
    # only one thread ever touches it.
    try:
        result = await run_in_threadpool(
            ingest_pdf,
            conn,
            filename=file.filename or "untitled.pdf",
            data=data,
            extract=extract,
        )
    except EmptyPdfError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PdfParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if result.deduplicated:
        response.status_code = status.HTTP_200_OK

    return DocumentUploadResponse(
        document=result.document,
        chunk_count=result.chunk_count,
        deduplicated=result.deduplicated,
        extraction=(
            ExtractionSummaryOut(**result.extraction.as_dict())
            if result.extraction
            else None
        ),
        linking=(
            LinkingSummaryOut(**result.linking.as_dict()) if result.linking else None
        ),
        self_check=result.self_check,
    )


@router.get(
    "",
    response_model=DocumentList,
    summary="The workspace: every document in the knowledge layer",
)
def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> DocumentList:
    """Every source in the layer, with what each contributed.

    `totals` is the corpus-wide rollup. Per-document `relationship_count`
    counts either end of a pair, so a cross-document link appears on both
    documents and the column deliberately does not sum to
    `totals.relationships`.
    """
    rows = repo.list_documents_with_counts(conn, limit=limit, offset=offset)
    return DocumentList(
        total=repo.count_documents(conn),
        totals=KnowledgeLayerTotals(**repo.knowledge_layer_totals(conn)),
        documents=[
            DocumentSummary(
                id=r["id"],
                filename=r["filename"],
                title=r["title"],
                sha256=r["sha256"],
                uploaded_at=r["uploaded_at"],
                page_count=r["page_count"],
                status=r["status"],
                chunk_count=r["chunk_count"],
                fact_count=r["fact_count"],
                embedded_count=r["embedded_count"],
                relationship_count=r["relationship_count"],
                open_review_count=r["open_review_count"],
            )
            for r in rows
        ],
    )


def _require_document(conn: sqlite3.Connection, document_id: int):
    document = repo.get_document(conn, document_id)
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no document {document_id}")
    return document


@router.get("/{document_id}", response_model=DocumentDetail, summary="Get one document")
def get_document(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> DocumentDetail:
    document = _require_document(conn, document_id)
    return DocumentDetail(
        **document.model_dump(),
        chunk_count=repo.count_chunks(conn, document_id),
    )


@router.get(
    "/{document_id}/chunks",
    response_model=ChunkList,
    summary="List a document's chunks",
)
def get_chunks(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> ChunkList:
    _require_document(conn, document_id)
    chunks = repo.list_chunks(conn, document_id)
    return ChunkList(document_id=document_id, total=len(chunks), chunks=chunks)


@router.get(
    "/{document_id}/file",
    response_class=FileResponse,
    summary="Download the original PDF",
)
def get_document_file(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> FileResponse:
    """Serve the stored PDF. The viewer needs this to render pages and draw
    highlight boxes over grounded quotes."""
    document = _require_document(conn, document_id)
    path = settings.upload_path / f"{document.sha256}.pdf"
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"stored PDF missing for document {document_id}"
        )
    return FileResponse(path, media_type="application/pdf", filename=document.filename)


@router.post(
    "/{document_id}/rechunk",
    response_model=RechunkResponse,
    summary="Re-parse and re-chunk a stored PDF",
)
def rechunk(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> RechunkResponse:
    """Rebuild chunks from the stored PDF, e.g. after changing chunk settings."""
    document = _require_document(conn, document_id)
    try:
        count = rechunk_document(conn, document)
    except PdfParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RechunkResponse(document_id=document_id, chunk_count=count)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete a document and everything derived from it",
)
def delete_document(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> Response:
    """Chunks, facts, attributes, embeddings and relationships cascade.

    The stored PDF is left on disk: it is content-addressed, so another
    document row could legitimately reference the same bytes.
    """
    if not repo.delete_document(conn, document_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no document {document_id}")
    # fact_types counters are maintained on write, so the cascade that just
    # removed this document's facts left them overstating reality. Rebuild.
    repo.resync_fact_types(conn)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{document_id}/extract",
    response_model=ExtractionSummaryOut,
    summary="Run (or re-run) fact extraction for a document",
)
def extract(
    document_id: int,
    replace: bool = Query(
        True,
        description=(
            "Clear existing facts for this document first. False appends, which "
            "will duplicate facts if the document was already extracted."
        ),
    ),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> ExtractionSummaryOut:
    """Extract facts from every chunk of an already-ingested document."""
    document = _require_document(conn, document_id)
    try:
        summary = extract_document_facts(conn, document, replace=replace)
    except ExtractionError as exc:
        # No key configured, or the client could not be built at all.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # The fact set just changed, so re-run the checks over it. Cheap (queries
    # only) and idempotent, so a re-extract leaves the queue consistent rather
    # than reflecting the previous run's facts.
    run_self_check(conn, document_id)
    return ExtractionSummaryOut(**summary.as_dict())


@router.post(
    "/{document_id}/link",
    response_model=LinkingSummaryOut,
    summary="Embed this document's facts and relate them to the corpus",
)
def link(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> LinkingSummaryOut:
    """Run (or re-run) the relationship engine for one document.

    Idempotent: pairs already judged are skipped, so re-running only fills in
    what is missing rather than re-billing every comparison.
    """
    _require_document(conn, document_id)
    try:
        summary = link_document_facts(conn, document_id)
    except (ExtractionError, EmbeddingError) as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return LinkingSummaryOut(**summary.as_dict())


@router.get(
    "/{document_id}/pages/{page_number}/image",
    response_class=FileResponse,
    summary="Rendered PNG of one page",
)
def get_page_image(
    document_id: int,
    page_number: int,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> FileResponse:
    """Render a 1-based page to PNG at PAGE_RENDER_SCALE.

    Cached on disk under the document hash: the source PDF is immutable and
    content-addressed, so the image is too. Coordinates from
    GET /facts/{id}/evidence are already in this image's pixel space.
    """
    document = _require_document(conn, document_id)
    pdf_path = settings.upload_path / f"{document.sha256}.pdf"
    try:
        rendered = render_page(pdf_path, page_number, document.sha256)
    except PageRenderError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        rendered.path,
        media_type="image/png",
        headers={
            # Content-addressed and scale-tagged, so it can be cached hard.
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Page-Width": str(rendered.width),
            "X-Page-Height": str(rendered.height),
            "X-Render-Scale": str(rendered.scale),
        },
    )


@router.post(
    "/{document_id}/reground",
    summary="Recompute page and bbox for this document's facts",
)
def reground(
    document_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> dict[str, int]:
    """Re-locate every stored quote in the PDF.

    Uses no model calls, so it is free to re-run -- after a change to the
    locator, or if grounding looks wrong. Facts are untouched apart from their
    page and bounding box.
    """
    document = _require_document(conn, document_id)
    return reground_document_facts(conn, document)
