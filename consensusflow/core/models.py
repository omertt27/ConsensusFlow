"""
models.py — Pydantic v2 schema for ConsensusFlow chain I/O.

Used for:
  * API serialisation / deserialisation (FastAPI, REST)
  * Chain reliability: validates every handoff between steps
  * Type-safe claim extraction from LLM JSON output

These mirror the dataclasses in protocol.py but add strict
validation, JSON-schema export, and field-level constraints.

When Pydantic is not installed, no-op shim decorators are used
so imports never crash and no validation runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from consensusflow.core.protocol import ChainStatus, ClaimStatus

# ---------------------------------------------------------------------------
# Pydantic v2 symbols — always available (pydantic is a transitive dep via
# litellm).  The _pydantic_compat shim exists only as a safety net and gives
# Pylance a single clean import path with no conflicting declarations.
# ---------------------------------------------------------------------------
from consensusflow.core._pydantic_compat import (
    PYDANTIC_AVAILABLE as _PYDANTIC_AVAILABLE,
    BaseModel,
    Field,
    field_validator,
    model_validator,
)


# ─────────────────────────────────────────────
# Atomic Claim Schema
# ─────────────────────────────────────────────

class AtomicClaimSchema(BaseModel):
    """Validated atomic claim as returned by the extractor model."""

    id: Optional[str] = Field(None, description="Short UUID for cross-referencing")
    text: str = Field(..., min_length=3, description="The verifiable statement")
    status: ClaimStatus = ClaimStatus.VERIFIED
    original_text: Optional[str] = None
    note: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("text")
    @classmethod
    def text_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Claim text must not be blank")
        return v.strip()

    @field_validator("status", mode="before")
    @classmethod
    def coerce_status(cls, v: Any) -> ClaimStatus:
        if isinstance(v, str):
            try:
                return ClaimStatus(v.upper())
            except ValueError:
                return ClaimStatus.DISPUTED
        return v


# ─────────────────────────────────────────────
# Step Result Schema
# ─────────────────────────────────────────────

class StepResultSchema(BaseModel):
    """Validated result from one pipeline step."""

    step: str = Field(..., pattern=r"^(proposer|auditor|resolver|extractor)$")
    model: str = Field(..., max_length=200)
    raw_text: str
    timestamp: Optional[datetime] = None
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ─────────────────────────────────────────────
# Gotcha Score Schema
# ─────────────────────────────────────────────

class GotchaScoreSchema(BaseModel):
    """Validated Gotcha Score — the single shareable metric."""

    score: int = Field(..., ge=0, le=100)
    grade: str
    label: str
    emoji: str
    total_claims: int = Field(default=0, ge=0)
    catches: int = Field(default=0, ge=0)
    penalty_breakdown: Dict[str, int] = Field(default_factory=dict)
    failure_taxonomy: Dict[str, int] = Field(default_factory=dict)
    share_text: str = ""

    @model_validator(mode="after")
    def catches_le_total(self) -> "GotchaScoreSchema":  # type: ignore[misc]
        if self.catches > self.total_claims:
            raise ValueError("catches cannot exceed total_claims")
        return self  # type: ignore[return-value]


# ─────────────────────────────────────────────
# Savings Report Schema
# ─────────────────────────────────────────────

class SavingsReportSchema(BaseModel):
    """Validated token / cost savings report."""

    tokens_used: int = Field(default=0, ge=0)
    tokens_saved: int = Field(default=0, ge=0)
    total_would_have_been: int = Field(default=0, ge=0)
    percent_saved: float = Field(default=0.0, ge=0.0, le=100.0)
    early_exit: bool = False
    cost_usd: float = Field(default=0.0, ge=0.0)
    saved_usd: float = Field(default=0.0, ge=0.0)


# ─────────────────────────────────────────────
# Full Verification Report Schema
# ─────────────────────────────────────────────

class VerificationReportSchema(BaseModel):
    """Complete, validated verification report suitable for API responses."""

    model_config = {"use_enum_values": True}  # type: ignore[misc]

    run_id: str
    prompt: str
    chain_models: List[str] = Field(min_length=1)
    status: ChainStatus
    final_answer: str = ""
    similarity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    early_exit: bool = False
    saved_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    total_latency_ms: float = Field(default=0.0, ge=0.0)
    atomic_claims: List[AtomicClaimSchema] = Field(default_factory=list)
    gotcha_score: Optional[GotchaScoreSchema] = None
    savings: Optional[SavingsReportSchema] = None
    steps: Dict[str, Optional[StepResultSchema]] = Field(default_factory=dict)

    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.VERIFIED)

    @property
    def corrected_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.CORRECTED)

    @property
    def rejected_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.REJECTED)


# ─────────────────────────────────────────────
# Chain input schema (for API / CLI validation)
# ─────────────────────────────────────────────

class VerifyRequestSchema(BaseModel):
    """Input schema for a verify() call — useful for API endpoints."""

    prompt: str = Field(..., min_length=3, max_length=8000)
    chain: Optional[List[str]] = Field(
        default=None,
        min_length=3,
        max_length=3,
        description="Exactly 3 LiteLLM model strings",
    )
    extractor_model: str = "gpt-4o-mini"
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    stream: bool = False
    budget_usd: Optional[float] = Field(default=None, ge=0.0)

    @field_validator("chain")
    @classmethod
    def chain_must_have_three(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and len(v) != 3:
            raise ValueError("chain must contain exactly 3 model strings")
        return v


# ─────────────────────────────────────────────
# Conversion helpers
# ─────────────────────────────────────────────

def report_to_schema(
    report: Any,
    gotcha_score: Optional[Any] = None,
    savings: Optional[Any] = None,
) -> VerificationReportSchema:
    """
    Convert a protocol.VerificationReport (dataclass) into a
    validated VerificationReportSchema (Pydantic model).
    """
    claims = [
        AtomicClaimSchema(
            id=c.id,
            text=c.text,
            status=c.status,
            original_text=c.original_text,
            note=c.note,
            confidence=c.confidence,
        )
        for c in report.atomic_claims
    ]

    gs = (
        GotchaScoreSchema(**gotcha_score.to_dict())
        if gotcha_score is not None else None
    )
    sv = (
        SavingsReportSchema(**savings.to_dict())
        if savings is not None else None
    )

    def _step(sr: Optional[Any]) -> Optional[StepResultSchema]:
        if sr is None:
            return None
        return StepResultSchema(
            step=sr.step,
            model=sr.model,
            raw_text=sr.raw_text,
            timestamp=sr.timestamp,
            prompt_tokens=sr.prompt_tokens,
            completion_tokens=sr.completion_tokens,
            latency_ms=sr.latency_ms,
        )

    return VerificationReportSchema(
        run_id=report.run_id,
        prompt=report.prompt,
        chain_models=report.chain_models,
        status=report.status,
        final_answer=report.final_answer,
        similarity_score=report.similarity_score,
        early_exit=report.early_exit,
        saved_tokens=report.saved_tokens,
        total_tokens=report.total_tokens,
        total_latency_ms=report.total_latency_ms,
        atomic_claims=claims,
        gotcha_score=gs,
        savings=sv,
        steps={
            "proposer": _step(report.proposer_result),
            "auditor":  _step(report.auditor_result),
            "resolver": _step(report.resolver_result),
        },
    )
