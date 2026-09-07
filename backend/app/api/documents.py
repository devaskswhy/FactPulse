"""Document ingestion and inspection: pipeline steps 1-3."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, Response

from app.core.config import settings
from app.db import repository as repo
from app.db.database import db_dependency
from app.schemas.document import (
    ChunkList,
    DocumentDetail,
    DocumentList,
    DocumentUploadResponse,
    RechunkResponse,
)
from app.services.ingest import EmptyPdfError, ingest_pdf, rechunk_document
from app.services.pdf import PdfParseError

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

    try:
        result = ingest_pdf(conn, filename=file.filename or "untitled.pdf", data=data)
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
    )


@router.get("", response_model=DocumentList, summary="List ingested documents")
def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> DocumentList:
    return DocumentList(
        total=repo.count_documents(conn),
        documents=repo.list_documents(conn, limit=limit, offset=offset),
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
    return Response(status_code=status.HTTP_204_NO_CONTENT)
