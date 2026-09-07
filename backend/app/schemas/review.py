"""Review queue schemas."""

from datetime import datetime

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
