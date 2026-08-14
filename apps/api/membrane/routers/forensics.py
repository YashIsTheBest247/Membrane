"""Time-travel forensics: sessions, replay, integrity, trust, corpus, stats.

Every session can be replayed with the full decision trace — what was blocked,
by which layer, and why. This answers the first question a security team asks
and the first question an auditor asks, and it is why the audit table is
append-only and hash-chained rather than merely written to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .. import audit, trust
from ..circuit import breaker
from ..classifier import escalation_available
from ..classifier import stats as escalation_stats
from ..config import get_settings
from ..db import get_db
from ..hashing import verify_span
from ..layers.l3_taint import tracker
from ..models import (
    AuditEvent,
    CorpusEntry,
    HeldAction,
    Layer,
    Provenance,
    ReplaySpan,
    Session,
    Verdict,
)

router = APIRouter(prefix="/v1", tags=["forensics"])


def _event_dict(event: AuditEvent) -> dict:
    return {
        "seq": event.seq,
        "at": event.created_at.isoformat(),
        "layer": event.layer.value if isinstance(event.layer, Layer) else event.layer,
        "verdict": event.verdict.value if isinstance(event.verdict, Verdict) else event.verdict,
        "reason": event.reason,
        "span": event.span_hash,
        "source": event.source_ref,
        "provenance": event.provenance.value
        if isinstance(event.provenance, Provenance) else event.provenance,
        "signals": event.signals,
        "latency_ms": event.latency_ms,
        "entry_hash": event.entry_hash,
    }


@router.get("/stats")
async def stats(hours: int = Query(default=24, ge=1, le=720),
                db: AsyncSession = Depends(get_db)) -> dict:
    base = await audit.stats(db, since_hours=hours)
    return {
        **base,
        "escalation": {
            "available": escalation_available(),
            **escalation_stats.to_dict(),
        },
        "taint_sessions_in_memory": tracker.active_sessions,
        "breaker_open_sessions": breaker.open_sessions(),
        "privacy": {
            "content_retained": get_settings().replay_retention_enabled,
            "live_preview_enabled": get_settings().live_preview_enabled,
        },
    }


@router.get("/sessions")
async def list_sessions(limit: int = Query(default=50, ge=1, le=500),
                        db: AsyncSession = Depends(get_db)) -> dict:
    rows = (await db.execute(
        select(Session).order_by(Session.last_seen_at.desc()).limit(limit)
    )).scalars().all()
    return {
        "sessions": [
            {
                "session_id": row.id,
                "subject": row.subject,
                "created_at": row.created_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "spans_seen": row.spans_seen,
                "holds": row.holds,
                "blocks": row.blocks,
                "breaker_open": breaker.is_open(row.id),
            }
            for row in rows
        ]
    }


@router.get("/sessions/{session_id}/replay")
async def replay(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Full decision trace for one session, with its integrity proof."""
    events = await audit.trace(db, session_id)
    if not events:
        raise HTTPException(404, "no audit trail for that session")
    integrity = await audit.verify_chain(db, session_id)
    graph = await audit.provenance_graph(db, session_id)
    held = (await db.execute(
        select(HeldAction).where(HeldAction.session_id == session_id)
        .order_by(HeldAction.created_at)
    )).scalars().all()

    return {
        "session_id": session_id,
        "integrity": integrity.to_dict(),
        "events": [_event_dict(e) for e in events],
        "provenance_graph": graph,
        "held_actions": [
            {"action_id": a.id, "tool": a.tool, "capability": a.capability,
             "status": a.status.value, "reason": a.reason,
             "created_at": a.created_at.isoformat(),
             "resolved_by": a.resolved_by, "card": a.card}
            for a in held
        ],
        "in_memory_graph": (tracker.graph(session_id).summary()
                            if session_id in tracker._graphs else None),
    }


@router.get("/sessions/{session_id}/evidence")
async def evidence(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Compliance evidence pack for one session.

    Shaped to EU AI Act Article 12 record-keeping and the ISO/IEC 42001
    monitoring controls. Contains no personal data by construction.
    """
    pack = await audit.evidence_pack(db, session_id)
    if not pack["records"]:
        raise HTTPException(404, "no audit trail for that session")
    return pack


@router.get("/sessions/{session_id}/integrity")
async def integrity(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    check = await audit.verify_chain(db, session_id)
    return {"session_id": session_id, **check.to_dict()}


@router.post("/sessions/{session_id}/forget")
async def forget(session_id: str) -> dict:
    """Drop a session's in-memory provenance graph.

    Content lives in memory for the life of a session and is discarded here.
    The audit trail, which holds only hashes, is unaffected.
    """
    tracker.drop(session_id)
    breaker.reset(session_id)
    return {"session_id": session_id, "in_memory_content_discarded": True}


@router.post("/sessions/{session_id}/breaker/reset")
async def reset_breaker(session_id: str) -> dict:
    breaker.reset(session_id)
    return {"session_id": session_id, "breaker_open": False}


@router.get("/events/recent")
async def recent_events(limit: int = Query(default=100, ge=1, le=500),
                        verdict: str | None = None,
                        db: AsyncSession = Depends(get_db)) -> dict:
    """Audit events across all sessions, newest first."""
    statement = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if verdict:
        statement = statement.where(AuditEvent.verdict == verdict)
    rows = (await db.execute(statement)).scalars().all()
    return {"events": [{**_event_dict(e), "session_id": e.session_id} for e in rows]}


@router.post("/verify-span")
async def verify_span_endpoint(payload: dict) -> dict:
    """Proof without disclosure.

    An operator who suspects a specific string was the payload behind an audit
    row can prove it by re-hashing the candidate here. Membrane never had to
    keep the text to make that answerable.
    """
    text = payload.get("text")
    expected = payload.get("span_hash")
    if not isinstance(text, str) or not isinstance(expected, str):
        raise HTTPException(422, "provide 'text' and 'span_hash'")
    return {"matches": verify_span(text, expected)}


@router.get("/trust")
async def trust_scores(limit: int = Query(default=50, ge=1, le=500),
                       db: AsyncSession = Depends(get_db)) -> dict:
    return {"sources": await trust.leaderboard(db, limit=limit)}


@router.get("/corpus")
async def corpus(limit: int = Query(default=100, ge=1, le=1000),
                 family: str | None = None,
                 db: AsyncSession = Depends(get_db)) -> dict:
    """The self-reinforcing corpus: every blocked payload, deduplicated.

    Each entry is a regression case. Coverage grows with every attempted attack
    rather than with engineering effort.
    """
    statement = select(CorpusEntry).order_by(CorpusEntry.hits.desc()).limit(limit)
    if family:
        statement = statement.where(CorpusEntry.family == family)
    rows = (await db.execute(statement)).scalars().all()
    families = (await db.execute(
        select(CorpusEntry.family, func.count()).group_by(CorpusEntry.family)
    )).all()
    return {
        "entries": [
            {"span": row.span_hash, "family": row.family, "hits": row.hits,
             "first_seen_at": row.first_seen_at.isoformat(),
             "last_seen_at": row.last_seen_at.isoformat(),
             "signals": row.signals}
            for row in rows
        ],
        "by_family": {family: count for family, count in families},
    }


@router.get("/replay-spans/{session_id}")
async def replay_spans(session_id: str, db: AsyncSession = Depends(get_db)) -> dict:
    """Retained span text, when the operator explicitly enabled retention."""
    if not get_settings().replay_retention_enabled:
        raise HTTPException(
            409, "replay retention is disabled; only hashes exist for this session"
        )
    rows = (await db.execute(
        select(ReplaySpan).where(ReplaySpan.session_id == session_id)
        .order_by(ReplaySpan.created_at)
    )).scalars().all()
    return {
        "session_id": session_id,
        "spans": [
            {"span": row.span_hash, "text": row.text,
             "expires_at": row.expires_at.isoformat()}
            for row in rows
        ],
    }
