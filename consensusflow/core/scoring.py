"""
scoring.py — The Gotcha Score engine.

The Gotcha Score is a single 0–100 metric that summarises how many
"gotchas" ConsensusFlow caught in an LLM answer.

Score design
────────────
  100  = perfect — every claim verified, nothing to catch
    0  = catastrophic — most claims rejected or disputed

Default penalty table (per claim, configurable via SequentialChain.penalty_weights):
  VERIFIED   →  0  penalty
  NUANCED    →  5  penalty  (soft: context was missing)
  DISPUTED   → 15  penalty  (medium-soft: couldn't confirm)
  CORRECTED  → 20  penalty  (medium: a fact was wrong)
  REJECTED   → 35  penalty  (hard: demonstrably false)

The raw penalty sum is normalised against a "worst-case" ceiling so
the score is always 0–100 regardless of the number of claims.

Failure Taxonomy (Layer 3)
──────────────────────────
Every correction is automatically classified into one of five
failure categories so developers learn *why* an answer failed:

  FACTUAL_ERROR   — The stated fact is simply wrong
  OUTDATED_INFO   — The fact was correct historically but is stale
  MISSING_CONTEXT — True but dangerously incomplete
  UNVERIFIABLE    — Cannot be confirmed from any source
  FABRICATION     — Invented with no basis in reality

Per-provider cost estimation
─────────────────────────────
Cost estimates use per-provider blended rates (input+output averaged)
rather than a single hardcoded $0.01/1K figure. Rates are approximate
and kept in _PROVIDER_RATES keyed on model-prefix substrings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

from consensusflow.core.protocol import AtomicClaim, ClaimStatus, VerificationReport


# ─────────────────────────────────────────────
# Failure taxonomy
# ─────────────────────────────────────────────

class FailureCategory(str, Enum):
    FACTUAL_ERROR    = "FACTUAL_ERROR"     # Stated fact is wrong
    OUTDATED_INFO    = "OUTDATED_INFO"     # Was true, now stale
    MISSING_CONTEXT  = "MISSING_CONTEXT"  # True but incomplete
    UNVERIFIABLE     = "UNVERIFIABLE"      # Can't be confirmed
    FABRICATION      = "FABRICATION"       # Invented with no basis


# Pattern-based taxonomy: ordered from most-specific to least-specific.
# Each tuple: (category, list-of-keyword-regexes).
# Using word-boundary regexes reduces false positives compared to simple
# substring matching (e.g. "now" as a standalone word vs. "known").
_TAXONOMY_PATTERNS: List[Tuple[FailureCategory, List[str]]] = [
    (FailureCategory.FABRICATION, [
        r"\bnever\b", r"\bdoes not exist\b", r"\binvented\b",
        r"\bfabricated\b", r"\bhallucin", r"\bno evidence\b",
        r"\bhas always been free\b", r"\balways been\b", r"\bno record\b",
        r"\bfictional\b",
    ]),
    (FailureCategory.OUTDATED_INFO, [
        r"\bchanged\b", r"\bupdated\b", r"\bno longer\b", r"\bsince\b",
        r"\bcurrent(ly)?\b", r"\brecent(ly)?\b", r"\bnow\b",
        r"\b202[3-9]\b", r"\b20[3-9]\d\b",  # years ≥ 2023
    ]),
    (FailureCategory.MISSING_CONTEXT, [
        r"\bhowever\b", r"\bexcept\b", r"\bclosed on\b",
        r"\bonly on\b", r"\bunless\b", r"\bnote that\b", r"\bcaveat\b",
        r"\bbut\b", r"\bexception\b",
    ]),
    (FailureCategory.UNVERIFIABLE, [
        r"\bunclear\b", r"\buncertain\b", r"\bcannot confirm\b",
        r"\bno source\b", r"\bunverifiable\b", r"\bdisputed\b",
        r"\bcontroversial\b", r"\bno consensus\b",
    ]),
    (FailureCategory.FACTUAL_ERROR, []),   # catch-all — always last
]


def classify_failure(claim: AtomicClaim) -> FailureCategory:
    """Classify a non-VERIFIED claim into a FailureCategory using regex patterns."""
    if claim.status == ClaimStatus.DISPUTED:
        return FailureCategory.UNVERIFIABLE
    text = ((claim.note or "") + " " + (claim.text or "")).lower()
    for category, patterns in _TAXONOMY_PATTERNS:
        if any(re.search(pat, text) for pat in patterns):
            return category
    return FailureCategory.FACTUAL_ERROR


# ─────────────────────────────────────────────
# Default penalty weights
# ─────────────────────────────────────────────

DEFAULT_PENALTIES: Dict[ClaimStatus, int] = {
    ClaimStatus.VERIFIED:  0,
    ClaimStatus.NUANCED:   5,
    ClaimStatus.DISPUTED: 15,
    ClaimStatus.CORRECTED: 20,
    ClaimStatus.REJECTED:  35,
}

# Worst-case penalty per claim (all REJECTED)
_MAX_PENALTY_PER_CLAIM = DEFAULT_PENALTIES[ClaimStatus.REJECTED]


# ─────────────────────────────────────────────
# Per-provider cost rates (USD per 1K tokens, blended input+output)
# ─────────────────────────────────────────────

# Keys are lowercase substrings matched against model names.
# The first matching entry wins. Rates as of Q1 2026 (approximate).
_PROVIDER_RATES: List[Tuple[str, float]] = [
    # OpenAI
    ("gpt-4o",              0.010),   # ~$0.005 input + $0.015 output blended
    ("gpt-4-turbo",         0.020),
    ("gpt-4",               0.030),
    ("gpt-3.5",             0.002),
    ("o1",                  0.060),
    # Anthropic
    ("claude-3-7",          0.009),   # ~$0.003 + $0.015
    ("claude-3-5",          0.009),
    ("claude-3-opus",       0.045),
    ("claude-3-haiku",      0.0013),
    ("claude",              0.009),   # generic claude fallback
    # Google
    ("gemini-2.0-flash",    0.0004),
    ("gemini-1.5-flash",    0.0004),
    ("gemini-1.5-pro",      0.0035),
    ("gemini",              0.002),   # generic gemini fallback
    # Cohere
    ("command-r-plus",      0.006),
    ("command-r",           0.0015),
    # Meta / Llama (via Together / Replicate)
    ("llama-3",             0.0009),
    ("llama",               0.0009),
    # Mistral
    ("mistral-large",       0.004),
    ("mistral",             0.001),
]

_DEFAULT_RATE = 0.005  # $0.005 per 1K tokens when provider unknown


def _rate_for_model(model: str) -> float:
    """Return the USD-per-1K-tokens rate for a given model string."""
    lm = model.lower()
    for substr, rate in _PROVIDER_RATES:
        if substr in lm:
            return rate
    return _DEFAULT_RATE


def _estimate_cost_usd(tokens: int, chain: Optional[List[str]] = None) -> float:
    """
    Estimate USD cost for a token count, using blended chain rates if available.
    Falls back to the default rate when chain is not provided.
    """
    if chain:
        blended_rate = sum(_rate_for_model(m) for m in chain) / len(chain)
    else:
        blended_rate = _DEFAULT_RATE
    return tokens * blended_rate / 1000


# ─────────────────────────────────────────────
# Gotcha Score dataclass
# ─────────────────────────────────────────────

@dataclass
class GotchaScore:
    """
    The single shareable metric produced by ConsensusFlow.

    Attributes
    ----------
    score : int
        0–100. Higher is better (100 = fully verified).
    grade : str
        Letter grade: A+ / A / B / C / D / F
    label : str
        Human-readable verdict (e.g. "Mostly Reliable").
    emoji : str
        Single emoji for social sharing.
    penalty_breakdown : dict
        Penalty points by ClaimStatus.
    failure_taxonomy : dict
        Count of failures by FailureCategory.
    total_claims : int
    catches : int
        Number of non-VERIFIED claims (the "gotchas").
    share_text : str
        Ready-to-tweet one-liner.
    """
    score: int = 100
    grade: str = "A+"
    label: str = "Fully Verified"
    emoji: str = "✅"
    penalty_breakdown: Dict[str, int] = field(default_factory=dict)
    failure_taxonomy: Dict[str, int] = field(default_factory=dict)
    total_claims: int = 0
    catches: int = 0
    share_text: str = ""

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "grade": self.grade,
            "label": self.label,
            "emoji": self.emoji,
            "total_claims": self.total_claims,
            "catches": self.catches,
            "penalty_breakdown": self.penalty_breakdown,
            "failure_taxonomy": self.failure_taxonomy,
            "share_text": self.share_text,
        }


# ─────────────────────────────────────────────
# Grade table
# ─────────────────────────────────────────────

_GRADE_TABLE = [
    (95, "A+", "Fully Verified",         "✅"),
    (85, "A",  "Highly Reliable",        "🟢"),
    (72, "B",  "Mostly Reliable",        "🟡"),
    (55, "C",  "Use With Caution",       "🟠"),
    (35, "D",  "Significant Errors",     "🔴"),
    ( 0, "F",  "Do Not Trust",           "💀"),
]


def _letter_grade(score: int) -> Tuple[str, str, str]:
    for threshold, grade, label, emoji in _GRADE_TABLE:
        if score >= threshold:
            return grade, label, emoji
    return "F", "Do Not Trust", "💀"


# ─────────────────────────────────────────────
# Main scorer
# ─────────────────────────────────────────────

def compute_gotcha_score(
    report: VerificationReport,
    penalty_weights: Optional[Dict[ClaimStatus, int]] = None,
) -> GotchaScore:
    """
    Compute the Gotcha Score for a completed VerificationReport.

    Parameters
    ----------
    report : VerificationReport
    penalty_weights : dict, optional
        Override default penalties per ClaimStatus. Useful for domain-specific
        tuning (e.g. medical use-cases may want higher REJECTED penalty).

    Returns a GotchaScore with score, grade, taxonomy, and share text.
    """
    penalties = penalty_weights if penalty_weights is not None else DEFAULT_PENALTIES
    max_penalty = max(penalties.values()) if penalties else _MAX_PENALTY_PER_CLAIM

    claims = report.atomic_claims
    total  = len(claims)

    if total == 0:
        # No claims → can't grade (treat as neutral)
        gs = GotchaScore(score=50, grade="C", label="No Claims Extracted",
                         emoji="❓", total_claims=0, catches=0)
        gs.share_text = _build_share_text(gs, report.prompt)
        return gs

    # ── Compute penalty ──────────────────────
    raw_penalty   = 0
    penalty_by_status: Dict[str, int] = {}
    taxonomy_counts: Dict[str, int]   = {}
    catches = 0

    for claim in claims:
        p = penalties.get(claim.status, 0)
        raw_penalty += p
        key = claim.status.value
        penalty_by_status[key] = penalty_by_status.get(key, 0) + p

        if claim.status != ClaimStatus.VERIFIED:
            catches += 1
            cat = classify_failure(claim).value
            taxonomy_counts[cat] = taxonomy_counts.get(cat, 0) + 1

    # Normalise: worst case = every claim at max penalty
    worst_case = total * max_penalty
    raw_score = max(0, round(100 * (1 - raw_penalty / worst_case)))

    # Bonus: early exit (full consensus) → always 100
    if report.early_exit and catches == 0:
        raw_score = 100

    grade, label, emoji = _letter_grade(raw_score)

    gs = GotchaScore(
        score=raw_score,
        grade=grade,
        label=label,
        emoji=emoji,
        penalty_breakdown=penalty_by_status,
        failure_taxonomy=taxonomy_counts,
        total_claims=total,
        catches=catches,
    )
    gs.share_text = _build_share_text(gs, report.prompt)
    return gs


def _build_share_text(gs: GotchaScore, prompt: str) -> str:
    """Build a tweet-ready one-liner."""
    short_prompt = prompt[:60].rstrip() + ("…" if len(prompt) > 60 else "")
    if gs.catches == 0:
        return (f"ConsensusFlow scored my AI answer {gs.score}/100 {gs.emoji} — "
                f"100% verified for: \"{short_prompt}\" #ConsensusFlow #VerifiedAI")
    return (f"ConsensusFlow caught {gs.catches} hallucination"
            f"{'s' if gs.catches > 1 else ''} in my AI answer! "
            f"Score: {gs.score}/100 {gs.emoji} | Grade: {gs.grade} "
            f"#ConsensusFlow #AIHallucination")


# ─────────────────────────────────────────────
# Savings report
# ─────────────────────────────────────────────

@dataclass
class SavingsReport:
    """
    Token and cost savings analysis for a completed run.
    """
    tokens_used: int       = 0
    tokens_saved: int      = 0
    total_would_have_been: int = 0
    percent_saved: float   = 0.0
    early_exit: bool       = False
    cost_usd: float        = 0.0
    saved_usd: float       = 0.0

    def to_dict(self) -> dict:
        return {
            "tokens_used": self.tokens_used,
            "tokens_saved": self.tokens_saved,
            "total_would_have_been": self.total_would_have_been,
            "percent_saved": round(self.percent_saved, 1),
            "early_exit": self.early_exit,
            "cost_usd": round(self.cost_usd, 4),
            "saved_usd": round(self.saved_usd, 4),
        }

    def __str__(self) -> str:
        lines = [
            "💰 Savings Report",
            f"   Tokens used    : {self.tokens_used:,}",
        ]
        if self.early_exit:
            lines += [
                f"   Tokens saved   : {self.tokens_saved:,}  (Early Exit — Resolver skipped)",
                f"   Savings        : {self.percent_saved:.0f}%",
                f"   Est. cost      : ${self.cost_usd:.4f}  (saved ${self.saved_usd:.4f})",
            ]
        else:
            lines.append(f"   Est. cost      : ${self.cost_usd:.4f}")
        return "\n".join(lines)


def compute_savings(
    report: VerificationReport,
    chain: Optional[List[str]] = None,
) -> SavingsReport:
    """
    Compute token and cost savings for a VerificationReport.

    Parameters
    ----------
    chain : list[str], optional
        The model chain used; enables per-provider cost estimation.
        Falls back to report.chain_models when not provided.
    """
    model_chain = chain or report.chain_models or None
    used   = report.total_tokens
    saved  = report.saved_tokens if report.early_exit else 0
    total  = used + saved
    pct    = (saved / total * 100) if total > 0 else 0.0
    return SavingsReport(
        tokens_used=used,
        tokens_saved=saved,
        total_would_have_been=total,
        percent_saved=pct,
        early_exit=report.early_exit,
        cost_usd=_estimate_cost_usd(used, model_chain),
        saved_usd=_estimate_cost_usd(saved, model_chain),
    )
