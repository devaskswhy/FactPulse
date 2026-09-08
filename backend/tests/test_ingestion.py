"""Ingestion and grounding.

The model is stubbed here on purpose. These tests are about what the PIPELINE
does with a model's output -- verify the quote, locate it on the page, store it
or queue it -- and that behaviour must be deterministic. A test that called
Gemini would be asserting the model's mood, not our code, and would fail on a
spent quota for reasons that have nothing to do with a regression.

The live-model behaviour is covered separately in test_relationships.py.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.db import repository as repo
from app.services import pipeline
from app.services.extract import ChunkExtraction, ExtractedFact
from app.services.ingest import ingest_pdf
from app.services.pdf import locate_quote_bbox, parse_pdf, verify_quote

# A quote copied verbatim from sample_pdf, and one that is not in it at all.
REAL_QUOTE = "Consolidated revenue for fiscal year 2024 was $4.2 million"
INVENTED_QUOTE = "Revenue reached $99 billion in the year 1823"


def stub_model(facts: list[ExtractedFact]):
    """Replace the batch extractor with a fixed set of facts per chunk."""

    def _batch(chunks, client=None, on_result=None):
        results = []
        for index, (chunk_id, _text) in enumerate(chunks):
            result = ChunkExtraction(index=index, chunk_id=chunk_id, facts=list(facts))
            results.append(result)
            if on_result:
                on_result(result, index + 1, len(chunks))
        return results

    return _batch


@pytest.fixture
def stubbed(monkeypatch):
    """Install a stub model; the caller supplies the facts."""

    def _install(facts: list[ExtractedFact]):
        monkeypatch.setattr(pipeline, "extract_chunks_concurrently", stub_model(facts))
        # No client to stub any more. The pipeline builds one per (key, model)
        # slot at the point of the call rather than pinning one for the run, so
        # its only preflight is that a key is configured at all -- which the
        # isolated_store fixture arranges with a placeholder.

    return _install


def grounded_fact(**overrides) -> ExtractedFact:
    base = dict(
        fact_type="financial-metric",
        subject="Test Corp",
        statement="Test Corp revenue for FY2024 was $4.2 million.",
        quote=REAL_QUOTE,
        confidence=0.95,
        normalized_value="4.2",
        unit="USD million",
        time_scope="FY2024",
        attributes={"basis": "consolidated"},
    )
    base.update(overrides)
    return ExtractedFact(**base)


# --------------------------------------------------------------------- tests


def test_ingestion_produces_a_fact_with_a_verified_bbox(conn, sample_pdf, stubbed):
    """A fact whose quote is really in the PDF gets a page and a bounding box."""
    stubbed([grounded_fact()])

    result = ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)

    assert result.extraction is not None
    assert result.extraction.facts_inserted >= 1
    assert result.extraction.facts_grounded >= 1

    facts = repo.list_facts(conn)
    assert len(facts) >= 1

    fact = facts[0]
    assert fact.grounding is not None
    assert fact.grounding.page_number == 1
    assert fact.grounding.bbox is not None

    box = fact.grounding.bbox
    assert box.x1 > box.x0 and box.y1 > box.y0, "bbox must have positive area"

    # The stored quote is the span as it appears in the document, not the
    # model's rendering of it.
    assert fact.grounding.quote is not None
    assert fact.grounding.quote.strip().startswith("Consolidated revenue")

    # And the box really does sit over that text in the stored PDF, not just in
    # the chunk it was verified against.
    document = repo.get_document(conn, fact.document_id)
    assert document is not None
    stored_pdf = settings.upload_path / f"{document.sha256}.pdf"
    assert stored_pdf.exists(), "the original bytes must be kept for grounding"

    located = locate_quote_bbox(stored_pdf, fact.grounding.quote)
    assert located is not None
    assert located[0] == fact.grounding.page_number


def test_unverifiable_quote_is_queued_not_dropped(conn, sample_pdf, stubbed):
    """A fabricated quote must be stored AND flagged, never silently discarded."""
    stubbed([grounded_fact(quote=INVENTED_QUOTE, fact_type="fabricated-claim")])

    result = ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)

    assert result.extraction is not None
    assert result.extraction.facts_unverified >= 1

    # The fact still exists and is queryable.
    facts = repo.list_facts(conn)
    assert any(f.fact_type == "fabricated-claim" for f in facts)

    # And it is flagged.
    queue = repo.list_review_queue(conn, resolved=False)
    issues = {item.issue_type for item in queue}
    assert "unverified_quote" in issues

    flagged = next(i for i in queue if i.issue_type == "unverified_quote")
    fact = repo.get_fact(conn, flagged.fact_id)
    assert fact is not None, "the review row must point at a real fact"
    assert fact.grounding is not None
    assert fact.grounding.bbox is None, "an unverified quote cannot have a box"


def test_low_confidence_is_queued(conn, sample_pdf, stubbed):
    """Confidence below the threshold is flagged, and the fact is kept."""
    stubbed([grounded_fact(confidence=0.20)])

    ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)

    assert len(repo.list_facts(conn)) >= 1
    issues = {i.issue_type for i in repo.list_review_queue(conn, resolved=False)}
    assert "low_confidence" in issues


def test_fact_type_registry_records_whatever_the_model_invents(
    conn, sample_pdf, stubbed
):
    """fact_type is free text: an unseen label is registered, not rejected."""
    stubbed([grounded_fact(fact_type="a-label-nobody-predefined")])

    ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)

    names = {t.name for t in repo.list_fact_types(conn)}
    assert "a-label-nobody-predefined" in names


def test_dedupe_returns_existing_document(conn, sample_pdf, stubbed):
    """The same bytes twice must not re-extract or duplicate facts."""
    stubbed([grounded_fact()])

    first = ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)
    before = len(repo.list_facts(conn))

    second = ingest_pdf(conn, filename="test.pdf", data=sample_pdf, extract=True)

    assert second.deduplicated is True
    assert second.document.id == first.document.id
    assert len(repo.list_facts(conn)) == before


# ------------------------------------------------------- grounding primitives


def test_verify_quote_returns_the_source_span_not_the_models_wording():
    chunk = "Consolidated revenue for fiscal year 2024 was $4.2 million."
    # Re-cased and reflowed, the way a model quotes.
    got = verify_quote(chunk, "CONSOLIDATED REVENUE   for fiscal\nyear 2024")
    assert got == "Consolidated revenue for fiscal year 2024"


def test_verify_quote_rejects_text_that_is_not_there():
    chunk = "Consolidated revenue for fiscal year 2024 was $4.2 million."
    assert verify_quote(chunk, INVENTED_QUOTE) is None
    assert verify_quote(chunk, "   ") is None


def test_locate_quote_bbox_prefers_the_exact_match_over_a_short_prefix(tmp_path):
    """Regression: the locator must not settle for a fallback on an early page.

    A repeated opening clause used to win on page 1 before the full quote --
    unique to page 2 -- was ever tried there, putting the highlight on the
    wrong page with a different number under it.
    """
    import fitz

    doc = fitz.open()
    for value in ("111", "222"):
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 500, 300),
            f"Gross reserves were placed at {value} at the end of the period.",
            fontsize=12,
            fontname="helv",
        )
    path = tmp_path / "repeated.pdf"
    doc.save(path)
    doc.close()

    located = locate_quote_bbox(path, "Gross reserves were placed at 222")
    assert located is not None
    assert located[0] == 2, "must find the page containing the full quote"


def test_parse_pdf_extracts_pages_in_order(sample_pdf):
    parsed = parse_pdf(sample_pdf)
    assert parsed.page_count == 1
    assert parsed.has_text
    assert [p.page_number for p in parsed.pages] == [1]
    assert "Consolidated revenue" in parsed.pages[0].text
