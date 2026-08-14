"""L3 — Taint tracker: provenance that survives reasoning.

Every span entering the context carries an immutable provenance label. When
the agent later proposes a tool call, each argument is traced back to the
spans that could have produced it.

A recipient address that originated in the user's typed request is trusted. A
recipient address that first appeared in a web page the agent happened to read
is tainted, and a tainted argument on a privileged call is held regardless of
how reasonable the model's justification sounds.

This is classical dataflow taint analysis with the token stream as the
dataflow. The graph is in memory for the life of a session, and only hashes
and edges are persisted.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field

from ..hashing import span_hash
from ..models import Provenance

# --------------------------------------------------------------------------
# atom extraction
# --------------------------------------------------------------------------

# Atoms are the values that actually end up as tool arguments: addresses,
# endpoints, account numbers, identifiers. Matching on these is far more
# precise than free-text similarity.
ATOM_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("url", re.compile(r"\b[a-z][a-z0-9+.-]{1,15}://[^\s<>\"')\]]+", re.I)),
    ("domain", re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
                          r"(?:com|net|org|io|ai|co|dev|app|xyz|ru|cn|info|biz|"
                          r"gov|edu|me|sh|to|top|link|click)\b", re.I)),
    ("iban", re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")),
    ("account_number", re.compile(r"\b\d{8,20}\b")),
    ("phone", re.compile(r"\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}\b")),
    ("uuid", re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                        r"[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)),
    ("path", re.compile(r"(?:^|\s)((?:[A-Za-z]:\\|/)(?:[\w .-]+[/\\])*[\w .-]+)")),
    ("handle", re.compile(r"(?<![\w.])@[A-Za-z0-9_]{3,32}\b")),
    ("token", re.compile(r"\b[A-Za-z0-9_-]{24,}\b")),
    ("currency", re.compile(r"(?:[$€£₹]\s?\d[\d,]*(?:\.\d+)?)|"
                            r"\b\d[\d,]*(?:\.\d{2})?\s?(?:USD|EUR|GBP|INR)\b")),
]

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}")

_STOPWORDS = {
    "the", "and", "for", "you", "your", "with", "this", "that", "from", "have",
    "are", "was", "were", "will", "can", "not", "but", "all", "any", "our",
    "their", "them", "they", "has", "had", "its", "who", "what", "when",
    "where", "how", "why", "please", "https", "http", "www", "com",
}

MIN_SUBSTRING_MATCH = 6
DERIVATION_OVERLAP = 0.55


def normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def extract_atoms(text: str) -> dict[str, set[str]]:
    """Map atom kind -> normalised values present in this span."""
    atoms: dict[str, set[str]] = {}
    for kind, pattern in ATOM_PATTERNS:
        for match in pattern.finditer(text):
            value = normalise(match.group(len(match.groups())) if match.groups() else match.group(0))
            if value:
                atoms.setdefault(kind, set()).add(value)
    return atoms


def content_tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(normalise(text)) if t not in _STOPWORDS}


# --------------------------------------------------------------------------
# graph
# --------------------------------------------------------------------------


@dataclass
class TaintedSpan:
    hash: str
    provenance: Provenance
    source_ref: str
    text: str                      # in memory only, for the life of the session
    atoms: dict[str, set[str]] = field(default_factory=dict)
    tokens: set[str] = field(default_factory=set)
    parents: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    @property
    def trusted(self) -> bool:
        return self.provenance.is_trusted


@dataclass
class LineageMatch:
    span_hash: str
    provenance: Provenance
    source_ref: str
    match_kind: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "span": self.span_hash,
            "provenance": self.provenance.value,
            "source": self.source_ref,
            "match": self.match_kind,
            "confidence": round(self.confidence, 3),
        }


@dataclass
class TaintResult:
    """The verdict on one argument value."""

    value_hash: str
    provenance: Provenance
    tainted: bool
    matches: list[LineageMatch] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "value": self.value_hash,
            "provenance": self.provenance.value,
            "tainted": self.tainted,
            "reason": self.reason,
            "lineage": [m.to_dict() for m in self.matches[:6]],
        }

    @property
    def chain(self) -> str:
        """One-line provenance chain for a decision card."""
        if not self.matches:
            return f"{self.provenance.value} (no matching span)"
        head = self.matches[0]
        return f"{head.provenance.value} · {head.source_ref or 'unknown source'} · {head.match_kind}"


_PROVENANCE_RANK = {
    Provenance.SYSTEM: 0,
    Provenance.USER: 1,
    Provenance.AGENT: 2,
    Provenance.UNKNOWN: 3,
    Provenance.TOOL: 4,
    Provenance.RETRIEVED: 5,
}


class SessionTaintGraph:
    """The provenance DAG for a single agent session."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.spans: dict[str, TaintedSpan] = {}
        self.order: list[str] = []
        self.created_at = time.time()
        self.touched_at = time.time()

    # -- ingest ----------------------------------------------------------

    def register(
        self,
        text: str,
        provenance: Provenance,
        source_ref: str = "",
        parents: list[str] | None = None,
    ) -> TaintedSpan:
        self.touched_at = time.time()
        digest = span_hash(text)
        existing = self.spans.get(digest)
        if existing is not None:
            # A span seen from two origins takes the less trusted label.
            if _PROVENANCE_RANK[provenance] > _PROVENANCE_RANK[existing.provenance]:
                existing.provenance = provenance
                existing.source_ref = source_ref or existing.source_ref
            return existing

        span = TaintedSpan(
            hash=digest,
            provenance=provenance,
            source_ref=source_ref,
            text=text,
            atoms=extract_atoms(text),
            tokens=content_tokens(text),
            parents=list(parents or []),
        )
        self.spans[digest] = span
        self.order.append(digest)
        return span

    # -- lineage ---------------------------------------------------------

    def trace(self, value: str) -> TaintResult:
        """Trace one argument value back to the spans that could have produced it."""
        self.touched_at = time.time()
        raw = value if isinstance(value, str) else str(value)
        needle = normalise(raw)
        digest = span_hash(raw)

        if not needle:
            return TaintResult(digest, Provenance.AGENT, False, reason="empty value")

        matches: list[LineageMatch] = []
        needle_atoms = extract_atoms(raw)
        needle_tokens = content_tokens(raw)

        for span in self.spans.values():
            # 1. Atom identity: the strongest link. The exact address, URL or
            #    account number appears in this span.
            hit = False
            for kind, values in needle_atoms.items():
                shared = values & span.atoms.get(kind, set())
                if shared:
                    matches.append(LineageMatch(
                        span.hash, span.provenance, span.source_ref,
                        f"atom:{kind}", 0.99))
                    hit = True
                    break
            if hit:
                continue

            # 2. Verbatim containment either direction.
            span_norm = normalise(span.text)
            if len(needle) >= MIN_SUBSTRING_MATCH and needle in span_norm:
                matches.append(LineageMatch(
                    span.hash, span.provenance, span.source_ref, "substring", 0.9))
                continue
            if len(span_norm) >= MIN_SUBSTRING_MATCH and span_norm in needle:
                matches.append(LineageMatch(
                    span.hash, span.provenance, span.source_ref, "contains-span", 0.85))
                continue

            # 3. Derivation: the argument is a rewrite of the span (a summary
            #    pasted into an email body, for instance).
            if needle_tokens and span.tokens:
                overlap = len(needle_tokens & span.tokens) / len(needle_tokens)
                if overlap >= DERIVATION_OVERLAP and len(needle_tokens) >= 4:
                    matches.append(LineageMatch(
                        span.hash, span.provenance, span.source_ref,
                        "derived", round(overlap, 3)))

        if not matches:
            # Nothing in the context produced this. It is model-originated,
            # which is not user intent and therefore not trusted.
            return TaintResult(
                digest, Provenance.AGENT, tainted=False,
                reason="no matching span; value originated with the model",
            )

        matches.sort(key=lambda m: (_PROVENANCE_RANK[m.provenance], m.confidence),
                     reverse=True)
        worst = matches[0].provenance
        tainted = not worst.is_trusted and worst is not Provenance.AGENT

        if tainted:
            reason = (f"value traces to {worst.value} content"
                      f" from {matches[0].source_ref or 'an untrusted source'}")
        else:
            reason = f"value traces to {worst.value} content"

        return TaintResult(digest, worst, tainted, matches, reason)

    def resolve_args(self, args: dict) -> dict[str, TaintResult]:
        """Trace every leaf value of a tool-call argument object."""
        results: dict[str, TaintResult] = {}

        def walk(node, path: str) -> None:
            if isinstance(node, dict):
                for key, child in node.items():
                    walk(child, f"{path}.{key}" if path else str(key))
            elif isinstance(node, (list, tuple)):
                for index, child in enumerate(node):
                    walk(child, f"{path}[{index}]")
            elif isinstance(node, str) and node.strip():
                results[path] = self.trace(node)
            elif node is not None and not isinstance(node, bool):
                results[path] = self.trace(str(node))

        walk(args, "")
        return results

    # -- introspection ---------------------------------------------------

    def summary(self) -> dict:
        by_provenance: dict[str, int] = {}
        for span in self.spans.values():
            by_provenance[span.provenance.value] = by_provenance.get(span.provenance.value, 0) + 1
        return {
            "spans": len(self.spans),
            "by_provenance": by_provenance,
            "age_seconds": round(time.time() - self.created_at, 1),
        }


class TaintTracker:
    """Bounded, in-memory store of per-session provenance graphs."""

    def __init__(self, max_sessions: int = 512, ttl_seconds: int = 3600) -> None:
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._graphs: dict[str, SessionTaintGraph] = {}

    def graph(self, session_id: str) -> SessionTaintGraph:
        self._evict()
        graph = self._graphs.get(session_id)
        if graph is None:
            graph = SessionTaintGraph(session_id)
            self._graphs[session_id] = graph
        return graph

    def drop(self, session_id: str) -> None:
        """Forget a session's content. Called when a session closes."""
        self._graphs.pop(session_id, None)

    def _evict(self) -> None:
        now = time.time()
        stale = [sid for sid, g in self._graphs.items()
                 if now - g.touched_at > self.ttl_seconds]
        for sid in stale:
            self._graphs.pop(sid, None)
        if len(self._graphs) > self.max_sessions:
            oldest = sorted(self._graphs.items(), key=lambda kv: kv[1].touched_at)
            for sid, _ in oldest[: len(self._graphs) - self.max_sessions]:
                self._graphs.pop(sid, None)

    @property
    def active_sessions(self) -> int:
        return len(self._graphs)


tracker = TaintTracker()
