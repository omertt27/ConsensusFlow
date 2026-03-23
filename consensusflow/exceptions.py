"""
exceptions.py — Custom exception hierarchy for ConsensusFlow.

All exceptions inherit from ConsensusFlowError so callers can catch
the broad base class or narrow specific conditions as needed.
"""

from __future__ import annotations


class ConsensusFlowError(Exception):
    """Base exception for all ConsensusFlow errors."""


class ClaimParseError(ConsensusFlowError):
    """Raised when claim extraction or audit JSON parsing fails unrecoverably."""


class ModelTimeoutError(ConsensusFlowError):
    """Raised when a model step exceeds its configured timeout."""


class ModelAuthError(ConsensusFlowError):
    """Raised on authentication / authorisation failures (not retryable)."""


class ModelUnavailableError(ConsensusFlowError):
    """Raised when all models in the fallback chain are unavailable."""


class BudgetExceededError(ConsensusFlowError):
    """Raised when the estimated cost exceeds the configured budget ceiling."""

    def __init__(self, cost_usd: float, budget_usd: float) -> None:
        self.cost_usd = cost_usd
        self.budget_usd = budget_usd
        super().__init__(
            f"Estimated cost ${cost_usd:.4f} exceeds budget ${budget_usd:.4f}"
        )


class ChainConfigError(ConsensusFlowError):
    """Raised for invalid SequentialChain configuration."""


class PromptNotFoundError(ConsensusFlowError):
    """Raised when a required prompt template file cannot be found."""
