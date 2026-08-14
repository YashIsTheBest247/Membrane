"""The Telegram decision loop.

Telegram is an architectural choice rather than a convenience. A control that
requires an operator to be watching a web console gets bypassed the first time
it is inconvenient; a push to the device already in their hand does not. It
also needs no app install and no licensed seat, which matters for exactly the
small teams and public-sector deployments least able to afford enterprise
security tooling.

Every card is HMAC-signed and bound to a single held action, expires after the
approval timeout, and is removed from the conversation once resolved, so a
compromised chat history can neither authorise anything nor leak anything.
"""

from __future__ import annotations

import html
import logging

import httpx

from .config import get_settings
from .contracts import ContractError, sign_callback, verify_callback
from .models import ApprovalStatus, HeldAction

logger = logging.getLogger("membrane.telegram")


def configured() -> bool:
    settings = get_settings()
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _api_url(method: str) -> str:
    settings = get_settings()
    return f"{settings.telegram_api_base}/bot{settings.telegram_bot_token}/{method}"


# --------------------------------------------------------------------------
# card rendering
# --------------------------------------------------------------------------


def render_card(action: HeldAction) -> str:
    """The four things, and nothing else.

    Note what is absent: the content the agent was reading, the argument
    values, and any part of the user's data. Only shapes, hashes, provenance
    and the capability diff.
    """
    settings = get_settings()
    card = action.card or {}
    what = card.get("what", {})
    diff = card.get("diff", {})

    def esc(value) -> str:
        return html.escape(str(value), quote=False)

    lines = [
        "🛑 <b>Membrane held an action</b>",
        "",
        f"<b>What</b> — <code>{esc(what.get('tool', action.tool))}</code>"
        f" · capability <code>{esc(what.get('capability', action.capability))}</code>"
        f" · <i>{esc(what.get('sensitivity', action.sensitivity.value))}</i>",
    ]

    arguments = what.get("arguments", [])
    if arguments:
        lines.append("")
        for argument in arguments[:6]:
            flag = " ⚠️ tainted" if argument.get("tainted") else ""
            lines.append(f"  · <code>{esc(argument['name'])}</code>: "
                         f"{esc(argument['shape'])}{flag}")

    lines += ["", f"<b>Why</b> — {esc(card.get('why', action.reason))}"]

    provenance = card.get("provenance", [])
    if provenance:
        lines += ["", "<b>Provenance</b>"]
        for entry in provenance[:4]:
            lines.append(f"  · <code>{esc(entry['argument'])}</code> ← "
                         f"{esc(entry['chain'])}")

    lines += [
        "",
        "<b>Diff against the signed intent</b>",
        f"  · authorised: <code>{esc(', '.join(diff.get('authorised_capabilities', [])) or 'none')}</code>",
        f"  · attempted: <code>{esc(diff.get('attempted_capability', '?'))}</code>",
        f"  · inside envelope: <b>{'yes' if diff.get('in_envelope') else 'no'}</b>",
    ]

    egress = card.get("egress", [])
    if egress:
        kinds = ", ".join(sorted({str(f.get("kind")) for f in egress}))
        lines += ["", f"<b>Egress findings</b> — {esc(kinds)}"]

    lines += [
        "",
        f"<i>Session {esc(action.session_id)} · card {esc(action.id)}</i>",
        f"<i>No decision within {settings.approval_timeout_seconds}s is treated "
        f"as DENY.</i>",
    ]
    return "\n".join(lines)


def _keyboard(action_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "✅ Approve once",
             "callback_data": sign_callback(action_id, "approve")},
            {"text": "⛔ Deny",
             "callback_data": sign_callback(action_id, "deny")},
        ]]
    }


# --------------------------------------------------------------------------
# outbound
# --------------------------------------------------------------------------


async def push_card(action: HeldAction,
                    client: httpx.AsyncClient | None = None) -> str | None:
    """Send a decision card. Returns the Telegram message id, or None.

    A failure to deliver is not a failure to protect: the action stays held and
    times out to denial, which is the safe outcome.
    """
    if not configured():
        logger.debug("telegram not configured; card %s stays in the queue", action.id)
        return None

    settings = get_settings()
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        response = await http.post(
            _api_url("sendMessage"),
            json={
                "chat_id": settings.telegram_chat_id,
                "text": render_card(action),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": _keyboard(action.id),
            },
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
        return str((payload.get("result") or {}).get("message_id") or "") or None
    except Exception as exc:
        logger.warning("could not deliver decision card %s: %s", action.id, exc)
        return None
    finally:
        if owned:
            await http.aclose()


async def close_card(action: HeldAction, outcome: str,
                     client: httpx.AsyncClient | None = None) -> None:
    """Remove the card's buttons and replace it with the outcome.

    The card is stripped on resolution so the chat history holds no reusable
    authorisation surface.
    """
    if not configured() or not action.telegram_message_id:
        return

    settings = get_settings()
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        await http.post(
            _api_url("editMessageText"),
            json={
                "chat_id": settings.telegram_chat_id,
                "message_id": int(action.telegram_message_id),
                "text": (f"{'✅' if outcome == 'approved' else '⛔'} "
                         f"<b>{html.escape(outcome.upper())}</b> — "
                         f"<code>{html.escape(action.tool)}</code> "
                         f"(card {html.escape(action.id)})\n"
                         f"<i>Resolved by "
                         f"{html.escape(action.resolved_by or 'timeout')}. "
                         f"Full trace is in the audit table.</i>"),
                "parse_mode": "HTML",
            },
            timeout=5.0,
        )
    except Exception as exc:  # pragma: no cover - network dependent
        logger.warning("could not close card %s: %s", action.id, exc)
    finally:
        if owned:
            await http.aclose()


async def answer_callback(callback_id: str, text: str,
                          client: httpx.AsyncClient | None = None) -> None:
    if not configured():
        return
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        await http.post(
            _api_url("answerCallbackQuery"),
            json={"callback_query_id": callback_id, "text": text[:200]},
            timeout=5.0,
        )
    except Exception:  # pragma: no cover
        pass
    finally:
        if owned:
            await http.aclose()


# --------------------------------------------------------------------------
# inbound
# --------------------------------------------------------------------------


class CallbackRejected(Exception):
    pass


def parse_update(update: dict) -> tuple[str, str, str, str]:
    """Validate an inbound webhook update.

    Returns (action_id, decision, callback_query_id, actor). Raises
    CallbackRejected if the payload is not a signed decision from the
    configured chat: an unsigned or foreign callback authorises nothing.
    """
    settings = get_settings()
    query = update.get("callback_query")
    if not query:
        raise CallbackRejected("update carries no callback_query")

    data = query.get("data") or ""
    try:
        action_id, decision = verify_callback(data)
    except ContractError as exc:
        raise CallbackRejected(f"callback rejected: {exc}") from exc

    message = query.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    if settings.telegram_chat_id and chat_id != str(settings.telegram_chat_id):
        raise CallbackRejected("callback arrived from an unexpected chat")

    user = query.get("from") or {}
    actor = (f"telegram:{user.get('username') or user.get('id') or 'unknown'}")

    return action_id, decision, str(query.get("id") or ""), actor


def decision_to_status(decision: str) -> ApprovalStatus:
    return ApprovalStatus.APPROVED if decision == "approve" else ApprovalStatus.DENIED


async def set_webhook(base_url: str, secret_token: str | None = None,
                      client: httpx.AsyncClient | None = None) -> dict:
    """Register the webhook. Convenience for deployment scripts."""
    if not configured():
        return {"ok": False, "reason": "telegram not configured"}
    owned = client is None
    http = client or httpx.AsyncClient()
    try:
        body = {"url": f"{base_url.rstrip('/')}/v1/telegram/webhook",
                "allowed_updates": ["callback_query"]}
        if secret_token:
            body["secret_token"] = secret_token
        response = await http.post(_api_url("setWebhook"), json=body, timeout=10.0)
        return response.json()
    finally:
        if owned:
            await http.aclose()
