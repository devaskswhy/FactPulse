"""Quote sufficiency: does the evidence actually support the claim?

These are the cases that motivated the check, taken from the working corpus
rather than invented, so a regression here is a regression against something
that really happened.
"""

from __future__ import annotations

from app.services.grounding import FULL, INSUFFICIENT, PARTIAL, assess


def test_bare_table_cell_does_not_support_a_sentence():
    """The case that started this: a quote of "Nil" under a full claim."""
    result = assess(
        statement=(
            "Delhivery has no corrective action taken or underway on issues "
            "related to anti-competitive conduct."
        ),
        quote="Nil",
        subject="Delhivery",
    )
    assert result.strength == INSUFFICIENT
    assert result.is_weak
    assert "enough surrounding text to read as a claim" in result.gaps


def test_bare_number_does_not_support_a_sentence():
    result = assess(
        statement="Real GDP growth at market prices for India in 2023/24 was 9.2 percent.",
        quote="9.2",
        subject="India",
        normalized_value="9.2",
        unit="percent",
        time_scope="2023/24",
    )
    assert result.strength == INSUFFICIENT


def test_a_self_contained_sentence_is_full():
    result = assess(
        statement="Headline inflation averaged 4.6 per cent during 2024-25.",
        quote="Headline inflation moderated to an average of 4.6 per cent during 2024-25",
        subject="headline inflation",
        normalized_value="4.6",
        unit="percent",
        time_scope="2024-25",
    )
    assert result.strength == FULL
    assert result.gaps == []


def test_quote_without_the_asserted_value_is_insufficient():
    """The highlight would point at text that does not say what the fact claims."""
    result = assess(
        statement="Revenue was 4.2 million in FY2024.",
        quote="revenue rose sharply during the year under review",
        subject="Acme Corp",
        normalized_value="4.2",
    )
    assert result.strength == INSUFFICIENT
    assert "the value it states" in result.gaps


def test_thousands_separators_do_not_cause_a_false_miss():
    """576,188.2 in the quote must match a normalized_value of 576188.2.

    This was a real miss on the corpus: without numeric comparison the check
    would report the value absent and under-grade a correct extraction.
    """
    result = assess(
        statement="Total Scope 3 emissions were 576188.2 tonnes of CO2 equivalent in FY24.",
        quote="Total Scope 3 emissions for the year were 576,188.2 metric tonnes of CO2e",
        subject="Delhivery",
        normalized_value="576188.2",
        unit="metric tonnes",
    )
    assert "the value it states" not in result.gaps


def test_anaphoric_self_reference_counts_as_naming_the_subject():
    """"The company is headquartered in Zurich" names its subject.

    Corporate disclosure refers to itself constantly; treating that as a
    missing subject would flag the most common phrasing in the corpus.
    """
    result = assess(
        statement="Acme Corp is headquartered in Zurich.",
        quote="The company is headquartered in Zurich, Switzerland.",
        subject="Acme Corp",
    )
    assert result.strength == FULL


def test_period_written_differently_still_counts():
    """FY2024/25 and 2024-25 are the same period across these documents."""
    result = assess(
        statement="GDP grew 6.5 percent in FY2024/25.",
        quote="real GDP grew by 6.5 percent during 2024-25 as a whole",
        subject="GDP",
        normalized_value="6.5",
        time_scope="FY2024/25",
    )
    assert "the period" not in result.gaps


def test_components_the_fact_does_not_assert_are_not_required():
    """A fact claiming no period is not penalised for a quote lacking one."""
    result = assess(
        statement="The company is headquartered in Zurich, Switzerland.",
        quote="The company is headquartered in Zurich, Switzerland.",
        subject="Acme Corp",
    )
    assert "the period" not in result.gaps
    assert result.strength == FULL


def test_missing_quote_is_insufficient():
    for empty in (None, "", "   "):
        result = assess(statement="Anything at all.", quote=empty)
        assert result.strength == INSUFFICIENT


def test_reason_is_a_usable_sentence():
    weak = assess(statement="Revenue was 4.2 million.", quote="Nil", subject="Acme")
    strong = assess(
        statement="The company is based in Zurich.",
        quote="The company is headquartered in Zurich, Switzerland.",
        subject="Acme Corp",
    )
    assert weak.reason().endswith(".")
    assert "does not support" in weak.reason()
    assert strong.reason() == "The quote states the claim on its own."


def test_only_the_weakest_tier_is_flagged_weak():
    """`is_weak` gates the review queue, so it must mean insufficient only."""
    insufficient = assess(statement="Revenue was 4.2 million.", quote="Nil", subject="Acme")
    partial = assess(
        statement="Bank credit growth moderated to 10.4 percent in September 2025.",
        quote="from 10.4 percent in the preceding quarter of the review period",
        subject="bank credit",
        normalized_value="10.4",
        time_scope="September 2025",
    )
    full = assess(
        statement="The company is based in Zurich.",
        quote="The company is headquartered in Zurich, Switzerland.",
        subject="Acme Corp",
    )

    assert insufficient.strength == INSUFFICIENT and insufficient.is_weak
    assert partial.strength == PARTIAL and not partial.is_weak
    assert full.strength == FULL and not full.is_weak
