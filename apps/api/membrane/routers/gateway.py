"""The proxy surface: contracts, ingest, and the tool-call boundary.

These three endpoints are the whole integration story. An agent framework calls
`/v1/intent` once per task, `/v1/ingest` for every piece of untrusted content it
retrieves, and `/v1/toolcall` before it executes anything. No change to the
model, the prompts, or the application logic.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .. import audit, contracts, pipeline
from ..config import get_settings
from ..db import get_db
from ..hashing import span_hash
from ..models import IntentContract, Verdict, utcnow
from ..policy import get_policy
from ..schemas import (
    ContractRequest,
    ContractResponse,
    IngestRequest,
    IngestResponse,
    ToolCallRequest,
    ToolCallResponse,
    UserIntentRequest,
)

router = APIRouter(prefix="/v1", tags=["gateway"])


@router.get("/capabilities")
async def capabilities() -> dict:
    """The catalogue an agent can request in an intent contract."""
    policy = get_policy()
    return {"capabilities": policy.catalogue()}


@router.post("/intent", response_model=ContractResponse)
async def issue_contract(
    body: ContractRequest, db: AsyncSession = Depends(get_db)
) -> ContractResponse:
    """Issue a signed, time-limited capability envelope for one task.

    The task text is registered as trusted provenance at the same time, which is
    what gives L3 an anchor to distinguish user intent from page content later.
    """
    policy = get_policy()
    known = set(policy.capability_names)
    unknown = sorted(
        c for c in body.capabilities
        if c not in known and not c.endswith(".*") and c != "*"
    )

    token, claims = contracts.issue(
        session_id=body.session_id,
        capabilities=body.capabilities,
        subject=body.subject,
        task_digest=span_hash(body.task) if body.task else "",
        ttl_seconds=body.ttl_seconds,
    )

    # The session row has to exist before the contract that references it.
    # SQLite does not enforce foreign keys by default, so ordering here is only
    # visibly required on PostgreSQL — which is exactly why it belongs in the
    # code rather than in a comment.
    await audit.touch_session(db, body.session_id, subject=body.subject,
                              task_digest=claims.task_digest)
    await db.flush()

    db.add(IntentContract(
        id=claims.contract_id,
        session_id=body.session_id,
        subject=body.subject,
        task_digest=claims.task_digest,
        capabilities=claims.capabilities,
        issued_at=claims.issued_at,
        expires_at=claims.expires_at,
        nonce=claims.nonce,
        signature=token.split(".", 1)[1][:64],
    ))

    if body.task:
        await pipeline.register_user_intent(
            db, session_id=body.session_id, text=body.task, subject=body.subject
        )

    return ContractResponse(
        contract=token, claims=claims.to_dict(), unknown_capabilities=unknown
    )


@router.get("/intent/{contract_id}")
async def inspect_contract(
    contract_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    row = (await db.execute(
        select(IntentContract).where(IntentContract.id == contract_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "no such contract")
    return {
        "contract_id": row.id,
        "session_id": row.session_id,
        "subject": row.subject,
        "capabilities": row.capabilities,
        "issued_at": row.issued_at.isoformat(),
        "expires_at": row.expires_at.isoformat(),
        "revoked_at": row.revoked_at.isoformat() if row.revoked_at else None,
    }


@router.post("/intent/{contract_id}/revoke")
async def revoke_contract(
    contract_id: str, db: AsyncSession = Depends(get_db)
) -> dict:
    row = (await db.execute(
        select(IntentContract).where(IntentContract.id == contract_id)
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "no such contract")
    row.revoked_at = utcnow()
    return {"contract_id": contract_id, "revoked_at": row.revoked_at.isoformat()}


@router.post("/user-intent")
async def register_intent(
    body: UserIntentRequest, db: AsyncSession = Depends(get_db)
) -> dict:
    """Register additional trusted user input mid-session."""
    digest = await pipeline.register_user_intent(
        db, session_id=body.session_id, text=body.text, subject=body.subject
    )
    return {"session_id": body.session_id, "span": digest, "provenance": "user"}


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    body: IngestRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> IngestResponse:
    """Pass one piece of untrusted content through L1, L2 and L3.

    The response body is what the agent should place in its context. Content
    passes through; instructions do not.
    """
    settings = get_settings()
    if len(body.content.encode("utf-8", "ignore")) > settings.max_content_bytes:
        raise HTTPException(413, "content exceeds the configured maximum")

    outcome = await pipeline.ingest(
        db,
        session_id=body.session_id,
        content=body.content,
        source_ref=body.source,
        content_type=body.content_type,
        provenance=body.provenance_label,
        subject=body.subject,
        http_client=getattr(request.app.state, "http_client", None),
    )
    return IngestResponse(**outcome.to_dict())


@router.post("/toolcall", response_model=ToolCallResponse)
async def toolcall(
    body: ToolCallRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> ToolCallResponse:
    """Gate one proposed tool call at the egress boundary.

    A `verdict` of `pass` with `allowed: true` means execute. Anything else
    means do not: `hold` carries an `action_id` the caller can wait on, and
    `block` is final.
    """
    outcome = await pipeline.check_tool_call(
        db,
        session_id=body.session_id,
        tool=body.tool,
        args=body.args,
        contract_token=body.contract,
        subject=body.subject,
        wait_for_approval=body.wait_for_approval,
        http_client=getattr(request.app.state, "http_client", None),
    )
    return ToolCallResponse(**outcome.to_dict())


@router.post("/simulate")
async def simulate(
    body: IngestRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """Dry-run a piece of content and show both channels side by side.

    Used by the dashboard's playground and by anyone who wants to see what the
    layers actually did without wiring an agent up first.
    """
    from ..layers import l1_sanitiser, l2_separator

    settings = get_settings()
    sanitised = l1_sanitiser.sanitise(
        body.content, content_type=body.content_type,
        max_decode_depth=settings.max_decode_depth,
    )
    separated = await l2_separator.separate(
        sanitised.text,
        quarantine_threshold=settings.separator_quarantine_threshold,
        ambiguous_threshold=settings.separator_ambiguous_threshold,
    )
    verdict = (Verdict.STRIP if sanitised.removed_anything or separated.quarantined
               else Verdict.PASS)

    return {
        "verdict": verdict.value,
        "forwarded_to_agent": separated.clean_text,
        "l1": {
            "summary": sanitised.audit_summary(),
            "removed": [
                {"kind": r.kind, "detail": r.detail, "depth": r.depth,
                 "recovered": r.text[:400], "span": span_hash(r.text)}
                for r in sanitised.removals[:25]
            ],
        },
        "l2": {
            "summary": separated.audit_summary(),
            "spans": [
                {**decision.to_audit(),
                 "text": decision.text[:400],
                 "span": span_hash(decision.text)}
                for decision in separated.spans[:40]
            ],
        },
    }
