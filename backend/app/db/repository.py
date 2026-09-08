"""SQL for documents and chunks.

Plain functions taking an open connection, so the caller controls the
transaction boundary. Rows come back as sqlite3.Row and are mapped to pydantic
models at the API edge.
"""

from __future__ import annotations

import sqlite3

from app.schemas.document import Chunk, Document
from app.schemas.entity import SubjectGroup, SubjectVariant
from app.schemas.fact import BBox, Fact, FactAttribute, FactType, Grounding
from app.schemas.relationship import Relationship
from app.schemas.review import ReviewItem
from app.services.chunker import ChunkDraft
from app.services.entity import canonicalize_subject

# ------------------------------------------------------------------ documents


def _to_document(row: sqlite3.Row) -> Document:
    return Document(
        id=row["id"],
        filename=row["filename"],
        title=row["title"],
        sha256=row["sha256"],
        uploaded_at=row["uploaded_at"],
        page_count=row["page_count"],
        status=row["status"],
    )


def get_document_by_sha256(conn: sqlite3.Connection, sha256: str) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE sha256 = ?", (sha256,)).fetchone()
    return _to_document(row) if row else None


def get_document(conn: sqlite3.Connection, document_id: int) -> Document | None:
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    return _to_document(row) if row else None


def list_documents(conn: sqlite3.Connection, limit: int = 50, offset: int = 0) -> list[Document]:
    rows = conn.execute(
        "SELECT * FROM documents ORDER BY uploaded_at DESC, id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [_to_document(r) for r in rows]


def count_documents(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"]


def insert_document(
    conn: sqlite3.Connection,
    *,
    filename: str,
    title: str | None,
    sha256: str,
    page_count: int,
    status: str = "uploaded",
) -> int:
    cur = conn.execute(
        "INSERT INTO documents (filename, title, sha256, page_count, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (filename, title, sha256, page_count, status),
    )
    return int(cur.lastrowid)


def set_document_status(conn: sqlite3.Connection, document_id: int, status: str) -> None:
    conn.execute("UPDATE documents SET status = ? WHERE id = ?", (status, document_id))


def delete_document(conn: sqlite3.Connection, document_id: int) -> bool:
    """Delete a document. Chunks, facts, embeddings and relationships follow via
    ON DELETE CASCADE, which is why connections set PRAGMA foreign_keys = ON."""
    cur = conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------- chunks


def _to_chunk(row: sqlite3.Row) -> Chunk:
    return Chunk(
        id=row["id"],
        document_id=row["document_id"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        text=row["text"],
        token_count=row["token_count"],
    )


def insert_chunks(
    conn: sqlite3.Connection, document_id: int, drafts: list[ChunkDraft]
) -> list[int]:
    ids: list[int] = []
    for draft in drafts:
        cur = conn.execute(
            "INSERT INTO chunks (document_id, page_start, page_end, text, token_count) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                document_id,
                draft.page_start,
                draft.page_end,
                draft.text,
                draft.token_count,
            ),
        )
        ids.append(int(cur.lastrowid))
    return ids


def list_chunks(conn: sqlite3.Connection, document_id: int) -> list[Chunk]:
    rows = conn.execute(
        "SELECT * FROM chunks WHERE document_id = ? ORDER BY page_start, id",
        (document_id,),
    ).fetchall()
    return [_to_chunk(r) for r in rows]


def count_chunks(conn: sqlite3.Connection, document_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (document_id,)
    ).fetchone()["n"]


def delete_chunks(conn: sqlite3.Connection, document_id: int) -> int:
    """Remove a document's chunks so it can be re-parsed from scratch."""
    cur = conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    return cur.rowcount


# ---------------------------------------------------------------------- facts


def _column(row: sqlite3.Row, name: str):
    """Read a column that may not exist on rows from an older query.

    Some joined queries select an explicit column list; this keeps _to_fact
    usable against those without every caller having to be updated in lockstep.
    """
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def _to_fact(row: sqlite3.Row, attributes: list[FactAttribute] | None = None) -> Fact:
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
    return Fact(
        id=row["id"],
        document_id=row["document_id"],
        chunk_id=row["chunk_id"],
        fact_type=row["fact_type"],
        subject=row["subject"],
        statement=row["statement"],
        normalized_value=row["normalized_value"],
        unit=row["unit"],
        time_scope=row["time_scope"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        grounding=grounding,
        attributes=attributes or [],
        canonical_subject=_column(row, "canonical_subject"),
        evidence_strength=_column(row, "evidence_strength"),
        evidence_gaps=[
            g for g in (_column(row, "evidence_gaps") or "").split("; ") if g
        ],
    )


def insert_fact(
    conn: sqlite3.Connection,
    *,
    document_id: int,
    chunk_id: int | None,
    fact_type: str,
    subject: str | None,
    statement: str,
    normalized_value: str | None,
    unit: str | None,
    time_scope: str | None,
    quote: str | None,
    page_number: int | None,
    bbox: tuple[float, float, float, float] | None,
    confidence: float | None,
    evidence_strength: str | None = None,
    evidence_gaps: str | None = None,
) -> int:
    x0, y0, x1, y1 = bbox if bbox else (None, None, None, None)
    # Computed here, not passed in, so every caller gets it for free and it
    # can never drift out of sync with `subject` -- see services/entity.py.
    canonical_subject = canonicalize_subject(subject)
    cur = conn.execute(
        "INSERT INTO facts (document_id, chunk_id, fact_type, subject, statement, "
        "normalized_value, unit, time_scope, quote, page_number, "
        "bbox_x0, bbox_y0, bbox_x1, bbox_y1, confidence, "
        "evidence_strength, evidence_gaps, canonical_subject) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            document_id, chunk_id, fact_type, subject, statement,
            normalized_value, unit, time_scope, quote, page_number,
            x0, y0, x1, y1, confidence,
            evidence_strength, evidence_gaps, canonical_subject,
        ),
    )
    return int(cur.lastrowid)


def insert_fact_attributes(
    conn: sqlite3.Connection, fact_id: int, attributes: dict[str, str]
) -> int:
    """Write the EAV rows for one fact. Keys are whatever the model chose."""
    rows = [(fact_id, str(k), str(v)) for k, v in attributes.items() if str(k).strip()]
    if rows:
        conn.executemany(
            "INSERT INTO fact_attributes (fact_id, key, value) VALUES (?, ?, ?)", rows
        )
    return len(rows)


def get_fact_attributes(conn: sqlite3.Connection, fact_id: int) -> list[FactAttribute]:
    rows = conn.execute(
        "SELECT key, value FROM fact_attributes WHERE fact_id = ? ORDER BY id",
        (fact_id,),
    ).fetchall()
    return [FactAttribute(key=r["key"], value=r["value"]) for r in rows]


def get_fact(conn: sqlite3.Connection, fact_id: int) -> Fact | None:
    row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
    if row is None:
        return None
    return _to_fact(row, get_fact_attributes(conn, fact_id))


def list_facts(
    conn: sqlite3.Connection,
    *,
    document_id: int | None = None,
    fact_type: str | None = None,
    subject: str | None = None,
    min_confidence: float | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Fact]:
    clauses: list[str] = []
    params: list[object] = []
    if document_id is not None:
        clauses.append("document_id = ?")
        params.append(document_id)
    if fact_type:
        clauses.append("fact_type = ?")
        params.append(fact_type)
    if subject:
        # Matches the raw substring OR the canonical form, so a search for
        # "Acme Corp" also finds facts stored under "Acme Corporation" --
        # see services/entity.py. The substring arm is kept for subjects the
        # canonicalizer had nothing to fold (a single free-text mention with
        # no legal suffix or address word).
        clauses.append("(subject LIKE ? OR canonical_subject = ?)")
        params.append(f"%{subject}%")
        params.append(canonicalize_subject(subject))
    if min_confidence is not None:
        clauses.append("confidence >= ?")
        params.append(min_confidence)

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM facts{where} ORDER BY id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()

    # One query for every attribute of the page of facts, rather than one per
    # fact. The EAV table is the thing most likely to be read hot.
    ids = [r["id"] for r in rows]
    by_fact: dict[int, list[FactAttribute]] = {i: [] for i in ids}
    if ids:
        placeholders = ",".join("?" * len(ids))
        for attr in conn.execute(
            f"SELECT fact_id, key, value FROM fact_attributes "
            f"WHERE fact_id IN ({placeholders}) ORDER BY id",
            ids,
        ):
            by_fact[attr["fact_id"]].append(
                FactAttribute(key=attr["key"], value=attr["value"])
            )

    return [_to_fact(r, by_fact.get(r["id"], [])) for r in rows]


def count_facts(
    conn: sqlite3.Connection,
    *,
    document_id: int | None = None,
    fact_type: str | None = None,
    subject: str | None = None,
    min_confidence: float | None = None,
) -> int:
    clauses: list[str] = []
    params: list[object] = []
    if document_id is not None:
        clauses.append("document_id = ?")
        params.append(document_id)
    if fact_type:
        clauses.append("fact_type = ?")
        params.append(fact_type)
    if subject:
        clauses.append("(subject LIKE ? OR canonical_subject = ?)")
        params.append(f"%{subject}%")
        params.append(canonicalize_subject(subject))
    if min_confidence is not None:
        clauses.append("confidence >= ?")
        params.append(min_confidence)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return conn.execute(f"SELECT COUNT(*) AS n FROM facts{where}", params).fetchone()["n"]


def delete_facts_for_document(conn: sqlite3.Connection, document_id: int) -> int:
    """Clear a document's facts so extraction can be re-run.

    fact_attributes, embeddings and review_queue rows cascade from facts. The
    fact_types registry is rebuilt separately by resync_fact_types().
    """
    cur = conn.execute("DELETE FROM facts WHERE document_id = ?", (document_id,))
    return cur.rowcount


# ----------------------------------------------------------------- fact_types


def upsert_fact_type(conn: sqlite3.Connection, name: str, example_fact_id: int) -> None:
    """Register a fact type, or bump its count if already known.

    This is a registry maintained on write, not a constraint: nothing stops a
    fact carrying a type absent from this table, and the ON CONFLICT branch
    deliberately leaves first_seen_at and example_fact_id alone so the first
    sighting stays the recorded one.
    """
    conn.execute(
        "INSERT INTO fact_types (name, example_fact_id, fact_count) VALUES (?, ?, 1) "
        "ON CONFLICT(name) DO UPDATE SET fact_count = fact_count + 1",
        (name, example_fact_id),
    )


def list_fact_types(conn: sqlite3.Connection) -> list[FactType]:
    rows = conn.execute(
        "SELECT * FROM fact_types ORDER BY fact_count DESC, name"
    ).fetchall()
    return [
        FactType(
            name=r["name"],
            first_seen_at=r["first_seen_at"],
            example_fact_id=r["example_fact_id"],
            fact_count=r["fact_count"],
        )
        for r in rows
    ]


def resync_fact_types(conn: sqlite3.Connection) -> int:
    """Rebuild the registry from the facts table.

    Needed after deleting facts, since the counters are maintained on write and
    a cascade delete does not decrement them. Types whose facts are all gone are
    dropped; first_seen_at is preserved for those that survive.
    """
    conn.execute(
        "DELETE FROM fact_types WHERE name NOT IN (SELECT DISTINCT fact_type FROM facts)"
    )
    rows = conn.execute(
        "SELECT fact_type, COUNT(*) AS n, MIN(id) AS example FROM facts GROUP BY fact_type"
    ).fetchall()
    for row in rows:
        conn.execute(
            "INSERT INTO fact_types (name, example_fact_id, fact_count) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET fact_count = excluded.fact_count, "
            "example_fact_id = COALESCE(fact_types.example_fact_id, excluded.example_fact_id)",
            (row["fact_type"], row["example"], row["n"]),
        )


# --------------------------------------------------------------- subjects


def backfill_canonical_subjects(
    conn: sqlite3.Connection, document_id: int | None = None
) -> int:
    """Recompute `canonical_subject` for every row, correcting drift.

    Pure and idempotent, like `assess_evidence_backfill`: canonicalize_subject
    is a function of `subject` alone, so this can be re-run freely -- after
    the corpus existed before the column did, or after the canonicalizer's
    rules changed and old rows carry a stale key. Only rows whose stored value
    is actually wrong are written, so a no-op run touches nothing.
    """
    where = " WHERE document_id = ?" if document_id is not None else ""
    params: tuple[object, ...] = (document_id,) if document_id is not None else ()
    rows = conn.execute(
        f"SELECT id, subject, canonical_subject FROM facts{where}", params
    ).fetchall()

    updated = 0
    for row in rows:
        want = canonicalize_subject(row["subject"])
        if want != row["canonical_subject"]:
            conn.execute(
                "UPDATE facts SET canonical_subject = ? WHERE id = ?",
                (want, row["id"]),
            )
            updated += 1
    return updated


def subject_registry(conn: sqlite3.Connection) -> list[SubjectGroup]:
    """Every canonical subject, with the raw spellings folded into it.

    Aggregated on read, like `superseded_by_map`, rather than maintained as a
    table: there is nothing here a plain GROUP BY cannot answer, and a second
    write path to keep in sync would be a second way for it to drift.

    Grouped by (canonical_subject, subject) rather than concatenating variant
    strings in SQL -- a subject can itself contain a comma (an address does),
    which would make a delimited string ambiguous to split back apart.
    """
    rows = conn.execute(
        "SELECT canonical_subject, subject, COUNT(*) AS n, MIN(id) AS example_id "
        "FROM facts WHERE canonical_subject IS NOT NULL AND canonical_subject != '' "
        "GROUP BY canonical_subject, subject"
    ).fetchall()

    groups: dict[str, dict[str, object]] = {}
    for row in rows:
        key = row["canonical_subject"]
        bucket = groups.setdefault(
            key, {"variants": [], "fact_count": 0, "example_fact_id": row["example_id"]}
        )
        bucket["variants"].append(
            SubjectVariant(subject=row["subject"], fact_count=row["n"])
        )
        bucket["fact_count"] += row["n"]
        bucket["example_fact_id"] = min(bucket["example_fact_id"], row["example_id"])

    result = [
        SubjectGroup(
            canonical=key,
            variants=sorted(v["variants"], key=lambda x: -x.fact_count),
            fact_count=v["fact_count"],
            example_fact_id=v["example_fact_id"],
        )
        for key, v in groups.items()
    ]
    return sorted(result, key=lambda g: (-g.fact_count, g.canonical))
    return len(rows)


# --------------------------------------------------------------- review_queue


def insert_review_item(
    conn: sqlite3.Connection,
    *,
    fact_id: int | None,
    chunk_id: int | None,
    issue_type: str,
    note: str | None,
) -> int:
    cur = conn.execute(
        "INSERT INTO review_queue (fact_id, chunk_id, issue_type, note) "
        "VALUES (?, ?, ?, ?)",
        (fact_id, chunk_id, issue_type, note),
    )
    return int(cur.lastrowid)


def list_review_queue(
    conn: sqlite3.Connection,
    *,
    resolved: bool | None = None,
    issue_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ReviewItem]:
    clauses: list[str] = []
    params: list[object] = []
    if resolved is not None:
        clauses.append("resolved = ?")
        params.append(1 if resolved else 0)
    if issue_type:
        clauses.append("issue_type = ?")
        params.append(issue_type)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM review_queue{where} ORDER BY id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [
        ReviewItem(
            id=r["id"],
            fact_id=r["fact_id"],
            chunk_id=r["chunk_id"],
            issue_type=r["issue_type"],
            note=r["note"],
            resolved=bool(r["resolved"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]


def count_review_queue(
    conn: sqlite3.Connection, *, resolved: bool | None = None
) -> int:
    if resolved is None:
        return conn.execute("SELECT COUNT(*) AS n FROM review_queue").fetchone()["n"]
    return conn.execute(
        "SELECT COUNT(*) AS n FROM review_queue WHERE resolved = ?",
        (1 if resolved else 0,),
    ).fetchone()["n"]


# ----------------------------------------------------------------- embeddings


def upsert_embedding(
    conn: sqlite3.Connection, fact_id: int, vector: bytes, dim: int
) -> None:
    conn.execute(
        "INSERT INTO embeddings (fact_id, vector, dim) VALUES (?, ?, ?) "
        "ON CONFLICT(fact_id) DO UPDATE SET vector = excluded.vector, dim = excluded.dim",
        (fact_id, vector, dim),
    )


def get_embedding(conn: sqlite3.Connection, fact_id: int) -> tuple[bytes, int] | None:
    row = conn.execute(
        "SELECT vector, dim FROM embeddings WHERE fact_id = ?", (fact_id,)
    ).fetchone()
    return (row["vector"], row["dim"]) if row else None


def count_embeddings(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM embeddings").fetchone()["n"]


def load_candidate_embeddings(
    conn: sqlite3.Connection, exclude_document_id: int
) -> list[sqlite3.Row]:
    """Every embedded fact NOT from the given document.

    Same-document facts are excluded because two sentences from one report
    restating the same figure are a property of that document's prose, not a
    cross-document corroboration. The interesting comparisons are between
    sources.
    """
    return conn.execute(
        "SELECT e.fact_id, e.vector, e.dim, f.document_id "
        "FROM embeddings e JOIN facts f ON f.id = e.fact_id "
        "WHERE f.document_id != ?",
        (exclude_document_id,),
    ).fetchall()


# --------------------------------------------------------------- relationships


def _to_relationship(row: sqlite3.Row) -> Relationship:
    return Relationship(
        id=row["id"],
        fact_id_a=row["fact_id_a"],
        fact_id_b=row["fact_id_b"],
        relationship_type=row["relationship_type"],
        rationale=row["rationale"],
        confidence=row["confidence"],
        created_at=row["created_at"],
    )


def insert_relationship(
    conn: sqlite3.Connection,
    *,
    fact_id_a: int,
    fact_id_b: int,
    relationship_type: str,
    rationale: str | None,
    confidence: float | None,
) -> int | None:
    """Write one relationship, or return None if the pair is already recorded.

    The UNIQUE index on (a, b, type) makes re-running the linking step
    idempotent; INSERT OR IGNORE turns the collision into a no-op rather than
    an error.
    """
    cur = conn.execute(
        "INSERT OR IGNORE INTO relationships "
        "(fact_id_a, fact_id_b, relationship_type, rationale, confidence) "
        "VALUES (?, ?, ?, ?, ?)",
        (fact_id_a, fact_id_b, relationship_type, rationale, confidence),
    )
    return int(cur.lastrowid) if cur.rowcount else None


def relationship_exists(
    conn: sqlite3.Connection, fact_id_a: int, fact_id_b: int
) -> bool:
    """Has this unordered pair already been judged, in either direction?"""
    row = conn.execute(
        "SELECT 1 FROM relationships WHERE "
        "(fact_id_a = ? AND fact_id_b = ?) OR (fact_id_a = ? AND fact_id_b = ?) "
        "LIMIT 1",
        (fact_id_a, fact_id_b, fact_id_b, fact_id_a),
    ).fetchone()
    return row is not None


def count_relationships(
    conn: sqlite3.Connection, *, relationship_type: str | None = None
) -> int:
    if relationship_type:
        return conn.execute(
            "SELECT COUNT(*) AS n FROM relationships WHERE relationship_type = ?",
            (relationship_type,),
        ).fetchone()["n"]
    return conn.execute("SELECT COUNT(*) AS n FROM relationships").fetchone()["n"]


def list_relationships(
    conn: sqlite3.Connection,
    *,
    relationship_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Relationship]:
    where = " WHERE relationship_type = ?" if relationship_type else ""
    params: list[object] = [relationship_type] if relationship_type else []
    rows = conn.execute(
        f"SELECT * FROM relationships{where} ORDER BY id LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [_to_relationship(r) for r in rows]


def list_related_facts(conn: sqlite3.Connection, fact_id: int) -> list[sqlite3.Row]:
    """Every relationship touching this fact, joined with the other fact and its
    source document.

    Returns enough in one query for the UI to render a side-by-side comparison
    without a second round trip: the relationship, the other fact in full, and
    the document it came from. The CASE picks whichever end of the pair is not
    the fact being asked about, so direction does not matter to the caller.
    """
    return conn.execute(
        """
        SELECT
            r.id                AS relationship_id,
            r.relationship_type AS relationship_type,
            r.rationale         AS rationale,
            r.confidence        AS relationship_confidence,
            r.created_at        AS related_at,
            r.fact_id_a         AS fact_id_a,
            r.fact_id_b         AS fact_id_b,
            f.*,
            d.id                AS doc_id,
            d.title             AS document_title,
            d.filename          AS document_filename
        FROM relationships r
        JOIN facts f
          ON f.id = CASE WHEN r.fact_id_a = :fid THEN r.fact_id_b ELSE r.fact_id_a END
        JOIN documents d ON d.id = f.document_id
        WHERE r.fact_id_a = :fid OR r.fact_id_b = :fid
        ORDER BY r.id
        """,
        {"fid": fact_id},
    ).fetchall()


def superseded_by_map(conn: sqlite3.Connection) -> dict[int, int]:
    """{superseded fact id -> the fact that replaced it}.

    Derived, never stored on the fact. A "still current?" flag on `facts` would
    have to be rewritten every time a new document arrives and would be wrong
    in between; the relationships table already holds the answer, and this is
    one small query over an indexed column.

    Relies on the SUPERSEDES orientation invariant that link.py establishes:
    fact_id_a is the current fact, fact_id_b the one it replaced. Direction is
    load-bearing here in a way it is not for any other relationship type.
    """
    rows = conn.execute(
        "SELECT fact_id_a, fact_id_b FROM relationships "
        "WHERE relationship_type = 'supersedes' ORDER BY id"
    ).fetchall()
    # Later rows win: if a fact is superseded twice, the most recently recorded
    # replacement is the one to point a reader at.
    return {row["fact_id_b"]: row["fact_id_a"] for row in rows}


def get_fact_context(conn: sqlite3.Connection, fact_id: int) -> sqlite3.Row | None:
    """A fact joined with its document title, for building the classifier prompt."""
    return conn.execute(
        "SELECT f.*, d.title AS document_title, d.filename AS document_filename "
        "FROM facts f JOIN documents d ON d.id = f.document_id WHERE f.id = ?",
        (fact_id,),
    ).fetchone()


# ------------------------------------------------- review queue (with context)


def list_review_rows(
    conn: sqlite3.Connection,
    *,
    resolved: bool | None = False,
    issue_type: str | None = None,
    document_id: int | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """Review items joined with their fact, chunk and source documents.

    One query rather than N+1: the queue view needs the fact or the chunk text
    behind every row, and fetching those per row would make an unusable page.
    LEFT JOINs throughout because an item may reference a fact, a chunk, or
    neither.
    """
    clauses: list[str] = []
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if resolved is not None:
        clauses.append("q.resolved = :resolved")
        params["resolved"] = 1 if resolved else 0
    if issue_type:
        clauses.append("q.issue_type = :issue_type")
        params["issue_type"] = issue_type
    if document_id is not None:
        clauses.append("COALESCE(fd.id, cd.id) = :document_id")
        params["document_id"] = document_id
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""

    return conn.execute(
        f"""
        SELECT
            q.id, q.issue_type, q.note, q.resolved, q.created_at,
            q.resolution_action, q.resolution_note, q.resolved_at,
            q.fact_id, q.chunk_id,

            f.fact_type, f.subject, f.statement, f.normalized_value, f.unit,
            f.time_scope, f.confidence, f.quote, f.page_number,
            f.document_id      AS fact_document_id,
            fd.title           AS fact_document_title,

            c.page_start, c.page_end,
            SUBSTR(c.text, 1, 600) AS chunk_excerpt,
            c.document_id      AS chunk_document_id,
            cd.title           AS chunk_document_title
        FROM review_queue q
        LEFT JOIN facts     f  ON f.id  = q.fact_id
        LEFT JOIN documents fd ON fd.id = f.document_id
        LEFT JOIN chunks    c  ON c.id  = q.chunk_id
        LEFT JOIN documents cd ON cd.id = c.document_id
        {where}
        ORDER BY q.resolved, q.id
        LIMIT :limit OFFSET :offset
        """,
        params,
    ).fetchall()


def review_counts_by_issue(
    conn: sqlite3.Connection, *, resolved: bool | None = False
) -> dict[str, int]:
    where = "" if resolved is None else " WHERE resolved = ?"
    params: tuple = () if resolved is None else (1 if resolved else 0,)
    rows = conn.execute(
        f"SELECT issue_type, COUNT(*) AS n FROM review_queue{where} "
        f"GROUP BY issue_type ORDER BY n DESC",
        params,
    ).fetchall()
    return {r["issue_type"]: r["n"] for r in rows}


def get_review_row(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM review_queue WHERE id = ?", (item_id,)
    ).fetchone()


def resolve_review_item(
    conn: sqlite3.Connection,
    item_id: int,
    *,
    action: str,
    resolution_note: str,
) -> bool:
    cur = conn.execute(
        "UPDATE review_queue SET resolved = 1, resolution_action = ?, "
        "resolution_note = ?, resolved_at = datetime('now') WHERE id = ?",
        (action, resolution_note, item_id),
    )
    return cur.rowcount > 0


def update_fact_fields(
    conn: sqlite3.Connection, fact_id: int, fields: dict[str, object]
) -> list[str]:
    """Write corrected values onto a fact.

    Only columns a human can sensibly correct are writable: grounding
    (quote/page/bbox) is derived from the document and must not be hand-edited,
    or the fact would claim evidence it does not have.
    """
    allowed = {
        "fact_type",
        "subject",
        "statement",
        "normalized_value",
        "unit",
        "time_scope",
        "confidence",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return []

    assignments = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE facts SET {assignments} WHERE id = ?",
        (*updates.values(), fact_id),
    )
    return sorted(updates)


def delete_fact(conn: sqlite3.Connection, fact_id: int) -> bool:
    """Remove a fact rejected in review. Attributes, embedding and
    relationships cascade; other review rows on the same fact go with it."""
    cur = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
    return cur.rowcount > 0


def find_relationship_for_fact(
    conn: sqlite3.Connection, fact_id: int
) -> sqlite3.Row | None:
    """The first relationship touching a fact, joined with the other end.

    Used to give a relationship-related queue item its two-sided context.
    """
    return conn.execute(
        """
        SELECT
            r.id AS relationship_id, r.relationship_type, r.rationale,
            f.id AS other_fact_id, f.fact_type, f.subject, f.statement,
            f.normalized_value, f.unit, f.time_scope, f.confidence,
            f.quote, f.page_number, f.document_id, d.title AS document_title
        FROM relationships r
        JOIN facts f
          ON f.id = CASE WHEN r.fact_id_a = :fid THEN r.fact_id_b ELSE r.fact_id_a END
        JOIN documents d ON d.id = f.document_id
        WHERE r.fact_id_a = :fid OR r.fact_id_b = :fid
        ORDER BY r.id LIMIT 1
        """,
        {"fid": fact_id},
    ).fetchone()


# ------------------------------------------------------- workspace / rollups


def list_documents_with_counts(
    conn: sqlite3.Connection, limit: int = 50, offset: int = 0
) -> list[sqlite3.Row]:
    """Every document with its fact, relationship and open-review counts.

    One query with correlated subqueries rather than a per-document rollup:
    the workspace view is the first thing loaded and must not cost N queries.

    A relationship is counted for a document when EITHER end of the pair is one
    of its facts, so a relationship spanning two documents shows on both -- that
    is the point of the view. Summing the column therefore double-counts
    cross-document links, which is why the response also carries a corpus-wide
    total taken from the relationships table directly.
    """
    return conn.execute(
        """
        SELECT
            d.*,
            (SELECT COUNT(*) FROM chunks c WHERE c.document_id = d.id)
                AS chunk_count,
            (SELECT COUNT(*) FROM facts f WHERE f.document_id = d.id)
                AS fact_count,
            (SELECT COUNT(*) FROM embeddings e
               JOIN facts f2 ON f2.id = e.fact_id
              WHERE f2.document_id = d.id)
                AS embedded_count,
            (SELECT COUNT(*) FROM relationships r
              WHERE r.fact_id_a IN (SELECT id FROM facts WHERE document_id = d.id)
                 OR r.fact_id_b IN (SELECT id FROM facts WHERE document_id = d.id))
                AS relationship_count,
            (SELECT COUNT(*) FROM review_queue q
               LEFT JOIN facts f3 ON f3.id = q.fact_id
               LEFT JOIN chunks c3 ON c3.id = q.chunk_id
              WHERE q.resolved = 0
                AND COALESCE(f3.document_id, c3.document_id) = d.id)
                AS open_review_count
        FROM documents d
        ORDER BY d.uploaded_at DESC, d.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()


def knowledge_layer_totals(conn: sqlite3.Connection) -> dict[str, int]:
    """Corpus-wide counts for the workspace header."""
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "documents": one("SELECT COUNT(*) FROM documents"),
        "facts": one("SELECT COUNT(*) FROM facts"),
        "fact_types": one("SELECT COUNT(*) FROM fact_types"),
        "embeddings": one("SELECT COUNT(*) FROM embeddings"),
        "relationships": one("SELECT COUNT(*) FROM relationships"),
        "cross_document_relationships": one(
            "SELECT COUNT(*) FROM relationships r "
            "JOIN facts a ON a.id = r.fact_id_a "
            "JOIN facts b ON b.id = r.fact_id_b "
            "WHERE a.document_id != b.document_id"
        ),
        "open_review_items": one("SELECT COUNT(*) FROM review_queue WHERE resolved = 0"),
    }


# --------------------------------------------------- evidence strength backfill


def set_evidence_assessment(
    conn: sqlite3.Connection, fact_id: int, strength: str, gaps: list[str]
) -> None:
    conn.execute(
        "UPDATE facts SET evidence_strength = ?, evidence_gaps = ? WHERE id = ?",
        (strength, "; ".join(gaps), fact_id),
    )


def facts_missing_evidence_assessment(
    conn: sqlite3.Connection, document_id: int | None = None
) -> list[sqlite3.Row]:
    """Facts with no evidence_strength yet.

    The assessment is a pure local computation, so backfilling costs nothing but
    a table scan -- no model calls, no quota. That is what makes it safe to add
    to a corpus that was already ingested.
    """
    where = "WHERE evidence_strength IS NULL"
    params: tuple = ()
    if document_id is not None:
        where += " AND document_id = ?"
        params = (document_id,)
    return conn.execute(
        f"SELECT id, statement, quote, subject, normalized_value, unit, time_scope "
        f"FROM facts {where}",
        params,
    ).fetchall()


def evidence_strength_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(evidence_strength, 'unassessed') AS s, COUNT(*) AS n "
        "FROM facts GROUP BY s"
    ).fetchall()
    return {r["s"]: r["n"] for r in rows}
