"""The offline demo seed: restoring the corpus without a single model call.

Extracting the committed corpus cost real Gemini calls. Nobody who clones this
repo, and nothing that redeploys it onto a fresh volume, should have to pay
that cost again just to see a populated knowledge layer -- scripts/seed_demo.py
exports the current database as data-only SQL and replays it into an empty one.

Two things matter more than the mechanism working at all:

  1. Ids survive exactly. docs/DEMO_CASES.md cites specific fact and
     relationship ids; a seed that renumbered them would silently break every
     citation in that document.
  2. It never touches a database that already has a real corpus in it, seeded
     or not, unless told to.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.core.config import settings
from app.db import repository as repo
from app.db.database import get_conn, init_db
from scripts.seed_demo import DEFAULT_SEED_PATH, export_seed, import_seed


def _seed_one_document(conn: sqlite3.Connection) -> dict[str, int]:
    """A tiny, self-contained corpus: one document, two facts, one relationship.

    Deliberately not the real 335-fact corpus -- these tests check the
    mechanism, which a small fixture proves just as well and much faster.
    """
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="seedtest", page_count=1
    )
    older = repo.insert_fact(
        conn, document_id=document_id, chunk_id=None, fact_type="governance",
        subject="Acme Corp", statement="Acme Corp statement one.",
        normalized_value=None, unit=None, time_scope="FY2023",
        quote="Acme Corp statement one.", page_number=1, bbox=None, confidence=0.9,
    )
    newer = repo.insert_fact(
        conn, document_id=document_id, chunk_id=None, fact_type="governance",
        subject="Acme Corp", statement="Acme Corp statement two.",
        normalized_value=None, unit=None, time_scope="FY2024",
        quote="Acme Corp statement two.", page_number=1, bbox=None, confidence=0.9,
    )
    repo.upsert_embedding(conn, older, b"\x00" * (768 * 4), 768)
    repo.upsert_embedding(conn, newer, b"\x01" * (768 * 4), 768)
    repo.upsert_fact_type(conn, "governance", older)
    repo.upsert_fact_type(conn, "governance", newer)
    repo.insert_relationship(
        conn, fact_id_a=newer, fact_id_b=older, relationship_type="supersedes",
        rationale="Later statement.", confidence=0.9,
    )
    conn.commit()
    return {"document_id": document_id, "older": older, "newer": newer}


def test_export_then_import_round_trips_exactly(conn, tmp_path):
    ids = _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"

    export_seed(settings.database_file, seed_path)

    fresh_db = tmp_path / "fresh.db"
    init_db(fresh_db)
    with get_conn(fresh_db) as fresh_conn:
        counts = import_seed(fresh_conn, seed_path)

        assert counts["facts"] == 2
        assert counts["relationships"] == 1
        assert counts["embeddings"] == 2

        older = repo.get_fact(fresh_conn, ids["older"])
        assert older.id == ids["older"], "ids must survive the round trip exactly"
        assert older.statement == "Acme Corp statement one."
        assert older.time_scope == "FY2023"

        rel = fresh_conn.execute(
            "SELECT fact_id_a, fact_id_b, relationship_type FROM relationships"
        ).fetchone()
        assert rel["fact_id_a"] == ids["newer"]
        assert rel["fact_id_b"] == ids["older"]

        vector = fresh_conn.execute(
            "SELECT vector, dim FROM embeddings WHERE fact_id = ?", (ids["older"],)
        ).fetchone()
        assert vector["dim"] == 768
        assert vector["vector"] == b"\x00" * (768 * 4), "BLOB must survive byte-exact"


def test_autoincrement_continues_after_the_seeded_max_id(conn, tmp_path):
    """A fact inserted after seeding must not collide with a seeded id."""
    ids = _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"
    export_seed(settings.database_file, seed_path)

    fresh_db = tmp_path / "fresh.db"
    init_db(fresh_db)
    with get_conn(fresh_db) as fresh_conn:
        import_seed(fresh_conn, seed_path)
        new_document_id = repo.insert_document(
            fresh_conn, filename="new.pdf", title="New", sha256="newdoc", page_count=1
        )
        new_fact_id = repo.insert_fact(
            fresh_conn, document_id=new_document_id, chunk_id=None,
            fact_type="governance", subject="Beta Inc",
            statement="Beta Inc statement.", normalized_value=None, unit=None,
            time_scope=None, quote="Beta Inc statement.", page_number=1,
            bbox=None, confidence=0.9,
        )

    assert new_fact_id > max(ids["older"], ids["newer"])


def test_import_refuses_a_nonempty_database_without_force(conn, tmp_path):
    ids = _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"
    export_seed(settings.database_file, seed_path)

    with pytest.raises(RuntimeError, match="already has"):
        import_seed(conn, seed_path)  # conn's db already has the fixture data

    # Refused, so nothing changed -- still exactly the fixture's 2 facts.
    assert repo.count_facts(conn) == 2


def test_force_replaces_an_existing_corpus_without_duplicating(conn, tmp_path):
    _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"
    export_seed(settings.database_file, seed_path)

    import_seed(conn, seed_path, force=True)

    assert repo.count_facts(conn) == 2, "force replaces, it does not append"


def test_force_clears_fact_types_too(conn, tmp_path):
    """fact_types has no FK to facts, so a plain cascade would miss it.

    A stale fact_types row after a --force reseed would misreport the
    vocabulary GET /schema shows, even though every fact is otherwise correct.
    """
    _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"
    export_seed(settings.database_file, seed_path)

    import_seed(conn, seed_path, force=True)

    types = {r["name"] for r in conn.execute("SELECT name FROM fact_types")}
    assert types == {"governance"}, "no leftover type from a prior corpus"


def test_import_missing_seed_file_raises(conn, tmp_path):
    with pytest.raises(FileNotFoundError):
        import_seed(conn, tmp_path / "does-not-exist.sql")


# --------------------------------------------------- the committed artifact


def test_the_committed_seed_file_exists_and_is_data_only():
    """A missing seed is a silent feature loss, not a loud one -- catch it here."""
    assert DEFAULT_SEED_PATH.exists(), (
        "backend/seed/demo_corpus.sql is missing; run "
        "`python scripts/seed_demo.py export` and commit the result"
    )
    text = DEFAULT_SEED_PATH.read_text(encoding="utf-8")
    assert "CREATE TABLE" not in text, (
        "the seed must be data only -- schema.sql is the single source of "
        "truth for table structure"
    )


def test_the_committed_seed_reproduces_the_demo_corpus(tmp_path):
    """Pins docs/DEMO_CASES.md's citations against a real import, not a claim.

    If this ever fails, either the corpus changed and DEMO_CASES.md needs
    updating, or the seed file is stale and needs re-exporting.
    """
    fresh_db = tmp_path / "fresh.db"
    init_db(fresh_db)
    with get_conn(fresh_db) as conn:
        counts = import_seed(conn, DEFAULT_SEED_PATH)

        assert counts["documents"] == 5
        assert counts["facts"] == 335
        assert counts["relationships"] == 53

        # Fact 326: the case that motivated grounding sufficiency.
        fact_326 = repo.get_fact(conn, 326)
        assert fact_326.grounding.quote == "Nil"

        # Fact 238: the "Delhivery" / "Delhivery Limited" canonical-subject case.
        fact_238 = repo.get_fact(conn, 238)
        assert fact_238.canonical_subject == "delhivery"

        # Relationship 14: the genuine-contradiction demo case.
        rel_14 = conn.execute(
            "SELECT relationship_type FROM relationships WHERE id = 14"
        ).fetchone()
        assert rel_14["relationship_type"] == "contradicts"


# ------------------------------------------------------ startup integration


def test_seeding_on_startup_is_off_by_default_in_tests(conn):
    """The conftest fixture disables this -- confirms the guard actually holds."""
    assert settings.seed_demo_on_empty_db is False


def test_seed_if_empty_only_fires_on_a_genuinely_empty_database(conn, tmp_path, monkeypatch):
    import scripts.seed_demo as seed_module
    from app.main import _seed_if_empty

    _seed_one_document(conn)
    seed_path = tmp_path / "seed.sql"
    export_seed(settings.database_file, seed_path)

    monkeypatch.setattr(settings, "seed_demo_on_empty_db", True)
    # _seed_if_empty imports DEFAULT_SEED_PATH from this module at call time,
    # so patching it here is what points the function at the small test seed
    # instead of the real 335-fact one committed to the repo.
    monkeypatch.setattr(seed_module, "DEFAULT_SEED_PATH", seed_path)

    # Point at a brand new, genuinely empty database.
    empty_db = tmp_path / "empty.db"
    init_db(empty_db)
    monkeypatch.setattr(settings, "database_path", str(empty_db))

    _seed_if_empty()

    with get_conn(empty_db) as check_conn:
        assert check_conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2

    # Second call: the database is no longer empty, so nothing changes.
    _seed_if_empty()
    with get_conn(empty_db) as check_conn:
        assert check_conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 2
