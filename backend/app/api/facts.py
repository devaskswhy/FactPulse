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
from app.schemas.fact import Fact, FactList, ReviewList, SchemaResponse

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
