"""SQL for documents and chunks.

Plain functions taking an open connection, so the caller controls the
transaction boundary. Rows come back as sqlite3.Row and are mapped to pydantic
models at the API edge.
"""

from __future__ import annotations

import sqlite3

from app.schemas.document import Chunk, Document
from app.schemas.fact import BBox, Fact, FactAttribute, FactType, Grounding
from app.schemas.relationship import Relationship
from app.schemas.review import ReviewItem
from app.services.chunker import ChunkDraft

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
) -> int:
    x0, y0, x1, y1 = bbox if bbox else (None, None, None, None)
    cur = conn.execute(
        "INSERT INTO facts (document_id, chunk_id, fact_type, subject, statement, "
        "normalized_value, unit, time_scope, quote, page_number, "
        "bbox_x0, bbox_y0, bbox_x1, bbox_y1, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            document_id, chunk_id, fact_type, subject, statement,
            normalized_value, unit, time_scope, quote, page_number,
            x0, y0, x1, y1, confidence,
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
        clauses.append("subject LIKE ?")
        params.append(f"%{subject}%")
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
        clauses.append("subject LIKE ?")
        params.append(f"%{subject}%")
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


def get_fact_context(conn: sqlite3.Connection, fact_id: int) -> sqlite3.Row | None:
    """A fact joined with its document title, for building the classifier prompt."""
    return conn.execute(
        "SELECT f.*, d.title AS document_title, d.filename AS document_filename "
        "FROM facts f JOIN documents d ON d.id = f.document_id WHERE f.id = ?",
        (fact_id,),
    ).fetchone()
