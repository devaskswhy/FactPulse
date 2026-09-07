"""Server-sent events for ingestion progress.

A 100-page document spends minutes inside model calls. Without granular
progress a client sees one long silence and cannot distinguish a slow job from
a hung one -- which is exactly what a demo needs to avoid.

Progress is reported as phase plus N-of-M within that phase, not as a single
global percentage. A global bar would have to guess how long extraction takes,
and that depends on model latency and how hard the rate limiter pushes back, so
it would either lie or stall. "extracting: chunk 84 of 221" is honest.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from app.schemas.progress import ProgressList, ProgressSnapshot
from app.services.progress import registry

router = APIRouter(tags=["progress"])

# How often to emit even when nothing changed. Keeps proxies from closing an
# idle connection, and gives the client a live elapsed counter during a long
# model call where no discrete step completes for a while.
HEARTBEAT_SECONDS = 2.0


def _event(state) -> str:
    return f"data: {json.dumps(state.as_dict(), default=str)}\n\n"


@router.get(
    "/progress",
    response_model=ProgressList,
    summary="Snapshot of recent and in-flight ingestions",
)
def list_progress() -> ProgressList:
    states = registry.all()
    return ProgressList(
        total=len(states),
        runs=[ProgressSnapshot(**s.as_dict()) for s in states],
    )


@router.get(
    "/documents/{document_id}/progress",
    response_model=ProgressSnapshot,
    summary="Current progress for one document",
)
def get_progress(document_id: int) -> ProgressSnapshot:
    state = registry.get(document_id)
    if state is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"no progress recorded for document {document_id}",
        )
    return ProgressSnapshot(**state.as_dict())


async def _stream(document_id: int, timeout: float) -> AsyncIterator[str]:
    state = registry.get(document_id)
    if state is None:
        yield f"data: {json.dumps({'error': f'no progress for document {document_id}'})}\n\n"
        return

    # Emit immediately so a client that connects late still sees where things
    # stand rather than waiting for the next change.
    yield _event(state)

    waited = 0.0
    while True:
        # registry.wait blocks a thread, so it runs in the default executor
        # rather than stalling the event loop for every other request.
        changed = await asyncio.to_thread(
            registry.wait, document_id, HEARTBEAT_SECONDS
        )
        state = registry.get(document_id)
        if state is None:
            return

        yield _event(state)

        if state.phase in {"done", "failed"}:
            return

        waited = 0.0 if changed else waited + HEARTBEAT_SECONDS
        if waited >= timeout:
            yield f"data: {json.dumps({'phase': state.phase, 'stalled': True})}\n\n"
            return


@router.get(
    "/documents/{document_id}/progress/stream",
    summary="Live progress for one document (SSE)",
)
async def stream_progress(
    document_id: int,
    timeout: float = Query(
        600.0,
        ge=5.0,
        le=3600.0,
        description="Give up after this many seconds with no change at all.",
    ),
) -> StreamingResponse:
    """Server-sent events, one JSON object per update.

    Emits on every change and at least every couple of seconds regardless, so
    the client can show a live elapsed timer through a long model call. Closes
    when the run reaches `done` or `failed`.

        const es = new EventSource('/documents/3/progress/stream');
        es.onmessage = (e) => render(JSON.parse(e.data));
    """
    if registry.get(document_id) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"no progress recorded for document {document_id}",
        )
    return StreamingResponse(
        _stream(document_id, timeout),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Nginx buffers streamed responses by default, which would hold
            # every event until the run finished.
            "X-Accel-Buffering": "no",
        },
    )
