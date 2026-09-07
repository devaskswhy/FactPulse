"""Reading extracted facts back out, plus the fact_types registry.

GET /schema is the interesting one: it reports the vocabulary the corpus has
actually produced rather than a vocabulary the application declared. Nothing
here validates fact_type against a list, because there is no list.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import repository as repo
from app.db.database import db_dependency
from app.schemas.fact import BBox, Fact, FactList, Grounding, ReviewList, SchemaResponse
from app.schemas.relationship import RelatedFact, RelationshipList

router = APIRouter(tags=["facts"])


@router.get("/facts", response_model=FactList, summary="List extracted facts")
def list_facts(
    document_id: int | None = Query(None, description="Restrict to one document."),
    fact_type: str | None = Query(
        None,
        description=(
            "Exact fact_type to filter by. Free text -- see GET /schema for the "
            "labels that actually exist."
        ),
    ),
    subject: str | None = Query(None, description="Substring match on subject."),
    min_confidence: float | None = Query(
        None, ge=0.0, le=1.0, description="Only facts at or above this confidence."
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> FactList:
    filters = {
        "document_id": document_id,
        "fact_type": fact_type,
        "subject": subject,
        "min_confidence": min_confidence,
    }
    return FactList(
        total=repo.count_facts(conn, **filters),
        facts=repo.list_facts(conn, limit=limit, offset=offset, **filters),
    )


@router.get(
    "/facts/{fact_id}",
    response_model=Fact,
    summary="Get one fact with its grounding and attributes",
)
def get_fact(
    fact_id: int, conn: sqlite3.Connection = Depends(db_dependency)
) -> Fact:
    fact = repo.get_fact(conn, fact_id)
    if fact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no fact {fact_id}")
    return fact


@router.get(
    "/schema",
    response_model=SchemaResponse,
    summary="The fact_types registry: what kinds of facts this corpus contains",
)
def get_schema(conn: sqlite3.Connection = Depends(db_dependency)) -> SchemaResponse:
    """Observed vocabulary, not permitted vocabulary.

    Types appear here as a side effect of facts being written. Near-duplicates
    with low counts are a signal that the vocabulary is drifting and may want
    review -- which is the point of keeping it visible rather than constrained.
    """
    types = repo.list_fact_types(conn)
    return SchemaResponse(
        total_types=len(types),
        total_facts=repo.count_facts(conn),
        fact_types=types,
    )


@router.get(
    "/review",
    response_model=ReviewList,
    summary="The review queue: facts the pipeline was not confident about",
)
def get_review_queue(
    resolved: bool | None = Query(None, description="Filter by resolved state."),
    issue_type: str | None = Query(
        None,
        description=(
            "Filter by issue type, e.g. unverified_quote, low_confidence, "
            "ungrounded_quote, extraction_failed. Free text."
        ),
    ),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> ReviewList:
    return ReviewList(
        total=repo.count_review_queue(conn, resolved=resolved),
        items=repo.list_review_queue(
            conn, resolved=resolved, issue_type=issue_type, limit=limit, offset=offset
        ),
    )


def _row_to_related(row) -> RelatedFact:
    """Map one joined relationship+fact+document row onto the response model."""
    bbox = None
    if row["bbox_x0"] is not None:
        bbox = BBox(
            x0=row["bbox_x0"], y0=row["bbox_y0"], x1=row["bbox_x1"], y1=row["bbox_y1"]
        )
    grounding = None
    if row["quote"] is not None or row["page_number"] is not None:
        grounding = Grounding(
            quote=row["quote"], page_number=row["page_number"], bbox=bbox
        )
    return RelatedFact(
        relationship_id=row["relationship_id"],
        relationship_type=row["relationship_type"],
        rationale=row["rationale"],
        relationship_confidence=row["relationship_confidence"],
        fact_id=row["id"],
        fact_type=row["fact_type"],
        subject=row["subject"],
        statement=row["statement"],
        normalized_value=row["normalized_value"],
        unit=row["unit"],
        time_scope=row["time_scope"],
        confidence=row["confidence"],
        grounding=grounding,
        document_id=row["document_id"],
        document_title=row["document_title"],
        document_filename=row["document_filename"],
    )


@router.get(
    "/facts/{fact_id}/relationships",
    response_model=RelationshipList,
    summary="Facts related to this one, across documents",
)
def get_fact_relationships(
    fact_id: int,
    relationship_type: str | None = Query(
        None, description="Filter to one type: corroborates, contradicts, reconciled."
    ),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> RelationshipList:
    """Every corroborating, contradicting or reconciled counterpart of a fact.

    Each entry carries the related fact in full plus its source document, page
    and quote, so a comparison view can be rendered without a follow-up request
    per relationship. Direction is not significant: a relationship stored as
    (a, b) is returned when asking about either end.
    """
    if repo.get_fact(conn, fact_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"no fact {fact_id}")

    rows = repo.list_related_facts(conn, fact_id)
    related = [_row_to_related(r) for r in rows]
    if relationship_type:
        related = [r for r in related if r.relationship_type == relationship_type]
    return RelationshipList(fact_id=fact_id, total=len(related), relationships=related)
