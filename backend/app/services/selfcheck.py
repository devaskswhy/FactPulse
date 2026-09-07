"""Post-ingestion self-check.

Runs once after a document finishes, looking for facts that are *suspicious*
rather than outright broken. The extraction step already catches quotes that do
not verify and confidence below the review threshold; this catches two quieter
problems that no single step is positioned to notice:

  ambiguous_unit        - a numeric value with no unit. "4.2" is not a fact
                          until you know whether it is millions, percent, or
                          days, and a comparison against another "4.2" would be
                          meaningless.
  borderline_confidence - the model hedged in a specific band. Not low enough
                          to look obviously wrong, not high enough to trust
                          unread. These are the ones that slip through.

The checks are queries over what was written, not extra model calls, so this
step is free.
"""

from __future__ import annotations

import logging
import re
import sqlite3

from app.core.config import settings
from app.db import repository as repo

logger = logging.getLogger(__name__)

ISSUE_AMBIGUOUS_UNIT = "ambiguous_unit"
ISSUE_BORDERLINE_CONFIDENCE = "borderline_confidence"

# The band where the model was neither confident nor clearly doubtful.
BORDERLINE_MIN = 0.5
BORDERLINE_MAX = 0.7

# Values that are numeric but do not want a unit: a date is not "4.2 of
# something". Flagging these would fill the queue with noise nobody can action.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}(-\d{2})?$")
_BARE_YEAR = re.compile(r"^(1[89]|20)\d{2}$")
_CURRENCY_PREFIX = "$€£¥₹"


def looks_numeric(value: str | None) -> bool:
    """True when a normalized_value is a bare number that ought to carry a unit.

    Deliberately excludes dates and bare years, which are numeric in form but
    complete without a unit.
    """
    if value is None:
        return False
    text = value.strip()
    if not text:
        return False
    if _ISO_DATE.match(text) or _BARE_YEAR.match(text):
        return False

    cleaned = text.replace(",", "").replace("%", "").strip()
    cleaned = cleaned.lstrip(_CURRENCY_PREFIX).strip()
    if not cleaned:
        return False
    try:
        float(cleaned)
    except ValueError:
        return False
    return True


def _already_flagged(
    conn: sqlite3.Connection, fact_id: int, issue_type: str
) -> bool:
    """Has this exact concern already been raised and left unresolved?

    Keeps the step idempotent: re-ingesting or re-running does not stack
    duplicate rows on the same fact.
    """
    row = conn.execute(
        "SELECT 1 FROM review_queue WHERE fact_id = ? AND issue_type = ? "
        "AND resolved = 0 LIMIT 1",
        (fact_id, issue_type),
    ).fetchone()
    return row is not None


def run_self_check(conn: sqlite3.Connection, document_id: int) -> dict[str, int]:
    """Queue suspicious facts from one document. Returns counts by issue type."""
    counts = {ISSUE_AMBIGUOUS_UNIT: 0, ISSUE_BORDERLINE_CONFIDENCE: 0}

    rows = conn.execute(
        "SELECT id, fact_type, statement, normalized_value, unit, confidence "
        "FROM facts WHERE document_id = ?",
        (document_id,),
    ).fetchall()

    for row in rows:
        fact_id = row["id"]

        # --- numeric value with nothing to interpret it by ------------------
        unit = (row["unit"] or "").strip()
        if not unit and looks_numeric(row["normalized_value"]):
            if not _already_flagged(conn, fact_id, ISSUE_AMBIGUOUS_UNIT):
                repo.insert_review_item(
                    conn,
                    fact_id=fact_id,
                    chunk_id=None,
                    issue_type=ISSUE_AMBIGUOUS_UNIT,
                    note=(
                        f"normalized_value is {row['normalized_value']!r} with no "
                        f"unit recorded, so the number cannot be compared against "
                        f"another value or interpreted on its own. "
                        f"Statement: {str(row['statement'])[:160]}"
                    ),
                )
                counts[ISSUE_AMBIGUOUS_UNIT] += 1

        # --- the hedging band ----------------------------------------------
        confidence = row["confidence"]
        if confidence is not None and BORDERLINE_MIN <= confidence < BORDERLINE_MAX:
            if not _already_flagged(conn, fact_id, ISSUE_BORDERLINE_CONFIDENCE):
                repo.insert_review_item(
                    conn,
                    fact_id=fact_id,
                    chunk_id=None,
                    issue_type=ISSUE_BORDERLINE_CONFIDENCE,
                    note=(
                        f"Confidence {confidence:.2f} sits in the "
                        f"{BORDERLINE_MIN}-{BORDERLINE_MAX} band: not low enough "
                        f"to look obviously wrong, not high enough to accept "
                        f"unread. Worth a human glance."
                    ),
                )
                counts[ISSUE_BORDERLINE_CONFIDENCE] += 1

    total = sum(counts.values())
    if total:
        logger.info(
            "self-check queued %d item(s) for document %s: %s",
            total, document_id, counts,
        )
    return counts
