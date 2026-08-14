"""L4 — Capability firewall: the intent contract.

Any call outside the envelope is held. It is not silently dropped, which
teaches an operator nothing, but frozen with an explanation and escalated to a
human. An agent asked to summarise a page has no legitimate reason to hold the
ability to send mail, and after this layer it does not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..contracts import ContractClaims, ContractError
from ..models import Sensitivity, Verdict
from ..policy import Policy, ToolBinding
from .l3_taint import TaintResult


def _article(word: str) -> str:
    """Operator-facing reasons are read under pressure; they should read well."""
    return "an" if word[:1].lower() in "aeiou" else "a"


@dataclass
class CapabilityDecision:
    verdict: Verdict
    reason: str
    capability: str | None
    sensitivity: Sensitivity
    binding: ToolBinding | None = None
    tainted_args: list[str] = field(default_factory=list)
    requires_human: bool = False
    code: str = ""

    def to_audit(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "capability": self.capability,
            "sensitivity": self.sensitivity.value,
            "tainted_args": self.tainted_args,
            "requires_human": self.requires_human,
            "code": self.code,
        }


def evaluate(
    *,
    tool: str,
    policy: Policy,
    claims: ContractClaims | None,
    contract_error: ContractError | None = None,
    taint: dict[str, TaintResult] | None = None,
    breaker_open: bool = False,
) -> CapabilityDecision:
    """Decide the fate of one proposed tool call.

    The ordering is deliberate: the cheapest and most certain refusals come
    first, and every branch that cannot reach a confident PASS resolves to HOLD
    or BLOCK. There is no default-allow path in this function.
    """
    taint = taint or {}
    binding = policy.bind(tool)
    capability = binding.capability
    sensitivity = binding.sensitivity

    tainted_args = sorted(
        path for path, result in taint.items() if result.tainted
    )
    tainted_destinations = sorted(
        path for path in tainted_args if policy.is_destination_arg(path)
    )

    # 1. No valid contract at all. Nothing was authorised, so nothing passes.
    if claims is None:
        message = str(contract_error) if contract_error else "no intent contract"
        code = contract_error.code if contract_error else "missing_contract"
        return CapabilityDecision(
            verdict=Verdict.BLOCK,
            reason=f"refused: {message}",
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            code=code,
        )

    # 2. The tool is not in the catalogue. An unknown tool has unknown reach,
    #    which is exactly the case that must not be guessed at.
    if not binding.known:
        return CapabilityDecision(
            verdict=Verdict.HOLD,
            reason=f"tool '{tool}' maps to no known capability; escalating for a decision",
            capability=None,
            sensitivity=Sensitivity.IRREVERSIBLE,
            binding=binding,
            tainted_args=tainted_args,
            requires_human=True,
            code="unmapped_tool",
        )

    # 3. Outside the envelope the user signed.
    if not claims.grants(capability):
        return CapabilityDecision(
            verdict=Verdict.HOLD,
            reason=(f"capability '{capability}' is outside the intent contract "
                    f"(granted: {', '.join(claims.capabilities) or 'none'})"),
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            requires_human=True,
            code="out_of_envelope",
        )

    # 4. The circuit breaker has tripped. Privileged capabilities are
    #    quarantined until the operator clears it.
    if breaker_open and sensitivity is not Sensitivity.READ:
        return CapabilityDecision(
            verdict=Verdict.BLOCK,
            reason="circuit breaker is open: privileged capabilities are quarantined",
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            code="breaker_open",
        )

    # 5. A tainted value has reached a destination argument on a write. This is
    #    the confused-deputy signature and the case L3 exists to catch.
    if tainted_destinations and sensitivity is not Sensitivity.READ:
        first = tainted_destinations[0]
        return CapabilityDecision(
            verdict=Verdict.HOLD,
            reason=(f"argument '{first}' traces to untrusted content "
                    f"({taint[first].chain}); a tainted destination on "
                    f"{_article(sensitivity.value)} {sensitivity.value} call is held"),
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            requires_human=True,
            code="tainted_destination",
        )

    # 6. Irreversible acts always require an explicit human decision, even
    #    inside the envelope and even with clean provenance. Membrane never
    #    approves on the user's behalf.
    if sensitivity is Sensitivity.IRREVERSIBLE and policy.always_human(capability):
        return CapabilityDecision(
            verdict=Verdict.HOLD,
            reason=f"'{capability}' is irreversible and always requires a human decision",
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            requires_human=True,
            code="irreversible",
        )

    # 7. Tainted content in a non-destination argument of a write. Lower risk
    #    than a tainted destination but still not something to wave through.
    if tainted_args and sensitivity is not Sensitivity.READ:
        first = tainted_args[0]
        return CapabilityDecision(
            verdict=Verdict.HOLD,
            reason=(f"argument '{first}' traces to untrusted content "
                    f"({taint[first].chain}) on {_article(sensitivity.value)} "
                    f"{sensitivity.value} call"),
            capability=capability,
            sensitivity=sensitivity,
            binding=binding,
            tainted_args=tainted_args,
            requires_human=True,
            code="tainted_argument",
        )

    return CapabilityDecision(
        verdict=Verdict.PASS,
        reason=(f"'{capability}' is inside the contract "
                f"and no argument traces to untrusted content"),
        capability=capability,
        sensitivity=sensitivity,
        binding=binding,
        tainted_args=tainted_args,
        code="authorised",
    )
