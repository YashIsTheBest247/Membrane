"""Egress inspection — defence in depth on the way out.

Even a fully successful injection cannot complete its objective if the
exfiltration path is closed. Outbound tool arguments are re-scanned for
credentials, personal data and beacon shapes before anything crosses the trust
boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Sensitivity, Verdict
from ..signals import EXFIL_PATTERNS, encoded_host_labels, find_pii, find_secrets

# Hosts whose entire purpose is to receive data from somewhere else. This list
# is a convenience, not a defence: it is a blocklist, it does not terminate, and
# nothing in the design depends on it. The structural checks below — encoded
# hostname labels, beacon-shaped URLs, credentials in arguments — are what
# actually carry the layer, and they hold for hosts nobody has ever seen.
COLLECTOR_HOSTS = re.compile(
    r"\b(?:[a-z0-9-]+\.)*(?:"
    r"webhook\.site|requestbin\.[a-z]+|pipedream\.net|ngrok\.(?:io|app|free\.dev)|"
    r"oastify\.com|burpcollaborator\.net|interact\.sh|beeceptor\.com|"
    r"postb\.in|hookbin\.com|mockbin\.org|pastebin\.com|paste\.ee|"
    r"transfer\.sh|file\.io|0x0\.st|termbin\.com"
    r")\b",
    re.I,
)

# A URL that carries a large opaque payload in its query or path.
_LOADED_URL = re.compile(
    r"[a-z][a-z0-9+.-]{1,15}://[^\s]{0,120}?[?&/][^\s]{120,}", re.I
)

_REDACTION = "[membrane:redacted]"


@dataclass
class EgressFinding:
    kind: str            # secret | pii | beacon | collector | loaded_url
    detail: str          # what specifically was recognised
    arg_path: str
    sample_hash: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail, "arg": self.arg_path}


@dataclass
class EgressResult:
    verdict: Verdict
    findings: list[EgressFinding] = field(default_factory=list)
    redactions: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    @property
    def clean(self) -> bool:
        return not self.findings

    def to_audit(self) -> dict:
        by_kind: dict[str, int] = {}
        for finding in self.findings:
            by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1
        return {
            "verdict": self.verdict.value,
            "findings": [f.to_dict() for f in self.findings[:12]],
            "counts": by_kind,
            "reason": self.reason,
        }


def _walk(args, path: str = ""):
    if isinstance(args, dict):
        for key, value in args.items():
            yield from _walk(value, f"{path}.{key}" if path else str(key))
    elif isinstance(args, (list, tuple)):
        for index, value in enumerate(args):
            yield from _walk(value, f"{path}[{index}]")
    elif isinstance(args, str):
        yield path, args


def inspect(
    args: dict,
    *,
    sensitivity: Sensitivity = Sensitivity.WRITE,
    pii_threshold: int = 3,
) -> EgressResult:
    """Scan an outbound argument object.

    Credentials are an unconditional refusal: there is no legitimate reason for
    a key to travel inside a tool argument, and a hijacked agent that has been
    told to "include the API key" produces exactly this shape. Personal data is
    a judgement call, so a bulk quantity of it holds for a human rather than
    being blocked outright.
    """
    from ..hashing import span_hash

    findings: list[EgressFinding] = []
    redactions: dict[str, str] = {}
    pii_total = 0

    for path, value in _walk(args):
        if not value.strip():
            continue

        for kind, matched in find_secrets(value):
            findings.append(EgressFinding("secret", kind, path, span_hash(matched)))
            redactions[path] = value.replace(matched, _REDACTION)

        pii_hits = find_pii(value)
        pii_total += len(pii_hits)
        for kind, matched in pii_hits[:5]:
            findings.append(EgressFinding("pii", kind, path, span_hash(matched)))

        for pattern in EXFIL_PATTERNS:
            if pattern.search(value):
                findings.append(EgressFinding("beacon", pattern.pattern[:36], path))
                break

        # Data encoded into a hostname leaves through DNS resolution, so a read
        # capability is enough to complete the exfiltration.
        for label in encoded_host_labels(value):
            findings.append(EgressFinding(
                "beacon", f"encoded hostname label '{label[:24]}'", path))
            break

        collector = COLLECTOR_HOSTS.search(value)
        if collector:
            findings.append(EgressFinding("collector", collector.group(0), path))

        if _LOADED_URL.search(value):
            findings.append(EgressFinding("loaded_url", "opaque payload in URL", path))

    kinds = {f.kind for f in findings}

    if "secret" in kinds:
        return EgressResult(
            Verdict.BLOCK, findings, redactions,
            reason="credential material found in an outbound argument",
        )
    if "collector" in kinds:
        return EgressResult(
            Verdict.BLOCK, findings, redactions,
            reason="outbound destination is a known collector endpoint",
        )
    if "beacon" in kinds or "loaded_url" in kinds:
        return EgressResult(
            Verdict.HOLD, findings, redactions,
            reason="outbound argument has the shape of an exfiltration beacon",
        )
    if pii_total >= pii_threshold and sensitivity is not Sensitivity.READ:
        return EgressResult(
            Verdict.HOLD, findings, redactions,
            reason=f"{pii_total} personal-data values in one outbound call",
        )

    return EgressResult(Verdict.PASS, findings, redactions, reason="no egress findings")
