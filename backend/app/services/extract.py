"""Fact extraction via Gemini structured output.

The response comes back through `response_schema`, so the model returns typed
JSON directly. Nothing here scrapes JSON out of a markdown fence.

One schema note worth stating plainly. The extractor is supposed to attach
open-ended `attributes` to a fact, and Gemini's response schema (an OpenAPI
subset) has no way to describe an object with arbitrary, unknown keys. So
attributes travel as an ARRAY of {key, value} pairs instead of an object. The
keys stay completely unconstrained -- which is the property that matters -- and
the shape maps one-to-one onto the fact_attributes EAV table.

`fact_type` is a free STRING with no enum and no candidate list in the prompt.
The model invents the label. That is the whole point of the schema design; see
docs/ARCHITECTURE.md section 4.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from app.core.config import settings

logger = logging.getLogger(__name__)

# Transient server-side conditions: overload, rate limiting, and generic 5xx.
# These are worth retrying; a 400 or a 404 is not, and retrying one only wastes
# quota and delays the real error.
_RETRYABLE_MARKERS = ("503", "429", "500", "502", "504", "UNAVAILABLE", "RESOURCE_EXHAUSTED")

# A 429 is only worth retrying when it is a per-minute rate limit. A *daily*
# quota will not clear within any backoff window we would sit through, so
# retrying one just burns four attempts and ~30 seconds before failing anyway.
# Gemini names the quota in the error body, which is how the two are told apart.
_NON_RETRYABLE_QUOTA_MARKERS = ("PerDay", "PerDayPerProject", "GenerateRequestsPerDay")


class ExtractionError(RuntimeError):
    """The model call failed or returned something unusable."""


class QuotaExhaustedError(ExtractionError):
    """A daily quota is spent. Retrying will not help until it resets."""


def _is_retryable(exc: Exception) -> bool:
    text = str(exc)
    if any(marker in text for marker in _NON_RETRYABLE_QUOTA_MARKERS):
        return False
    return any(marker in text for marker in _RETRYABLE_MARKERS)


def _is_daily_quota(exc: Exception) -> bool:
    return any(marker in str(exc) for marker in _NON_RETRYABLE_QUOTA_MARKERS)


# Gemini tells us how long to wait ("retryDelay": "59s"). Honour it: a
# per-minute cap needs ~60s, and exponential backoff capped at 30s never waits
# long enough, so every attempt is spent before the window reopens.
_RETRY_DELAY_RE = re.compile(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s")

# Ceiling on an honoured hint. Long enough for a per-minute window, short
# enough that a pathological value cannot stall a run indefinitely.
MAX_RETRY_DELAY_SECONDS = 70.0


def _retry_delay(exc: Exception, attempt: int) -> float:
    """Seconds to wait: the server's own hint when it gives one, else backoff."""
    match = _RETRY_DELAY_RE.search(str(exc))
    if match:
        hinted = float(match.group(1)) + 1.0  # clear the boundary
        return min(hinted, MAX_RETRY_DELAY_SECONDS)
    return min(2.0 * (2**attempt), 30.0) + random.uniform(0, 1.0)


@dataclass
class ExtractedFact:
    """One fact as the model returned it, before verification or grounding."""

    fact_type: str
    subject: str
    statement: str
    quote: str
    confidence: float
    normalized_value: str | None = None
    unit: str | None = None
    time_scope: str | None = None
    attributes: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------- schema


def _fact_schema() -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        properties={
            "fact_type": types.Schema(
                type=types.Type.STRING,
                description=(
                    "A short label naming what KIND of fact this is, in "
                    "lowercase kebab-case, one to three words. Invent whatever "
                    "label best describes this particular fact. There is no "
                    "list to choose from and no preferred vocabulary -- if this "
                    "fact is a kind you have not labelled before, make up a new "
                    "label for it."
                ),
            ),
            "subject": types.Schema(
                type=types.Type.STRING,
                description="What or who the fact is about.",
            ),
            "statement": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The fact as one clear, standalone sentence that still "
                    "makes sense when read on its own, away from the source "
                    "text."
                ),
            ),
            "normalized_value": types.Schema(
                type=types.Type.STRING,
                nullable=True,
                description=(
                    "The core value if the fact is numeric or a date, as a bare "
                    "string without its unit (for example 4.2, 128, "
                    "2025-03-31). Null for facts that are not numeric."
                ),
            ),
            "unit": types.Schema(
                type=types.Type.STRING,
                nullable=True,
                description=(
                    "Unit of normalized_value if it has one, for example "
                    "USD million, percent, count, INR crore. Null otherwise."
                ),
            ),
            "time_scope": types.Schema(
                type=types.Type.STRING,
                nullable=True,
                description=(
                    "The period or date the fact applies to, if the text states "
                    "one, for example FY2024, CY2024, as of 2025-01-31. Null if "
                    "the text does not say."
                ),
            ),
            "attributes": types.Schema(
                type=types.Type.ARRAY,
                description=(
                    "Any other qualifiers worth recording about this fact, as "
                    "key/value pairs. Use whatever keys are appropriate -- "
                    "there is no fixed set. Return an empty array if there is "
                    "nothing to add."
                ),
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "key": types.Schema(type=types.Type.STRING),
                        "value": types.Schema(type=types.Type.STRING),
                    },
                    required=["key", "value"],
                ),
            ),
            "quote": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The exact substring of the source text that supports this "
                    "fact, copied character for character. Do not paraphrase, "
                    "summarise, correct, or join separated passages. This is "
                    "checked against the source and the fact is flagged if it "
                    "does not match."
                ),
            ),
            "confidence": types.Schema(
                type=types.Type.NUMBER,
                description=(
                    "Your own confidence from 0 to 1 that this is a real fact, "
                    "correctly extracted, and correctly quoted. Be honest: use "
                    "a low value when the text is ambiguous."
                ),
            ),
        },
        required=["fact_type", "subject", "statement", "quote", "confidence"],
    )


def response_schema() -> types.Schema:
    return types.Schema(
        type=types.Type.OBJECT,
        properties={"facts": types.Schema(type=types.Type.ARRAY, items=_fact_schema())},
        required=["facts"],
    )


SYSTEM_INSTRUCTION = """You extract checkable facts from documents for a fact-verification system.

Extract every meaningful, checkable fact the text actually states:
  - numerical facts: figures, dates, percentages, counts, amounts, durations
  - semantic facts: roles, titles, statuses, relationships, locations,
    ownership, obligations, and similar clear assertions

Rules:
  - Only extract what the text states. Never infer, calculate, combine numbers,
    or bring in outside knowledge.
  - One fact per entry. Split a sentence carrying several facts into several.
  - The quote must be copied verbatim from the source text, character for
    character, as one continuous span. It is checked against the source.
  - Prefer the shortest quote that fully supports the fact.
  - Skip headings, boilerplate, page furniture, and anything that is not a
    factual claim.
  - If the text contains no checkable facts, return an empty list.
  - Choose fact_type yourself. There is no list of allowed values and no house
    vocabulary to match. Name the kind of fact as precisely as you can.
  - Set confidence honestly. Ambiguous or hedged statements deserve a low
    value; it is used to route facts for human review, not to score you.
"""

USER_TEMPLATE = """Extract the facts stated in the following source text.

<source_text>
{chunk_text}
</source_text>
"""


# ------------------------------------------------------------------ the call


def build_client(api_key: str | None = None) -> genai.Client:
    key = api_key or settings.gemini_api_key
    if not key:
        raise ExtractionError(
            "GEMINI_API_KEY is not set -- create backend/.env from .env.example"
        )
    return genai.Client(api_key=key)


def _coerce_attributes(raw: object) -> dict[str, str]:
    """Fold the key/value array back into a dict.

    Tolerates the model returning a plain object anyway, and drops entries with
    an empty key. Last write wins on duplicate keys.
    """
    result: dict[str, str] = {}
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if str(k).strip()}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                key = str(item.get("key", "")).strip()
                if key:
                    result[key] = str(item.get("value", ""))
    return result


def _clean_optional(value: object) -> str | None:
    """Normalise the model's several ways of saying 'nothing here'."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "na", "-"}:
        return None
    return text


def extract_facts_from_chunk(
    chunk_text: str, client: genai.Client | None = None
) -> list[ExtractedFact]:
    """Ask Gemini for the facts in one chunk.

    Raises ExtractionError if the call fails or the payload cannot be read.
    Returns an empty list when the model genuinely finds nothing, which is a
    valid answer and not an error.
    """
    if not chunk_text or not chunk_text.strip():
        return []

    client = client or build_client()

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_INSTRUCTION,
        response_mime_type="application/json",
        response_schema=response_schema(),
        temperature=0.1,
    )

    # Retry transient overload/rate-limit responses with exponential backoff and
    # jitter. Without this a momentary 503 permanently costs the chunk its
    # facts, which showed up immediately in practice.
    attempts = max(1, settings.extraction_max_attempts)
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=USER_TEMPLATE.format(chunk_text=chunk_text),
                config=config,
            )
            break
        except Exception as exc:
            last = exc
            if _is_daily_quota(exc):
                raise QuotaExhaustedError(
                    f"daily Gemini quota exhausted for {settings.gemini_model}: {exc}"
                ) from exc
            if attempt == attempts - 1 or not _is_retryable(exc):
                raise ExtractionError(f"Gemini call failed: {exc}") from exc
            delay = _retry_delay(exc, attempt)
            logger.warning(
                "retryable Gemini error (attempt %d/%d), sleeping %.1fs: %s",
                attempt + 1, attempts, delay, str(exc)[:120],
            )
            time.sleep(delay)
    else:  # pragma: no cover - loop always breaks or raises
        raise ExtractionError(f"Gemini call failed: {last}")

    payload = getattr(response, "text", None)
    if not payload:
        raise ExtractionError("Gemini returned an empty response")

    try:
        # Structured output, so this is already JSON -- there is no fence here
        # to strip and no free text to salvage it from.
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"response was not valid JSON: {exc}") from exc

    raw_facts = data.get("facts") if isinstance(data, dict) else None
    if not isinstance(raw_facts, list):
        raise ExtractionError("response did not contain a facts array")

    facts: list[ExtractedFact] = []
    for raw in raw_facts:
        if not isinstance(raw, dict):
            continue

        fact_type = str(raw.get("fact_type") or "").strip()
        statement = str(raw.get("statement") or "").strip()
        quote = str(raw.get("quote") or "")
        # A fact with no type, no statement, or no quote cannot be stored or
        # checked, so there is nothing meaningful to review either. Drop it,
        # but log it rather than passing over it in silence.
        if not fact_type or not statement or not quote.strip():
            logger.warning("discarding malformed fact from model: %r", raw)
            continue

        try:
            confidence = float(raw.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = min(1.0, max(0.0, confidence))

        facts.append(
            ExtractedFact(
                fact_type=fact_type,
                subject=str(raw.get("subject") or "").strip(),
                statement=statement,
                quote=quote,
                confidence=confidence,
                normalized_value=_clean_optional(raw.get("normalized_value")),
                unit=_clean_optional(raw.get("unit")),
                time_scope=_clean_optional(raw.get("time_scope")),
                attributes=_coerce_attributes(raw.get("attributes")),
            )
        )
    return facts
