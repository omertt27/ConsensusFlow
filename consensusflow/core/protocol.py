"""
protocol.py — Definitions for Claims, Verification, and StepResult.
Every unit of data flowing through the chain is typed here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


# ─────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────

class ClaimStatus(str, Enum):
    """Verdict assigned by the Auditor to each atomic claim."""
    VERIFIED   = "VERIFIED"    # Claim is factually correct
    CORRECTED  = "CORRECTED"   # Claim was wrong; correction supplied
    DISPUTED   = "DISPUTED"    # Claim is uncertain / needs sourcing
    NUANCED    = "NUANCED"     # Claim is correct but needs context
    REJECTED   = "REJECTED"    # Claim is demonstrably false


class ChainStatus(str, Enum):
    """Overall pipeline outcome."""
    SUCCESS       = "SUCCESS"
    EARLY_EXIT    = "EARLY_EXIT"   # 100 % consensus; resolver skipped
    PARTIAL       = "PARTIAL"      # Some claims disputed
    ERROR         = "ERROR"


# ─────────────────────────────────────────────
# Atomic Claim
# ─────────────────────────────────────────────

@dataclass
class AtomicClaim:
    """
    A single, independently verifiable statement extracted from
    the Proposer's answer.

    Example:
        text   = "The Blue Mosque closes at 9 PM."
        status = ClaimStatus.CORRECTED
        note   = "Closing time is 10 PM as of 2026."
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    text: str = ""
    status: ClaimStatus = ClaimStatus.VERIFIED
    original_text: Optional[str] = None   # set when CORRECTED
    note: Optional[str] = None            # Auditor's free-text remark
    confidence: float = 1.0               # 0.0 – 1.0
    sources: List[str] = field(default_factory=list)  # verifiable URLs cited

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "status": self.status.value,
            "original_text": self.original_text,
            "note": self.note,
            "confidence": self.confidence,
            "sources": self.sources,
        }


# ─────────────────────────────────────────────
# Step Result
# ─────────────────────────────────────────────

@dataclass
class StepResult:
    """
    Immutable record produced by every step in the chain.
    Provides a full audit trail.
    """
    step: str                           # "proposer" | "auditor" | "resolver"
    model: str                          # e.g. "gpt-4o"
    raw_text: str                       # Raw LLM output
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step,
            "model": self.model,
            "raw_text": self.raw_text,
            "timestamp": self.timestamp.isoformat(),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


# ─────────────────────────────────────────────
# Verification Report
# ─────────────────────────────────────────────

@dataclass
class VerificationReport:
    """
    Top-level object returned to the user after the full chain runs.
    Contains every intermediate result plus the final synthesized answer.
    """
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    prompt: str = ""
    chain_models: List[str] = field(default_factory=list)
    status: ChainStatus = ChainStatus.SUCCESS

    # Step artefacts
    proposer_result: Optional[StepResult] = None
    auditor_result: Optional[StepResult] = None
    resolver_result: Optional[StepResult] = None

    # Claim-level detail
    atomic_claims: List[AtomicClaim] = field(default_factory=list)

    # Derived outputs
    final_answer: str = ""
    similarity_score: float = 0.0          # 0.0 – 1.0 (1.0 = full consensus)
    early_exit: bool = False
    saved_tokens: int = 0                  # tokens saved by early exit
    saved_cost_usd: float = 0.0            # USD saved by early exit (resolver skipped)

    # Aggregate cost
    total_tokens: int = 0
    total_latency_ms: float = 0.0

    # Custom penalty weights forwarded from SequentialChain (optional)
    penalty_weights: Optional[Dict] = None

    # Auditor reliability warning (set when >60% of claims are DISPUTED)
    auditor_reliability_warning: Optional[str] = None

    # ── Convenience helpers ──────────────────

    @property
    def verified_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.VERIFIED)

    @property
    def corrected_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.CORRECTED)

    @property
    def disputed_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.DISPUTED)

    @property
    def nuanced_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.NUANCED)

    @property
    def rejected_count(self) -> int:
        return sum(1 for c in self.atomic_claims if c.status == ClaimStatus.REJECTED)

    @property
    def disputed_ratio(self) -> float:
        """Fraction of claims that are DISPUTED (0.0–1.0)."""
        if not self.atomic_claims:
            return 0.0
        return self.disputed_count / len(self.atomic_claims)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "prompt": self.prompt,
            "chain_models": self.chain_models,
            "status": self.status.value,
            "final_answer": self.final_answer,
            "similarity_score": self.similarity_score,
            "early_exit": self.early_exit,
            "saved_tokens": self.saved_tokens,
            "saved_cost_usd": round(self.saved_cost_usd, 6),
            "total_tokens": self.total_tokens,
            "total_latency_ms": self.total_latency_ms,
            "claim_summary": {
                "verified": self.verified_count,
                "corrected": self.corrected_count,
                "disputed": self.disputed_count,
                "nuanced": self.nuanced_count,
                "rejected": self.rejected_count,
                "disputed_ratio": round(self.disputed_ratio, 3),
            },
            "auditor_reliability_warning": self.auditor_reliability_warning,
            "atomic_claims": [c.to_dict() for c in self.atomic_claims],
            "steps": {
                "proposer": self.proposer_result.to_dict() if self.proposer_result else None,
                "auditor":  self.auditor_result.to_dict()  if self.auditor_result  else None,
                "resolver": self.resolver_result.to_dict() if self.resolver_result else None,
            },
        }
