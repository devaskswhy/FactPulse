"""Review queue schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Advisory, not enforced.
KNOWN_ISSUE_TYPES = (
    "ungrounded_quote",    # quote not found verbatim in the source page
    "low_confidence",      # extractor was unsure
    "contradiction",       # a contradicts-relationship a human should adjudicate
    "parse_failure",       # chunk could not be parsed
    "ambiguous_scope",     # time/scope could not be pinned down
)


class ReviewItemBase(BaseModel):
    issue_type: str = Field(
        ...,
        description="Free text. Typically one of: " + ", ".join(KNOWN_ISSUE_TYPES) + ".",
    )
    note: str | None = Field(None, description="Human-readable detail about the issue.")
    resolved: bool = False


class ReviewItemCreate(ReviewItemBase):
    fact_id: int | None = None
    chunk_id: int | None = None


class ReviewItem(ReviewItemBase):
    id: int
    fact_id: int | None = None
    chunk_id: int | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReviewFactContext(BaseModel):
    """The fact an item is about, with just enough to judge it."""

    fact_id: int
    fact_type: str
    subject: str | None = None
    statement: str
    normalized_value: str | None = None
    unit: str | None = None
    time_scope: str | None = None
    confidence: float | None = None
    quote: str | None = None
    page_number: int | None = None
    document_id: int
    document_title: str | None = None


class ReviewChunkContext(BaseModel):
    """The source text an item came from, when there is no fact to point at."""

    chunk_id: int
    page_start: int | None = None
    page_end: int | None = None
    text_excerpt: str = Field(
        ..., description="Leading portion of the chunk text, for context."
    )
    document_id: int
    document_title: str | None = None


class ReviewRelationshipContext(BaseModel):
    """The two facts a relationship judgement struggled with."""

    relationship_id: int | None = None
    relationship_type: str | None = None
    rationale: str | None = None
    other_fact: ReviewFactContext | None = None


class ReviewQueueEntry(BaseModel):
    """One queue item with the context needed to act on it without digging."""

    id: int
    issue_type: str = Field(
        ...,
        description=(
            "unverified_quote, ungrounded_quote, low_confidence, "
            "ambiguous_unit, borderline_confidence, or extraction_failed."
        ),
    )
    note: str | None = None
    resolved: bool = False
    created_at: datetime
    resolution_action: str | None = None
    resolution_note: str | None = None
    resolved_at: datetime | None = None

    fact: ReviewFactContext | None = Field(
        None, description="Present when the item is about a specific fact."
    )
    chunk: ReviewChunkContext | None = Field(
        None, description="Present when the item is about a chunk, e.g. a failed call."
    )
    relationship: ReviewRelationshipContext | None = Field(
        None, description="Present when the item concerns a relationship judgement."
    )
    evidence_url: str | None = Field(
        None, description="Where to fetch the highlighted page for this fact."
    )


class ReviewQueueList(BaseModel):
    total: int = Field(..., ge=0)
    by_issue_type: dict[str, int] = Field(
        default_factory=dict, description="Counts within the current filter."
    )
    entries: list[ReviewQueueEntry] = Field(default_factory=list)


class FactCorrection(BaseModel):
    """Corrected fields for action='edited'. Omitted fields are left alone."""

    fact_type: str | None = None
    subject: str | None = None
    statement: str | None = None
    normalized_value: str | None = None
    unit: str | None = None
    time_scope: str | None = None
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class ResolveRequest(BaseModel):
    action: Literal["accepted", "rejected", "edited"] = Field(
        ...,
        description=(
            "accepted: the fact is fine as extracted. "
            "rejected: it is wrong and the fact is deleted. "
            "edited: a correction is supplied in `correction`."
        ),
    )
    resolution_note: str = Field(
        ..., min_length=1, description="Why this decision was made."
    )
    correction: FactCorrection | None = Field(
        None, description="Required when action is 'edited'."
    )


class ResolveResponse(BaseModel):
    id: int
    resolved: bool
    action: str
    resolution_note: str
    resolved_at: datetime
    fact_deleted: bool = Field(
        False, description="True when action was 'rejected' and the fact was removed."
    )
    fact_updated: bool = Field(
        False, description="True when action was 'edited' and the fact was rewritten."
    )
    updated_fields: list[str] = Field(
        default_factory=list, description="Fields changed by an edit."
    )
