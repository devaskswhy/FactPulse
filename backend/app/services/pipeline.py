"""Pipeline steps 4-5 and 8: extract, ground, and route for review.

Runs per chunk. For each fact the model returns:

  1. verify the quote is really a substring of the chunk we sent it
  2. locate that quote in the stored PDF to get a page and bounding box
  3. write the fact, its EAV attributes, and its fact_type registry entry
  4. queue anything doubtful for review

Nothing is ever discarded for being doubtful. A fact whose quote does not
verify, or whose confidence is low, is still written -- it just also gets a
review_queue row pointing at it. The failure stays visible in the data instead
of vanishing between the model and the database.

The one exception is a fact so malformed it has no type, statement, or quote at
all; that is dropped in extract.py, because there would be nothing to review.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field

from app.core.config import settings
from app.db import repository as repo
from app.schemas.document import Chunk, Document
from app.services.extract import (
    ExtractedFact,
    ExtractionError,
    QuotaExhaustedError,
    build_client,
    extract_chunks_concurrently,
)
from app.services import grounding
from app.services.pdf import locate_quote_bbox, verify_quote
from app.services.progress import ProgressTracker

logger = logging.getLogger(__name__)

# Issue types written to review_queue. Free text in the schema; these are the
# values this module happens to use.
ISSUE_UNVERIFIED_QUOTE = "unverified_quote"
ISSUE_LOW_CONFIDENCE = "low_confidence"
ISSUE_UNGROUNDED_QUOTE = "ungrounded_quote"
ISSUE_EXTRACTION_FAILED = "extraction_failed"
ISSUE_QUOTA_EXHAUSTED = "quota_exhausted"
ISSUE_WEAK_EVIDENCE = "weak_evidence"

# Written by the linking step, not this module: a supersession where the
# classifier's direction and the dates in the two facts pointed opposite ways.
# The dates win, because they are checkable, but the disagreement is exactly
# the kind of thing a human should adjudicate.
ISSUE_UNCERTAIN_SUPERSESSION = "uncertain_supersession"


@dataclass
class ExtractionSummary:
    document_id: int
    chunks_processed: int = 0
    chunks_failed: int = 0
    facts_inserted: int = 0
    facts_grounded: int = 0        # quote verified AND located in the PDF
    facts_unverified: int = 0      # quote not found in the chunk
    facts_low_confidence: int = 0  # below the review threshold
    facts_weak_evidence: int = 0   # quote real, but does not support the claim
    review_items: int = 0
    quota_exhausted: bool = False
    fact_types: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "chunks_processed": self.chunks_processed,
            "chunks_failed": self.chunks_failed,
            "facts_inserted": self.facts_inserted,
            "facts_grounded": self.facts_grounded,
            "facts_unverified": self.facts_unverified,
            "facts_low_confidence": self.facts_low_confidence,
            "facts_weak_evidence": self.facts_weak_evidence,
            "review_items": self.review_items,
            "quota_exhausted": self.quota_exhausted,
            "fact_types": sorted(self.fact_types),
        }


def _persist_fact(
    conn: sqlite3.Connection,
    *,
    document: Document,
    chunk: Chunk,
    fact: ExtractedFact,
    pdf_path,
    summary: ExtractionSummary,
) -> int:
    """Verify, ground, and write one fact. Returns its id."""
    # --- step 1: is the quote really in the text we sent? ------------------
    # verify_quote returns the span as it appears in the chunk, so what we
    # store is the document's wording rather than the model's rendering of it.
    verified = verify_quote(chunk.text, fact.quote)
    quote_ok = verified is not None
    quote_to_store = verified if quote_ok else fact.quote

    # --- step 2: where is it on the page? ----------------------------------
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    if quote_ok and pdf_path is not None and pdf_path.exists():
        located = locate_quote_bbox(
            pdf_path, quote_to_store, chunk.page_start, chunk.page_end
        )
        if located:
            page_number, bbox = located

    if page_number is None and chunk.page_start == chunk.page_end:
        # No box, but the chunk sits on exactly one page, so the page itself is
        # not in doubt. For a multi-page chunk it would be a guess, so it stays
        # NULL rather than being invented.
        page_number = chunk.page_start

    # --- step 2b: is the quote ENOUGH, not just real? ----------------------
    # verify_quote proves the quote exists in the source. It says nothing about
    # whether the quote supports the claim -- a table cell reading "Nil" is a
    # real substring and supports almost nothing. Assessed locally, so this
    # costs no model call.
    assessment = grounding.assess(
        statement=fact.statement,
        quote=quote_to_store,
        subject=fact.subject or None,
        normalized_value=fact.normalized_value,
        unit=fact.unit,
        time_scope=fact.time_scope,
    )

    fact_id = repo.insert_fact(
        conn,
        document_id=document.id,
        chunk_id=chunk.id,
        fact_type=fact.fact_type,
        subject=fact.subject or None,
        statement=fact.statement,
        normalized_value=fact.normalized_value,
        unit=fact.unit,
        time_scope=fact.time_scope,
        quote=quote_to_store,
        page_number=page_number,
        bbox=bbox,
        confidence=fact.confidence,
        evidence_strength=assessment.strength,
        evidence_gaps="; ".join(assessment.gaps),
    )

    # --- step 3: EAV attributes and the type registry ----------------------
    repo.insert_fact_attributes(conn, fact_id, fact.attributes)
    repo.upsert_fact_type(conn, fact.fact_type, fact_id)

    summary.facts_inserted += 1
    summary.fact_types.add(fact.fact_type)

    # --- step 4: route anything doubtful to review -------------------------
    if not quote_ok:
        summary.facts_unverified += 1
        repo.insert_review_item(
            conn,
            fact_id=fact_id,
            chunk_id=chunk.id,
            issue_type=ISSUE_UNVERIFIED_QUOTE,
            note=(
                "Quote is not a verbatim substring of the source chunk, so this "
                "fact is not grounded in the document. Model returned: "
                f"{fact.quote[:200]!r}"
            ),
        )
        summary.review_items += 1
    elif bbox is None:
        # Verified against the chunk but not locatable in the PDF itself. The
        # fact is real; the highlight box is missing. Different problem, so a
        # different issue type.
        repo.insert_review_item(
            conn,
            fact_id=fact_id,
            chunk_id=chunk.id,
            issue_type=ISSUE_UNGROUNDED_QUOTE,
            note=(
                "Quote verified against the chunk text but could not be located "
                "in the PDF, so it has no bounding box. Usually means the text "
                "was reflowed during extraction."
            ),
        )
        summary.review_items += 1
    else:
        summary.facts_grounded += 1

    # A quote that does not support its claim is a different failure from a
    # quote that was invented, and needs a different fix, so it gets its own
    # issue type rather than being folded into unverified_quote.
    if quote_ok and assessment.is_weak:
        summary.facts_weak_evidence += 1
        repo.insert_review_item(
            conn,
            fact_id=fact_id,
            chunk_id=chunk.id,
            issue_type=ISSUE_WEAK_EVIDENCE,
            note=(
                f"{assessment.reason()} Quote: {quote_to_store[:160]!r}"
            ),
        )
        summary.review_items += 1

    if fact.confidence < settings.review_confidence_threshold:
        summary.facts_low_confidence += 1
        repo.insert_review_item(
            conn,
            fact_id=fact_id,
            chunk_id=chunk.id,
            issue_type=ISSUE_LOW_CONFIDENCE,
            note=(
                f"Model reported confidence {fact.confidence:.2f}, below the "
                f"{settings.review_confidence_threshold:.2f} review threshold."
            ),
        )
        summary.review_items += 1

    return fact_id


def extract_document_facts(
    conn: sqlite3.Connection,
    document: Document,
    *,
    replace: bool = False,
    progress: "ProgressTracker | None" = None,
) -> ExtractionSummary:
    """Extract facts from every chunk of a document.

    Model calls fan out across a bounded thread pool; database writes stay on
    this thread, in chunk order, so fact ids remain deterministic and the
    SQLite connection is never touched concurrently.

    With replace=True, existing facts for the document are cleared first so the
    step can be re-run without duplicating everything.
    """
    summary = ExtractionSummary(document_id=document.id)

    if replace:
        repo.delete_facts_for_document(conn, document.id)

    chunks = repo.list_chunks(conn, document.id)
    if not chunks:
        return summary

    client = build_client()  # raises ExtractionError when no key is configured
    pdf_path = settings.upload_path / f"{document.sha256}.pdf"
    if not pdf_path.exists():
        logger.warning(
            "stored PDF missing for document %s; facts will have no bounding boxes",
            document.id,
        )
        pdf_path = None

    repo.set_document_status(conn, document.id, "extracting")
    if progress:
        progress.phase(
            "extracting",
            f"extracting facts from {len(chunks)} chunks",
            total=len(chunks),
        )

    # Facts are persisted after every model call has returned, so the stored
    # count stays 0 for the whole extraction phase. Counting them as each
    # chunk's result arrives keeps the live tally moving during the longest
    # part of the run, which is exactly when a viewer needs to see something
    # happening.
    seen_facts = 0

    def on_result(result, done: int, total: int) -> None:
        nonlocal seen_facts
        seen_facts += len(result.facts)
        if progress:
            progress.update(
                current=done,
                total=total,
                facts=seen_facts,
                message=f"extracted chunk {done} of {total}",
            )

    results = extract_chunks_concurrently(
        [(c.id, c.text) for c in chunks],
        client=client,
        on_result=on_result,
    )

    by_chunk = {c.id: c for c in chunks}
    quota_hit = False

    for position, result in enumerate(results):
        chunk = by_chunk.get(result.chunk_id)
        if chunk is None:  # pragma: no cover - ids come straight from chunks
            continue

        if result.error is not None:
            exc = result.error
            if isinstance(exc, QuotaExhaustedError):
                # Every in-flight call after this one fails the same way. Record
                # it once with the scope of what was missed, rather than one
                # identical row per remaining chunk.
                if not quota_hit:
                    quota_hit = True
                    remaining = sum(
                        1
                        for r in results[position:]
                        if isinstance(r.error, QuotaExhaustedError)
                    )
                    logger.warning(
                        "daily quota exhausted; %d chunk(s) unprocessed", remaining
                    )
                    repo.insert_review_item(
                        conn,
                        fact_id=None,
                        chunk_id=chunk.id,
                        issue_type=ISSUE_QUOTA_EXHAUSTED,
                        note=(
                            f"Daily model quota exhausted with {remaining} of "
                            f"{len(chunks)} chunk(s) unprocessed, so this "
                            f"document's facts are incomplete. Re-run "
                            f"POST /documents/{document.id}/extract once quota "
                            f"resets, or switch GEMINI_MODEL (quotas are per "
                            f"model). Underlying error: {str(exc)[:200]}"
                        ),
                    )
                    summary.review_items += 1
                    summary.quota_exhausted = True
                summary.chunks_failed += 1
                continue

            logger.warning("extraction failed for chunk %s: %s", chunk.id, exc)
            summary.chunks_failed += 1
            repo.insert_review_item(
                conn,
                fact_id=None,
                chunk_id=chunk.id,
                issue_type=ISSUE_EXTRACTION_FAILED,
                note=str(exc)[:500],
            )
            summary.review_items += 1
            continue

        for fact in result.facts:
            _persist_fact(
                conn,
                document=document,
                chunk=chunk,
                fact=fact,
                pdf_path=pdf_path,
                summary=summary,
            )
        summary.chunks_processed += 1
        if progress:
            progress.update(facts=summary.facts_inserted)

    # Counters are maintained on write, so a replace=True run leaves stale ones
    # behind. Rebuild from the facts table to keep the registry honest.
    if replace:
        repo.resync_fact_types(conn)

    if summary.quota_exhausted:
        status = "extraction_incomplete"
    elif summary.chunks_failed:
        status = "extracted_with_errors"
    else:
        status = "extracted"
    repo.set_document_status(conn, document.id, status)
    return summary


def reground_document_facts(
    conn: sqlite3.Connection, document: Document
) -> dict[str, int]:
    """Recompute page and bbox for a document's facts from their stored quotes.

    Grounding is deterministic and uses no model calls, so it can be re-run
    freely -- after a fix to the locator, or if the stored PDF is replaced.
    Facts themselves are untouched; only their grounding columns move.

    Resolves any ungrounded_quote review items that now ground, since the
    condition that raised them no longer holds.
    """
    result = {"checked": 0, "regrounded": 0, "changed_page": 0, "still_ungrounded": 0}
    pdf_path = settings.upload_path / f"{document.sha256}.pdf"
    if not pdf_path.exists():
        return result

    rows = conn.execute(
        "SELECT f.id, f.quote, f.page_number, f.bbox_x0, c.page_start, c.page_end "
        "FROM facts f LEFT JOIN chunks c ON c.id = f.chunk_id "
        "WHERE f.document_id = ? AND f.quote IS NOT NULL",
        (document.id,),
    ).fetchall()

    for row in rows:
        result["checked"] += 1
        located = locate_quote_bbox(
            pdf_path, row["quote"], row["page_start"], row["page_end"]
        )
        if located is None:
            if row["bbox_x0"] is not None:
                # It used to have a box and no longer does; clear it rather
                # than leave a stale rectangle behind.
                conn.execute(
                    "UPDATE facts SET bbox_x0=NULL, bbox_y0=NULL, bbox_x1=NULL, "
                    "bbox_y1=NULL WHERE id = ?",
                    (row["id"],),
                )
            result["still_ungrounded"] += 1
            continue

        page_number, (x0, y0, x1, y1) = located
        if page_number != row["page_number"]:
            result["changed_page"] += 1
        conn.execute(
            "UPDATE facts SET page_number=?, bbox_x0=?, bbox_y0=?, bbox_x1=?, "
            "bbox_y1=? WHERE id = ?",
            (page_number, x0, y0, x1, y1, row["id"]),
        )
        result["regrounded"] += 1

        conn.execute(
            "UPDATE review_queue SET resolved = 1, "
            "resolution_action = 'accepted', "
            "resolution_note = 'Re-grounded: the quote now resolves to a page "
            "and bounding box.', resolved_at = datetime('now') "
            "WHERE fact_id = ? AND issue_type = ? AND resolved = 0",
            (row["id"], ISSUE_UNGROUNDED_QUOTE),
        )

    logger.info("re-grounded document %s: %s", document.id, result)
    return result


def assess_evidence_backfill(
    conn: sqlite3.Connection, document_id: int | None = None
) -> dict[str, int]:
    """Assess evidence strength for facts that do not have it yet.

    Pure local computation, so this can be run over an already-ingested corpus
    without spending a single model call. That is the point: the check was
    added after the corpus existed, and re-extracting 335 facts to get it would
    have cost hundreds of API calls for information already in the database.

    Weak results are queued for review, deduplicated so a repeat run does not
    stack rows on the same fact.
    """
    counts = {"assessed": 0, "full": 0, "partial": 0, "insufficient": 0, "queued": 0}

    for row in repo.facts_missing_evidence_assessment(conn, document_id):
        assessment = grounding.assess(
            statement=row["statement"],
            quote=row["quote"],
            subject=row["subject"],
            normalized_value=row["normalized_value"],
            unit=row["unit"],
            time_scope=row["time_scope"],
        )
        repo.set_evidence_assessment(
            conn, row["id"], assessment.strength, assessment.gaps
        )
        counts["assessed"] += 1
        counts[assessment.strength] += 1

        if assessment.is_weak:
            already = conn.execute(
                "SELECT 1 FROM review_queue WHERE fact_id = ? AND issue_type = ? "
                "AND resolved = 0 LIMIT 1",
                (row["id"], ISSUE_WEAK_EVIDENCE),
            ).fetchone()
            if not already:
                repo.insert_review_item(
                    conn,
                    fact_id=row["id"],
                    chunk_id=None,
                    issue_type=ISSUE_WEAK_EVIDENCE,
                    note=(
                        f"{assessment.reason()} "
                        f"Quote: {(row['quote'] or '')[:160]!r}"
                    ),
                )
                counts["queued"] += 1

    logger.info("evidence backfill: %s", counts)
    return counts
