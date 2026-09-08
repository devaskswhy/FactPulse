"""Which API key and which Gemini model to call right now.

Free-tier quota is metered **per project, per model, per day**, and a key
belongs to a project. So the unit that runs out is not a model and not a key
but the pair of them, and that pair is what this module tracks. Configure three
keys against seven models and there are twenty-one independent daily
allowances; exhausting one is a non-event.

Per-minute limits are NOT a reason to advance. Those clear on their own and the
retry/backoff in extract.py already handles them. Only a daily quota removes a
slot, because nothing we could wait through will bring it back.

**Keys rotate before models.** Given models [A, B] and keys [1, 2, 3] the order
is A/1, A/2, A/3, B/1, B/2, B/3 -- every key is tried on the preferred model
before the pipeline settles for a lesser one. The alternative, exhausting one
key across all its models first, would downgrade output quality while a fresh
key still had the good model available.

The pool is process-global on purpose. Exhaustion is a property of the account
and the day, not of one request, so a document that burns through a slot should
not leave the next document to rediscover that from scratch.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# The pairs known to be spent for today, as (key index, model name). Indices
# rather than the keys themselves so nothing here ever holds a secret longer
# than it must, and so a log line or a /health payload cannot leak one.
_exhausted: set[tuple[int, str]] = set()


@dataclass(frozen=True)
class Slot:
    """One usable (key, model) pair: everything a call needs to be made."""

    key_index: int
    api_key: str
    model: str

    @property
    def label(self) -> str:
        """How a slot is named in logs and /health. Never includes the key."""
        return f"key{self.key_index + 1}/{self.model}"


def _models() -> list[str]:
    """The ordered model pool: the primary first, then the fallbacks.

    Duplicates are removed while preserving order, so listing the primary in
    GEMINI_MODEL_FALLBACKS as well is harmless.
    """
    names = [settings.gemini_model, *settings.fallback_models]
    seen: set[str] = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def _slots() -> list[Slot]:
    """Every configured pair, in the order they should be tried."""
    keys = settings.api_keys
    return [
        Slot(index, key, model)
        for model in _models()
        for index, key in enumerate(keys)
    ]


def current_slot() -> Slot | None:
    """The first pair with quota left, or None when every pair is spent.

    None is a real answer, not a failure to compute one: it is the caller's
    signal to stop and report rather than to keep making calls that cannot
    succeed.
    """
    with _lock:
        for slot in _slots():
            if (slot.key_index, slot.model) not in _exhausted:
                return slot
    return None


def slot_for(model: str) -> Slot | None:
    """The first key with quota left for ONE specific model.

    Embeddings need this. There is a single embedding model and no substitute
    for it, so the generate pool cannot help -- but a second key still can, and
    that is the whole point of configuring one.
    """
    with _lock:
        for index, key in enumerate(settings.api_keys):
            if (index, model) not in _exhausted:
                return Slot(index, key, model)
    return None


def mark_exhausted(slot: Slot) -> None:
    """Record that this pair's daily quota is gone.

    Recording only. The caller asks for what it wants next -- `current_slot()`
    for a generate call, `slot_for()` for an embedding -- because the right
    successor depends on which of those it was doing, and this function cannot
    know.
    """
    with _lock:
        pair = (slot.key_index, slot.model)
        if pair not in _exhausted:
            _exhausted.add(pair)
            logger.warning("daily quota exhausted for %s; dropping it", slot.label)


def pool_status() -> dict[str, object]:
    """For /health, so quota state is visible without reading logs.

    Reports slots by label. No key, and no fragment of a key, appears here:
    /health is unauthenticated and this payload is the sort of thing that ends
    up pasted into an issue.
    """
    with _lock:
        slots = _slots()
        live = [s for s in slots if (s.key_index, s.model) not in _exhausted]
        return {
            "keys": len(settings.api_keys),
            "models": _models(),
            "slots": len(slots),
            "active": live[0].label if live else None,
            "exhausted": sorted(f"key{i + 1}/{m}" for i, m in _exhausted),
            "remaining": len(live),
        }


def reset() -> None:
    """Clear the exhausted set. Used by tests, and after a quota reset."""
    with _lock:
        _exhausted.clear()
