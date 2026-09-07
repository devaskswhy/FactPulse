"""SQLite access.

Raw sqlite3 rather than an ORM: the point of this schema is that fact_type and
fact_attributes keys are open sets decided at extraction time, and mapping that
onto ORM classes buys nothing. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a connection with the pragmas we always want."""
    path = Path(db_path) if db_path else settings.database_file
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Connection as a context manager; commits on success, rolls back on error."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> Path:
    """Apply schema.sql. Idempotent -- every statement is CREATE ... IF NOT EXISTS."""
    path = Path(db_path) if db_path else settings.database_file
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn(path) as conn:
        conn.executescript(sql)
    migrate(path)
    return path


# Columns added to existing tables after the first release. schema.sql covers
# fresh databases; this covers ones already on disk. Keyed by table, each entry
# is (column, DDL type). Adding a nullable column is the only migration shape
# supported here -- anything more involved deserves a real migration tool.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "review_queue": [
        ("resolution_action", "TEXT"),
        ("resolution_note", "TEXT"),
        ("resolved_at", "TEXT"),
    ],
}


def migrate(db_path: Path | str | None = None) -> list[str]:
    """Add columns missing from an existing database. Idempotent."""
    applied: list[str] = []
    with get_conn(db_path) as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {
                row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if not existing:
                continue  # table not created yet; schema.sql will handle it
            for name, ddl in columns:
                if name not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                    applied.append(f"{table}.{name}")
    return applied


def table_names(db_path: Path | str | None = None) -> list[str]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    return [r["name"] for r in rows]


# FastAPI dependency
def db_dependency() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":  # python -m app.db.database
    p = init_db()
    print(f"initialized {p}")
    for t in table_names():
        print(f"  - {t}")
