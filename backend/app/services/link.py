"""Pipeline step 7: relate new facts to what the knowledge layer already holds.

Runs as a batch at the end of a document's ingestion. For each new fact:

  1. retrieve similar facts from *other* documents by cosine similarity
  2. ask Gemini to judge each candidate pair
  3. store everything except UNRELATED

Two things keep the model call count sane. Cosine pre-filtering replaces an
all-pairs comparison with O(n*m) cheap float operations plus a capped shortlist
per fact. Batching then judges that whole shortlist in ONE call, so the cost is
one model call per fact rather than one per pair -- the difference between
21 calls and 3 on a two-document corpus, which matters immediately on a
free-tier daily quota.
"""

from __future__ import annotations

import json
import logging
import random
import sqlite3
import time
from dataclasses import dataclass, field

import numpy as np
from google import genai
from google.genai import types

from app.core.config import settings
from app.db import repository as repo
from app.services import model_pool, temporal
from app.services.embed import (
    EmbeddingError,
    cosine_against_matrix,
    embed_texts,
    pack_vector,
    unpack_vector,
)
from app.services.pipeline import ISSUE_UNCERTAIN_SUPERSESSION
from app.services.progress import ProgressTracker
from app.services.extract import (
    ExtractionError,
    QuotaExhaustedError,
    _is_daily_quota,
    _is_retryable,
    _retry_delay,
    build_client,
)

logger = logging.getLogger(__name__)

# Tuning knobs for candidate retrieval.
#
# SIMILARITY_THRESHOLD trades model calls against recall: too high and a real
# contradiction phrased differently never reaches the classifier; too low and
# the classifier spends its budget rejecting unrelated pairs. 0.75 is a
# starting point measured on this corpus, not a derived constant.
SIMILARITY_THRESHOLD = 0.75
TOP_K = 8

# Relationship labels the classifier chooses between. Unlike fact_type, this IS
# a closed set: the categories are the product's own vocabulary, they are what
# the UI renders, and a model inventing another would silently break that
# rendering. The database column stays free text so the set can grow later
# without a migration -- the constraint lives here, at the point of decision,
# not in the schema.
CORROBORATES = "corroborates"
CONTRADICTS = "contradicts"
RECONCILED = "reconciled"
SUPERSEDES = "supersedes"
UNRELATED = "unrelated"

RELATIONSHIP_TYPES = (CORROBORATES, CONTRADICTS, RECONCILED, SUPERSEDES, UNRELATED)

# Which side of a supersession the classifier can name.
CURRENT_A = "a"
CURRENT_CANDIDATE = "candidate"
CURRENT_SIDES = (CURRENT_A, CURRENT_CANDIDATE)

# SUPERSEDES is the only asymmetric verdict, so it carries a storage invariant
# the other four do not need: `fact_id_a` is always the CURRENT fact and
# `fact_id_b` the one it replaced. Everything that reads the table can rely on
# that, and _resolve_direction below is the single place that establishes it.


@dataclass
class LinkingSummary:
    document_id: int
    facts_embedded: int = 0
    facts_compared: int = 0
    pairs_evaluated: int = 0
    relationships_created: int = 0
    pool_size: int = 0  # existing facts compared against, loaded once
    by_type: dict[str, int] = field(default_factory=dict)
    errors: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "document_id": self.document_id,
            "facts_embedded": self.facts_embedded,
            "facts_compared": self.facts_compared,
            "pairs_evaluated": self.pairs_evaluated,
            "relationships_created": self.relationships_created,
            "pool_size": self.pool_size,
            "by_type": dict(sorted(self.by_type.items())),
            "errors": self.errors,
        }


# ------------------------------------------------------------------ embedding


def embed_document_facts(
    conn: sqlite3.Connection, document_id: int, client: genai.Client | None = None
) -> int:
    """Embed every fact of a document that does not have a vector yet."""
    facts = repo.list_facts(conn, document_id=document_id, limit=10_000)
    pending = [f for f in facts if repo.get_embedding(conn, f.id) is None]
    if not pending:
        return 0

    vectors = embed_texts([f.statement for f in pending], client=client)
    for fact, vector in zip(pending, vectors):
        repo.upsert_embedding(conn, fact.id, pack_vector(vector), int(vector.size))
    return len(pending)


# ------------------------------------------------------------------ retrieval


@dataclass
class CandidatePool:
    """The existing knowledge layer, loaded once as a matrix.

    Built from every embedded fact OUTSIDE the document being ingested. Facts
    already in the layer are read here and never rewritten -- ingesting a new
    document does not re-embed, re-extract, or re-classify anything that was
    already stored.
    """

    fact_ids: list[int]
    matrix: np.ndarray  # shape (n_facts, dim), rows already L2-normalised

    @property
    def size(self) -> int:
        return len(self.fact_ids)

    @property
    def dim(self) -> int:
        return int(self.matrix.shape[1]) if self.matrix.size else 0


def load_candidate_pool(
    conn: sqlite3.Connection, exclude_document_id: int, expected_dim: int | None = None
) -> CandidatePool:
    """Read every other document's embeddings into one matrix.

    Loaded ONCE per ingested document, not once per new fact. That distinction
    matters: this reads and unpacks the whole corpus, so calling it inside the
    per-fact loop made ingestion cost O(new_facts x existing_facts) BLOB
    unpacks -- quadratic in the size of the knowledge layer, and the single
    worst scaling property the pipeline had. Hoisting it out makes each
    document's linking cost one corpus read plus one matrix multiply per new
    fact.

    The pool is a snapshot: the facts it holds belong to already-ingested
    documents and cannot change while this document is being linked.
    """
    rows = repo.load_candidate_embeddings(conn, exclude_document_id=exclude_document_id)
    if not rows:
        return CandidatePool(fact_ids=[], matrix=np.zeros((0, 0), dtype=np.float32))

    fact_ids: list[int] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        try:
            vector = unpack_vector(row["vector"], row["dim"])
        except EmbeddingError as exc:
            # Written at a width the current model no longer produces. Skip it
            # rather than failing the whole comparison.
            logger.warning("skipping fact %s: %s", row["fact_id"], exc)
            continue
        if expected_dim is not None and vector.size != expected_dim:
            logger.warning(
                "skipping fact %s: dim %d != expected %d",
                row["fact_id"], vector.size, expected_dim,
            )
            continue
        fact_ids.append(row["fact_id"])
        vectors.append(vector)

    if not vectors:
        return CandidatePool(fact_ids=[], matrix=np.zeros((0, 0), dtype=np.float32))

    return CandidatePool(fact_ids=fact_ids, matrix=np.vstack(vectors))


def find_candidates(
    pool: CandidatePool,
    query_vector: np.ndarray,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
    top_k: int = TOP_K,
) -> list[tuple[int, float]]:
    """Top-k facts in the pool most similar to `query_vector`, above `threshold`.

    Brute force: one vectorized dot product against the whole pool, O(n) per
    new fact in time and O(n) memory for the pool held once.

    Deliberate and appropriate at the hundreds-to-low-thousands scale this
    system targets -- at 1k facts by 768 float32 dims the pool is ~3 MB and the
    multiply is sub-millisecond, so an index would add operational weight for no
    measurable gain. It stops being appropriate somewhere in the high tens of
    thousands. FAISS (in-process), pgvector (if the store moves to Postgres), or
    Qdrant (standalone) are the natural upgrade paths; `embeddings` is the only
    table that would change, and this function plus load_candidate_pool are the
    only callers.
    """
    if pool.size == 0 or query_vector.size == 0:
        return []
    if pool.dim != query_vector.size:
        logger.warning(
            "pool dim %d != query dim %d; no candidates", pool.dim, query_vector.size
        )
        return []

    scores = cosine_against_matrix(query_vector, pool.matrix)
    order = np.argsort(-scores)[:top_k]
    return [
        (pool.fact_ids[i], float(scores[i]))
        for i in order
        if float(scores[i]) >= threshold
    ]


# ----------------------------------------------------------------- classifier


def _classification_schema() -> types.Schema:
    """One verdict per candidate, returned in a single call.

    Judging each pair in its own request cost one model call per pair, which at
    top-k candidates per fact is k calls per fact -- enough to exhaust a daily
    free-tier quota on a two-document corpus. Since every candidate is being
    compared against the *same* fact A, they can share one request: the model
    sees A once and returns a verdict per candidate. That turns O(facts x k)
    calls into O(facts), and gives the model all the candidates at once, which
    helps it pick the closest match rather than judging each in isolation.
    """
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "verdicts": types.Schema(
                type=types.Type.ARRAY,
                description="Exactly one verdict per candidate, in the order given.",
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "candidate_index": types.Schema(
                            type=types.Type.INTEGER,
                            description="The 1-based index of the candidate judged.",
                        ),
                        "relationship_type": types.Schema(
                            type=types.Type.STRING,
                            enum=list(RELATIONSHIP_TYPES),
                            description="How Fact A relates to this candidate.",
                        ),
                        "rationale": types.Schema(
                            type=types.Type.STRING,
                            description=(
                                "Why, citing the specific values, periods, units "
                                "or wording from BOTH facts that drove the "
                                "decision. Must be concrete and checkable. For "
                                "'reconciled' it must name the exact difference "
                                "that explains the apparent conflict."
                            ),
                        ),
                        "reconciling_dimension": types.Schema(
                            type=types.Type.STRING,
                            nullable=True,
                            description=(
                                "For 'reconciled' only: which axis explains the "
                                "difference -- time period, scope, unit, "
                                "definition, or basis. Null otherwise."
                            ),
                        ),
                        "current_fact": types.Schema(
                            type=types.Type.STRING,
                            nullable=True,
                            enum=list(CURRENT_SIDES),
                            description=(
                                "For 'supersedes' only: which of the two "
                                "describes the LATER state -- 'a' for Fact A, "
                                "'candidate' for this candidate. Null "
                                "otherwise."
                            ),
                        ),
                        "confidence": types.Schema(
                            type=types.Type.NUMBER,
                            description="Confidence in this judgement, 0 to 1.",
                        ),
                    },
                    required=[
                        "candidate_index",
                        "relationship_type",
                        "rationale",
                        "confidence",
                    ],
                ),
            )
        },
        required=["verdicts"],
    )


CLASSIFIER_SYSTEM = """You compare one fact against several candidate facts from other documents and decide how each relates to it.

For each candidate choose exactly one:

  corroborates - The two facts assert the same underlying thing. Wording may
                 differ and values may be compatible (rounding, or one more
                 precise than the other). They agree.

  contradicts  - The two facts genuinely conflict. They cannot both be true of
                 the same subject under the same scope, period, unit and
                 definition. Only use this when you have checked that the scope
                 and period really are the same.

  reconciled   - They look like a conflict at first glance, but the difference
                 is fully explained by something concrete: a different time
                 period, a different scope (segment vs consolidated, one
                 country vs a region), a different unit, or a different
                 definition or accounting basis. You must name that difference.

  supersedes   - Both facts describe the same property of the same subject, and
                 one records a CHANGE that replaces the other. The earlier fact
                 was true when it was written and has since stopped being true.
                 A 2023 annual report listing a board of directors and a 2024
                 filing recording that one of those directors resigned do not
                 contradict each other: the later fact supersedes the earlier
                 one. Use this only when the two facts give explicit evidence of
                 which state came later -- a date, a period, an effective-from.
                 Without that evidence it is 'contradicts' or 'reconciled'.

  unrelated    - They are about different things, or share only a topic. Two
                 facts both mentioning revenue are not related unless they are
                 about the same revenue. Most candidates will be unrelated;
                 say so rather than forcing a connection.

'supersedes' is the only verdict where direction matters, so it needs one extra
field. Set current_fact to whichever side describes the LATER state: "a" if
Fact A does, "candidate" if the candidate does. Fact A is not necessarily the
newer of the two -- documents are not processed in date order, so decide from
the periods and dates in the facts themselves, never from which one is
presented first. Leave current_fact null for every other verdict.

The distinction between 'supersedes' and 'contradicts' is whether the world
changed or a source is wrong. "Revenue was 4.2m in FY2023" and "revenue was
5.1m in FY2024" is neither -- that is two different measurements, so
'unrelated' or 'reconciled'. Supersession is for a claim about a CURRENT state
that a later document revises: a board composition, a headquarters, a credit
rating, a policy, a holding.

The rationale is shown to a human as the explanation for the verdict, so it
must be concrete and checkable. Quote or name the actual values, periods, units
and words from BOTH facts that drove your decision.

  Not acceptable: "both facts discuss revenue"
  Acceptable:     "Fact A gives FY2024 consolidated revenue of 4.2 USD million
                   on a GAAP basis; candidate 2 gives CY2024 revenue of 5.1 USD
                   million on a non-GAAP basis including the Nordics
                   acquisition. Different period and different basis, so these
                   do not conflict."

Be conservative about 'contradicts'. If a difference in period, scope, unit or
basis could explain the gap, it is 'reconciled', not a contradiction. If a
later document records that the situation changed, it is 'supersedes'. If the
two facts are simply about different things, it is 'unrelated'.

Return exactly one verdict per candidate, using the candidate's given index.
"""

FACT_A_TEMPLATE = """Fact A (from "{doc_a}"):
  statement        : {stmt_a}
  subject          : {subj_a}
  normalized_value : {val_a}
  unit             : {unit_a}
  time_scope       : {time_a}
  source quote     : {quote_a}
"""

CANDIDATE_TEMPLATE = """
Candidate {index} (from "{doc_b}"):
  statement        : {stmt_b}
  subject          : {subj_b}
  normalized_value : {val_b}
  unit             : {unit_b}
  time_scope       : {time_b}
  source quote     : {quote_b}
"""


def _fmt(value: object) -> str:
    return "(not stated)" if value is None or value == "" else str(value)


def _render_request(row_a: sqlite3.Row, candidates: list[sqlite3.Row]) -> str:
    parts = [
        FACT_A_TEMPLATE.format(
            doc_a=_fmt(row_a["document_title"] or row_a["document_filename"]),
            stmt_a=_fmt(row_a["statement"]),
            subj_a=_fmt(row_a["subject"]),
            val_a=_fmt(row_a["normalized_value"]),
            unit_a=_fmt(row_a["unit"]),
            time_a=_fmt(row_a["time_scope"]),
            quote_a=_fmt(row_a["quote"]),
        )
    ]
    for index, row_b in enumerate(candidates, start=1):
        parts.append(
            CANDIDATE_TEMPLATE.format(
                index=index,
                doc_b=_fmt(row_b["document_title"] or row_b["document_filename"]),
                stmt_b=_fmt(row_b["statement"]),
                subj_b=_fmt(row_b["subject"]),
                val_b=_fmt(row_b["normalized_value"]),
                unit_b=_fmt(row_b["unit"]),
                time_b=_fmt(row_b["time_scope"]),
                quote_b=_fmt(row_b["quote"]),
            )
        )
    parts.append(
        f"\nHow does Fact A relate to each of the {len(candidates)} candidates?"
    )
    return "".join(parts)


def classify_candidates(
    row_a: sqlite3.Row,
    candidates: list[sqlite3.Row],
    client: genai.Client | None = None,
) -> dict[int, dict[str, object]]:
    """Judge every candidate against fact A in one model call.

    Returns {candidate_index (0-based) -> verdict}. A candidate the model omits
    simply has no entry, and the caller skips it rather than inventing a
    verdict for it.
    """
    if not candidates:
        return {}

    client = client or build_client()
    config = types.GenerateContentConfig(
        system_instruction=CLASSIFIER_SYSTEM,
        response_mime_type="application/json",
        response_schema=_classification_schema(),
        temperature=0.0,  # a judgement, not a generation
    )

    attempts = max(1, settings.extraction_max_attempts)
    for attempt in range(attempts):
        model = model_pool.current_model()
        try:
            response = client.models.generate_content(
                model=model,
                contents=_render_request(row_a, candidates),
                config=config,
            )
            break
        except Exception as exc:
            if _is_daily_quota(exc):
                nxt = model_pool.mark_exhausted(model)
                if nxt is not None:
                    continue
                raise QuotaExhaustedError(
                    f"daily Gemini quota exhausted for every model in the pool: {exc}"
                ) from exc
            if attempt == attempts - 1 or not _is_retryable(exc):
                raise ExtractionError(f"classification failed: {exc}") from exc
            delay = _retry_delay(exc, attempt)
            logger.warning("retryable classifier error, sleeping %.1fs", delay)
            time.sleep(delay)

    payload = getattr(response, "text", None)
    if not payload:
        raise ExtractionError("classifier returned an empty response")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"classifier response was not JSON: {exc}") from exc

    raw = data.get("verdicts") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise ExtractionError("classifier response had no verdicts array")

    results: dict[int, dict[str, object]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            # The model indexes from 1, as the prompt asks; store 0-based.
            position = int(item.get("candidate_index")) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= position < len(candidates):
            logger.warning("classifier returned out-of-range index %s", position + 1)
            continue

        kind = str(item.get("relationship_type") or "").strip().lower()
        if kind not in RELATIONSHIP_TYPES:
            logger.warning("classifier returned unknown type %r", kind)
            continue

        try:
            confidence = min(1.0, max(0.0, float(item.get("confidence"))))
        except (TypeError, ValueError):
            confidence = 0.0

        # Only meaningful for SUPERSEDES, and normalised here rather than at
        # the call site so an unexpected value becomes None once, in one place.
        current = str(item.get("current_fact") or "").strip().lower()

        results[position] = {
            "relationship_type": kind,
            "rationale": str(item.get("rationale") or "").strip(),
            "reconciling_dimension": item.get("reconciling_dimension"),
            "current_fact": current if current in CURRENT_SIDES else None,
            "confidence": confidence,
        }

    return results


# ------------------------------------------------------------------ direction


@dataclass
class Direction:
    """Which end of a supersession is the current fact, and how sure we are."""

    current: str  # CURRENT_A or CURRENT_CANDIDATE
    disputed: bool = False  # the classifier read the arrow the other way
    evidence: str = ""  # the dates that settled it, for the rationale


def resolve_direction(
    row_a: sqlite3.Row, row_b: sqlite3.Row, claimed: str | None
) -> Direction | None:
    """Settle which of two facts describes the later state.

    The classifier proposes a direction; the dates in the facts get the final
    say. That ordering is deliberate. A verdict is an unverifiable judgement,
    but "FY2023 is before FY2024" is checkable evidence sitting in the data,
    and when the two disagree the checkable one should win.

    The dates only speak when they can. `temporal.order` returns 0 for periods
    it cannot rank -- FY2024 against March 2024, or two facts naming no period
    at all -- and in that case the classifier's answer stands unchallenged.

    Returns None when neither source can name a direction. That is the one case
    where nothing is stored: a supersession without a direction is not a weaker
    claim, it is a different and unmade one, and guessing the arrow would put a
    confidently backwards statement in front of a user.
    """
    measured = temporal.order(
        scope_a=row_a["time_scope"],
        statement_a=row_a["statement"],
        scope_b=row_b["time_scope"],
        statement_b=row_b["statement"],
    )
    if measured == 0:
        return Direction(claimed) if claimed else None

    local = CURRENT_A if measured > 0 else CURRENT_CANDIDATE
    if claimed is None or claimed == local:
        return Direction(local)

    later, earlier = (row_a, row_b) if measured > 0 else (row_b, row_a)
    return Direction(
        local,
        disputed=True,
        evidence=(
            f"{_fmt(later['time_scope'] or later['statement'])} is after "
            f"{_fmt(earlier['time_scope'] or earlier['statement'])}"
        ),
    )


# ------------------------------------------------------------------- the step


def link_document_facts(
    conn: sqlite3.Connection,
    document_id: int,
    progress: "ProgressTracker | None" = None,
) -> LinkingSummary:
    """Embed a document's facts and relate them to the rest of the corpus.

    One model call per fact, not per pair: all of a fact's candidates are
    judged together. See _classification_schema for why.
    """
    summary = LinkingSummary(document_id=document_id)
    client = build_client()

    if progress:
        progress.phase("embedding", "embedding new facts")
    summary.facts_embedded = embed_document_facts(conn, document_id, client=client)

    facts = repo.list_facts(conn, document_id=document_id, limit=10_000)

    # Load the existing knowledge layer ONCE, not once per new fact. These rows
    # belong to already-ingested documents and are only ever read here.
    pool = load_candidate_pool(
        conn, exclude_document_id=document_id, expected_dim=settings.embedding_dim
    )
    summary.pool_size = pool.size
    logger.info(
        "linking document %s: %d new fact(s) against a pool of %d existing fact(s)",
        document_id, len(facts), pool.size,
    )
    if pool.size == 0:
        # First document in the layer: nothing to compare against, and no
        # reason to walk its facts at all.
        return summary

    if progress:
        progress.phase(
            "linking",
            f"classifying relationships against {pool.size} existing facts",
            total=len(facts),
        )

    for position, fact in enumerate(facts, start=1):
        if progress:
            progress.update(
                current=position,
                message=f"classifying fact {position} of {len(facts)}",
                relationships=summary.relationships_created,
            )
        stored = repo.get_embedding(conn, fact.id)
        if stored is None:
            continue
        query = unpack_vector(stored[0], stored[1])

        scored = find_candidates(pool, query)
        # Drop pairs already judged on an earlier run, so a re-run costs
        # nothing for work already done.
        scored = [
            (other_id, score)
            for other_id, score in scored
            if not repo.relationship_exists(conn, fact.id, other_id)
        ]
        if not scored:
            continue

        row_a = repo.get_fact_context(conn, fact.id)
        if row_a is None:
            continue

        candidate_rows = []
        candidate_ids = []
        candidate_scores = []
        for other_id, score in scored:
            row_b = repo.get_fact_context(conn, other_id)
            if row_b is None:
                continue
            candidate_rows.append(row_b)
            candidate_ids.append(other_id)
            candidate_scores.append(score)

        if not candidate_rows:
            continue

        summary.facts_compared += 1
        summary.pairs_evaluated += len(candidate_rows)

        try:
            verdicts = classify_candidates(row_a, candidate_rows, client=client)
        except QuotaExhaustedError:
            # Every remaining call would fail the same way. Stop and report
            # what was linked rather than grinding through the rest.
            logger.warning("daily quota exhausted; stopping linking early")
            summary.errors += len(candidate_rows)
            break
        except ExtractionError as exc:
            logger.warning("classification failed for fact %s: %s", fact.id, exc)
            summary.errors += len(candidate_rows)
            continue

        for position, other_id in enumerate(candidate_ids):
            verdict = verdicts.get(position)
            if verdict is None:
                # The model returned no verdict for this candidate. Skipping is
                # safer than defaulting: guessing 'unrelated' would silently
                # bury a pair, and guessing anything else would invent a claim.
                logger.warning(
                    "no verdict for fact %s vs %s", fact.id, other_id
                )
                summary.errors += 1
                continue

            kind = str(verdict["relationship_type"])
            summary.by_type[kind] = summary.by_type.get(kind, 0) + 1

            # UNRELATED pairs are not stored. The table answers "what does this
            # fact relate to", and filling it with negatives would bury the
            # answer -- most candidates above the similarity threshold still
            # turn out unrelated.
            if kind == UNRELATED:
                continue

            rationale = str(verdict["rationale"])
            dimension = verdict.get("reconciling_dimension")
            if kind == RECONCILED and dimension:
                rationale = f"[{dimension}] {rationale}"

            # Every other verdict is symmetric, so the pair goes in as it came.
            # SUPERSEDES has to be oriented: fact_id_a is the current fact.
            id_a, id_b = fact.id, other_id
            direction: Direction | None = None
            if kind == SUPERSEDES:
                direction = resolve_direction(
                    row_a, candidate_rows[position], verdict.get("current_fact")
                )
                if direction is None:
                    # Neither the classifier nor the dates could say which fact
                    # replaced which. Storing it either way round would be an
                    # invention, so this counts as a failed judgement.
                    logger.warning(
                        "supersedes with no resolvable direction: %s vs %s",
                        fact.id, other_id,
                    )
                    summary.errors += 1
                    continue
                if direction.current == CURRENT_CANDIDATE:
                    id_a, id_b = other_id, fact.id
                if direction.disputed:
                    rationale = (
                        f"[direction corrected] The classifier named the other "
                        f"fact as the current one, but {direction.evidence}, so "
                        f"the arrow is stored the other way. {rationale}"
                    )

            created = repo.insert_relationship(
                conn,
                fact_id_a=id_a,
                fact_id_b=id_b,
                relationship_type=kind,
                rationale=rationale,
                confidence=float(verdict["confidence"]),
            )
            if created is not None:
                summary.relationships_created += 1
                if direction is not None and direction.disputed:
                    # Two signals disagreed and one was overruled. The dates are
                    # the better evidence, but a human should still see it.
                    repo.insert_review_item(
                        conn,
                        fact_id=id_a,
                        chunk_id=None,
                        issue_type=ISSUE_UNCERTAIN_SUPERSESSION,
                        note=(
                            f"Fact {id_a} is stored as superseding fact {id_b} "
                            f"on the dates ({direction.evidence}), but the "
                            f"classifier read the supersession the other way."
                        ),
                    )
            logger.info(
                "fact %s -> %s : %s (cosine %.3f)",
                id_a, id_b, kind, candidate_scores[position],
            )

    return summary
