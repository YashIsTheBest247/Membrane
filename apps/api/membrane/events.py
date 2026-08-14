"""The live event bus behind the dashboard's attack feed.

In-process fan-out is always active. When Redis is configured, events are also
published to a channel and mirrored back in, so several Cloud Run instances
share one feed. Events carry verdicts, hashes and provenance; content previews
appear only when `live_preview_enabled` is set, and are never persisted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from typing import Any

from .config import get_settings

logger = logging.getLogger("membrane.events")

CHANNEL = "membrane:events"
_RING_SIZE = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._recent: deque[dict] = deque(maxlen=_RING_SIZE)
        self._redis: Any | None = None
        self._pump: asyncio.Task | None = None
        self._seq = 0

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        settings = get_settings()
        if not settings.redis_url:
            return
        try:
            import redis.asyncio as aioredis  # type: ignore

            self._redis = aioredis.from_url(settings.redis_url,
                                            decode_responses=True)
            await self._redis.ping()
            self._pump = asyncio.create_task(self._mirror_from_redis())
            logger.info("event bus attached to redis at %s", settings.redis_url)
        except Exception as exc:
            logger.warning("redis unavailable, using in-process bus only: %s", exc)
            self._redis = None

    async def stop(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._pump
            self._pump = None
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()
            self._redis = None

    async def _mirror_from_redis(self) -> None:  # pragma: no cover - needs redis
        assert self._redis is not None
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(CHANNEL)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                except (TypeError, ValueError):
                    continue
                if event.get("origin") == id(self):
                    continue  # already delivered locally
                self._fan_out(event)
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(CHANNEL)
                await pubsub.aclose()

    # -- publish / subscribe ---------------------------------------------

    def _fan_out(self, event: dict) -> None:
        self._recent.append(event)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A dashboard that cannot keep up loses events, never the proxy.
                pass

    async def publish(self, kind: str, payload: dict) -> dict:
        self._seq += 1
        event = {
            "seq": self._seq,
            "kind": kind,
            "ts": time.time(),
            "origin": id(self),
            **payload,
        }
        self._fan_out(event)
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.publish(CHANNEL, json.dumps(event, default=str))
        return event

    def subscribe(self, maxsize: int = 256) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    # -- introspection ---------------------------------------------------

    def recent(self, limit: int = 100, kind: str | None = None) -> list[dict]:
        events = [e for e in self._recent if kind is None or e["kind"] == kind]
        return list(events)[-limit:][::-1]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


bus = EventBus()


def preview(text: str | None) -> str | None:
    """Truncated content preview for the live feed, gated by configuration.

    This is the only place in the codebase where inspected text is allowed to
    leave the request, and it goes to an in-memory stream, never to storage.
    """
    settings = get_settings()
    if not settings.live_preview_enabled or not text:
        return None
    collapsed = " ".join(text.split())
    limit = settings.live_preview_chars
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"
