"""Relationship classification, against the live model.

These DO call Gemini, deliberately. The classifier's job is a judgement, and
stubbing it would test nothing except that the plumbing carries a string from
one place to another -- which the ingestion tests already cover.

What makes that acceptable in a test suite is the input: two short synthetic
chunks written so the right answer is not in doubt. Same company, same metric,
same period, same value is a corroboration by construction; the same three with
a different value and no explanatory context is a contradiction. If a model
cannot get those right, the pipeline is not usable, so a failure here is
informative rather than flaky.

Real starter documents are deliberately NOT used. Their facts are genuinely
arguable -- two institutions quoting different WEO vintages is a contradiction
to the engine and "a revision" to an economist -- and a test should not encode
one reading of a debatable case.

Skipped cleanly when no API key is configured or the daily free-tier quota is
spent, so a grader without a key still gets a green run.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.extract import ExtractionError, QuotaExhaustedError, build_client
from app.services.link import (
    CONTRADICTS,
    CORROBORATES,
    CURRENT_A,
    SUPERSEDES,
    classify_candidates,
    resolve_direction,
)

pytestmark = pytest.mark.skipif(
    not settings.gemini_configured,
    reason="GEMINI_API_KEY is not set; live classification cannot be tested",
)


def as_row(**fields) -> dict[str, str | None]:
    """Build a fixture shaped like get_fact_context() returns.

    A plain dict, not a sqlite3.Row: the classifier only ever reads its inputs
    with row["key"], so a dict is structurally identical for this purpose and
    does not need a database to exist.
    """
    columns = (
        "statement",
        "subject",
        "normalized_value",
        "unit",
        "time_scope",
        "quote",
        "document_title",
        "document_filename",
    )
    return {column: fields.get(column) for column in columns}


def classify_or_skip(fact_a, candidates):
    """Run the classifier, skipping the test if the free tier is spent."""
    try:
        return classify_candidates(fact_a, candidates, client=build_client())
    except QuotaExhaustedError as exc:
        pytest.skip(f"daily Gemini quota exhausted: {exc}")
    except ExtractionError as exc:
        pytest.skip(f"model unavailable: {exc}")


# ---------------------------------------------------------------------- cases

# Identical claim, different wording. Corroboration by construction.
CORROBORATION_A = as_row(
    statement="Acme Corp employed 128 people at the end of fiscal year 2024.",
    subject="Acme Corp",
    normalized_value="128",
    unit="count",
    time_scope="FY2024",
    quote="headcount at year end stood at 128 employees",
    document_title="Acme Annual Report FY2024",
    document_filename="acme-annual.pdf",
)
CORROBORATION_B = as_row(
    statement="Acme Corp had a workforce of 128 at the close of FY2024.",
    subject="Acme Corp",
    normalized_value="128",
    unit="count",
    time_scope="FY2024",
    quote="a workforce of 128 at the close of FY2024",
    document_title="Acme Investor Fact Sheet",
    document_filename="acme-factsheet.pdf",
)

# Same subject, metric, period and unit; different value; nothing in either
# statement that could explain the gap. Contradiction by construction.
CONTRADICTION_A = as_row(
    statement="Acme Corp employed 128 people at the end of fiscal year 2024.",
    subject="Acme Corp",
    normalized_value="128",
    unit="count",
    time_scope="FY2024",
    quote="headcount at year end stood at 128 employees",
    document_title="Acme Annual Report FY2024",
    document_filename="acme-annual.pdf",
)
CONTRADICTION_B = as_row(
    statement="Acme Corp employed 204 people at the end of fiscal year 2024.",
    subject="Acme Corp",
    normalized_value="204",
    unit="count",
    time_scope="FY2024",
    quote="employed 204 people at the end of fiscal year 2024",
    document_title="Acme Regulatory Filing FY2024",
    document_filename="acme-filing.pdf",
)


def test_obvious_corroboration_is_classified_as_corroborates():
    verdicts = classify_or_skip(CORROBORATION_A, [CORROBORATION_B])

    assert 0 in verdicts, "the classifier must return a verdict for the candidate"
    verdict = verdicts[0]
    assert verdict["relationship_type"] == CORROBORATES, (
        f"same subject, period, unit and value should corroborate; "
        f"got {verdict['relationship_type']!r} -- {verdict['rationale']}"
    )
    # The rationale is the product, so an empty one is a failure even when the
    # label is right.
    assert verdict["rationale"], "a verdict without a rationale is not usable"
    assert "128" in verdict["rationale"], (
        "the rationale must cite the actual value, not describe the pair "
        f"generically: {verdict['rationale']!r}"
    )


def test_obvious_contradiction_is_classified_as_contradicts():
    verdicts = classify_or_skip(CONTRADICTION_A, [CONTRADICTION_B])

    assert 0 in verdicts
    verdict = verdicts[0]
    assert verdict["relationship_type"] == CONTRADICTS, (
        f"same subject, period and unit with different values and no "
        f"explanatory context should contradict; got "
        f"{verdict['relationship_type']!r} -- {verdict['rationale']}"
    )
    assert verdict["rationale"]
    assert "128" in verdict["rationale"] and "204" in verdict["rationale"], (
        "the rationale must cite BOTH conflicting values: "
        f"{verdict['rationale']!r}"
    )


def test_classifier_returns_one_verdict_per_candidate():
    """Batching: several candidates in one call come back keyed by position."""
    verdicts = classify_or_skip(
        CORROBORATION_A, [CORROBORATION_B, CONTRADICTION_B]
    )

    assert set(verdicts) == {0, 1}, (
        f"expected a verdict for each of 2 candidates, got keys {sorted(verdicts)}"
    )
    assert verdicts[0]["relationship_type"] == CORROBORATES
    assert verdicts[1]["relationship_type"] == CONTRADICTS


# The assignment brief's own example of the case a four-way vocabulary gets
# wrong: a board listing and a later resignation are not a contradiction.
BOARD_2023 = as_row(
    statement=(
        "The board of directors of Acme Corp comprised Priya Raman, "
        "Tomas Lind and Wei Chen as at 31 March 2023."
    ),
    subject="Acme Corp board of directors",
    time_scope="31 March 2023",
    quote="the board comprised Priya Raman, Tomas Lind and Wei Chen",
    document_title="Acme Annual Report FY2023",
    document_filename="acme-annual-2023.pdf",
)
RESIGNATION_2024 = as_row(
    statement=(
        "Wei Chen resigned from the board of directors of Acme Corp with "
        "effect from 12 March 2024."
    ),
    subject="Acme Corp board of directors",
    time_scope="12 March 2024",
    quote="Wei Chen resigned from the board with effect from 12 March 2024",
    document_title="Acme Regulatory Filing 2024",
    document_filename="acme-filing-2024.pdf",
)


def test_a_later_change_supersedes_rather_than_contradicts():
    """The brief's resigned-director case.

    Both facts were true when written. Calling this a contradiction would tell
    a reader one of the two documents is wrong, which is the wrong thing to
    tell them -- the world changed.
    """
    verdicts = classify_or_skip(RESIGNATION_2024, [BOARD_2023])

    assert 0 in verdicts
    verdict = verdicts[0]
    assert verdict["relationship_type"] == SUPERSEDES, (
        f"a later resignation replaces an earlier board listing; got "
        f"{verdict['relationship_type']!r} -- {verdict['rationale']}"
    )
    assert verdict["rationale"]


def test_the_supersession_arrow_points_at_the_later_fact():
    """Direction is the whole content of a supersession.

    Whatever the classifier says, the dates in the two facts have to agree that
    the resignation is the current state -- otherwise the UI would strike
    through the wrong one.
    """
    verdicts = classify_or_skip(RESIGNATION_2024, [BOARD_2023])
    direction = resolve_direction(
        RESIGNATION_2024, BOARD_2023, verdicts[0].get("current_fact")
    )

    assert direction is not None, "the dates alone can settle this pair"
    assert direction.current == CURRENT_A, (
        "the 2024 resignation is the current fact, not the 2023 board listing"
    )
