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
    build_client,
    extract_facts_from_chunk,
)
from app.services.pdf import locate_quote_bbox, verify_quote

logger = logging.getLogger(__name__)

# Issue types written to review_queue. Free text in the schema; these are the
# values this module happens to use.
ISSUE_UNVERIFIED_QUOTE = "unverified_quote"
ISSUE_LOW_CONFIDENCE = "low_confidence"
ISSUE_UNGROUNDED_QUOTE = "ungrounded_quote"
ISSUE_EXTRACTION_FAILED = "extraction_failed"


@dataclass
class ExtractionSummary:
    document_id: int
    chunks_processed: int = 0
    chunks_failed: int = 0
    facts_inserted: int = 0
    facts_grounded: int = 0        # quote verified AND located in the PDF
    facts_unverified: int = 0      # quote not found in the chunk
    facts_low_confidence: int = 0  # below the review threshold
    review_items: int = 0
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
            "review_items": self.review_items,
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
    conn: sqlite3.Connection, document: Document, *, replace: bool = False
) -> ExtractionSummary:
    """Extract facts from every chunk of a document.

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

    for chunk in chunks:
        try:
            facts = extract_facts_from_chunk(chunk.text, client=client)
        except ExtractionError as exc:
            # One chunk failing must not lose the rest of the document. Record
            # it against the chunk so the gap is visible.
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

        for fact in facts:
            _persist_fact(
                conn,
                document=document,
                chunk=chunk,
                fact=fact,
                pdf_path=pdf_path,
                summary=summary,
            )
        summary.chunks_processed += 1

    # Counters are maintained on write, so a replace=True run leaves stale ones
    # behind. Rebuild from the facts table to keep the registry honest.
    if replace:
        repo.resync_fact_types(conn)

    status = "extracted" if summary.chunks_failed == 0 else "extracted_with_errors"
    repo.set_document_status(conn, document.id, status)
    return summary
