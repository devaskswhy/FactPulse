"""SQL for documents and chunks.

Plain functions taking an open connection, so the caller controls the
transaction boundary. Rows come back as sqlite3.Row and are mapped to pydantic
models at the API edge.
"""

from __future__ import annotations

import sqlite3

from app.schemas.document import Chunk, Document
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
