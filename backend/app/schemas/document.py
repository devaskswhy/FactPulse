"""Document and chunk schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentBase(BaseModel):
    filename: str = Field(..., description="Original filename as uploaded.")
    title: str | None = Field(None, description="Document title, from PDF metadata or inferred.")
    page_count: int | None = Field(None, ge=0, description="Number of pages in the PDF.")
    status: str = Field(
        "uploaded",
        description="Free-text pipeline status: uploaded | parsing | chunked | extracted | linked | failed.",
    )


class DocumentCreate(DocumentBase):
    sha256: str = Field(..., description="SHA-256 of the file bytes; used to dedupe uploads.")


class Document(DocumentBase):
    id: int
    sha256: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class ChunkBase(BaseModel):
    page_start: int | None = Field(None, ge=0, description="First page this chunk covers.")
    page_end: int | None = Field(None, ge=0, description="Last page this chunk covers.")
    text: str = Field(..., description="Chunk text as extracted from the PDF.")
    token_count: int | None = Field(None, ge=0, description="Approximate token count.")


class ChunkCreate(ChunkBase):
    document_id: int


class Chunk(ChunkBase):
    id: int
    document_id: int

    model_config = {"from_attributes": True}


class DocumentDetail(Document):
    """A document plus counts that the list view does not need."""

    chunk_count: int = Field(0, ge=0, description="Number of chunks stored for this document.")


class DocumentList(BaseModel):
    total: int = Field(..., ge=0, description="Total documents stored.")
    documents: list[Document] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    """Result of POST /documents.

    `deduplicated` is True when the sha256 matched a document already ingested;
    in that case the existing document is returned and nothing was re-parsed.
    """

    document: Document
    chunk_count: int = Field(..., ge=0)
    deduplicated: bool = Field(
        ..., description="True when this exact file had already been ingested."
    )


class ChunkList(BaseModel):
    document_id: int
    total: int = Field(..., ge=0)
    chunks: list[Chunk] = Field(default_factory=list)


class RechunkResponse(BaseModel):
    document_id: int
    chunk_count: int = Field(..., ge=0, description="Chunks after re-chunking.")
