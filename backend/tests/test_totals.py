"""Corpus-wide rollups: one function, read by the app header AND the landing
page's "by the numbers" section, so the two can never disagree.
"""

from __future__ import annotations

from app.db import repository as repo


def _add_fact(conn, document_id, *, evidence_strength=None) -> int:
    return repo.insert_fact(
        conn, document_id=document_id, chunk_id=None, fact_type="governance",
        subject="Acme Corp", statement="Acme Corp statement.",
        normalized_value=None, unit=None, time_scope=None,
        quote="Acme Corp statement.", page_number=1, bbox=None, confidence=0.9,
        evidence_strength=evidence_strength,
    )


def test_relationships_and_evidence_are_broken_down_by_kind(conn):
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="totals1", page_count=1
    )
    a = _add_fact(conn, document_id, evidence_strength="full")
    b = _add_fact(conn, document_id, evidence_strength="partial")
    c = _add_fact(conn, document_id, evidence_strength="full")
    repo.insert_relationship(
        conn, fact_id_a=a, fact_id_b=b, relationship_type="corroborates",
        rationale="r", confidence=0.9,
    )
    repo.insert_relationship(
        conn, fact_id_a=b, fact_id_b=c, relationship_type="corroborates",
        rationale="r", confidence=0.9,
    )
    repo.insert_relationship(
        conn, fact_id_a=a, fact_id_b=c, relationship_type="contradicts",
        rationale="r", confidence=0.9,
    )

    totals = repo.knowledge_layer_totals(conn)

    assert totals["relationships_by_type"] == {"corroborates": 2, "contradicts": 1}
    assert totals["evidence_by_strength"] == {"full": 2, "partial": 1}


def test_facts_with_no_evidence_assessment_are_not_counted(conn):
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="totals2", page_count=1
    )
    _add_fact(conn, document_id, evidence_strength=None)

    totals = repo.knowledge_layer_totals(conn)

    assert totals["evidence_by_strength"] == {}
    assert totals["facts"] == 1, "the fact itself is still counted, just not by tier"


def test_an_empty_corpus_reports_empty_breakdowns_not_missing_keys(conn):
    totals = repo.knowledge_layer_totals(conn)

    assert totals["relationships_by_type"] == {}
    assert totals["evidence_by_strength"] == {}
    assert totals["relationships"] == 0
