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
