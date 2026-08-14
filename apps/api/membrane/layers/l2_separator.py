"""L2 — Separator: split content from imperatives.

Surviving text is parsed into two channels. Declarative content (facts, prose,
data) is forwarded to the agent. Imperative spans that attempt to direct
behaviour are quarantined and replaced with a neutral marker that preserves
document structure without preserving authority.

Two tiers. The deterministic ruleset resolves the spans that are unambiguous,
which is the overwhelming majority of them, at effectively zero cost and
without the text ever leaving the process. Only the genuinely ambiguous band
escalates to a model. That is what makes both the latency budget and the
privacy guarantee achievable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ..signals import SpanSignals, score_text

QUARANTINE_MARKER = "[membrane: instruction removed]"


class Channel(str, Enum):
    DECLARATIVE = "declarative"   # forwarded to the agent
    IMPERATIVE = "imperative"     # quarantined


class Tier(str, Enum):
    DETERMINISTIC = "deterministic"
    ESCALATED = "escalated"
    ESCALATION_UNAVAILABLE = "escalation_unavailable"


@dataclass
class SpanDecision:
    index: int
    text: str
    channel: Channel
    tier: Tier
    signals: SpanSignals
    model_verdict: str | None = None
    reason: str = ""

    @property
    def quarantined(self) -> bool:
        return self.channel is Channel.IMPERATIVE

    def to_audit(self) -> dict:
        return {
            "index": self.index,
            "channel": self.channel.value,
            "tier": self.tier.value,
            "reason": self.reason,
            "chars": len(self.text),
            **self.signals.as_dict(),
        }


@dataclass
class SeparationResult:
    clean_text: str
    spans: list[SpanDecision] = field(default_factory=list)
    escalations: int = 0

    @property
    def quarantined(self) -> list[SpanDecision]:
        return [s for s in self.spans if s.quarantined]

    @property
    def max_score(self) -> float:
        return max((s.signals.score for s in self.spans), default=0.0)

    def audit_summary(self) -> dict:
        return {
            "spans": len(self.spans),
            "quarantined": len(self.quarantined),
            "escalations": self.escalations,
            "max_score": round(self.max_score, 4),
            "families": sorted({s.signals.family for s in self.quarantined}),
        }


# --------------------------------------------------------------------------
# span segmentation
# --------------------------------------------------------------------------

# Sentence boundaries, with the usual abbreviations held back so "Dr. Smith"
# is not torn in half.
_ABBREVIATIONS = r"(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bSt)(?<!\bvs)(?<!\bNo)(?<!\be\.g)(?<!\bi\.e)"
_SENTENCE_BOUNDARY = re.compile(rf"{_ABBREVIATIONS}(?<=[.!?])\s+(?=[A-Z\"'(\[])")


def segment(text: str, *, max_span_chars: int = 1200) -> list[str]:
    """Split text into spans that are small enough to judge individually.

    Blocks first (an injected payload is usually its own paragraph or list
    item), then sentences inside long blocks.
    """
    spans: list[str] = []
    for block in re.split(r"\n\s*\n|\n(?=\s*[-*•>#\d]+[.)\s])", text):
        block = block.strip()
        if not block:
            continue
        if len(block) <= max_span_chars:
            spans.append(block)
            continue
        buffer = ""
        for sentence in _SENTENCE_BOUNDARY.split(block):
            if len(buffer) + len(sentence) + 1 > max_span_chars and buffer:
                spans.append(buffer.strip())
                buffer = sentence
            else:
                buffer = f"{buffer} {sentence}".strip()
        if buffer.strip():
            spans.append(buffer.strip())
    return spans


# --------------------------------------------------------------------------
# separation
# --------------------------------------------------------------------------


def _thresholds(trust: float, quarantine: float, ambiguous: float,
                baseline: float = 0.70) -> tuple[float, float]:
    """Lower the bar for sources with a history of injecting.

    The penalty is measured against the *initial* trust score, not against a
    perfect one. A source nobody has seen before is judged at the nominal
    thresholds; only a source that has actually injected — and so has had its
    score collapsed — is held to a stricter standard, for as long as it takes
    the score to recover.
    """
    deficit = max(0.0, baseline - max(0.0, min(1.0, trust)))
    penalty = (deficit / baseline) * 0.25 if baseline else 0.0
    return max(0.20, quarantine - penalty), max(0.12, ambiguous - penalty)


async def separate(
    text: str,
    *,
    quarantine_threshold: float = 0.60,
    ambiguous_threshold: float = 0.35,
    source_trust: float = 0.70,
    escalate=None,
) -> SeparationResult:
    """Run the dual-channel split.

    `escalate` is an optional async callable taking a span of text and
    returning "imperative" | "declarative" | None. When it is absent or fails,
    ambiguous spans quarantine: unavailability never becomes permissiveness.
    """
    quarantine_at, ambiguous_at = _thresholds(
        source_trust, quarantine_threshold, ambiguous_threshold
    )

    decisions: list[SpanDecision] = []
    escalations = 0
    output: list[str] = []

    for index, span_text in enumerate(segment(text)):
        signals = score_text(span_text)

        if signals.score >= quarantine_at:
            decision = SpanDecision(
                index=index, text=span_text, channel=Channel.IMPERATIVE,
                tier=Tier.DETERMINISTIC, signals=signals,
                reason=f"deterministic score {signals.score:.2f} >= {quarantine_at:.2f}",
            )
        elif signals.score >= ambiguous_at:
            escalations += 1
            verdict = None
            if escalate is not None:
                try:
                    verdict = await escalate(span_text)
                except Exception:
                    verdict = None
            if verdict == "declarative":
                decision = SpanDecision(
                    index=index, text=span_text, channel=Channel.DECLARATIVE,
                    tier=Tier.ESCALATED, signals=signals, model_verdict=verdict,
                    reason="model tier judged the span declarative",
                )
            elif verdict == "imperative":
                decision = SpanDecision(
                    index=index, text=span_text, channel=Channel.IMPERATIVE,
                    tier=Tier.ESCALATED, signals=signals, model_verdict=verdict,
                    reason="model tier judged the span imperative",
                )
            else:
                decision = SpanDecision(
                    index=index, text=span_text, channel=Channel.IMPERATIVE,
                    tier=Tier.ESCALATION_UNAVAILABLE, signals=signals,
                    reason="ambiguous and no model verdict available; failing closed",
                )
        else:
            decision = SpanDecision(
                index=index, text=span_text, channel=Channel.DECLARATIVE,
                tier=Tier.DETERMINISTIC, signals=signals,
                reason=f"deterministic score {signals.score:.2f} < {ambiguous_at:.2f}",
            )

        decisions.append(decision)
        output.append(QUARANTINE_MARKER if decision.quarantined else span_text)

    return SeparationResult(
        clean_text="\n\n".join(output).strip(),
        spans=decisions,
        escalations=escalations,
    )
