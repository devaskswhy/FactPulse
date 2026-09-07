"""Ingestion progress, published for the SSE endpoint.

A 100-page document spends minutes in model calls. Without granular progress
the client sees one long silence and cannot tell a slow job from a hung one.

State lives in memory, keyed by document id, and is deliberately not persisted:
it describes a run in flight, and a run does not survive a restart either.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ProgressState:
    """Where an ingestion has got to."""

    document_id: int | None = None
    filename: str = ""
    phase: str = "queued"        # queued|parsing|chunking|extracting|embedding|linking|checking|done|failed
    message: str = ""
    current: int = 0             # units done in this phase
    total: int = 0               # units in this phase (0 when unknown)
    # Running tallies, so a client can show the layer growing live.
    pages: int = 0
    chunks: int = 0
    facts: int = 0
    relationships: int = 0
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str | None = None

    @property
    def elapsed(self) -> float:
        return self.updated_at - self.started_at

    @property
    def percent(self) -> float | None:
        """Progress within the current phase, not the whole job.

        The whole job cannot be a single honest percentage: extraction time
        depends on model latency and how hard the rate limiter pushes back, so
        a global bar would either lie or stall. Phase plus N-of-M is truthful.
        """
        if self.total <= 0:
            return None
        return round(min(100.0, self.current / self.total * 100), 1)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["elapsed"] = round(self.elapsed, 1)
        data["percent"] = self.percent
        return data


class ProgressTracker:
    """Publishes progress for one document. Safe to update from worker threads."""

    def __init__(self, registry: "ProgressRegistry", state: ProgressState) -> None:
        self._registry = registry
        self._state = state
        self._lock = threading.Lock()

    @property
    def state(self) -> ProgressState:
        return self._state

    def update(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._state, key, value)
            self._state.updated_at = time.time()
        self._registry.notify(self._state.document_id)

    def phase(self, phase: str, message: str, total: int = 0) -> None:
        """Enter a new phase, resetting the within-phase counter."""
        self.update(phase=phase, message=message, current=0, total=total)

    def advance(self, by: int = 1, **fields: Any) -> None:
        with self._lock:
            self._state.current += by
            for key, value in fields.items():
                setattr(self._state, key, value)
            self._state.updated_at = time.time()
        self._registry.notify(self._state.document_id)


class ProgressRegistry:
    """In-memory store of in-flight and recently finished ingestions."""

    def __init__(self, keep: int = 20) -> None:
        self._states: dict[int, ProgressState] = {}
        self._events: dict[int, threading.Event] = {}
        self._order: list[int] = []
        self._keep = keep
        self._lock = threading.Lock()
        # Runs that have no document id yet (parsing happens before the row
        # exists) are held under a negative temporary key.
        self._next_temp = -1

    def start(self, filename: str, document_id: int | None = None) -> ProgressTracker:
        with self._lock:
            key = document_id if document_id is not None else self._next_temp
            if document_id is None:
                self._next_temp -= 1
            state = ProgressState(document_id=key, filename=filename)
            self._states[key] = state
            self._events[key] = threading.Event()
            self._order.append(key)
            self._evict()
        return ProgressTracker(self, state)

    def rekey(self, tracker: ProgressTracker, document_id: int) -> None:
        """Move a run from its temporary key to the real document id.

        Parsing starts before the document row exists, so the first updates
        have nowhere permanent to live. Once the id is known the state moves,
        and the old key is left pointing at the same object so a client that
        subscribed early is not orphaned.
        """
        old = tracker.state.document_id
        with self._lock:
            if old == document_id:
                return
            self._states[document_id] = tracker.state
            self._events[document_id] = self._events.get(old, threading.Event())
            if document_id not in self._order:
                self._order.append(document_id)
        tracker.update(document_id=document_id)

    def get(self, document_id: int) -> ProgressState | None:
        return self._states.get(document_id)

    def latest(self) -> ProgressState | None:
        with self._lock:
            return self._states.get(self._order[-1]) if self._order else None

    def all(self) -> list[ProgressState]:
        with self._lock:
            seen: set[int] = set()
            out = []
            for key in reversed(self._order):
                state = self._states.get(key)
                if state is not None and id(state) not in seen:
                    seen.add(id(state))
                    out.append(state)
            return out

    def notify(self, document_id: int | None) -> None:
        """Wake anything waiting on this document's next update."""
        if document_id is None:
            return
        event = self._events.get(document_id)
        if event is not None:
            event.set()

    def wait(self, document_id: int, timeout: float) -> bool:
        """Block until the next update, or until `timeout`."""
        event = self._events.get(document_id)
        if event is None:
            return False
        fired = event.wait(timeout)
        if fired:
            event.clear()
        return fired

    def _evict(self) -> None:
        while len(self._order) > self._keep:
            key = self._order.pop(0)
            self._states.pop(key, None)
            self._events.pop(key, None)


# One registry per process. Ingestion is in-process, so this is sufficient;
# a multi-worker deployment would need Redis or similar behind the same API.
registry = ProgressRegistry()
