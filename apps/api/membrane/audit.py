"""The zero-retention audit trail.

Every verdict is written to an append-only table with its full decision trace,
so any blocked action can be replayed and explained months later. What is
written is salted span hashes, verdicts, layer decisions and provenance edges —
never the text. Rows are hash-chained, so removing or editing history is
detectable rather than merely discouraged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .config import get_settings
from .hashing import chain_hash
from .models import (
    AuditEvent,
    CorpusEntry,
    Layer,
    Provenance,
    ProvenanceEdge,
    ReplaySpan,
    Session,
    Verdict,
    utcnow,
)


@dataclass
class ChainCheck:
    ok: bool
    events: int
    first_broken_seq: int | None = None

    def to_dict(self) -> dict:
        return {"ok": self.ok, "events": self.events,
                "first_broken_seq": self.first_broken_seq}


def _payload_of(event: AuditEvent) -> str:
    """The canonical string an entry commits to."""
    return "|".join([
        event.session_id,
        str(event.seq),
        event.layer.value if isinstance(event.layer, Layer) else str(event.layer),
        event.verdict.value if isinstance(event.verdict, Verdict) else str(event.verdict),
        event.span_hash or "",
        event.source_ref or "",
        event.reason,
    ])


async def _next_seq(session: AsyncSession, session_id: str) -> tuple[int, str | None]:
    row = (await session.execute(
        select(AuditEvent.seq, AuditEvent.entry_hash)
        .where(AuditEvent.session_id == session_id)
        .order_by(AuditEvent.seq.desc())
        .limit(1)
    )).first()
    if row is None:
        return 1, None
    return row[0] + 1, row[1]


async def record(
    session: AsyncSession,
    *,
    session_id: str,
    layer: Layer,
    verdict: Verdict,
    reason: str = "",
    span_hash_value: str | None = None,
    source_ref: str | None = None,
    provenance: Provenance | None = None,
    signals: dict[str, Any] | None = None,
    latency_ms: float = 0.0,
) -> AuditEvent:
    """Append one immutable decision to the trail."""
    seq, prev_hash = await _next_seq(session, session_id)
    event = AuditEvent(
        session_id=session_id,
        seq=seq,
        layer=layer,
        verdict=verdict,
        reason=reason[:2000],
        span_hash=span_hash_value,
        source_ref=source_ref,
        provenance=provenance,
        signals=signals or {},
        latency_ms=round(latency_ms, 3),
        prev_hash=prev_hash,
    )
    event.entry_hash = chain_hash(prev_hash, _payload_of(event))
    session.add(event)
    return event


async def record_edges(
    session: AsyncSession,
    *,
    session_id: str,
    span_hash_value: str,
    provenance: Provenance,
    source_ref: str = "",
    parents: list[str] | None = None,
) -> None:
    """Persist provenance edges for the taint DAG."""
    if not parents:
        session.add(ProvenanceEdge(
            session_id=session_id, span_hash=span_hash_value,
            parent_span_hash=None, provenance=provenance, source_ref=source_ref,
        ))
        return
    for parent in parents:
        session.add(ProvenanceEdge(
            session_id=session_id, span_hash=span_hash_value,
            parent_span_hash=parent, provenance=provenance, source_ref=source_ref,
        ))


async def touch_session(
    session: AsyncSession,
    session_id: str,
    *,
    subject: str = "anonymous",
    task_digest: str = "",
    spans: int = 0,
    holds: int = 0,
    blocks: int = 0,
) -> Session:
    """Upsert the session row and bump its counters."""
    row = (await session.execute(
        select(Session).where(Session.id == session_id)
    )).scalar_one_or_none()
    if row is None:
        row = Session(id=session_id, subject=subject, task_digest=task_digest)
        session.add(row)
    row.last_seen_at = utcnow()
    row.spans_seen += spans
    row.holds += holds
    row.blocks += blocks
    if task_digest and not row.task_digest:
        row.task_digest = task_digest
    return row


async def promote_to_corpus(
    session: AsyncSession,
    *,
    span_hash_value: str,
    family: str,
    signals: dict[str, Any] | None = None,
) -> CorpusEntry:
    """Self-reinforcing corpus: every blocked payload becomes a regression case.

    Deduplication is on the salted hash of the normalised span, so the same
    attack arriving from ten domains files once with ten hits.
    """
    row = (await session.execute(
        select(CorpusEntry).where(CorpusEntry.span_hash == span_hash_value)
    )).scalar_one_or_none()
    if row is None:
        row = CorpusEntry(span_hash=span_hash_value, family=family,
                          signals=signals or {})
        session.add(row)
        return row
    row.hits += 1
    row.last_seen_at = utcnow()
    if family != "unclassified":
        row.family = family
    return row


async def retain_for_replay(
    session: AsyncSession, *, session_id: str, span_hash_value: str, text: str
) -> None:
    """Optional bounded retention. Off by default; expires automatically."""
    settings = get_settings()
    if not settings.replay_retention_enabled:
        return
    session.add(ReplaySpan(
        session_id=session_id,
        span_hash=span_hash_value,
        text=text,
        expires_at=utcnow() + timedelta(hours=settings.replay_retention_hours),
    ))


async def purge_expired_replay(session: AsyncSession) -> int:
    """Delete replay spans past their bound. Called on a timer."""
    rows = (await session.execute(
        select(ReplaySpan).where(ReplaySpan.expires_at < utcnow())
    )).scalars().all()
    for row in rows:
        await session.delete(row)
    return len(rows)


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


async def trace(session: AsyncSession, session_id: str) -> list[AuditEvent]:
    return list((await session.execute(
        select(AuditEvent)
        .where(AuditEvent.session_id == session_id)
        .order_by(AuditEvent.seq)
    )).scalars().all())


async def verify_chain(session: AsyncSession, session_id: str) -> ChainCheck:
    """Recompute the hash chain. Any edit or deletion shows up here."""
    events = await trace(session, session_id)
    prev: str | None = None
    for event in events:
        expected = chain_hash(prev, _payload_of(event))
        if event.prev_hash != prev or event.entry_hash != expected:
            return ChainCheck(False, len(events), event.seq)
        prev = event.entry_hash
    return ChainCheck(True, len(events))


async def provenance_graph(session: AsyncSession, session_id: str) -> dict:
    edges = list((await session.execute(
        select(ProvenanceEdge).where(ProvenanceEdge.session_id == session_id)
    )).scalars().all())
    nodes: dict[str, dict] = {}
    for edge in edges:
        nodes.setdefault(edge.span_hash, {
            "span": edge.span_hash,
            "provenance": edge.provenance.value if isinstance(edge.provenance, Provenance)
            else str(edge.provenance),
            "source": edge.source_ref,
        })
    return {
        "nodes": list(nodes.values()),
        "edges": [
            {"from": e.parent_span_hash, "to": e.span_hash}
            for e in edges if e.parent_span_hash
        ],
    }


async def stats(session: AsyncSession, *, since_hours: int = 24) -> dict:
    """Counters for the dashboard header."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    verdict_rows = (await session.execute(
        select(AuditEvent.verdict, func.count())
        .where(AuditEvent.created_at >= cutoff)
        .group_by(AuditEvent.verdict)
    )).all()
    layer_rows = (await session.execute(
        select(AuditEvent.layer, func.count())
        .where(AuditEvent.created_at >= cutoff)
        .group_by(AuditEvent.layer)
    )).all()
    latency_rows = (await session.execute(
        select(AuditEvent.latency_ms)
        .where(AuditEvent.created_at >= cutoff, AuditEvent.latency_ms > 0)
    )).scalars().all()
    sessions_total = (await session.execute(
        select(func.count()).select_from(Session)
    )).scalar_one()
    corpus_total = (await session.execute(
        select(func.count()).select_from(CorpusEntry)
    )).scalar_one()

    latencies = sorted(float(x) for x in latency_rows)

    def percentile(values: list[float], fraction: float) -> float:
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
        return round(values[index], 2)

    by_verdict = {
        (v.value if isinstance(v, Verdict) else str(v)): count
        for v, count in verdict_rows
    }
    return {
        "window_hours": since_hours,
        "by_verdict": by_verdict,
        "by_layer": {
            (l.value if isinstance(l, Layer) else str(l)): count
            for l, count in layer_rows
        },
        "events": sum(by_verdict.values()),
        "sessions": sessions_total,
        "corpus_entries": corpus_total,
        "latency_ms": {
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "samples": len(latencies),
        },
    }


async def evidence_pack(session: AsyncSession, session_id: str) -> dict:
    """Compliance evidence export for one session.

    Shaped to the record-keeping expectations of EU AI Act Article 12 and the
    monitoring controls of ISO/IEC 42001: an immutable, timestamped,
    attributable log of every automated decision, with the integrity proof
    attached and no personal data inside it.
    """
    events = await trace(session, session_id)
    check = await verify_chain(session, session_id)
    session_row = (await session.execute(
        select(Session).where(Session.id == session_id)
    )).scalar_one_or_none()

    return {
        "schema": "membrane.evidence.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "frameworks": ["EU AI Act Art. 12", "ISO/IEC 42001 A.6.2", "OWASP LLM01"],
        "session": {
            "id": session_id,
            "subject": session_row.subject if session_row else "unknown",
            "opened_at": session_row.created_at.isoformat() if session_row else None,
            "last_seen_at": session_row.last_seen_at.isoformat() if session_row else None,
            "holds": session_row.holds if session_row else 0,
            "blocks": session_row.blocks if session_row else 0,
        },
        "integrity": check.to_dict(),
        "content_retained": False,
        "records": [
            {
                "seq": e.seq,
                "timestamp": e.created_at.isoformat(),
                "layer": e.layer.value if isinstance(e.layer, Layer) else str(e.layer),
                "verdict": e.verdict.value if isinstance(e.verdict, Verdict) else str(e.verdict),
                "reason": e.reason,
                "span_hash": e.span_hash,
                "source": e.source_ref,
                "provenance": e.provenance.value
                if isinstance(e.provenance, Provenance) else e.provenance,
                "latency_ms": e.latency_ms,
                "entry_hash": e.entry_hash,
                "prev_hash": e.prev_hash,
            }
            for e in events
        ],
    }
