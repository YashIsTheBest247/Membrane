"""The request lifecycle.

Every span of untrusted text traverses six stages and resolves to exactly one
of four outcomes:

    1 ingest → 2 normalise → 3 classify → 4 taint → 5 envelope → 6 decide

    PASS   clean content reaches the agent unchanged
    STRIP  payload excised, benign remainder forwarded
    HOLD   frozen, pushed to a human for a decision
    BLOCK  refused, logged, fully replayable in forensics

The design constraint is that the common case — clean content from a reputable
source — completes on the deterministic path without invoking a model at all.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from . import approvals, audit, telegram, trust
from .circuit import breaker
from .classifier import escalation_available, make_escalator
from .config import get_settings
from .contracts import ContractClaims, ContractError
from .contracts import verify as verify_contract
from .events import bus, preview
from .hashing import span_hash
from .layers import egress as egress_layer
from .layers import l1_sanitiser, l2_separator, l4_capability
from .layers.l3_taint import TaintResult, tracker
from .models import ApprovalStatus, Layer, Provenance, Sensitivity, Verdict
from .policy import get_policy

logger = logging.getLogger("membrane.pipeline")

_SEVERITY = {Verdict.PASS: 0, Verdict.STRIP: 1, Verdict.HOLD: 2, Verdict.BLOCK: 3}


def worst(*verdicts: Verdict) -> Verdict:
    return max(verdicts, key=lambda v: _SEVERITY[v])


# --------------------------------------------------------------------------
# results
# --------------------------------------------------------------------------


@dataclass
class IngestOutcome:
    session_id: str
    verdict: Verdict
    content: str
    source_ref: str
    provenance: Provenance
    spans: int = 0
    quarantined: int = 0
    removed: int = 0
    escalations: int = 0
    trust_score: float = 0.0
    families: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    layer_latency_ms: dict[str, float] = field(default_factory=dict)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "verdict": self.verdict.value,
            "content": self.content,
            "source": self.source_ref,
            "provenance": self.provenance.value,
            "spans": self.spans,
            "quarantined": self.quarantined,
            "removed": self.removed,
            "escalations": self.escalations,
            "trust_score": round(self.trust_score, 4),
            "attack_families": self.families,
            "latency_ms": round(self.latency_ms, 2),
            "layer_latency_ms": {k: round(v, 3) for k, v in self.layer_latency_ms.items()},
            "detail": self.detail,
        }


@dataclass
class ToolCallOutcome:
    session_id: str
    verdict: Verdict
    allowed: bool
    tool: str
    capability: str | None
    sensitivity: Sensitivity
    reason: str
    code: str = ""
    action_id: str | None = None
    approval_status: ApprovalStatus | None = None
    taint: dict[str, TaintResult] = field(default_factory=dict)
    egress: dict = field(default_factory=dict)
    card: dict = field(default_factory=dict)
    breaker_open: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "verdict": self.verdict.value,
            "allowed": self.allowed,
            "tool": self.tool,
            "capability": self.capability,
            "sensitivity": self.sensitivity.value,
            "reason": self.reason,
            "code": self.code,
            "action_id": self.action_id,
            "approval_status": self.approval_status.value if self.approval_status else None,
            "provenance": {path: result.to_dict() for path, result in self.taint.items()},
            "egress": self.egress,
            "card": self.card,
            "breaker_open": self.breaker_open,
            "latency_ms": round(self.latency_ms, 2),
        }


# --------------------------------------------------------------------------
# ingest path: L1 → L2 → L3
# --------------------------------------------------------------------------


async def ingest(
    db: AsyncSession,
    *,
    session_id: str,
    content: str | bytes,
    source_ref: str = "",
    content_type: str = "text/plain",
    provenance: Provenance = Provenance.RETRIEVED,
    subject: str = "anonymous",
    http_client: httpx.AsyncClient | None = None,
) -> IngestOutcome:
    """Run one piece of untrusted content through the ingest layers."""
    settings = get_settings()
    started = time.perf_counter()
    layer_latency: dict[str, float] = {}

    await audit.touch_session(db, session_id, subject=subject)

    # Trust modulates L2's thresholds: a source that has injected before is
    # held to a stricter standard.
    trust_score = 1.0 if provenance.is_trusted else await trust.get_score(db, source_ref)

    # ---- stage 2: normalise (L1) ----------------------------------------
    t0 = time.perf_counter()
    sanitised = l1_sanitiser.sanitise(
        content, content_type=content_type, max_decode_depth=settings.max_decode_depth
    )
    layer_latency["l1"] = (time.perf_counter() - t0) * 1000

    hostile_removals = sanitised.hostile_removals

    await audit.record(
        db,
        session_id=session_id,
        layer=Layer.L1_SANITISER,
        verdict=Verdict.STRIP if sanitised.removed_anything else Verdict.PASS,
        reason=(f"{len(sanitised.removals)} hidden region(s) removed, "
                f"{len(hostile_removals)} of them instruction-bearing")
        if sanitised.removed_anything else "no hidden content",
        source_ref=source_ref,
        provenance=provenance,
        signals=sanitised.audit_summary(),
        latency_ms=layer_latency["l1"],
    )

    for removal in hostile_removals:
        digest = span_hash(removal.text)
        await audit.record(
            db,
            session_id=session_id,
            layer=Layer.L1_SANITISER,
            verdict=Verdict.BLOCK,
            reason=f"instruction-bearing payload hidden in {removal.kind}",
            span_hash_value=digest,
            source_ref=source_ref,
            provenance=provenance,
            signals={"kind": removal.kind, "detail": removal.detail,
                     "depth": removal.depth},
        )
        await audit.promote_to_corpus(
            db, span_hash_value=digest,
            family="invisible_payload" if removal.depth == 0 else "encoding_evasion",
            signals={"kind": removal.kind},
        )
        await bus.publish("attack.blocked", {
            "session_id": session_id,
            "layer": Layer.L1_SANITISER.value,
            "verdict": Verdict.BLOCK.value,
            "source": source_ref,
            "reason": f"hidden payload in {removal.kind}",
            "span": digest,
            "family": "invisible_payload",
            "preview": preview(removal.text),
        })

    # ---- stage 3: classify (L2) -----------------------------------------
    t0 = time.perf_counter()
    escalate = None if provenance.is_trusted else make_escalator(http_client)
    separated = await l2_separator.separate(
        sanitised.text,
        quarantine_threshold=settings.separator_quarantine_threshold,
        ambiguous_threshold=settings.separator_ambiguous_threshold,
        source_trust=trust_score,
        escalate=escalate,
    )
    layer_latency["l2"] = (time.perf_counter() - t0) * 1000

    await audit.record(
        db,
        session_id=session_id,
        layer=Layer.L2_SEPARATOR,
        verdict=Verdict.STRIP if separated.quarantined else Verdict.PASS,
        reason=(f"{len(separated.quarantined)} of {len(separated.spans)} span(s) "
                f"quarantined as imperative"),
        source_ref=source_ref,
        provenance=provenance,
        signals=separated.audit_summary(),
        latency_ms=layer_latency["l2"],
    )

    for decision in separated.quarantined:
        digest = span_hash(decision.text)
        await audit.record(
            db,
            session_id=session_id,
            layer=Layer.L2_SEPARATOR,
            verdict=Verdict.STRIP,
            reason=decision.reason,
            span_hash_value=digest,
            source_ref=source_ref,
            provenance=provenance,
            signals=decision.to_audit(),
        )
        await audit.promote_to_corpus(
            db, span_hash_value=digest, family=decision.signals.family,
            signals=decision.signals.as_dict(),
        )
        await audit.retain_for_replay(
            db, session_id=session_id, span_hash_value=digest, text=decision.text
        )
        await bus.publish("attack.blocked", {
            "session_id": session_id,
            "layer": Layer.L2_SEPARATOR.value,
            "verdict": Verdict.STRIP.value,
            "source": source_ref,
            "reason": decision.reason,
            "span": digest,
            "family": decision.signals.family,
            "score": round(decision.signals.score, 3),
            "tier": decision.tier.value,
            "preview": preview(decision.text),
        })

    # ---- stage 4: taint (L3) --------------------------------------------
    t0 = time.perf_counter()
    graph = tracker.graph(session_id)
    for decision in separated.spans:
        span = graph.register(decision.text, provenance, source_ref)
        await audit.record_edges(
            db, session_id=session_id, span_hash_value=span.hash,
            provenance=provenance, source_ref=source_ref,
        )

    # Payloads L1 excised are registered too. The agent never saw them, but the
    # addresses and endpoints inside them stay traceable, so a value that
    # reaches a tool call by some other route is still recognised as having
    # originated in this source rather than in the user's request.
    for removal in sanitised.removals:
        if not removal.text.strip():
            continue
        span = graph.register(removal.text, provenance, source_ref)
        await audit.record_edges(
            db, session_id=session_id, span_hash_value=span.hash,
            provenance=provenance, source_ref=source_ref,
        )
    layer_latency["l3"] = (time.perf_counter() - t0) * 1000

    # ---- trust feedback --------------------------------------------------
    incident = bool(separated.quarantined) or bool(hostile_removals)
    if not provenance.is_trusted:
        if incident:
            trust_score = await trust.record_incident(
                db, source_ref,
                weight=min(2.0, 0.5 + 0.5 * len(separated.quarantined)),
            )
        else:
            trust_score = await trust.record_clean(db, source_ref,
                                                   spans=len(separated.spans))

    verdict = Verdict.PASS
    if sanitised.removed_anything or separated.quarantined:
        verdict = Verdict.STRIP

    await audit.touch_session(db, session_id, spans=len(separated.spans))

    latency = (time.perf_counter() - started) * 1000
    families = sorted({d.signals.family for d in separated.quarantined}
                      | {"invisible_payload" for _ in hostile_removals[:1]})

    outcome = IngestOutcome(
        session_id=session_id,
        verdict=verdict,
        content=separated.clean_text,
        source_ref=source_ref,
        provenance=provenance,
        spans=len(separated.spans),
        quarantined=len(separated.quarantined),
        removed=len(sanitised.removals),
        escalations=separated.escalations,
        trust_score=trust_score,
        families=families,
        latency_ms=latency,
        layer_latency_ms=layer_latency,
        detail={
            "l1": sanitised.audit_summary(),
            "l2": separated.audit_summary(),
            "l3": graph.summary(),
            "escalation_available": escalation_available(),
        },
    )

    await bus.publish("ingest", {
        "session_id": session_id,
        "verdict": verdict.value,
        "source": source_ref,
        "provenance": provenance.value,
        "spans": outcome.spans,
        "quarantined": outcome.quarantined,
        "removed": outcome.removed,
        "trust_score": round(trust_score, 3),
        "latency_ms": round(latency, 2),
        "families": families,
    })

    return outcome


# --------------------------------------------------------------------------
# egress path: L4 → egress inspection → human loop
# --------------------------------------------------------------------------


async def check_tool_call(
    db: AsyncSession,
    *,
    session_id: str,
    tool: str,
    args: dict,
    contract_token: str | None,
    subject: str = "anonymous",
    wait_for_approval: bool = False,
    http_client: httpx.AsyncClient | None = None,
) -> ToolCallOutcome:
    """Decide the fate of one proposed tool call."""
    started = time.perf_counter()
    policy = get_policy()
    await audit.touch_session(db, session_id, subject=subject)

    # ---- contract --------------------------------------------------------
    claims: ContractClaims | None = None
    contract_error: ContractError | None = None
    try:
        claims = verify_contract(contract_token, session_id=session_id)
    except ContractError as exc:
        contract_error = exc

    # ---- stage 4: taint --------------------------------------------------
    graph = tracker.graph(session_id)
    taint = graph.resolve_args(args)

    # Record the argument values as agent-originated spans whose parents are
    # the spans they were traced to. This is what makes lineage survive
    # multiple hops of reasoning.
    for path, result in taint.items():
        parents = [m.span_hash for m in result.matches[:4]]
        await audit.record_edges(
            db, session_id=session_id, span_hash_value=result.value_hash,
            provenance=Provenance.AGENT, source_ref=f"toolarg:{tool}.{path}",
            parents=parents,
        )

    breaker_open = breaker.is_open(session_id)

    # ---- stage 5: envelope (L4) -----------------------------------------
    decision = l4_capability.evaluate(
        tool=tool,
        policy=policy,
        claims=claims,
        contract_error=contract_error,
        taint=taint,
        breaker_open=breaker_open,
    )

    # ---- egress inspection ----------------------------------------------
    egress_result = egress_layer.inspect(args, sensitivity=decision.sensitivity)
    verdict = worst(decision.verdict, egress_result.verdict)
    reason = decision.reason
    code = decision.code
    if egress_result.verdict is not Verdict.PASS and _SEVERITY[egress_result.verdict] >= _SEVERITY[decision.verdict]:
        reason = f"{egress_result.reason} ({decision.reason})"
        code = f"egress_{egress_result.verdict.value}"

    await audit.record(
        db,
        session_id=session_id,
        layer=Layer.L4_CAPABILITY,
        verdict=decision.verdict,
        reason=decision.reason,
        source_ref=f"tool:{tool}",
        signals=decision.to_audit(),
    )
    if not egress_result.clean:
        await audit.record(
            db,
            session_id=session_id,
            layer=Layer.EGRESS,
            verdict=egress_result.verdict,
            reason=egress_result.reason,
            source_ref=f"tool:{tool}",
            signals=egress_result.to_audit(),
        )

    # A tainted argument reaching a privileged call is an incident for the
    # source that supplied it, not only for this session.
    for result in taint.values():
        if result.tainted and result.matches and verdict in (Verdict.HOLD, Verdict.BLOCK):
            await trust.record_incident(db, result.matches[0].source_ref, weight=1.5)
            break

    card: dict = {}
    action_id: str | None = None
    approval_status: ApprovalStatus | None = None
    allowed = verdict is Verdict.PASS

    # ---- stage 6: decide -------------------------------------------------
    if verdict is Verdict.HOLD:
        card = approvals.build_card(
            tool=tool,
            capability=decision.capability,
            sensitivity=decision.sensitivity,
            args=args,
            reason=reason,
            taint=taint,
            claims=claims,
            egress_findings=[f.to_dict() for f in egress_result.findings],
        )
        action = await approvals.create(
            db,
            session_id=session_id,
            tool=tool,
            capability=decision.capability,
            sensitivity=decision.sensitivity,
            reason=reason,
            card=card,
            args_digest=span_hash(repr(sorted(args.items())) if isinstance(args, dict)
                                  else str(args)),
        )
        action_id = action.id

        if telegram.configured():
            message_id = await telegram.push_card(action, http_client)
            action.telegram_message_id = message_id

        await audit.record(
            db, session_id=session_id, layer=Layer.APPROVAL, verdict=Verdict.HOLD,
            reason=f"decision card {action.id} pushed to the human loop",
            source_ref=f"tool:{tool}", signals={"action_id": action.id},
        )
        await audit.touch_session(db, session_id, holds=1)

        if breaker.record_hold(session_id):
            await audit.record(
                db, session_id=session_id, layer=Layer.BREAKER, verdict=Verdict.BLOCK,
                reason="circuit breaker tripped: privileged capabilities quarantined",
                signals=breaker.state(session_id).to_dict(),
            )
            await bus.publish("breaker.tripped", {
                "session_id": session_id,
                "cooldown_seconds": get_settings().breaker_cooldown_seconds,
            })
            breaker_open = True

        await bus.publish("action.held", {
            "session_id": session_id,
            "action_id": action.id,
            "tool": tool,
            "capability": decision.capability,
            "sensitivity": decision.sensitivity.value,
            "verdict": Verdict.HOLD.value,
            "reason": reason,
            "code": code,
            "card": card,
        })

        if wait_for_approval:
            # Commit so the waiting card is visible to the webhook handler and
            # the dashboard while we block.
            await db.commit()
            approval_status = await approvals.wait_for_decision(action.id)
            allowed = approval_status is ApprovalStatus.APPROVED
            verdict = Verdict.PASS if allowed else Verdict.BLOCK
            reason = (f"human {approval_status.value} the held action"
                      if approval_status is not ApprovalStatus.EXPIRED
                      else "no human decision within the timeout; treated as denial")

            refreshed = await approvals.get(db, action.id)
            if refreshed is not None and telegram.configured():
                await telegram.close_card(refreshed, approval_status.value, http_client)

            await audit.record(
                db, session_id=session_id, layer=Layer.APPROVAL,
                verdict=Verdict.PASS if allowed else Verdict.BLOCK,
                reason=reason, source_ref=f"tool:{tool}",
                signals={"action_id": action.id,
                         "status": approval_status.value,
                         "resolved_by": refreshed.resolved_by if refreshed else None},
            )

    elif verdict is Verdict.BLOCK:
        await audit.touch_session(db, session_id, blocks=1)
        if breaker.record_hold(session_id):
            await bus.publish("breaker.tripped", {
                "session_id": session_id,
                "cooldown_seconds": get_settings().breaker_cooldown_seconds,
            })
            breaker_open = True
        await bus.publish("action.blocked", {
            "session_id": session_id,
            "tool": tool,
            "capability": decision.capability,
            "verdict": Verdict.BLOCK.value,
            "reason": reason,
            "code": code,
        })
    else:
        await bus.publish("action.passed", {
            "session_id": session_id,
            "tool": tool,
            "capability": decision.capability,
            "verdict": Verdict.PASS.value,
            "reason": reason,
        })

    latency = (time.perf_counter() - started) * 1000

    return ToolCallOutcome(
        session_id=session_id,
        verdict=verdict,
        allowed=allowed,
        tool=tool,
        capability=decision.capability,
        sensitivity=decision.sensitivity,
        reason=reason,
        code=code,
        action_id=action_id,
        approval_status=approval_status,
        taint=taint,
        egress=egress_result.to_audit(),
        card=card,
        breaker_open=breaker_open,
        latency_ms=latency,
    )


# --------------------------------------------------------------------------
# trusted-channel registration
# --------------------------------------------------------------------------


async def register_user_intent(
    db: AsyncSession,
    *,
    session_id: str,
    text: str,
    subject: str = "anonymous",
) -> str:
    """Register what the user actually typed as trusted provenance.

    This is the anchor the whole taint analysis hangs from: an argument that
    traces back here is user intent, and an argument that does not is, at best,
    the model's idea.
    """
    graph = tracker.graph(session_id)
    span = graph.register(text, Provenance.USER, f"user:{subject}")
    await audit.record_edges(
        db, session_id=session_id, span_hash_value=span.hash,
        provenance=Provenance.USER, source_ref=f"user:{subject}",
    )
    await audit.record(
        db, session_id=session_id, layer=Layer.L3_TAINT, verdict=Verdict.PASS,
        reason="user intent registered as trusted provenance",
        span_hash_value=span.hash, source_ref=f"user:{subject}",
        provenance=Provenance.USER,
    )
    await audit.touch_session(db, session_id, subject=subject,
                              task_digest=span.hash, spans=1)
    return span.hash
