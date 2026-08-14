"""Relational model.

Design rule for this file: no column ever holds inspected content. Text is
represented by salted hashes, verdicts, provenance labels and structural
metadata. The one exception is `ReplaySpan`, which is written only when the
operator explicitly enables bounded replay retention.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(enum_class: type[enum.Enum]) -> sa.Enum:
    """Store an enum as its value, and read it back as the enum member.

    `native_enum=False` keeps this a VARCHAR with a check constraint, so the
    schema is identical on SQLite and PostgreSQL and adding a member is a
    migration rather than a database-specific type alteration.
    """
    return sa.Enum(
        enum_class,
        native_enum=False,
        length=32,
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


# --------------------------------------------------------------------------
# enumerations
# --------------------------------------------------------------------------


class Provenance(str, enum.Enum):
    """Where a span came from. Trust is a property of origin, not content."""

    SYSTEM = "system"          # Membrane's own policy text
    USER = "user"              # the operator typed it
    RETRIEVED = "retrieved"    # web page, email, document, file upload
    TOOL = "tool"              # MCP or API response
    AGENT = "agent"            # the model produced it
    UNKNOWN = "unknown"

    @property
    def is_trusted(self) -> bool:
        return self in (Provenance.SYSTEM, Provenance.USER)


class Verdict(str, enum.Enum):
    PASS = "pass"      # clean content reaches the agent unchanged
    STRIP = "strip"    # payload excised, benign remainder forwarded
    HOLD = "hold"      # frozen, pushed to a human for a decision
    BLOCK = "block"    # refused, logged, replayable


class Layer(str, enum.Enum):
    L1_SANITISER = "l1_sanitiser"
    L2_SEPARATOR = "l2_separator"
    L3_TAINT = "l3_taint"
    L4_CAPABILITY = "l4_capability"
    EGRESS = "egress"
    MCP_SCAN = "mcp_scan"
    BREAKER = "breaker"
    APPROVAL = "approval"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class Sensitivity(str, enum.Enum):
    READ = "read"                  # observing the world
    WRITE = "write"                # changing state, recoverable
    IRREVERSIBLE = "irreversible"  # money, mail, deletion, shell


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


class Session(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    subject: str = Field(default="anonymous", index=True)
    task_digest: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, index=True)
    last_seen_at: datetime = Field(default_factory=utcnow)
    breaker_tripped_at: datetime | None = Field(default=None)
    spans_seen: int = Field(default=0)
    holds: int = Field(default=0)
    blocks: int = Field(default=0)


class IntentContract(SQLModel, table=True):
    """A signed, time-limited capability envelope agreed before the task runs."""

    __tablename__ = "intent_contracts"

    id: str = Field(primary_key=True)
    session_id: str = Field(index=True, foreign_key="sessions.id")
    subject: str = Field(default="anonymous")
    task_digest: str = Field(default="")
    capabilities: list[str] = Field(default_factory=list, sa_column=sa.Column(sa.JSON))
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(index=True)
    nonce: str = Field(default="")
    signature: str = Field(default="")
    revoked_at: datetime | None = Field(default=None)


class AuditEvent(SQLModel, table=True):
    """Append-only, hash-chained decision log. Never contains content."""

    __tablename__ = "audit_events"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    seq: int = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    layer: Layer = Field(sa_column=sa.Column(_enum(Layer), index=True))
    verdict: Verdict = Field(sa_column=sa.Column(_enum(Verdict), index=True))
    span_hash: str | None = Field(default=None, index=True)
    source_ref: str | None = Field(default=None, index=True)
    provenance: Provenance | None = Field(default=None, sa_column=sa.Column(_enum(Provenance)))
    reason: str = Field(default="")
    signals: dict[str, Any] = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))
    latency_ms: float = Field(default=0.0)
    prev_hash: str | None = Field(default=None)
    entry_hash: str = Field(default="")


class ProvenanceEdge(SQLModel, table=True):
    """One edge of the taint DAG: a span, and what it derived from."""

    __tablename__ = "provenance_edges"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    span_hash: str = Field(index=True)
    parent_span_hash: str | None = Field(default=None, index=True)
    provenance: Provenance = Field(sa_column=sa.Column(_enum(Provenance)))
    source_ref: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)


class HeldAction(SQLModel, table=True):
    """A tool call frozen pending a human decision."""

    __tablename__ = "held_actions"

    id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    tool: str = Field(default="")
    capability: str = Field(default="")
    sensitivity: Sensitivity = Field(
        default=Sensitivity.WRITE, sa_column=sa.Column(_enum(Sensitivity))
    )
    args_digest: str = Field(default="")
    # Card payload: what / why / provenance / diff. Argument values are
    # redacted to shape descriptors before they are stored or transmitted.
    card: dict[str, Any] = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))
    reason: str = Field(default="")
    status: ApprovalStatus = Field(
        default=ApprovalStatus.PENDING, sa_column=sa.Column(_enum(ApprovalStatus), index=True)
    )
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: datetime | None = Field(default=None)
    resolved_by: str | None = Field(default=None)
    telegram_message_id: str | None = Field(default=None)


class SourceTrust(SQLModel, table=True):
    """Per-domain / per-tool trust that decays on incident and recovers slowly."""

    __tablename__ = "source_trust"

    source_ref: str = Field(primary_key=True)
    score: float = Field(default=0.70)
    incidents: int = Field(default=0)
    clean_spans: int = Field(default=0)
    last_incident_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=utcnow)


class CorpusEntry(SQLModel, table=True):
    """Self-reinforcing corpus: every blocked payload becomes a regression case."""

    __tablename__ = "corpus_entries"

    id: int | None = Field(default=None, primary_key=True)
    span_hash: str = Field(index=True, unique=True)
    family: str = Field(default="unclassified", index=True)
    signals: dict[str, Any] = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))
    first_seen_at: datetime = Field(default_factory=utcnow)
    last_seen_at: datetime = Field(default_factory=utcnow)
    hits: int = Field(default=1)
    promoted: bool = Field(default=False)


class BenchRun(SQLModel, table=True):
    """A recorded InjectBench execution, for the public leaderboard."""

    __tablename__ = "bench_runs"

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    label: str = Field(default="local")
    total_cases: int = Field(default=0)
    unprotected_success: int = Field(default=0)
    protected_success: int = Field(default=0)
    asr_reduction: float = Field(default=0.0)
    false_positive_rate: float = Field(default=0.0)
    p50_latency_ms: float = Field(default=0.0)
    p95_latency_ms: float = Field(default=0.0)
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=sa.Column(sa.JSON))


class ReplaySpan(SQLModel, table=True):
    """Optional bounded retention. Written only when explicitly enabled."""

    __tablename__ = "replay_spans"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    span_hash: str = Field(index=True)
    text: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime = Field(default_factory=utcnow, index=True)
