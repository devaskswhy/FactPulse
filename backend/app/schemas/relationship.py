"""Cross-document relationship schemas."""

from datetime import datetime

from pydantic import BaseModel, Field

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
