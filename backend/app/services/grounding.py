"""How well does a quote actually support the fact it is attached to?

`verify_quote` answers a narrower question: is this quote a real substring of
the source? That catches invention, and nothing else. It happily accepts a
quote of "Nil" attached to the sentence "Delhivery has no corrective action
taken or underway on issues related to anti-competitive conduct", because "Nil"
really is in the document -- in a table cell whose meaning comes entirely from
a row label and a column header the quote does not include.

Measured on the working corpus, 59 of 335 facts had a quote under half the
length of their statement. Those facts are marked "grounded" in the UI, and a
reader clicking through sees a highlight over a single word. That is a
correctness problem dressed as a feature.

This module asks the second question. A statement asserts a small number of
components -- a subject, a value, a period -- and a quote is sufficient to the
extent that it carries them. Counting which ones survive into the quote is a
local, deterministic check: no model call, no quota, no latency, and the same
answer every time.

The output is three-valued rather than a score. "0.62 grounded" invites false
precision; full / partial / insufficient maps onto what a reviewer would
actually do about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Evidence strength, strongest first.
FULL = "full"
PARTIAL = "partial"
INSUFFICIENT = "insufficient"

STRENGTHS = (FULL, PARTIAL, INSUFFICIENT)

# Words carrying no identifying weight when matching a subject against a quote.
# Kept short on purpose: this is not stopword removal for retrieval, it is
# "which token would a human look for to decide the quote is about the right
# thing".
_NOISE = {
    "the", "a", "an", "of", "for", "in", "on", "at", "to", "and", "or",
    "s", "its", "their", "was", "were", "is", "are", "be", "been",
    "total", "overall", "value", "number", "amount", "rate",
}

# A quote with fewer content tokens than this cannot state a claim on its own,
# whatever else it contains. A bare table cell is the case in point.
_MIN_STANDALONE_TOKENS = 4

# Above this length, a quote is running prose rather than a fragment, and prose
# routinely omits or pronominalises the subject. Demanding the subject appear
# in a full sentence would flag ordinary writing, so the subject check only
# applies to shorter quotes where there is genuinely no way to tell what the
# text is about.
_SUBJECT_CHECK_MAX_TOKENS = 8

# Self-references a report uses for itself. A filing that says "the Company"
# IS naming its subject; treating that as a missing subject would penalise the
# single most common phrasing in corporate disclosure.
_SELF_REFERENCE = (
    "the company", "the firm", "the group", "the bank", "the issuer",
    "the corporation", "the organisation", "the organization",
    "our ", "we ", "the entity",
)

_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_YEAR = re.compile(r"(?:19|20)\d{2}")
_NUMBER_RUN = re.compile(r"[\d][\d,.\s]*")


@dataclass
class GroundingAssessment:
    """What the quote carries, and what it leaves out."""

    strength: str
    # Which asserted components the quote does NOT contain, in plain words, so
    # the UI and the review queue can say why rather than showing a grade.
    gaps: list[str] = field(default_factory=list)
    # Which components were applicable at all, so "no gaps" can be read as
    # "nothing was missing" rather than "nothing was checked".
    checked: list[str] = field(default_factory=list)

    @property
    def is_weak(self) -> bool:
        return self.strength == INSUFFICIENT

    def reason(self) -> str:
        """One sentence a reviewer can act on."""
        if self.strength == FULL:
            return "The quote states the claim on its own."
        if not self.gaps:
            return "The quote supports the claim but carries little context."
        missing = ", ".join(self.gaps)
        if self.strength == INSUFFICIENT:
            return (
                f"On its own the quote does not support the statement: it is "
                f"missing {missing}."
            )
        return f"The quote supports the claim but is missing {missing}."


def _numeric_key(text: str) -> str | None:
    """Comparable form of a number: digits and a decimal point, nothing else.

    Without this, "576,188.2" in a quote fails to match a normalized_value of
    "576188.2" -- a real miss on the working corpus, and one that would have
    made this whole check under-report.
    """
    cleaned = re.sub(r"[^\d.]", "", text or "").strip(".")
    if not cleaned or not any(ch.isdigit() for ch in cleaned):
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    # Collapse trailing zeros so 128 and 128.0 compare equal.
    return f"{number:.10g}"


def _contains_value(quote: str, value: str) -> bool:
    """Is the asserted value present in the quote, allowing for formatting?"""
    if value.lower() in quote.lower():
        return True

    target = _numeric_key(value)
    if target is None:
        return False
    # Compare every number-like run numerically, so thousands separators,
    # currency symbols and stray spacing do not cause a false miss.
    return any(_numeric_key(run) == target for run in _NUMBER_RUN.findall(quote))


def _content_tokens(text: str) -> list[str]:
    return [
        word.lower()
        for word in _WORD.findall(text or "")
        if word.lower() not in _NOISE and len(word) > 1
    ]


def _contains_subject(quote: str, subject: str) -> bool:
    """Does the quote name the thing the fact is about?

    Any distinctive token is enough. Requiring the whole subject string would
    fail on the ordinary case where the sentence says "the Company" and the
    subject was recorded as "Delhivery Limited".
    """
    lowered = quote.lower()
    # A document referring to itself has named its subject.
    if any(phrase in lowered for phrase in _SELF_REFERENCE):
        return True
    tokens = _content_tokens(subject)
    if not tokens:
        return False
    return any(token in lowered for token in tokens)


def _contains_period(quote: str, time_scope: str) -> bool:
    """Does the quote pin the period the fact claims?

    Matched on the years involved rather than the literal string, because the
    same period is written FY2024, 2024-25 and FY2024/25 across three documents
    in this corpus alone.
    """
    lowered = quote.lower()
    if time_scope.lower() in lowered:
        return True
    years = _YEAR.findall(time_scope)
    if years:
        return any(year in quote for year in years)
    # Non-year scopes such as "Q1" or "as of quarter end".
    tokens = _content_tokens(time_scope)
    return bool(tokens) and any(token in lowered for token in tokens)


def assess(
    *,
    statement: str,
    quote: str | None,
    subject: str | None = None,
    normalized_value: str | None = None,
    unit: str | None = None,
    time_scope: str | None = None,
) -> GroundingAssessment:
    """Judge how much of the claim the quote actually carries.

    Only components the fact ACTUALLY asserts are checked. A fact with no
    time_scope is not penalised for a quote that lacks one: the statement did
    not claim a period, so the quote need not supply one.
    """
    del unit, statement  # asserted elsewhere; not separately checkable here

    if not quote or not quote.strip():
        return GroundingAssessment(INSUFFICIENT, ["any quote at all"], [])

    checked: list[str] = []
    gaps: list[str] = []

    value_present: bool | None = None
    if normalized_value:
        checked.append("value")
        value_present = _contains_value(quote, normalized_value)
        if not value_present:
            gaps.append("the value it states")

    quote_tokens = _content_tokens(quote)

    # Only meaningful on a short quote -- see _SUBJECT_CHECK_MAX_TOKENS.
    if subject and len(quote_tokens) <= _SUBJECT_CHECK_MAX_TOKENS:
        checked.append("subject")
        if not _contains_subject(quote, subject):
            gaps.append("the subject")

    if time_scope:
        checked.append("period")
        if not _contains_period(quote, time_scope):
            gaps.append("the period")

    standalone = len(quote_tokens) >= _MIN_STANDALONE_TOKENS

    # Worst case: a numeric fact whose quote does not contain the number. The
    # highlight points at text that does not say what the fact claims.
    if value_present is False:
        return GroundingAssessment(INSUFFICIENT, gaps, checked)

    # A fragment too short to be a sentence cannot support a claim on its own,
    # however many components it happens to contain. This is the "Nil" case,
    # and the bare-table-cell case generally.
    if not standalone:
        gaps.append("enough surrounding text to read as a claim")
        return GroundingAssessment(INSUFFICIENT, gaps, checked)

    if gaps:
        return GroundingAssessment(PARTIAL, gaps, checked)

    return GroundingAssessment(FULL, [], checked)
