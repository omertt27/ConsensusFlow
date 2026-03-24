"""
ConsensusFlow — Multi-model verification pipeline.

Quick start::

    import asyncio
    from consensusflow import verify

    report = asyncio.run(verify("What time does the Blue Mosque open?"))
    print(report.final_answer)

Swap models::

    report = asyncio.run(verify(
        "Is Istanbul safe for solo travelers in 2026?",
        chain=["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-5-sonnet-20241022"],
    ))
"""

from consensusflow.core.engine import SequentialChain, verify
from consensusflow.core.protocol import (
    AtomicClaim,
    ChainStatus,
    ClaimStatus,
    StepResult,
    VerificationReport,
)
from consensusflow.ui.report import render_markdown, render_terminal, render_json
from consensusflow.exceptions import (
    ConsensusFlowError,
    ClaimParseError,
    ModelTimeoutError,
    ModelAuthError,
    ModelUnavailableError,
    BudgetExceededError,
    ChainConfigError,
    PromptNotFoundError,
)

__version__ = "0.1.0"
__author__  = "ConsensusFlow Contributors"

__all__ = [
    # Core
    "verify",
    "SequentialChain",
    # Protocol types
    "VerificationReport",
    "StepResult",
    "AtomicClaim",
    "ClaimStatus",
    "ChainStatus",
    # Renderers
    "render_markdown",
    "render_terminal",
    "render_json",
    # Exceptions
    "ConsensusFlowError",
    "ClaimParseError",
    "ModelTimeoutError",
    "ModelAuthError",
    "ModelUnavailableError",
    "BudgetExceededError",
    "ChainConfigError",
    "PromptNotFoundError",
]
