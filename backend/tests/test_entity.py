"""Canonical subject resolution: which spellings name the same entity.

The brief's own example is an address written two ways across two documents.
The corpus turned up a real instance independently -- "Delhivery" and
"Delhivery Limited" -- which is the case `test_the_corpus_actually_has_one`
locks in.

The design rule, stated once here and enforced by every other test: fold only
MECHANICAL differences (case, punctuation, a legal suffix, an address
abbreviation), never semantic ones. "RBI" and "Reserve Bank of India" are
never merged -- that identity is not verifiable from the string, and guessing
risks merging two facts that are not actually about the same subject.
"""

from __future__ import annotations

import pytest

from app.db import repository as repo
from app.services.entity import canonicalize_subject


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme Corp", "Acme Corporation"),
        ("Acme Corp", "ACME CORP."),
        ("Acme Corp Ltd", "Acme Corporation"),
        ("14 Charter Street", "14 Charter St."),
        ("14 Charter St", "14, Charter Street"),
        ("The Reserve Bank of India", "Reserve Bank of India"),
        ("Acme & Sons", "Acme and Sons"),
        ("Delhivery", "Delhivery Limited"),
    ],
)
def test_mechanically_equivalent_spellings_fold_together(a, b):
    assert canonicalize_subject(a) == canonicalize_subject(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Acme Corp", "Beta Corp"),
        # "Group" and "Holdings" are not stripped: a holding company and its
        # subsidiary are often distinct subjects that happen to share a name,
        # and folding them could merge two real, different entities.
        ("Acme Group", "Acme Holdings"),
        ("Acme Group", "Acme Corp"),
        # An acronym is not verifiably the same subject from the string alone.
        ("RBI", "Reserve Bank of India"),
    ],
)
def test_different_subjects_are_not_folded_together(a, b):
    assert canonicalize_subject(a) != canonicalize_subject(b)


def test_none_and_blank_resolve_to_none():
    assert canonicalize_subject(None) is None
    assert canonicalize_subject("") is None
    assert canonicalize_subject("   ") is None


def test_the_key_is_idempotent():
    """Canonicalizing an already-canonical key must not change it further.

    This is what makes re-running the backfill safe: it recomputes from
    `subject`, never from a previous `canonical_subject`, but the property
    still has to hold or a second pass could drift.
    """
    key = canonicalize_subject("Acme Corp Ltd.")
    assert canonicalize_subject(key) == key


def test_a_legal_suffix_mid_name_is_left_alone():
    """Suffixes are stripped from the END only.

    A word that happens to match a legal-form suffix but sits in the middle of
    the name is far more likely to be part of the actual name there.
    """
    assert canonicalize_subject("Company Co-op Federation") == "company co op federation"


# ------------------------------------------------------------ storage + read


def test_insert_fact_computes_the_key_automatically(conn):
    """Every caller gets it for free -- it is derived from `subject`, not passed."""
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e1", page_count=1
    )
    fact_id = repo.insert_fact(
        conn,
        document_id=document_id,
        chunk_id=None,
        fact_type="corporate",
        subject="Acme Corp",
        statement="Acme Corp is based in Zurich.",
        normalized_value=None,
        unit=None,
        time_scope=None,
        quote="Acme Corp is based in Zurich.",
        page_number=1,
        bbox=None,
        confidence=0.9,
    )

    fact = repo.get_fact(conn, fact_id)
    assert fact.canonical_subject == canonicalize_subject("Acme Corp")


def test_the_corpus_actually_has_one(conn):
    """The end-to-end case the corpus turned up on its own.

    Two documents write the same company as "Delhivery" and "Delhivery
    Limited". Filtering by one spelling must surface facts stored under
    both -- this is the entire point of the feature.
    """
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e2", page_count=1
    )

    def add(subject: str) -> int:
        return repo.insert_fact(
            conn,
            document_id=document_id,
            chunk_id=None,
            fact_type="corporate",
            subject=subject,
            statement=f"{subject} reported results.",
            normalized_value=None,
            unit=None,
            time_scope=None,
            quote=f"{subject} reported results.",
            page_number=1,
            bbox=None,
            confidence=0.9,
        )

    short = add("Delhivery")
    long = add("Delhivery Limited")

    found = {f.id for f in repo.list_facts(conn, subject="Delhivery Limited")}
    assert {short, long} <= found

    found_short = {f.id for f in repo.list_facts(conn, subject="Delhivery")}
    assert long in found_short, "the substring arm alone already covers this direction"


def test_subject_filter_still_matches_a_subject_with_nothing_to_fold(conn):
    """A subject the canonicalizer changes nothing about must still be findable.

    Guards against a regression where adding the canonical arm to the query
    accidentally narrows what a plain substring search used to return.
    """
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e3", page_count=1
    )
    repo.insert_fact(
        conn,
        document_id=document_id,
        chunk_id=None,
        fact_type="macro",
        subject="unemployment rate",
        statement="The unemployment rate was 4.1 percent.",
        normalized_value="4.1",
        unit="percent",
        time_scope=None,
        quote="The unemployment rate was 4.1 percent.",
        page_number=1,
        bbox=None,
        confidence=0.9,
    )

    assert repo.count_facts(conn, subject="unemployment") == 1


# -------------------------------------------------------------- the registry


def test_subject_registry_groups_variants_under_one_canonical_key(conn):
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e4", page_count=1
    )

    def add(subject: str) -> None:
        repo.insert_fact(
            conn,
            document_id=document_id,
            chunk_id=None,
            fact_type="corporate",
            subject=subject,
            statement=f"{subject} statement.",
            normalized_value=None,
            unit=None,
            time_scope=None,
            quote=f"{subject} statement.",
            page_number=1,
            bbox=None,
            confidence=0.9,
        )

    add("Acme Corp")
    add("Acme Corp")
    add("Acme Corporation")
    add("Beta Inc")

    registry = repo.subject_registry(conn)
    by_key = {g.canonical: g for g in registry}

    acme = by_key[canonicalize_subject("Acme Corp")]
    assert acme.fact_count == 3
    assert {v.subject for v in acme.variants} == {"Acme Corp", "Acme Corporation"}
    # Most common spelling listed first, since a caller displays variants[0].
    assert acme.variants[0].subject == "Acme Corp"

    beta = by_key[canonicalize_subject("Beta Inc")]
    assert len(beta.variants) == 1, "a subject nobody wrote a second way is still listed"


def test_an_address_written_two_ways_is_the_briefs_own_example(conn):
    """Same real-world thing, two spellings, from two different documents."""
    doc_a = repo.insert_document(
        conn, filename="a.pdf", title="Filing A", sha256="e5", page_count=1
    )
    doc_b = repo.insert_document(
        conn, filename="b.pdf", title="Filing B", sha256="e6", page_count=1
    )
    repo.insert_fact(
        conn, document_id=doc_a, chunk_id=None, fact_type="governance",
        subject="14 Charter Street", statement="The registered office is at 14 Charter Street.",
        normalized_value=None, unit=None, time_scope=None,
        quote="The registered office is at 14 Charter Street.",
        page_number=1, bbox=None, confidence=0.9,
    )
    repo.insert_fact(
        conn, document_id=doc_b, chunk_id=None, fact_type="governance",
        subject="14 Charter St.", statement="Registered office: 14 Charter St.",
        normalized_value=None, unit=None, time_scope=None,
        quote="Registered office: 14 Charter St.",
        page_number=1, bbox=None, confidence=0.9,
    )

    registry = repo.subject_registry(conn)
    match = [g for g in registry if g.canonical == canonicalize_subject("14 Charter Street")]
    assert len(match) == 1
    assert match[0].fact_count == 2
    assert {v.subject for v in match[0].variants} == {
        "14 Charter Street", "14 Charter St.",
    }


# --------------------------------------------------------------- the backfill


def test_backfill_is_idempotent(conn):
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e7", page_count=1
    )
    repo.insert_fact(
        conn, document_id=document_id, chunk_id=None, fact_type="corporate",
        subject="Acme Corp", statement="Acme Corp statement.",
        normalized_value=None, unit=None, time_scope=None,
        quote="Acme Corp statement.", page_number=1, bbox=None, confidence=0.9,
    )

    assert repo.backfill_canonical_subjects(conn) == 0, (
        "insert_fact already computed it; a fresh backfill should touch nothing"
    )


def test_backfill_repairs_rows_written_before_the_column_existed(conn):
    """The realistic case: a corpus ingested before canonical_subject existed."""
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="e8", page_count=1
    )
    fact_id = repo.insert_fact(
        conn, document_id=document_id, chunk_id=None, fact_type="corporate",
        subject="Acme Corp", statement="Acme Corp statement.",
        normalized_value=None, unit=None, time_scope=None,
        quote="Acme Corp statement.", page_number=1, bbox=None, confidence=0.9,
    )
    # Simulate a pre-migration row: the key was never computed.
    conn.execute("UPDATE facts SET canonical_subject = NULL WHERE id = ?", (fact_id,))

    updated = repo.backfill_canonical_subjects(conn)

    assert updated == 1
    assert repo.get_fact(conn, fact_id).canonical_subject == canonicalize_subject("Acme Corp")
