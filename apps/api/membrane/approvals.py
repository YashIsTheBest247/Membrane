"""The held-action queue and the human decision loop.

When Membrane is uncertain it does not guess: it freezes the action and pushes
a decision card to a human, who approves or denies with one tap. Silence is
treated as denial.

A card carries only what a human needs in order to decide — the action being
attempted, why it was held, the provenance chain of the offending argument, and
a diff against what the user originally asked for. The content the agent was
reading is never mirrored into the chat, and argument values are reduced to
shape descriptors before they are stored or transmitted.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from datetime import timedelta
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .config import get_settings
from .contracts import ContractClaims
from .db import session_scope
from .events import bus
from .hashing import short, span_hash
from .layers.l3_taint import TaintResult
from .models import ApprovalStatus, HeldAction, Sensitivity, utcnow

logger = logging.getLogger("membrane.approvals")

# Resolution notifications for long-polling callers on this instance. Callers on
# other instances fall back to the database poll below.
_waiters: dict[str, asyncio.Event] = {}


# --------------------------------------------------------------------------
# value redaction
# --------------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_IBAN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")
_DIGITS = re.compile(r"^[\d\s.-]{6,}$")


def describe_value(value) -> str:
    """Reduce an argument value to a shape a human can reason about.

    The operator needs to know "this is an email address at a domain you have
    never sent mail to", not the address itself. Domains are shown because the
    destination is the decision; local parts and bodies are not.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"number {value}"
    if isinstance(value, (list, tuple)):
        return f"list of {len(value)} item(s)"
    if isinstance(value, dict):
        return f"object with {len(value)} field(s)"

    text = str(value).strip()
    if not text:
        return "empty"
    digest = short(span_hash(text), 8)

    if _EMAIL.match(text):
        domain = text.split("@", 1)[1]
        return f"email address at {domain} · {digest}"
    if "://" in text:
        host = urlparse(text).hostname or "unknown host"
        return f"URL at {host} · {digest}"
    if _IBAN.match(text):
        return f"IBAN in {text[:2]} ending {text[-4:]} · {digest}"
    if _DIGITS.match(text):
        stripped = re.sub(r"\D", "", text)
        return f"{len(stripped)}-digit number ending {stripped[-4:]} · {digest}"
    if len(text) > 80:
        return f"{len(text)} characters of text · {digest}"
    return f"text ({len(text)} chars) · {digest}"


def _flatten(args, path: str = ""):
    if isinstance(args, dict):
        for key, value in args.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(value, (dict, list, tuple)):
                yield from _flatten(value, child)
            else:
                yield child, value
    elif isinstance(args, (list, tuple)):
        for index, value in enumerate(args):
            yield from _flatten(value, f"{path}[{index}]")
    else:
        yield path or "value", args


def build_card(
    *,
    tool: str,
    capability: str | None,
    sensitivity: Sensitivity,
    args: dict,
    reason: str,
    taint: dict[str, TaintResult] | None = None,
    claims: ContractClaims | None = None,
    egress_findings: list[dict] | None = None,
) -> dict:
    """Assemble the four things a decision card carries."""
    taint = taint or {}

    what = {
        "tool": tool,
        "capability": capability or "unmapped",
        "sensitivity": sensitivity.value,
        "arguments": [
            {"name": name, "shape": describe_value(value),
             "tainted": bool(taint.get(name) and taint[name].tainted)}
            for name, value in list(_flatten(args))[:12]
        ],
    }

    provenance = [
        {"argument": path, "chain": result.chain,
         "lineage": [m.to_dict() for m in result.matches[:3]]}
        for path, result in taint.items() if result.tainted
    ][:6]

    granted = claims.capabilities if claims else []
    diff = {
        "authorised_capabilities": granted,
        "attempted_capability": capability or "unmapped",
        "in_envelope": bool(claims and capability and claims.grants(capability)),
        "task_digest": claims.task_digest if claims else "",
        "contract_id": claims.contract_id if claims else None,
        "contract_ttl_remaining_seconds": round(claims.ttl_remaining_seconds, 1)
        if claims else 0.0,
    }

    return {
        "what": what,
        "why": reason,
        "provenance": provenance,
        "diff": diff,
        "egress": egress_findings or [],
    }


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------


async def create(
    session: AsyncSession,
    *,
    session_id: str,
    tool: str,
    capability: str | None,
    sensitivity: Sensitivity,
    reason: str,
    card: dict,
    args_digest: str = "",
) -> HeldAction:
    """Freeze an action and put it in front of a human."""
    settings = get_settings()
    action = HeldAction(
        id=f"held_{secrets.token_hex(8)}",
        session_id=session_id,
        tool=tool,
        capability=capability or "unmapped",
        sensitivity=sensitivity,
        args_digest=args_digest,
        card=card,
        reason=reason,
        status=ApprovalStatus.PENDING,
        expires_at=utcnow() + timedelta(seconds=settings.approval_timeout_seconds),
    )
    session.add(action)
    await session.flush()
    _waiters.setdefault(action.id, asyncio.Event())
    return action


async def resolve(
    session: AsyncSession,
    action_id: str,
    *,
    decision: ApprovalStatus,
    resolved_by: str,
) -> HeldAction | None:
    """Record a human decision. Idempotent: the first decision wins."""
    action = (await session.execute(
        select(HeldAction).where(HeldAction.id == action_id)
    )).scalar_one_or_none()
    if action is None:
        return None
    if action.status is not ApprovalStatus.PENDING:
        return action

    # An expired card cannot be approved, only recorded as denied by timeout.
    expires_at = action.expires_at
    if expires_at.tzinfo is None:
        from datetime import timezone as _tz

        expires_at = expires_at.replace(tzinfo=_tz.utc)
    if utcnow() >= expires_at and decision is ApprovalStatus.APPROVED:
        decision = ApprovalStatus.EXPIRED
        resolved_by = f"{resolved_by} (too late: card had expired)"

    action.status = decision
    action.resolved_at = utcnow()
    action.resolved_by = resolved_by

    waiter = _waiters.get(action_id)
    if waiter is not None:
        waiter.set()

    await bus.publish("approval.resolved", {
        "session_id": action.session_id,
        "action_id": action.id,
        "tool": action.tool,
        "capability": action.capability,
        "status": action.status.value,
        "resolved_by": resolved_by,
    })
    return action


async def get(session: AsyncSession, action_id: str) -> HeldAction | None:
    return (await session.execute(
        select(HeldAction).where(HeldAction.id == action_id)
    )).scalar_one_or_none()


async def pending(session: AsyncSession, limit: int = 50) -> list[HeldAction]:
    rows = (await session.execute(
        select(HeldAction)
        .where(HeldAction.status == ApprovalStatus.PENDING)
        .order_by(HeldAction.created_at.desc())
        .limit(limit)
    )).scalars().all()
    return list(rows)


async def expire_stale() -> int:
    """Time out pending cards. Expiry is denial, never approval."""
    count = 0
    async with session_scope() as session:
        rows = (await session.execute(
            select(HeldAction).where(
                HeldAction.status == ApprovalStatus.PENDING,
                HeldAction.expires_at < utcnow(),
            )
        )).scalars().all()
        for action in rows:
            action.status = ApprovalStatus.EXPIRED
            action.resolved_at = utcnow()
            action.resolved_by = "timeout"
            waiter = _waiters.get(action.id)
            if waiter is not None:
                waiter.set()
            await bus.publish("approval.expired", {
                "session_id": action.session_id,
                "action_id": action.id,
                "tool": action.tool,
                "capability": action.capability,
                "status": ApprovalStatus.EXPIRED.value,
            })
            count += 1
    return count


async def wait_for_decision(action_id: str, timeout: float | None = None) -> ApprovalStatus:
    """Block until the human decides, or the card expires.

    The timeout resolves to EXPIRED, which the caller treats as denial. There is
    no branch here that returns APPROVED without a recorded human decision.
    """
    settings = get_settings()
    deadline = timeout if timeout is not None else settings.approval_timeout_seconds
    event = _waiters.setdefault(action_id, asyncio.Event())

    async def poll_database() -> ApprovalStatus:
        # Covers resolutions that landed on another instance.
        while True:
            async with session_scope() as session:
                action = await get(session, action_id)
                if action is not None and action.status is not ApprovalStatus.PENDING:
                    return action.status
            await asyncio.sleep(0.25)

    waiter = asyncio.create_task(event.wait())
    poller = asyncio.create_task(poll_database())
    try:
        done, _ = await asyncio.wait(
            {waiter, poller}, timeout=deadline, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (waiter, poller):
            if not task.done():
                task.cancel()

    _waiters.pop(action_id, None)

    if poller in done and not poller.cancelled():
        try:
            return poller.result()
        except asyncio.CancelledError:  # pragma: no cover
            pass

    async with session_scope() as session:
        action = await get(session, action_id)
        if action is not None and action.status is not ApprovalStatus.PENDING:
            return action.status
        if action is not None:
            action.status = ApprovalStatus.EXPIRED
            action.resolved_at = utcnow()
            action.resolved_by = "timeout"

    await bus.publish("approval.expired", {
        "action_id": action_id,
        "status": ApprovalStatus.EXPIRED.value,
    })
    return ApprovalStatus.EXPIRED
