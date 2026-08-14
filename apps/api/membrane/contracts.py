"""Signed intent contracts.

Before a task begins the agent declares what it intends to do, and Membrane
issues a signed, time-limited contract enumerating the permitted capabilities.
This is the inversion the whole design rests on: instead of asking the
open-ended question "is this input malicious?", the enforcement point asks the
closed one, "is this action inside the envelope the user authorised?".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import get_settings

CONTRACT_VERSION = 1


class ContractError(Exception):
    """Raised when a contract is absent, malformed, expired or unsigned."""

    def __init__(self, message: str, code: str = "invalid_contract") -> None:
        super().__init__(message)
        self.code = code


def _b64(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


@dataclass
class ContractClaims:
    session_id: str
    subject: str
    capabilities: list[str]
    issued_at: datetime
    expires_at: datetime
    nonce: str
    task_digest: str = ""
    contract_id: str = ""
    version: int = CONTRACT_VERSION
    raw: dict = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at

    @property
    def ttl_remaining_seconds(self) -> float:
        return max(0.0, (self.expires_at - datetime.now(timezone.utc)).total_seconds())

    def grants(self, capability: str) -> bool:
        """Exact match, or a `namespace.*` wildcard the operator wrote."""
        if capability in self.capabilities:
            return True
        namespace = capability.split(".", 1)[0]
        return f"{namespace}.*" in self.capabilities or "*" in self.capabilities

    def to_dict(self) -> dict:
        return {
            "contract_id": self.contract_id,
            "session_id": self.session_id,
            "subject": self.subject,
            "capabilities": self.capabilities,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "ttl_remaining_seconds": round(self.ttl_remaining_seconds, 1),
            "task_digest": self.task_digest,
        }


def issue(
    *,
    session_id: str,
    capabilities: list[str],
    subject: str = "anonymous",
    task_digest: str = "",
    ttl_seconds: int | None = None,
    key: str | None = None,
) -> tuple[str, ContractClaims]:
    """Mint a contract. Returns (token, claims)."""
    settings = get_settings()
    signing_key = key or settings.signing_key
    ttl = ttl_seconds or settings.contract_ttl_seconds

    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(seconds=ttl)
    nonce = secrets.token_urlsafe(12)
    contract_id = f"ctr_{secrets.token_hex(8)}"

    body = {
        "v": CONTRACT_VERSION,
        "cid": contract_id,
        "sid": session_id,
        "sub": subject,
        "caps": sorted(set(capabilities)),
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
        "nonce": nonce,
        "task": task_digest,
    }
    payload = _b64(json.dumps(body, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(payload, signing_key)
    token = f"{payload}.{signature}"

    claims = ContractClaims(
        session_id=session_id,
        subject=subject,
        capabilities=body["caps"],
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=nonce,
        task_digest=task_digest,
        contract_id=contract_id,
        raw=body,
    )
    return token, claims


def verify(token: str | None, *, key: str | None = None,
           session_id: str | None = None) -> ContractClaims:
    """Verify a contract token. Raises ContractError on any problem.

    There is no permissive branch in this function. An absent, malformed,
    mis-signed, expired or mis-bound contract all resolve the same way.
    """
    settings = get_settings()
    signing_key = key or settings.signing_key

    if not token:
        raise ContractError("no intent contract presented", "missing_contract")

    payload, _, signature = token.partition(".")
    if not payload or not signature:
        raise ContractError("contract is not in payload.signature form", "malformed")

    expected = _sign(payload, signing_key)
    if not hmac.compare_digest(expected, signature):
        raise ContractError("contract signature does not verify", "bad_signature")

    try:
        body = json.loads(_unb64(payload))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ContractError(f"contract payload unreadable: {exc}", "malformed") from exc

    if body.get("v") != CONTRACT_VERSION:
        raise ContractError("unsupported contract version", "version")

    claims = ContractClaims(
        session_id=body.get("sid", ""),
        subject=body.get("sub", "anonymous"),
        capabilities=list(body.get("caps", [])),
        issued_at=datetime.fromtimestamp(body.get("iat", 0), timezone.utc),
        expires_at=datetime.fromtimestamp(body.get("exp", 0), timezone.utc),
        nonce=body.get("nonce", ""),
        task_digest=body.get("task", ""),
        contract_id=body.get("cid", ""),
        raw=body,
    )

    if claims.expired:
        raise ContractError("contract has expired", "expired")
    if session_id is not None and claims.session_id != session_id:
        raise ContractError("contract is bound to a different session", "session_mismatch")

    return claims


# --------------------------------------------------------------------------
# callback signing, used by the Telegram decision cards
# --------------------------------------------------------------------------


def sign_callback(action_id: str, decision: str, *, key: str | None = None) -> str:
    """Compact signed payload for an inline-keyboard button.

    Telegram limits callback_data to 64 bytes, so the signature is truncated to
    16 hex characters (64 bits). That is sufficient here because the token is
    additionally bound to a single held action which expires in 60 seconds and
    can only be resolved once.
    """
    settings = get_settings()
    signing_key = key or settings.signing_key
    payload = f"{action_id}:{decision}"
    signature = _sign(payload, signing_key)[:16]
    return f"{payload}:{signature}"


def verify_callback(data: str, *, key: str | None = None) -> tuple[str, str]:
    """Returns (action_id, decision). Raises ContractError if unsigned."""
    settings = get_settings()
    signing_key = key or settings.signing_key
    parts = data.split(":")
    if len(parts) != 3:
        raise ContractError("callback payload malformed", "malformed")
    action_id, decision, signature = parts
    expected = _sign(f"{action_id}:{decision}", signing_key)[:16]
    if not hmac.compare_digest(expected, signature):
        raise ContractError("callback signature does not verify", "bad_signature")
    if decision not in ("approve", "deny"):
        raise ContractError("unknown decision", "bad_decision")
    return action_id, decision
