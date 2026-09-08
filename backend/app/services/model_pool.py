"""Which Gemini model to call right now.

Free-tier quota is per model per day, so a single configured model name is a
single point of failure: when its daily allowance is gone the pipeline stops,
and on a demo or a reviewer's first upload that looks like the product is
broken rather than like a quota limit.

This keeps an ordered pool of interchangeable models and advances to the next
one when the current model's DAILY quota is exhausted. Per-minute limits are
not a reason to advance -- those clear on their own and the retry/backoff in
extract.py already handles them.

The pool is process-global on purpose. Exhaustion is a property of the API key,
not of one request, so a document that burns through a model should not leave
the next document to rediscover that from scratch.
"""

from __future__ import annotations

import logging
import threading

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_exhausted: set[str] = set()


def _configured_pool() -> list[str]:
    """The ordered pool: the primary model first, then the fallbacks.

    Duplicates are removed while preserving order, so listing the primary in
    GEMINI_MODEL_FALLBACKS as well is harmless.
    """
    names = [settings.gemini_model, *settings.fallback_models]
    seen: set[str] = set()
    return [n for n in names if n and not (n in seen or seen.add(n))]


def current_model() -> str:
    """The first model in the pool that has not exhausted its daily quota.

    Falls back to the configured primary when every model is spent, so the
    caller still gets a real name and a real error rather than a None to
    special-case.
    """
    with _lock:
        for name in _configured_pool():
            if name not in _exhausted:
                return name
    return settings.gemini_model


def mark_exhausted(model: str) -> str | None:
    """Record that a model's daily quota is gone; return the next one to try.

    Returns None when the pool is spent, which is the caller's signal to stop
    rather than keep failing.
    """
    with _lock:
        if model not in _exhausted:
            _exhausted.add(model)
            logger.warning("daily quota exhausted for %s; removing from pool", model)
        for name in _configured_pool():
            if name not in _exhausted:
                logger.info("falling back to %s", name)
                return name
    return None


def pool_status() -> dict[str, object]:
    """For /health, so quota state is visible without reading logs."""
    with _lock:
        pool = _configured_pool()
        available = [n for n in pool if n not in _exhausted]
        return {
            "pool": pool,
            "active": available[0] if available else None,
            "exhausted": sorted(_exhausted),
            "remaining": len(available),
        }


def reset() -> None:
    """Clear the exhausted set. Used by tests, and after a quota reset."""
    with _lock:
        _exhausted.clear()
