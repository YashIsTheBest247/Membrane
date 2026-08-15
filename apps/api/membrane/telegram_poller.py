"""Long-polling transport for Telegram decisions.

The webhook is the right transport in production: Telegram pushes, we do no
work while idle. But a webhook needs a public HTTPS URL, and a laptop on a
conference network does not have one. Rather than make a live demo depend on a
tunnel, this polls `getUpdates` and feeds whatever it finds through the very
same handler the webhook uses — identical signature check, identical
fail-closed branches, identical audit entry. The transport changes; the
decision path does not.

It is off unless `MEMBRANE_TELEGRAM_POLLING` is set, and it refuses to start if
a webhook is already registered, because Telegram will not serve both and the
resulting 409 is confusing to debug.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from .config import get_settings
from .db import session_scope
from .telegram import _api_url, configured

logger = logging.getLogger("membrane.telegram.poller")

# Telegram holds the request open until something happens or this elapses.
LONG_POLL_SECONDS = 25
# After a network error, wait before retrying so a flapping connection does not
# become a busy loop.
BACKOFF_SECONDS = 5.0


async def _drop_webhook(client: httpx.AsyncClient) -> bool:
    """Return True if we now own the update stream."""
    info = await client.get(_api_url("getWebhookInfo"), timeout=10)
    url = (info.json().get("result") or {}).get("url") or ""
    if not url:
        return True
    logger.warning(
        "a webhook is registered at %s; polling and webhooks are mutually "
        "exclusive, so the poller will not start", url,
    )
    return False


async def run(stop: asyncio.Event) -> None:
    """Poll until `stop` is set. Safe to cancel."""
    settings = get_settings()
    if not configured():
        return

    # Imported here rather than at module scope: the router imports telegram,
    # and importing the router at module scope would close the cycle.
    from .routers.approvals import apply_telegram_update

    async with httpx.AsyncClient() as client:
        if not await _drop_webhook(client):
            return

        logger.info("telegram: long-polling for decisions (no webhook needed)")
        offset: int | None = None

        while not stop.is_set():
            try:
                params: dict = {
                    "timeout": LONG_POLL_SECONDS,
                    "allowed_updates": '["callback_query"]',
                }
                if offset is not None:
                    params["offset"] = offset

                response = await client.get(
                    _api_url("getUpdates"), params=params,
                    # Slightly longer than the long poll, or httpx times out
                    # first and every cycle looks like a failure.
                    timeout=LONG_POLL_SECONDS + 10,
                )
                response.raise_for_status()
                updates = response.json().get("result") or []
            except Exception as exc:                      # noqa: BLE001
                if stop.is_set():
                    return
                logger.warning("telegram poll failed, retrying: %s", exc)
                await asyncio.sleep(BACKOFF_SECONDS)
                continue

            for update in updates:
                # Acknowledge before handling: a payload that makes the handler
                # throw must not be redelivered forever.
                offset = update.get("update_id", 0) + 1
                try:
                    async with session_scope() as db:
                        await apply_telegram_update(update, db, client)
                except Exception as exc:                  # noqa: BLE001
                    logger.exception("telegram update failed: %s", exc)

    logger.info("telegram: poller stopped")
