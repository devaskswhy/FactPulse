"""Quota rotation across keys and models.

Free-tier quota is metered per project, per model, per day, and a key belongs
to a project. So the unit that runs out is the (key, model) PAIR, and that is
what the pool hands out. A reviewer's first upload failing because one pair is
spent would look like a broken product rather than a rate limit, which is the
whole reason this exists.

The tests configure keys explicitly rather than reading whatever is in .env, so
they behave the same on a machine with one key and on a machine with three.
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


@pytest.fixture
def three_keys(monkeypatch):
    """Three distinct keys, so key rotation is observable."""
    monkeypatch.setattr(settings, "gemini_api_key", "key-one")
    monkeypatch.setattr(settings, "gemini_api_key_2", "key-two")
    monkeypatch.setattr(settings, "gemini_api_key_3", "key-three")
    monkeypatch.setattr(settings, "gemini_api_keys", "")
    return settings.api_keys


def test_duplicate_keys_are_collapsed(monkeypatch):
    """Two slots backed by one key share one quota.

    Counting a duplicate twice would make the pool advertise capacity it does
    not have, and turn a single exhausted allowance into two dead slots.
    """
    monkeypatch.setattr(settings, "gemini_api_key", "same")
    monkeypatch.setattr(settings, "gemini_api_key_2", "same")
    monkeypatch.setattr(settings, "gemini_api_key_3", "  ")
    monkeypatch.setattr(settings, "gemini_api_keys", "same,other")

    assert settings.api_keys == ["same", "other"]


def test_blank_slots_are_ignored(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "only")
    monkeypatch.setattr(settings, "gemini_api_key_2", "")
    monkeypatch.setattr(settings, "gemini_api_key_3", None)
    monkeypatch.setattr(settings, "gemini_api_keys", " , ")

    assert settings.api_keys == ["only"]


def test_the_first_slot_is_the_primary_key_on_the_primary_model(three_keys):
    slot = model_pool.current_slot()

    assert slot.api_key == three_keys[0]
    assert slot.model == settings.gemini_model


def test_keys_rotate_before_models(three_keys):
    """The ordering that protects output quality.

    Every key is tried on the preferred model before the pipeline settles for a
    lesser one. Exhausting one key across all its models first would downgrade
    quality while a fresh key still had the good model available.
    """
    primary = settings.gemini_model
    seen = []
    for _ in range(len(three_keys)):
        slot = model_pool.current_slot()
        seen.append(slot)
        model_pool.mark_exhausted(slot)

    assert [s.model for s in seen] == [primary] * len(three_keys)
    assert [s.api_key for s in seen] == three_keys

    # Only now does it fall back to a different model, starting again at key 1.
    nxt = model_pool.current_slot()
    assert nxt.model != primary
    assert nxt.api_key == three_keys[0]


def test_every_pair_gets_a_turn_and_then_the_pool_is_empty(three_keys):
    seen = []
    while (slot := model_pool.current_slot()) is not None:
        seen.append((slot.key_index, slot.model))
        model_pool.mark_exhausted(slot)

    assert len(seen) == len(set(seen)), "no pair should be handed out twice"
    status = model_pool.pool_status()
    assert len(seen) == status["slots"] == len(three_keys) * len(status["models"])
    assert status["remaining"] == 0
    assert status["active"] is None


def test_an_exhausted_pair_does_not_block_the_same_model_on_another_key(three_keys):
    """The point of a second key: the good model is still reachable."""
    first = model_pool.current_slot()
    model_pool.mark_exhausted(first)

    second = model_pool.current_slot()
    assert second.model == first.model, "the model itself is not spent"
    assert second.api_key != first.api_key


def test_embeddings_walk_keys_for_their_one_model(three_keys):
    """There is no substitute embedding model, but there is another key."""
    model = settings.gemini_embedding_model

    first = model_pool.slot_for(model)
    assert first.model == model

    model_pool.mark_exhausted(first)
    second = model_pool.slot_for(model)

    assert second.model == model
    assert second.api_key != first.api_key

    for _ in range(len(three_keys)):
        remaining = model_pool.slot_for(model)
        if remaining is None:
            break
        model_pool.mark_exhausted(remaining)
    assert model_pool.slot_for(model) is None


def test_exhausting_an_embedding_slot_leaves_generate_slots_alone(three_keys):
    """Quota is per model, so spending one says nothing about the others."""
    model_pool.mark_exhausted(model_pool.slot_for(settings.gemini_embedding_model))

    assert model_pool.current_slot() is not None
    assert model_pool.current_slot().model == settings.gemini_model


def test_status_never_leaks_a_key(three_keys):
    model_pool.mark_exhausted(model_pool.current_slot())
    status = model_pool.pool_status()

    rendered = repr(status)
    for key in three_keys:
        assert key not in rendered, "an unauthenticated payload must not carry keys"
    assert status["keys"] == len(three_keys)
    assert status["active"].startswith("key")


def test_marking_the_same_pair_twice_is_harmless(three_keys):
    slot = model_pool.current_slot()
    model_pool.mark_exhausted(slot)
    before = model_pool.pool_status()["remaining"]
    model_pool.mark_exhausted(slot)

    assert model_pool.pool_status()["remaining"] == before
