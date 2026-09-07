"""Ingestion progress schemas."""

from pydantic import BaseModel, Field


class ProgressSnapshot(BaseModel):
    """Where one ingestion has got to.

    `percent` is progress within the CURRENT PHASE, not the whole job. A single
    global percentage would have to predict extraction time, which depends on
    model latency and rate-limit backoff, so it would either lie or stall.
    Phase plus N-of-M is honest.
    """

    document_id: int | None = Field(
        None,
        description=(
            "Negative until the document row exists -- parsing starts before "
            "the insert."
        ),
    )
    filename: str = ""
    phase: str = Field(
        ...,
        description=(
            "queued, parsing, chunking, extracting, embedding, linking, "
            "checking, done, or failed."
        ),
    )
    message: str = Field("", description="Human-readable detail for the phase.")
    current: int = Field(0, ge=0, description="Units completed in this phase.")
    total: int = Field(0, ge=0, description="Units in this phase; 0 when unknown.")
    percent: float | None = Field(
        None, ge=0.0, le=100.0, description="Progress within the current phase."
    )

    # Running tallies, so a client can show the layer growing live.
    pages: int = Field(0, ge=0)
    chunks: int = Field(0, ge=0)
    facts: int = Field(0, ge=0)
    relationships: int = Field(0, ge=0)

    started_at: float
    updated_at: float
    elapsed: float = Field(..., description="Seconds since the run started.")
    error: str | None = None


class ProgressList(BaseModel):
    total: int = Field(..., ge=0)
    runs: list[ProgressSnapshot] = Field(default_factory=list)
