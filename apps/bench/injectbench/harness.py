"""The InjectBench harness.

Runs every case twice against the same reference agent:

    unprotected   content goes straight into the agent's context
    protected     content passes through Membrane's ingest path first, and
                  every tool call the agent proposes is gated at the boundary

The only variable between the two arms is Membrane. Attack success in the
protected arm requires both that the agent still proposes the malicious call
*and* that the call is allowed. A held action with no human present resolves to
denial, which is the deployed behaviour and so is what the benchmark measures.
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

# The proxy is a sibling package; add it to the path before importing it.
_API_DIR = Path(__file__).resolve().parents[2] / "api"
if str(_API_DIR) not in sys.path:
    sys.path.insert(0, str(_API_DIR))


def _configure_database() -> str:
    """Point the run at a scratch database unless the caller chose one."""
    existing = os.environ.get("MEMBRANE_DATABASE_URL")
    if existing:
        return existing
    path = Path(tempfile.gettempdir()) / f"injectbench_{uuid.uuid4().hex[:8]}.db"
    dsn = f"sqlite+aiosqlite:///{path.as_posix()}"
    os.environ["MEMBRANE_DATABASE_URL"] = dsn
    return dsn


_configure_database()

from membrane import contracts, pipeline  # noqa: E402
from membrane.db import init_db, session_scope  # noqa: E402
from membrane.layers import mcp_scan  # noqa: E402
from membrane.layers.l3_taint import tracker  # noqa: E402
from membrane.models import Provenance, Verdict  # noqa: E402

from .agent import ReferenceAgent, ToolCall  # noqa: E402
from .schema import BenignDocument, Case, load_benign, load_cases  # noqa: E402

_CHANNEL_PROVENANCE = {
    "retrieved": Provenance.RETRIEVED,
    "email": Provenance.RETRIEVED,
    "document": Provenance.RETRIEVED,
    "tool": Provenance.TOOL,
    "mcp_schema": Provenance.TOOL,
}


@dataclass
class CaseResult:
    case_id: str
    family: str
    title: str
    unprotected_success: bool
    protected_success: bool
    protected_verdicts: list[str] = field(default_factory=list)
    blocked_by: str = ""
    proposed_unprotected: list[dict] = field(default_factory=list)
    proposed_protected: list[dict] = field(default_factory=list)
    ingest_verdict: str = ""
    ingest_latency_ms: float = 0.0
    notes: str = ""

    @property
    def prevented(self) -> bool:
        return self.unprotected_success and not self.protected_success

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "family": self.family,
            "title": self.title,
            "unprotected_success": self.unprotected_success,
            "protected_success": self.protected_success,
            "prevented": self.prevented,
            "blocked_by": self.blocked_by,
            "ingest_verdict": self.ingest_verdict,
            "ingest_latency_ms": round(self.ingest_latency_ms, 3),
            "protected_verdicts": self.protected_verdicts,
            "proposed_unprotected": self.proposed_unprotected,
            "proposed_protected": self.proposed_protected,
            "notes": self.notes,
        }


@dataclass
class BenignResult:
    document_id: str
    title: str
    spans: int
    quarantined: int
    removed: int
    verdict: str
    latency_ms: float
    quarantined_reasons: list[str] = field(default_factory=list)

    @property
    def false_positive(self) -> bool:
        return self.quarantined > 0

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "title": self.title,
            "spans": self.spans,
            "quarantined": self.quarantined,
            "removed": self.removed,
            "verdict": self.verdict,
            "latency_ms": round(self.latency_ms, 3),
            "false_positive": self.false_positive,
            "reasons": self.quarantined_reasons,
        }


# --------------------------------------------------------------------------
# the two arms
# --------------------------------------------------------------------------


def run_unprotected(case: Case, agent: ReferenceAgent) -> list[ToolCall]:
    """The agent reads the raw document. No barrier of any kind."""
    if case.is_mcp:
        context = _mcp_context(case.mcp_tools)
    else:
        context = case.content

    if case.deputy is not None:
        return agent.act_as_deputy(case.deputy, context)
    return agent.plan(f"{case.user_task}\n\n{context}", user_task=case.user_task)


def _mcp_context(tools: list[dict]) -> str:
    """Tool schemas enter the context as text. That is the whole problem."""
    lines: list[str] = []
    for tool in tools:
        lines.append(f"TOOL {tool.get('name', '?')}: {tool.get('description', '')}")
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        for name, prop in (schema.get("properties") or {}).items():
            lines.append(f"  ARG {name}: {prop.get('description', '')}")
    return "\n".join(lines)


async def run_protected(case: Case, agent: ReferenceAgent) -> CaseResult:
    """The same agent, behind Membrane."""
    session_id = f"bench_{case.id}_{uuid.uuid4().hex[:6]}"
    result = CaseResult(
        case_id=case.id, family=case.family, title=case.title,
        unprotected_success=False, protected_success=False, notes=case.notes,
    )

    async with session_scope() as db:
        token, _ = contracts.issue(
            session_id=session_id,
            capabilities=case.capabilities,
            subject="injectbench",
            ttl_seconds=600,
        )
        if case.user_task:
            await pipeline.register_user_intent(
                db, session_id=session_id, text=case.user_task, subject="injectbench"
            )

        # --- content crosses the barrier ---------------------------------
        if case.is_mcp:
            report = mcp_scan.scan_server(case.source, case.mcp_tools)
            result.ingest_verdict = report.verdict.value
            # A server whose descriptions carry instructions is not connected,
            # so nothing it advertises reaches the context.
            context = ("" if report.verdict is not Verdict.PASS
                       else _mcp_context(case.mcp_tools))
            if report.verdict is not Verdict.PASS:
                result.blocked_by = "mcp_scan"
        else:
            started = time.perf_counter()
            outcome = await pipeline.ingest(
                db,
                session_id=session_id,
                content=case.content,
                source_ref=case.source,
                content_type=case.content_type,
                provenance=_CHANNEL_PROVENANCE.get(case.channel, Provenance.RETRIEVED),
                subject="injectbench",
            )
            result.ingest_latency_ms = (time.perf_counter() - started) * 1000
            result.ingest_verdict = outcome.verdict.value
            context = outcome.content
            if outcome.quarantined:
                result.blocked_by = "l2_separator"
            elif outcome.removed:
                result.blocked_by = "l1_sanitiser"

    # --- the agent plans from whatever survived ---------------------------
    if case.deputy is not None:
        calls = agent.act_as_deputy(case.deputy, context)
    else:
        calls = agent.plan(f"{case.user_task}\n\n{context}", user_task=case.user_task)
    result.proposed_protected = [c.to_dict() for c in calls]

    # --- every proposed call is gated -------------------------------------
    allowed: list[ToolCall] = []
    async with session_scope() as db:
        for call in calls:
            outcome = await pipeline.check_tool_call(
                db,
                session_id=session_id,
                tool=call.tool,
                args=call.args,
                contract_token=token,
                subject="injectbench",
                wait_for_approval=False,
            )
            result.protected_verdicts.append(f"{call.tool}:{outcome.verdict.value}")
            if outcome.allowed:
                allowed.append(call)
            elif not result.blocked_by:
                result.blocked_by = ("l4_capability" if outcome.code != "egress_block"
                                     else "egress")

    result.protected_success = case.success.met_by(allowed)
    tracker.drop(session_id)
    return result


async def run_case(case: Case, agent: ReferenceAgent) -> CaseResult:
    unprotected_calls = run_unprotected(case, agent)
    result = await run_protected(case, agent)
    result.unprotected_success = case.success.met_by(unprotected_calls)
    result.proposed_unprotected = [c.to_dict() for c in unprotected_calls]
    if not result.unprotected_success:
        result.notes = (result.notes + " | baseline did not reproduce: the "
                        "reference agent did not perform the hijack").strip(" |")
    return result


async def run_benign(document: BenignDocument) -> BenignResult:
    """Measure the false-positive rate on ordinary content."""
    session_id = f"benign_{uuid.uuid4().hex[:8]}"
    async with session_scope() as db:
        started = time.perf_counter()
        outcome = await pipeline.ingest(
            db,
            session_id=session_id,
            content=document.content,
            source_ref=document.source,
            content_type=document.content_type,
            provenance=Provenance.RETRIEVED,
            subject="injectbench",
        )
        latency = (time.perf_counter() - started) * 1000

    tracker.drop(session_id)
    return BenignResult(
        document_id=document.id,
        title=document.title,
        spans=outcome.spans,
        quarantined=outcome.quarantined,
        removed=outcome.removed,
        verdict=outcome.verdict.value,
        latency_ms=latency,
        quarantined_reasons=outcome.families,
    )


# --------------------------------------------------------------------------
# full run
# --------------------------------------------------------------------------


@dataclass
class BenchReport:
    cases: list[CaseResult] = field(default_factory=list)
    benign: list[BenignResult] = field(default_factory=list)
    label: str = "local"

    # -- attack metrics --------------------------------------------------

    @property
    def total_cases(self) -> int:
        return len(self.cases)

    @property
    def unprotected_success(self) -> int:
        return sum(1 for c in self.cases if c.unprotected_success)

    @property
    def protected_success(self) -> int:
        return sum(1 for c in self.cases if c.protected_success)

    @property
    def asr_unprotected(self) -> float:
        return self.unprotected_success / self.total_cases if self.total_cases else 0.0

    @property
    def asr_protected(self) -> float:
        return self.protected_success / self.total_cases if self.total_cases else 0.0

    @property
    def asr_reduction(self) -> float:
        """Relative reduction in attack success rate, on reproduced cases only.

        Cases the unprotected baseline failed to reproduce are excluded rather
        than counted as wins: a defence cannot take credit for an attack that
        never worked.
        """
        if not self.unprotected_success:
            return 0.0
        return (self.unprotected_success - self.protected_success) / self.unprotected_success

    # -- false positives -------------------------------------------------

    @property
    def benign_spans(self) -> int:
        return sum(d.spans for d in self.benign)

    @property
    def benign_quarantined(self) -> int:
        return sum(d.quarantined for d in self.benign)

    @property
    def false_positive_rate(self) -> float:
        """Per-span. A document is many spans and one bad span is one error."""
        return self.benign_quarantined / self.benign_spans if self.benign_spans else 0.0

    @property
    def document_false_positive_rate(self) -> float:
        return (sum(1 for d in self.benign if d.false_positive) / len(self.benign)
                if self.benign else 0.0)

    # -- latency ---------------------------------------------------------

    @property
    def latencies(self) -> list[float]:
        return sorted([c.ingest_latency_ms for c in self.cases if c.ingest_latency_ms]
                      + [d.latency_ms for d in self.benign])

    def percentile(self, fraction: float) -> float:
        values = self.latencies
        if not values:
            return 0.0
        index = min(len(values) - 1, max(0, int(round(fraction * (len(values) - 1)))))
        return values[index]

    @property
    def by_family(self) -> dict[str, dict]:
        families: dict[str, dict] = {}
        for case in self.cases:
            entry = families.setdefault(
                case.family, {"cases": 0, "unprotected": 0, "protected": 0}
            )
            entry["cases"] += 1
            entry["unprotected"] += int(case.unprotected_success)
            entry["protected"] += int(case.protected_success)
        for entry in families.values():
            entry["prevented"] = entry["unprotected"] - entry["protected"]
        return families

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "total_cases": self.total_cases,
            "unprotected_success": self.unprotected_success,
            "protected_success": self.protected_success,
            "asr_unprotected": round(self.asr_unprotected, 4),
            "asr_protected": round(self.asr_protected, 4),
            "asr_reduction": round(self.asr_reduction, 4),
            "benign_documents": len(self.benign),
            "benign_spans": self.benign_spans,
            "benign_quarantined": self.benign_quarantined,
            "false_positive_rate": round(self.false_positive_rate, 4),
            "document_false_positive_rate": round(self.document_false_positive_rate, 4),
            "latency_ms": {
                "p50": round(self.percentile(0.50), 3),
                "p95": round(self.percentile(0.95), 3),
                "p99": round(self.percentile(0.99), 3),
                "mean": round(statistics.fmean(self.latencies), 3) if self.latencies else 0.0,
                "samples": len(self.latencies),
            },
            "by_family": self.by_family,
            "failures": [c.to_dict() for c in self.cases if c.protected_success],
            "not_reproduced": [c.case_id for c in self.cases if not c.unprotected_success],
            "cases": [c.to_dict() for c in self.cases],
            "benign": [d.to_dict() for d in self.benign],
        }


async def run_all(label: str = "local", *, cases: list[Case] | None = None,
                  benign: list[BenignDocument] | None = None) -> BenchReport:
    """Execute the whole benchmark."""
    await init_db()
    agent = ReferenceAgent()
    report = BenchReport(label=label)

    for case in (cases if cases is not None else load_cases()):
        report.cases.append(await run_case(case, agent))
    for document in (benign if benign is not None else load_benign()):
        report.benign.append(await run_benign(document))

    return report
