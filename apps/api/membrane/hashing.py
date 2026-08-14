"""Salted hashing primitives for the zero-retention audit trail.

The audit table never stores text. It stores a salted hash of every span it
judged, which lets an operator later prove "this exact span was seen, and it
was judged this way" by re-hashing the candidate text, without Membrane
holding a copy of what it said.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata

from .config import get_settings

_HASH_PREFIX = "sha256"


def normalise_for_hash(text: str) -> str:
    """Canonical form used before hashing so trivial rewrites still match."""
    folded = unicodedata.normalize("NFKC", text).strip().lower()
    return " ".join(folded.split())


def span_hash(text: str, *, salt: str | None = None) -> str:
    """Salted, normalised hash of a span of text."""
    key = (salt or get_settings().hash_salt).encode("utf-8")
    payload = normalise_for_hash(text).encode("utf-8")
    digest = hmac.new(key, payload, hashlib.sha256).hexdigest()
    return f"{_HASH_PREFIX}:{digest}"


def verify_span(text: str, expected_hash: str, *, salt: str | None = None) -> bool:
    """Prove a candidate text is the span behind an audit row."""
    return hmac.compare_digest(span_hash(text, salt=salt), expected_hash)


def chain_hash(prev_hash: str | None, payload: str) -> str:
    """Tamper-evident chaining for the append-only audit log.

    Each entry commits to its predecessor, so removing or editing a historical
    row breaks every hash after it.
    """
    material = f"{prev_hash or 'genesis'}|{payload}".encode("utf-8")
    return f"{_HASH_PREFIX}:{hashlib.sha256(material).hexdigest()}"


def short(hash_value: str, length: int = 12) -> str:
    """Display form for dashboards and decision cards."""
    return hash_value.split(":", 1)[-1][:length]
