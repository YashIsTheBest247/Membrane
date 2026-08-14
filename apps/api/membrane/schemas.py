"""Request and response models for the proxy API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .models import Provenance


class ContractRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    capabilities: list[str] = Field(default_factory=list)
    subject: str = "anonymous"
    # What the user actually asked for. Registered as trusted provenance and
    # used as the diff baseline on decision cards.
    task: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=10, le=86_400)

    @field_validator("capabilities")
    @classmethod
    def _cap_shape(cls, value: list[str]) -> list[str]:
        for capability in value:
            if not capability or len(capability) > 64:
                raise ValueError("capability names must be 1-64 characters")
        return value


class ContractResponse(BaseModel):
    contract: str
    claims: dict[str, Any]
    unknown_capabilities: list[str] = Field(default_factory=list)


class IngestRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    content: str
    source: str = Field(default="", max_length=2048,
                        description="URL, message id, file name or tool name")
    content_type: str = "text/plain"
    provenance: Literal["retrieved", "tool", "user", "system", "agent"] = "retrieved"
    subject: str = "anonymous"

    @property
    def provenance_label(self) -> Provenance:
        return Provenance(self.provenance)


class IngestResponse(BaseModel):
    session_id: str
    verdict: str
    content: str
    source: str
    provenance: str
    spans: int
    quarantined: int
    removed: int
    escalations: int
    trust_score: float
    attack_families: list[str]
    latency_ms: float
    layer_latency_ms: dict[str, float]
    detail: dict[str, Any]


class ToolCallRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    tool: str = Field(min_length=1, max_length=128)
    args: dict[str, Any] = Field(default_factory=dict)
    contract: str | None = None
    subject: str = "anonymous"
    # When true the call blocks until a human decides or the card expires.
    wait_for_approval: bool = False


class ToolCallResponse(BaseModel):
    session_id: str
    verdict: str
    allowed: bool
    tool: str
    capability: str | None
    sensitivity: str
    reason: str
    code: str
    action_id: str | None
    approval_status: str | None
    provenance: dict[str, Any]
    egress: dict[str, Any]
    card: dict[str, Any]
    breaker_open: bool
    latency_ms: float


class UserIntentRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    subject: str = "anonymous"


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "deny"]
    resolved_by: str = "dashboard"


class McpScanRequest(BaseModel):
    server: str = Field(default="unnamed-server", max_length=256)
    tools: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = None


class BenchRunSubmission(BaseModel):
    label: str = "local"
    total_cases: int = 0
    unprotected_success: int = 0
    protected_success: int = 0
    asr_reduction: float = 0.0
    false_positive_rate: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    detail: dict[str, Any] = Field(default_factory=dict)
