"""Server-sent events for the dashboard's live attack feed."""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..events import bus

router = APIRouter(prefix="/v1", tags=["stream"])

_HEARTBEAT_SECONDS = 15.0


@router.get("/stream")
async def stream(request: Request, backlog: int = Query(default=25, ge=0, le=200)):
    """Live verdict feed.

    Sends a short backlog so a dashboard that connects mid-incident is not
    staring at an empty screen, then streams events as they resolve. Heartbeat
    comments keep the connection open through Cloud Run's idle timeout.
    """

    async def generator():
        queue = bus.subscribe()
        try:
            for event in reversed(bus.recent(limit=backlog)):
                yield f"event: {event['kind']}\ndata: {json.dumps(event, default=str)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                yield f"event: {event['kind']}\ndata: {json.dumps(event, default=str)}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/stream/recent")
async def recent(limit: int = Query(default=100, ge=1, le=200),
                 kind: str | None = None) -> dict:
    """Polling fallback for clients that cannot hold an SSE connection."""
    with contextlib.suppress(Exception):
        return {"events": bus.recent(limit=limit, kind=kind),
                "subscribers": bus.subscriber_count}
    return {"events": []}
