"""The subject registry: which spellings the canonicalizer has folded together.

Mirrors `SchemaResponse` in fact.py -- that one shows the fact_type vocabulary
the corpus has actually produced, this one shows the subject identities it has
actually produced. Both are observed, not designed: nothing here is a fixed
list of expected entities, only a summary of what canonicalize_subject() did
to the subjects that were actually written.
"""

from pydantic import BaseModel, Field


class SubjectVariant(BaseModel):
    """One raw spelling folded into a canonical subject, and how often it appears."""

    subject: str
    fact_count: int = Field(..., ge=0)


class SubjectGroup(BaseModel):
    """Every raw spelling that canonicalized to the same key."""

    canonical: str = Field(
        ..., description="The matching key -- see services/entity.py. Not for display."
    )
    variants: list[SubjectVariant] = Field(default_factory=list)
    fact_count: int = Field(..., ge=0, description="Facts across all variants.")
    example_fact_id: int


class SubjectRegistryResponse(BaseModel):
    total_canonical: int = Field(..., ge=0)
    total_facts: int = Field(..., ge=0, description="Facts with a resolvable subject.")
    merged_count: int = Field(
        ...,
        ge=0,
        description="Canonical subjects folded from more than one raw spelling.",
    )
    subjects: list[SubjectGroup] = Field(default_factory=list)
