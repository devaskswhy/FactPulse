"""Which of two facts describes the later state of the world?

Every other relationship verdict is symmetric. "A corroborates B" and "B
corroborates A" say the same thing, so the pair can be stored in either order
and read back from either end. `supersedes` is the first verdict where that
stops being true: it asserts that one fact replaced the other, and getting the
arrow backwards inverts the claim.

The classifier is asked to name the direction, and it usually gets it right.
But its answer is an unverifiable judgement, and the facts themselves often
carry checkable evidence -- a period, an effective-from date, a year in the
statement. This module reads that evidence so the model's direction can be
cross-examined rather than taken on trust.

The design rule throughout is **decide only on evidence**. Every function here
would rather return "cannot tell" than guess, because a wrong ordering here
does not produce a vague answer, it produces a confident and inverted one. So:

  - Different years decide it. 2023 is before 2024 whatever else is unknown.
  - The same year decides nothing unless both facts name a month, because
    "FY2024" and "March 2024" are not comparable -- one is a period and the
    other a point inside it.
  - The same month decides nothing unless both name a day.

Free text in, so the parsing has to survive the several ways one period is
written across a real corpus: FY2024, 2024-25, FY2024/25, 31 March 2024,
2024-03-31, Q3 2024.
"""

from __future__ import annotations

import re

# A parsed time point. Zero means "not stated at this precision", which is
# different from zero-as-a-value and is why the comparison below checks for it
# rather than just ordering the tuples.
TimePoint = tuple[int, int, int]  # (year, month, day)

_MONTH_NUMBER = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALT = "|".join(_MONTH_NUMBER)

_YEAR = r"(?:19|20)\d{2}"

# Ordered most specific first. A match by an earlier pattern consumes its
# characters, so a later, looser pattern cannot re-read the same text: without
# that, the fiscal-span pattern would read "2024-03-31" as the span 2024-03.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("iso", re.compile(rf"\b({_YEAR})-(\d{{1,2}})-(\d{{1,2}})\b")),
    (
        "dmy",
        re.compile(
            rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_ALT})[a-z]*\.?,?\s+({_YEAR})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mdy",
        re.compile(
            rf"\b({_MONTH_ALT})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+({_YEAR})\b",
            re.IGNORECASE,
        ),
    ),
    # FY2024-25, 2024/2025, 2024--2025. A fiscal span resolves to its END year
    # with the month left unknown; see _end_year for why the two-digit tail is
    # not simply parsed as a year.
    # The FY prefix has to be part of the pattern, not skipped: there is no
    # word boundary between "FY" and "2024", so \b(?:19|20) never matches
    # inside "FY2024-25" and the span would be read as the bare year 2024.
    (
        "span",
        re.compile(
            rf"\b(?:FY\s*)?({_YEAR})\s*[-/–—]\s*(\d{{4}}|\d{{2}})\b", re.IGNORECASE
        ),
    ),
    ("quarter", re.compile(rf"\bQ([1-4])\s*(?:FY\s*)?({_YEAR})\b", re.IGNORECASE)),
    ("my", re.compile(rf"\b({_MONTH_ALT})[a-z]*\.?,?\s+({_YEAR})\b", re.IGNORECASE)),
    ("year", re.compile(rf"\b(?:FY\s*)?({_YEAR})\b", re.IGNORECASE)),
)


def _end_year(start: str, tail: str) -> int:
    """The closing year of a span written "2024-25" or "2024-2025".

    The two-digit form is a suffix of the start year, not a year in its own
    right: 2024-25 ends in 2025, and reading "25" as a year would give 25 AD.
    Guarding on the century keeps 1999-00 working.
    """
    if len(tail) == 4:
        return int(tail)
    century = int(start) // 100 * 100
    end = century + int(tail)
    return end + 100 if end < int(start) else end


def _parse(kind: str, match: re.Match[str]) -> TimePoint | None:
    if kind == "iso":
        return (int(match[1]), int(match[2]), int(match[3]))
    if kind == "dmy":
        return (int(match[3]), _MONTH_NUMBER[match[2][:3].lower()], int(match[1]))
    if kind == "mdy":
        return (int(match[3]), _MONTH_NUMBER[match[1][:3].lower()], int(match[2]))
    if kind == "span":
        return (_end_year(match[1], match[2]), 0, 0)
    if kind == "quarter":
        # A quarter is a period, so it resolves to the month it closes in.
        return (int(match[2]), int(match[1]) * 3, 0)
    if kind == "my":
        return (int(match[2]), _MONTH_NUMBER[match[1][:3].lower()], 0)
    if kind == "year":
        return (int(match[1]), 0, 0)
    return None


def points(text: str | None) -> list[TimePoint]:
    """Every time point stated in a piece of free text, most specific patterns
    winning any overlap."""
    if not text:
        return []

    found: list[tuple[int, TimePoint]] = []
    consumed: list[tuple[int, int]] = []

    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if any(match.start() < end and start < match.end() for start, end in consumed):
                continue
            parsed = _parse(kind, match)
            if parsed is None:
                continue
            consumed.append((match.start(), match.end()))
            found.append((match.start(), parsed))

    return [point for _, point in sorted(found)]


def as_of(time_scope: str | None, statement: str | None = None) -> TimePoint | None:
    """The moment a fact pins itself to, or None if it names none.

    `time_scope` is the fact's own answer to "when", so it is read first and
    the statement is only a fallback. Within either, the LATEST point wins: a
    statement reading "revenue grew from 3.1 in FY2023 to 4.2 in FY2024"
    describes the FY2024 state, and a resignation "effective from 12 March
    2024" describes the world after that date.
    """
    for text in (time_scope, statement):
        candidates = points(text)
        if candidates:
            return max(candidates)
    return None


def compare(a: TimePoint | None, b: TimePoint | None) -> int:
    """+1 if `a` is later, -1 if `b` is later, 0 if the evidence cannot say.

    The zero cases are the point of the function. Two facts scoped "FY2024" and
    "March 2024" are not ordered by this comparison even though the tuples
    differ, because one is a period containing the other -- and a supersession
    stored on that basis would be an invention.
    """
    if a is None or b is None:
        return 0
    if a[0] != b[0]:
        return 1 if a[0] > b[0] else -1
    if not (a[1] and b[1]):
        return 0
    if a[1] != b[1]:
        return 1 if a[1] > b[1] else -1
    if not (a[2] and b[2]):
        return 0
    if a[2] != b[2]:
        return 1 if a[2] > b[2] else -1
    return 0


def order(
    *,
    scope_a: str | None,
    statement_a: str | None,
    scope_b: str | None,
    statement_b: str | None,
) -> int:
    """+1 if fact A describes the later state, -1 if B does, 0 if undecidable."""
    return compare(as_of(scope_a, statement_a), as_of(scope_b, statement_b))
