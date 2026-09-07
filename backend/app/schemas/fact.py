"""Fact, grounding, attribute and fact-type schemas.

fact_type is `str`, not an Enum -- see docs/ARCHITECTURE.md. Validating it
against a closed set here would defeat the whole design.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.review import ReviewItem


class BBox(BaseModel):
    """Bounding box of the source quote on its page, in PDF points."""

    x0: float
    y0: float
    x1: float
    y1: float


class Grounding(BaseModel):
    """Where a fact came from in the source document."""

    quote: str | None = Field(None, description="Verbatim span supporting the fact.")
    page_number: int | None = Field(None, ge=0, description="1-based page the quote is on.")
    bbox: BBox | None = Field(None, description="Quote's bounding box on that page.")


class FactAttribute(BaseModel):
    """One EAV row: an open-ended qualifier on a fact."""

    key: str = Field(..., description="Attribute name, e.g. 'segment', 'basis', 'restated'.")
    value: str | None = Field(None, description="Attribute value, always stored as text.")


class FactAttributeOut(FactAttribute):
    id: int
    fact_id: int


class FactBase(BaseModel):
    fact_type: str = Field(
        ...,
        description=(
            "Free-text kind of fact, e.g. 'financial_metric', 'headcount', "
            "'regulatory_deadline'. Deliberately not an enum."
        ),
    )
    subject: str | None = Field(None, description="What the fact is about.")
    statement: str = Field(..., description="The fact as a single self-contained sentence.")
    normalized_value: str | None = Field(
        None, description="Canonical value as text, so non-numeric facts fit too."
    )
    unit: str | None = Field(None, description="Unit of normalized_value, e.g. 'USD_millions'.")
    time_scope: str | None = Field(
        None, description="Period the fact holds over, e.g. 'FY2024', '2024-Q3', 'as-of 2025-01-31'."
    )
    confidence: float | None = Field(None, ge=0.0, le=1.0, description="Extractor confidence.")


class FactCreate(FactBase):
    document_id: int
    chunk_id: int | None = None
    grounding: Grounding | None = None
    attributes: list[FactAttribute] = Field(default_factory=list)


class Fact(FactBase):
    id: int
    document_id: int
    chunk_id: int | None = None
    grounding: Grounding | None = None
    attributes: list[FactAttribute] = Field(default_factory=list)
    created_at: datetime

    model_config = {"from_attributes": True}


class FactType(BaseModel):
    """A row of the fact_types registry -- observed types, not permitted ones."""

    name: str
    first_seen_at: datetime
    example_fact_id: int | None = None
    fact_count: int = 0

    model_config = {"from_attributes": True}


class FactList(BaseModel):
    total: int = Field(..., ge=0, description="Facts matching the filter, before paging.")
    facts: list[Fact] = Field(default_factory=list)


class ExtractionSummaryOut(BaseModel):
    """What one extraction run did. Returned by upload and by /extract."""

    document_id: int
    chunks_processed: int = Field(..., ge=0)
    chunks_failed: int = Field(..., ge=0, description="Chunks whose model call failed.")
    facts_inserted: int = Field(..., ge=0)
    facts_grounded: int = Field(
        ..., ge=0, description="Facts whose quote verified AND was located in the PDF."
    )
    facts_unverified: int = Field(
        ..., ge=0, description="Facts whose quote was not found in the source chunk."
    )
    facts_low_confidence: int = Field(
        ..., ge=0, description="Facts below the review confidence threshold."
    )
    review_items: int = Field(..., ge=0, description="Rows added to review_queue.")
    fact_types: list[str] = Field(
        default_factory=list,
        description="Distinct fact_type labels the model invented in this run.",
    )


class SchemaResponse(BaseModel):
    """The fact_types registry: the vocabulary the corpus has actually produced.

    This is observed, not permitted. Nothing constrains facts to these types --
    the registry exists so the evolving vocabulary is visible.
    """

    total_types: int = Field(..., ge=0)
    total_facts: int = Field(..., ge=0)
    fact_types: list[FactType] = Field(default_factory=list)


class ReviewList(BaseModel):
    total: int = Field(..., ge=0)
    items: list[ReviewItem] = Field(default_factory=list)
