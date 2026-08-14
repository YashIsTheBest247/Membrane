"""The human loop: the held-action queue, the dashboard resolver, and the
Telegram webhook."""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .. import approvals as queue
from .. import audit, telegram
from ..config import get_settings
from ..db import get_db
from ..models import ApprovalStatus, HeldAction, Layer, Verdict
from ..schemas import ApprovalDecision

logger = logging.getLogger("membrane.routers.approvals")

router = APIRouter(prefix="/v1", tags=["approvals"])


def _serialise(action: HeldAction) -> dict:
    return {
        "action_id": action.id,
        "session_id": action.session_id,
        "tool": action.tool,
        "capability": action.capability,
        "sensitivity": action.sensitivity.value,
        "status": action.status.value,
        "reason": action.reason,
        "card": action.card,
        "created_at": action.created_at.isoformat(),
        "expires_at": action.expires_at.isoformat(),
        "resolved_at": action.resolved_at.isoformat() if action.resolved_at else None,
        "resolved_by": action.resolved_by,
        "telegram_delivered": bool(action.telegram_message_id),
    }


@router.get("/approvals")
async def list_pending(
    limit: int = 50, db: AsyncSession = Depends(get_db)
) -> dict:
    actions = await queue.pending(db, limit=limit)
    return {
        "pending": [_serialise(a) for a in actions],
        "telegram_configured": telegram.configured(),
        "timeout_seconds": get_settings().approval_timeout_seconds,
    }


@router.get("/approvals/recent")
async def list_recent(limit: int = 50, db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(
        select(HeldAction).order_by(HeldAction.created_at.desc()).limit(limit)
    )).scalars().all()
    return {"actions": [_serialise(a) for a in rows]}


@router.get("/approvals/{action_id}")
async def get_action(action_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    action = await queue.get(db, action_id)
    if action is None:
        raise HTTPException(404, "no such held action")
    return _serialise(action)


@router.post("/approvals/{action_id}/decision")
async def decide(
    action_id: str,
    body: ApprovalDecision,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Resolve a held action from the dashboard.

    This path exists so the system is operable without a Telegram bot, which
    matters for local development and for deployments that cannot use a
    third-party messenger. It can be switched off.
    """
    settings = get_settings()
    if not settings.dashboard_approvals_enabled:
        raise HTTPException(403, "dashboard approvals are disabled; use the bot")

    action = await queue.get(db, action_id)
    if action is None:
        raise HTTPException(404, "no such held action")
    if action.status is not ApprovalStatus.PENDING:
        return {**_serialise(action), "note": "already resolved; first decision wins"}

    resolved = await queue.resolve(
        db, action_id,
        decision=telegram.decision_to_status(body.decision),
        resolved_by=f"dashboard:{body.resolved_by}",
    )
    assert resolved is not None

    await audit.record(
        db, session_id=resolved.session_id, layer=Layer.APPROVAL,
        verdict=Verdict.PASS if resolved.status is ApprovalStatus.APPROVED
        else Verdict.BLOCK,
        reason=f"human {resolved.status.value} via dashboard",
        source_ref=f"tool:{resolved.tool}",
        signals={"action_id": resolved.id, "resolved_by": resolved.resolved_by},
    )
    if telegram.configured():
        await telegram.close_card(
            resolved, resolved.status.value,
            getattr(request.app.state, "http_client", None),
        )
    return _serialise(resolved)


@router.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> Response:
    """Inbound decision from an inline keyboard.

    Every layer of this handler is fail-closed: an unsigned callback, a foreign
    chat, an unknown action or an expired card all resolve to no authorisation
    being granted. The handler always answers 200 so Telegram does not retry a
    rejected payload into a queue.
    """
    settings = get_settings()

    expected_secret = settings.signing_key[:32]
    if x_telegram_bot_api_secret_token is not None and not hmac.compare_digest(
        x_telegram_bot_api_secret_token, expected_secret
    ):
        logger.warning("telegram webhook secret token mismatch")
        return Response(status_code=200)

    try:
        update = await request.json()
    except Exception:
        return Response(status_code=200)

    try:
        action_id, decision, callback_id, actor = telegram.parse_update(update)
    except telegram.CallbackRejected as exc:
        logger.warning("%s", exc)
        return Response(status_code=200)

    action = await queue.get(db, action_id)
    if action is None:
        await telegram.answer_callback(callback_id, "This card is no longer known.")
        return Response(status_code=200)
    if action.status is not ApprovalStatus.PENDING:
        await telegram.answer_callback(
            callback_id, f"Already {action.status.value}."
        )
        return Response(status_code=200)

    resolved = await queue.resolve(
        db, action_id,
        decision=telegram.decision_to_status(decision),
        resolved_by=actor,
    )
    assert resolved is not None

    await audit.record(
        db, session_id=resolved.session_id, layer=Layer.APPROVAL,
        verdict=Verdict.PASS if resolved.status is ApprovalStatus.APPROVED
        else Verdict.BLOCK,
        reason=f"human {resolved.status.value} via telegram",
        source_ref=f"tool:{resolved.tool}",
        signals={"action_id": resolved.id, "resolved_by": actor},
    )
    await db.commit()

    client = getattr(request.app.state, "http_client", None)
    await telegram.answer_callback(
        callback_id,
        "Approved for this call only." if resolved.status is ApprovalStatus.APPROVED
        else "Denied. The agent has been refused.",
        client,
    )
    await telegram.close_card(resolved, resolved.status.value, client)
    return Response(status_code=200)


@router.post("/telegram/register")
async def register_webhook(request: Request, base_url: str) -> dict:
    """Point the bot at this deployment. Operator convenience."""
    settings = get_settings()
    if not telegram.configured():
        raise HTTPException(400, "telegram bot token or chat id is not configured")
    return await telegram.set_webhook(
        base_url,
        secret_token=settings.signing_key[:32],
        client=getattr(request.app.state, "http_client", None),
    )
