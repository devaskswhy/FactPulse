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
    scope: str = Field(
        "knowledge-layer",
        description=(
            "'knowledge-layer' when returning facts across every document "
            "(the default), or 'document' when filtered to one."
        ),
    )
    documents: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "document_id -> title, for the documents represented in `facts`. "
            "Lets a cross-document list show each fact's source without a "
            "lookup per row."
        ),
    )
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
    quota_exhausted: bool = Field(
        False,
        description=(
            "True when the run stopped early because the daily model quota "
            "was spent; the document's facts are incomplete."
        ),
    )
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


class PixelBBox(BaseModel):
    """A box in PIXELS of the rendered page image, not PDF points.

    Already scaled by the server so the frontend can draw it directly over the
    image from `page_image_url` with no conversion.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class EvidenceBundle(BaseModel):
    """Everything needed to show "this fact came from exactly here".

    bbox is in image pixels and page_image_width/height give the image it
    belongs to, so a frontend that scales the image to fit can scale the box by
    the same ratio.
    """

    fact: Fact
    page_image_url: str = Field(
        ..., description="URL of the rendered page PNG this bbox applies to."
    )
    page_image_width: int = Field(..., description="Rendered image width in pixels.")
    page_image_height: int = Field(..., description="Rendered image height in pixels.")
    render_scale: float = Field(
        ..., description="Pixels per PDF point used to render the page."
    )
    bbox: PixelBBox | None = Field(
        None,
        description=(
            "Highlight region in image pixels. Null when the quote could not be "
            "located in the PDF -- the fact is still returned, ungrounded."
        ),
    )
    bbox_pdf_points: BBox | None = Field(
        None, description="The same box in raw PDF points, for reference."
    )
    quote: str | None = Field(None, description="The verbatim source text.")
    document_id: int
    document_title: str | None = None
    document_filename: str
    page_number: int | None = Field(
        None, description="1-based page the quote appears on."
    )
    grounded: bool = Field(
        ..., description="True when a real bounding box was located for the quote."
    )
