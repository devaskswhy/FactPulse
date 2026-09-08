"""Cross-document relationship schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.fact import Grounding

# Not an Enum, for the same reason fact_type isn't: the reconciliation model may
# discover categories we did not anticipate. Kept here as documentation and as a
# suggestion for the UI's legend.
KNOWN_RELATIONSHIP_TYPES = (
    "corroborates",      # same claim, independently stated
    "contradicts",       # incompatible claims about the same subject+scope
    "reconcilable",      # differ only by context: time, scope, units, basis
    "refines",           # one is a more specific version of the other
    "supersedes",        # a later statement replaces an earlier one
    "unrelated",         # looked similar, is not
)


class RelationshipBase(BaseModel):
    relationship_type: str = Field(
        ...,
        description=(
            "Free text. Typically one of: "
            + ", ".join(KNOWN_RELATIONSHIP_TYPES)
            + " -- but not constrained to them."
        ),
    )
    rationale: str | None = Field(
        None, description="Why the model judged the pair this way, in one or two sentences."
    )
    confidence: float | None = Field(None, ge=0.0, le=1.0)


class RelationshipCreate(RelationshipBase):
    fact_id_a: int
    fact_id_b: int


class Relationship(RelationshipBase):
    id: int
    fact_id_a: int
    fact_id_b: int
    created_at: datetime

    model_config = {"from_attributes": True}


class RelatedFact(BaseModel):
    """One relationship, joined with the fact on the other end of it.

    Carries enough of the related fact and its source for a UI to render a full
    side-by-side comparison -- statement, values, document, page, quote -- so
    the frontend does not need a second request per relationship.
    """

    relationship_id: int
    relationship_type: str = Field(
        ..., description="corroborates, contradicts, reconciled, or supersedes."
    )
    rationale: str | None = Field(
        None,
        description=(
            "Concrete explanation citing the values and periods from both "
            "facts. For a reconciled pair it is prefixed with the reconciling "
            "dimension in square brackets."
        ),
    )
    relationship_confidence: float | None = Field(None, ge=0.0, le=1.0)
    direction: str = Field(
        "outgoing",
        description=(
            "Which end of the stored pair the requested fact sits on: "
            "'outgoing' if it is fact_id_a, 'incoming' if it is fact_id_b. "
            "Only meaningful for asymmetric types -- for a supersedes, "
            "'outgoing' means the requested fact is the current one and "
            "'incoming' means it is the one that was replaced."
        ),
    )

    # The fact on the other side.
    fact_id: int
    fact_type: str
    subject: str | None = None
    statement: str
    normalized_value: str | None = None
    unit: str | None = None
    time_scope: str | None = None
    confidence: float | None = None
    grounding: Grounding | None = None

    # Where it came from.
    document_id: int
    document_title: str | None = None
    document_filename: str


class RelationshipList(BaseModel):
    fact_id: int
    total: int = Field(..., ge=0)
    relationships: list[RelatedFact] = Field(default_factory=list)


class LinkingSummaryOut(BaseModel):
    """What one linking run did."""

    document_id: int
    facts_embedded: int = Field(..., ge=0)
    facts_compared: int = Field(
        ..., ge=0, description="Facts that had at least one candidate above threshold."
    )
    pairs_evaluated: int = Field(
        ..., ge=0, description="Candidate pairs sent to the classifier."
    )
    relationships_created: int = Field(..., ge=0)
    pool_size: int = Field(
        0,
        ge=0,
        description=(
            "Existing facts from other documents this run compared against. "
            "Loaded once per document, never rewritten."
        ),
    )
    by_type: dict[str, int] = Field(
        default_factory=dict,
        description="Verdict counts including unrelated, which is not stored.",
    )
    errors: int = Field(..., ge=0)
