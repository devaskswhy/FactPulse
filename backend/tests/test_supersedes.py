"""The one asymmetric verdict: which fact replaced which.

Two things are being protected here. The first is that a period written any of
the several ways a real filing writes it still parses. The second, and the one
that would actually hurt, is that the arrow never gets stored backwards -- a
supersession pointing the wrong way does not read as uncertain, it reads as a
confident and false claim that the older fact is the current one.
"""

from __future__ import annotations

import pytest

from app.db import repository as repo
from app.services import temporal
from app.services.link import (
    CURRENT_A,
    CURRENT_CANDIDATE,
    SUPERSEDES,
    resolve_direction,
)


def fact(statement: str, time_scope: str | None = None) -> dict[str, object]:
    """The subset of a fact row `resolve_direction` reads."""
    return {"statement": statement, "time_scope": time_scope}


# ------------------------------------------------------- reading the calendar


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("FY2024", (2024, 0, 0)),
        ("2024", (2024, 0, 0)),
        # A fiscal span is its CLOSING year: FY2024-25 is later than FY2024.
        ("FY2024-25", (2025, 0, 0)),
        ("2024/25", (2025, 0, 0)),
        ("2024-2025", (2025, 0, 0)),
        # The two-digit tail is a suffix, not a year of its own.
        ("1999-00", (2000, 0, 0)),
        ("31 March 2024", (2024, 3, 31)),
        ("as at 31 March 2024", (2024, 3, 31)),
        ("2024-03-31", (2024, 3, 31)),
        ("March 31, 2024", (2024, 3, 31)),
        ("March 2024", (2024, 3, 0)),
        # A quarter is a period, so it resolves to the month it closes in.
        ("Q3 2024", (2024, 9, 0)),
        ("Q3 FY2024", (2024, 9, 0)),
        ("the year under review", None),
        ("", None),
        (None, None),
    ],
)
def test_periods_parse_however_they_are_written(text, expected):
    assert temporal.as_of(text) == expected


def test_an_iso_date_is_not_misread_as_a_fiscal_span():
    """Without ordered, non-overlapping matching, 2024-03-31 reads as 2024-03."""
    assert temporal.as_of("2024-03-31") == (2024, 3, 31)


def test_the_statement_is_a_fallback_when_there_is_no_scope():
    assert temporal.as_of(None, "resigned with effect from 12 March 2024") == (
        2024,
        3,
        12,
    )


def test_the_latest_point_in_a_statement_wins():
    """"grew from X in FY2023 to Y in FY2024" describes the FY2024 state."""
    assert temporal.as_of(None, "revenue grew from 3.1 in FY2023 to 4.2 in FY2024") == (
        2024,
        0,
        0,
    )


# ------------------------------------------------- refusing to rank the unrankable


def test_a_period_and_a_point_inside_it_are_not_ordered():
    """FY2024 vs March 2024: one contains the other, so neither is 'later'."""
    assert temporal.compare(temporal.as_of("FY2024"), temporal.as_of("March 2024")) == 0


def test_the_same_month_needs_days_to_be_ordered():
    assert (
        temporal.compare(temporal.as_of("March 2024"), temporal.as_of("31 March 2024"))
        == 0
    )


def test_differing_years_settle_it_whatever_else_is_unknown():
    assert temporal.compare(temporal.as_of("FY2023"), temporal.as_of("March 2024")) == -1


# --------------------------------------------------------- resolving the arrow


def test_the_dates_confirm_the_classifier():
    board = fact("The board comprises A, B and C.", "FY2023")
    resigned = fact("Director C resigned from the board.", "12 March 2024")

    direction = resolve_direction(resigned, board, CURRENT_A)

    assert direction is not None
    assert direction.current == CURRENT_A
    assert not direction.disputed


def test_the_dates_overrule_the_classifier():
    """The one case that matters: the model reads the arrow backwards.

    A verdict is an unverifiable judgement; "FY2023 is before 12 March 2024" is
    evidence sitting in the data. When they disagree the evidence wins, and the
    disagreement is recorded rather than quietly resolved.
    """
    board = fact("The board comprises A, B and C.", "FY2023")
    resigned = fact("Director C resigned from the board.", "12 March 2024")

    # The classifier claims the OLD board listing is the current fact.
    direction = resolve_direction(resigned, board, CURRENT_CANDIDATE)

    assert direction is not None
    assert direction.current == CURRENT_A, "the later fact must win"
    assert direction.disputed
    assert "12 March 2024" in direction.evidence
    assert "FY2023" in direction.evidence


def test_the_classifier_decides_when_the_dates_cannot():
    """No extractable period on either side, so the verdict stands unchallenged."""
    a = fact("The registered office is at 14 Charter Street.")
    b = fact("The registered office is at 2 Mill Road.")

    direction = resolve_direction(a, b, CURRENT_CANDIDATE)

    assert direction is not None
    assert direction.current == CURRENT_CANDIDATE
    assert not direction.disputed


def test_dates_alone_can_settle_it():
    """The model returned no direction, but the periods are unambiguous."""
    older = fact("The rating is BBB.", "FY2023")
    newer = fact("The rating is A-.", "FY2025")

    assert resolve_direction(newer, older, None).current == CURRENT_A
    assert resolve_direction(older, newer, None).current == CURRENT_CANDIDATE


def test_no_direction_from_either_source_stores_nothing():
    """A supersession without an arrow is a different claim, not a weaker one.

    Returning None here is what makes the caller skip the pair. Defaulting to
    either side would put a confidently backwards statement in front of a user.
    """
    a = fact("The registered office is at 14 Charter Street.")
    b = fact("The registered office is at 2 Mill Road.")

    assert resolve_direction(a, b, None) is None


# ------------------------------------------------------------ the stored shape


def test_superseded_facts_are_derived_from_the_stored_direction(conn):
    """`fact_id_a` is the current fact. The read side depends on that holding."""
    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="abc", page_count=1
    )
    older = repo.insert_fact(
        conn,
        document_id=document_id,
        chunk_id=None,
        fact_type="governance",
        subject="board",
        statement="The board comprises A, B and C.",
        normalized_value=None,
        unit=None,
        time_scope="FY2023",
        quote="The board comprises A, B and C.",
        page_number=1,
        bbox=None,
        confidence=0.9,
    )
    newer = repo.insert_fact(
        conn,
        document_id=document_id,
        chunk_id=None,
        fact_type="governance",
        subject="board",
        statement="Director C resigned from the board.",
        normalized_value=None,
        unit=None,
        time_scope="12 March 2024",
        quote="Director C resigned from the board.",
        page_number=1,
        bbox=None,
        confidence=0.9,
    )
    repo.insert_relationship(
        conn,
        fact_id_a=newer,
        fact_id_b=older,
        relationship_type=SUPERSEDES,
        rationale="A later filing records the resignation.",
        confidence=0.9,
    )

    superseded = repo.superseded_by_map(conn)

    assert superseded == {older: newer}
    assert newer not in superseded, "the current fact is not itself superseded"


def test_the_api_reads_the_arrow_from_both_ends(conn):
    """The same stored row is the opposite claim depending on which end asks.

    This is the whole reason `direction` exists on the response. Without it a
    client rendering "SUPERSEDES" on both sides would tell a reader that each
    fact replaced the other.
    """
    from fastapi.testclient import TestClient

    from app.main import app

    document_id = repo.insert_document(
        conn, filename="r.pdf", title="Report", sha256="def", page_count=1
    )

    def add(statement: str, scope: str) -> int:
        return repo.insert_fact(
            conn,
            document_id=document_id,
            chunk_id=None,
            fact_type="governance",
            subject="registered office",
            statement=statement,
            normalized_value=None,
            unit=None,
            time_scope=scope,
            quote=statement,
            page_number=1,
            bbox=None,
            confidence=0.9,
        )

    older = add("The registered office is at 14 Charter Street.", "FY2023")
    newer = add("The registered office moved to 2 Mill Road.", "FY2025")
    repo.insert_relationship(
        conn,
        fact_id_a=newer,
        fact_id_b=older,
        relationship_type=SUPERSEDES,
        rationale="The office moved.",
        confidence=0.9,
    )
    conn.commit()

    with TestClient(app) as client:
        from_current = client.get(f"/facts/{newer}/relationships").json()
        from_replaced = client.get(f"/facts/{older}/relationships").json()
        listed = client.get("/facts").json()["facts"]

    assert from_current["relationships"][0]["direction"] == "outgoing"
    assert from_current["relationships"][0]["fact_id"] == older
    assert from_replaced["relationships"][0]["direction"] == "incoming"
    assert from_replaced["relationships"][0]["fact_id"] == newer

    by_id = {f["id"]: f for f in listed}
    assert by_id[older]["superseded_by"] == newer
    assert by_id[newer]["superseded_by"] is None
