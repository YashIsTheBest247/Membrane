"""Adaptive source trust.

Each domain and tool carries a score that decays sharply on incident and
recovers slowly with clean history. The effect is that the system gets harder
to attack the longer it runs: a domain that has injected once is held to a
stricter standard thereafter, because L2's thresholds move with the score.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from .config import get_settings
from .models import SourceTrust, utcnow

_HOST_RE = re.compile(r"^[a-z0-9.-]+$", re.I)


def source_key(source: str | None) -> str:
    """Normalise a source reference to the unit trust is tracked on.

    URLs collapse to their registrable host, tools to `tool:<name>`, and
    everything else passes through as a label. Tracking per-host rather than
    per-URL is deliberate: a site that injects on one page has demonstrated
    something about the site.
    """
    if not source:
        return "unknown"
    value = source.strip()
    if "://" in value:
        host = (urlparse(value).hostname or "").lower()
        return host or "unknown"
    if value.startswith(("tool:", "mcp:", "email:", "file:", "user:")):
        return value.lower()
    if _HOST_RE.match(value):
        return value.lower()
    return value[:120].lower()


def _decayed(score: float, updated_at: datetime, recovery_per_hour: float) -> float:
    """Apply slow recovery for elapsed clean time."""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    hours = max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds() / 3600)
    return min(1.0, score + hours * recovery_per_hour)


async def get_score(session: AsyncSession, source: str | None) -> float:
    """Current trust for a source, with time-based recovery applied."""
    settings = get_settings()
    key = source_key(source)
    if key.startswith("user:"):
        return 1.0
    row = (await session.execute(
        select(SourceTrust).where(SourceTrust.source_ref == key)
    )).scalar_one_or_none()
    if row is None:
        return settings.trust_initial
    return _decayed(row.score, row.updated_at, settings.trust_recovery_per_hour)


async def _upsert(session: AsyncSession, key: str) -> SourceTrust:
    row = (await session.execute(
        select(SourceTrust).where(SourceTrust.source_ref == key)
    )).scalar_one_or_none()
    if row is None:
        row = SourceTrust(source_ref=key, score=get_settings().trust_initial)
        session.add(row)
    return row


async def record_incident(session: AsyncSession, source: str | None,
                          *, weight: float = 1.0) -> float:
    """A source injected. Collapse its score."""
    settings = get_settings()
    key = source_key(source)
    row = await _upsert(session, key)
    current = _decayed(row.score, row.updated_at, settings.trust_recovery_per_hour)
    multiplier = settings.trust_incident_multiplier ** max(0.1, weight)
    row.score = max(0.02, current * multiplier)
    row.incidents += 1
    row.last_incident_at = utcnow()
    row.updated_at = utcnow()
    return row.score


async def record_clean(session: AsyncSession, source: str | None,
                       *, spans: int = 1) -> float:
    """A source behaved. Nudge its score up, bounded."""
    settings = get_settings()
    key = source_key(source)
    row = await _upsert(session, key)
    current = _decayed(row.score, row.updated_at, settings.trust_recovery_per_hour)
    row.score = min(1.0, current + 0.002 * spans)
    row.clean_spans += spans
    row.updated_at = utcnow()
    return row.score


async def leaderboard(session: AsyncSession, limit: int = 50) -> list[dict]:
    """Least trusted sources first: the operator's watch list."""
    rows = (await session.execute(
        select(SourceTrust).order_by(SourceTrust.score).limit(limit)
    )).scalars().all()
    settings = get_settings()
    return [
        {
            "source": row.source_ref,
            "score": round(_decayed(row.score, row.updated_at,
                                    settings.trust_recovery_per_hour), 4),
            "incidents": row.incidents,
            "clean_spans": row.clean_spans,
            "last_incident_at": row.last_incident_at.isoformat()
            if row.last_incident_at else None,
        }
        for row in rows
    ]
