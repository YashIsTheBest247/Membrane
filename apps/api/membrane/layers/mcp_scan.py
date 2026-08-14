"""MCP tool-poisoning scanner.

Tool descriptions enter the model's context as trusted text. Nobody is
scanning them. A poisoned third-party MCP server therefore compromises every
agent that installs it, which makes this a supply-chain control rather than a
per-deployment one.

The scanner audits a server's advertised tool schemas *before* the agent is
permitted to connect, and treats description fields the same way L1 and L2
treat a web page: as untrusted text that is allowed to describe but not to
instruct.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..hashing import span_hash
from ..models import Verdict
from ..signals import score_text
from .l1_sanitiser import decode_unicode_tags, sanitise

# Fields in a JSON Schema / MCP tool definition that hold prose the model reads.
_TEXT_FIELDS = ("description", "title", "summary", "instructions", "usage",
                "notes", "examples", "prompt", "default", "const")


@dataclass
class FieldFinding:
    path: str
    score: float
    family: str
    reason: str
    span_hash: str
    hidden_payload: bool = False

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "score": round(self.score, 3),
            "family": self.family,
            "reason": self.reason,
            "span": self.span_hash,
            "hidden_payload": self.hidden_payload,
        }


@dataclass
class ToolReport:
    tool: str
    verdict: Verdict
    max_score: float
    findings: list[FieldFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "verdict": self.verdict.value,
            "max_score": round(self.max_score, 3),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class ScanReport:
    server: str
    verdict: Verdict
    tools: list[ToolReport] = field(default_factory=list)
    reason: str = ""

    @property
    def poisoned_tools(self) -> list[str]:
        return [t.tool for t in self.tools if t.verdict is not Verdict.PASS]

    def to_dict(self) -> dict:
        return {
            "server": self.server,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "tools_scanned": len(self.tools),
            "poisoned_tools": self.poisoned_tools,
            "tools": [t.to_dict() for t in self.tools],
        }


def _walk_text(node, path: str = ""):
    """Yield (path, text) for every prose-bearing field in a schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in _TEXT_FIELDS and isinstance(value, str):
                yield child_path, value
            else:
                yield from _walk_text(value, child_path)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk_text(value, f"{path}[{index}]")
    elif isinstance(node, str) and path.split(".")[-1] in _TEXT_FIELDS:
        yield path, node


def scan_tool(tool: dict, *, block_threshold: float = 0.35,
              hold_threshold: float = 0.2) -> ToolReport:
    """Audit one tool definition.

    The thresholds are lower than L2's because the prior is different. A web
    page containing an imperative is unremarkable; a *tool schema* containing
    one is not. A description field exists to describe, and the base rate of
    legitimate instruction-like prose in one is close to zero, so evidence that
    would only be suggestive on a retrieved page is close to conclusive here.
    """
    name = str(tool.get("name") or tool.get("tool") or "<unnamed>")
    findings: list[FieldFinding] = []

    for path, text in _walk_text(tool):
        if not text.strip():
            continue

        # A description containing hidden characters is already disqualifying:
        # there is no honest reason for zero-width text in a tool schema.
        hidden = bool(decode_unicode_tags(text).strip())
        sanitised = sanitise(text, content_type="text/plain")
        recovered = "\n".join([sanitised.text] + [r.text for r in sanitised.removals])

        signals = score_text(recovered)
        # Invisible characters are counted in `stats`, not `removals`, so both
        # have to be consulted — a description that is *only* zero-width padding
        # would otherwise scan clean.
        invisible_chars = sum(
            count for kind, count in sanitised.stats.items()
            if kind in ("format_char", "unicode_tag", "control_char",
                        "variation_selector")
        )
        has_hidden = hidden or bool(sanitised.removals) or invisible_chars > 0

        if signals.score >= hold_threshold or has_hidden:
            reasons = []
            if signals.score >= hold_threshold:
                reasons.append(f"instruction-like text (score {signals.score:.2f})")
            if has_hidden:
                kinds = sorted({r.kind for r in sanitised.removals})
                if invisible_chars:
                    kinds.append(f"{invisible_chars} invisible character(s)")
                reasons.append("hidden content: " + (", ".join(kinds) or "unicode tags"))
            findings.append(FieldFinding(
                path=path,
                score=max(signals.score, 0.7 if has_hidden else 0.0),
                family=signals.family if signals.score >= hold_threshold else "invisible_payload",
                reason="; ".join(reasons),
                span_hash=span_hash(text),
                hidden_payload=has_hidden,
            ))

    max_score = max((f.score for f in findings), default=0.0)
    if max_score >= block_threshold:
        verdict = Verdict.BLOCK
    elif max_score >= hold_threshold:
        verdict = Verdict.HOLD
    else:
        verdict = Verdict.PASS

    return ToolReport(tool=name, verdict=verdict, max_score=max_score, findings=findings)


def scan_server(server: str, tools: list[dict]) -> ScanReport:
    """Audit every tool a server advertises and decide whether to connect."""
    reports = [scan_tool(tool) for tool in tools]

    if any(r.verdict is Verdict.BLOCK for r in reports):
        blocked = [r.tool for r in reports if r.verdict is Verdict.BLOCK]
        return ScanReport(
            server, Verdict.BLOCK, reports,
            reason=(f"{len(blocked)} tool description(s) contain embedded "
                    f"instructions: {', '.join(blocked)}"),
        )
    if any(r.verdict is Verdict.HOLD for r in reports):
        held = [r.tool for r in reports if r.verdict is Verdict.HOLD]
        return ScanReport(
            server, Verdict.HOLD, reports,
            reason=f"suspicious description text in: {', '.join(held)}",
        )
    return ScanReport(
        server, Verdict.PASS, reports,
        reason=f"{len(reports)} tool description(s) contain description only",
    )
