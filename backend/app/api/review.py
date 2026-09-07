"""The review queue as a workflow, not a log.

Every entry carries the context needed to judge it without a second request:
the fact and its confidence, or the chunk text a failed call came from, or the
two facts a relationship judgement was made between.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import repository as repo
from app.db.database import db_dependency
from app.schemas.review import (
    ResolveRequest,
    ResolveResponse,
    ReviewChunkContext,
    ReviewFactContext,
    ReviewQueueEntry,
    ReviewQueueList,
    ReviewRelationshipContext,
)

router = APIRouter(tags=["review"])

# Issue types whose meaning is about a relationship judgement rather than the
# fact in isolation, so the entry is worth expanding with the other fact.
_RELATIONSHIP_ISSUES = {"contradiction", "relationship_uncertain"}


def _fact_context(row: sqlite3.Row) -> ReviewFactContext | None:
    if row["fact_id"] is None or row["statement"] is None:
        return None
    return ReviewFactContext(
        fact_id=row["fact_id"],
        fact_type=row["fact_type"],
        subject=row["subject"],
        statement=row["statement"],
        normalized_value=row["normalized_value"],
        unit=row["unit"],
        time_scope=row["time_scope"],
        confidence=row["confidence"],
        quote=row["quote"],
        page_number=row["page_number"],
        document_id=row["fact_document_id"],
        document_title=row["fact_document_title"],
    )


def _chunk_context(row: sqlite3.Row) -> ReviewChunkContext | None:
    if row["chunk_id"] is None or row["chunk_excerpt"] is None:
        return None
    return ReviewChunkContext(
        chunk_id=row["chunk_id"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        text_excerpt=row["chunk_excerpt"],
        document_id=row["chunk_document_id"],
        document_title=row["chunk_document_title"],
    )


def _relationship_context(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> ReviewRelationshipContext | None:
    if row["fact_id"] is None or row["issue_type"] not in _RELATIONSHIP_ISSUES:
        return None
    other = repo.find_relationship_for_fact(conn, row["fact_id"])
    if other is None:
        return None
    return ReviewRelationshipContext(
        relationship_id=other["relationship_id"],
        relationship_type=other["relationship_type"],
        rationale=other["rationale"],
        other_fact=ReviewFactContext(
            fact_id=other["other_fact_id"],
            fact_type=other["fact_type"],
            subject=other["subject"],
            statement=other["statement"],
            normalized_value=other["normalized_value"],
            unit=other["unit"],
            time_scope=other["time_scope"],
            confidence=other["confidence"],
            quote=other["quote"],
            page_number=other["page_number"],
            document_id=other["document_id"],
            document_title=other["document_title"],
        ),
    )


@router.get(
    "/review-queue",
    response_model=ReviewQueueList,
    summary="Unresolved review items, with the context to judge each one",
)
def get_review_queue(
    resolved: bool | None = Query(
        False,
        description=(
            "False (default) for the open queue, true for handled items, "
            "null/omitted-as-null for everything."
        ),
    ),
    issue_type: str | None = Query(None, description="Filter to one issue type."),
    document_id: int | None = Query(None, description="Filter to one document."),
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    conn: sqlite3.Connection = Depends(db_dependency),
) -> ReviewQueueList:
    rows = repo.list_review_rows(
        conn,
        resolved=resolved,
        issue_type=issue_type,
        document_id=document_id,
        limit=limit,
        offset=offset,
    )

    entries: list[ReviewQueueEntry] = []
    for row in rows:
        fact = _fact_context(row)
        entries.append(
            ReviewQueueEntry(
                id=row["id"],
                issue_type=row["issue_type"],
                note=row["note"],
                resolved=bool(row["resolved"]),
                created_at=row["created_at"],
                resolution_action=row["resolution_action"],
                resolution_note=row["resolution_note"],
                resolved_at=row["resolved_at"],
                fact=fact,
                chunk=_chunk_context(row),
                relationship=_relationship_context(conn, row),
                evidence_url=(
                    f"/facts/{row['fact_id']}/evidence" if fact is not None else None
                ),
            )
        )

    return ReviewQueueList(
        total=len(entries),
        by_issue_type=repo.review_counts_by_issue(conn, resolved=resolved),
        entries=entries,
    )


@router.post(
    "/review-queue/{item_id}/resolve",
    response_model=ResolveResponse,
    summary="Resolve one review item",
)
def resolve_review_item(
    item_id: int,
    body: ResolveRequest,
    conn: sqlite3.Connection = Depends(db_dependency),
) -> ResolveResponse:
    """Mark an item handled, and apply the reviewer's decision to the fact.

    - `accepted`: the fact stands as extracted.
    - `rejected`: the fact is deleted; attributes, embedding and relationships
      cascade with it.
    - `edited`: the supplied corrections are written onto the fact.

    Grounding fields are deliberately not editable. Quote, page and bbox are
    derived from the document, and letting a reviewer type over them would let
    a fact claim evidence the PDF does not support -- the opposite of what this
    system is for. A fact whose quote is wrong should be rejected, not patched.
    """
    row = repo.get_review_row(conn, item_id)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail=f"no review item {item_id}"
        )
    if row["resolved"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"review item {item_id} was already resolved as "
                f"{row['resolution_action']!r} at {row['resolved_at']}"
            ),
        )

    fact_id = row["fact_id"]
    fact_deleted = False
    fact_updated = False
    updated_fields: list[str] = []

    if body.action == "edited":
        if body.correction is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="action 'edited' requires a `correction` object",
            )
        if fact_id is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "this item is not about a specific fact, so there is "
                    "nothing to edit"
                ),
            )
        if repo.get_fact(conn, fact_id) is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"fact {fact_id} no longer exists",
            )
        updated_fields = repo.update_fact_fields(
            conn, fact_id, body.correction.model_dump(exclude_none=True)
        )
        if not updated_fields:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="`correction` contained no editable fields",
            )
        fact_updated = True
        # A corrected fact_type may be a label the registry has not seen.
        repo.resync_fact_types(conn)

    elif body.action == "rejected":
        if fact_id is not None:
            fact_deleted = repo.delete_fact(conn, fact_id)
            repo.resync_fact_types(conn)

    repo.resolve_review_item(
        conn, item_id, action=body.action, resolution_note=body.resolution_note
    )

    # Re-read so the timestamp returned is the one the database wrote. Deleting
    # the fact cascades its review rows away, in which case there is no row to
    # read back and the response reports the intended outcome.
    updated = repo.get_review_row(conn, item_id)
    resolved_at = updated["resolved_at"] if updated else None
    if resolved_at is None:
        from datetime import datetime, timezone

        resolved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    return ResolveResponse(
        id=item_id,
        resolved=True,
        action=body.action,
        resolution_note=body.resolution_note,
        resolved_at=resolved_at,
        fact_deleted=fact_deleted,
        fact_updated=fact_updated,
        updated_fields=updated_fields,
    )
