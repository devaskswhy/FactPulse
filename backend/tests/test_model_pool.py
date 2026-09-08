"""Model rotation on daily-quota exhaustion.

Free-tier quota is per model per day. A single configured model name means a
reviewer's first upload can fail for a reason that has nothing to do with the
document, so the pool exists to make that a non-event.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import model_pool


@pytest.fixture(autouse=True)
def clean_pool():
    model_pool.reset()
    yield
    model_pool.reset()


def test_active_model_is_the_configured_primary():
    assert model_pool.current_model() == settings.gemini_model


def test_exhausting_a_model_advances_to_the_next():
    first = model_pool.current_model()
    second = model_pool.mark_exhausted(first)

    assert second is not None, "a fallback must be available"
    assert second != first
    assert model_pool.current_model() == second


def test_pool_is_walked_in_order_until_empty():
    """Every model gets a turn, and the last exhaustion reports no fallback."""
    seen = []
    nxt = model_pool.current_model()
    while nxt is not None:
        seen.append(nxt)
        nxt = model_pool.mark_exhausted(nxt)

    assert len(seen) == len(set(seen)), "no model should be handed out twice"
    assert len(seen) > 1, "the pool must contain fallbacks, not just the primary"
    assert model_pool.pool_status()["remaining"] == 0


def test_status_reports_what_is_left():
    first = model_pool.current_model()
    model_pool.mark_exhausted(first)

    status = model_pool.pool_status()
    assert first in status["exhausted"]
    assert status["active"] not in status["exhausted"]
    assert status["remaining"] == len(status["pool"]) - 1


def test_marking_the_same_model_twice_is_harmless():
    first = model_pool.current_model()
    model_pool.mark_exhausted(first)
    before = model_pool.pool_status()["remaining"]
    model_pool.mark_exhausted(first)

    assert model_pool.pool_status()["remaining"] == before
