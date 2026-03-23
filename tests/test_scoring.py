"""
test_scoring.py — Unit tests for scoring.py.
No API calls required.
"""

from __future__ import annotations

import pytest

from consensusflow.core.protocol import (
    AtomicClaim,
    ChainStatus,
    ClaimStatus,
    VerificationReport,
)
from consensusflow.core.scoring import (
    DEFAULT_PENALTIES,
    FailureCategory,
    GotchaScore,
    SavingsReport,
    _estimate_cost_usd,
    _letter_grade,
    _rate_for_model,
    classify_failure,
    compute_gotcha_score,
    compute_savings,
)


# ── classify_failure ──────────────────────────────────────────

class TestClassifyFailure:
    def _claim(self, status: ClaimStatus, note: str = "", text: str = "claim") -> AtomicClaim:
        c = AtomicClaim(text=text)
        c.status = status
        c.note = note
        return c

    def test_disputed_always_unverifiable(self):
        c = self._claim(ClaimStatus.DISPUTED, note="This seems wrong")
        assert classify_failure(c) == FailureCategory.UNVERIFIABLE

    def test_fabrication_keywords(self):
        c = self._claim(ClaimStatus.REJECTED, note="This never existed, fabricated claim")
        assert classify_failure(c) == FailureCategory.FABRICATION

    def test_outdated_year_pattern(self):
        c = self._claim(ClaimStatus.CORRECTED, note="Changed in 2024, no longer valid")
        assert classify_failure(c) == FailureCategory.OUTDATED_INFO

    def test_missing_context_however(self):
        c = self._claim(ClaimStatus.NUANCED, note="True, however only on weekdays")
        assert classify_failure(c) == FailureCategory.MISSING_CONTEXT

    def test_unverifiable_cannot_confirm(self):
        c = self._claim(ClaimStatus.CORRECTED, note="Cannot confirm from any source")
        assert classify_failure(c) == FailureCategory.UNVERIFIABLE

    def test_factual_error_fallback(self):
        c = self._claim(ClaimStatus.CORRECTED, note="The price is wrong")
        assert classify_failure(c) == FailureCategory.FACTUAL_ERROR

    def test_fabrication_beats_outdated(self):
        # "never" (fabrication) should match before "changed" (outdated)
        c = self._claim(ClaimStatus.REJECTED, note="This never existed and changed nothing")
        assert classify_failure(c) == FailureCategory.FABRICATION

    def test_no_note_defaults_to_factual_error(self):
        c = self._claim(ClaimStatus.CORRECTED, note="")
        assert classify_failure(c) == FailureCategory.FACTUAL_ERROR


# ── compute_gotcha_score ──────────────────────────────────────

class TestComputeGotchaScore:
    def _report(self, claims: list[AtomicClaim], early_exit: bool = False) -> VerificationReport:
        r = VerificationReport(prompt="test", atomic_claims=claims)
        r.early_exit = early_exit
        return r

    def test_all_verified_score_100(self):
        claims = [AtomicClaim(text="A"), AtomicClaim(text="B")]
        gs = compute_gotcha_score(self._report(claims))
        assert gs.score == 100
        assert gs.grade == "A+"
        assert gs.catches == 0

    def test_all_rejected_score_0(self):
        claims = [AtomicClaim(text="A"), AtomicClaim(text="B")]
        for c in claims:
            c.status = ClaimStatus.REJECTED
        gs = compute_gotcha_score(self._report(claims))
        assert gs.score == 0
        assert gs.grade == "F"
        assert gs.catches == 2

    def test_mixed_claims_score_between(self):
        claims = [
            AtomicClaim(text="A"),  # VERIFIED  → 0
            AtomicClaim(text="B"),  # CORRECTED → 20
        ]
        claims[1].status = ClaimStatus.CORRECTED
        gs = compute_gotcha_score(self._report(claims))
        # penalty=20, worst=70 → 100*(1-20/70) ≈ 71
        assert 0 < gs.score < 100
        assert gs.catches == 1

    def test_no_claims_returns_neutral(self):
        gs = compute_gotcha_score(self._report([]))
        assert gs.score == 50
        assert gs.grade == "C"
        assert gs.total_claims == 0

    def test_early_exit_all_verified_is_100(self):
        claims = [AtomicClaim(text="A")]
        gs = compute_gotcha_score(self._report(claims, early_exit=True))
        assert gs.score == 100

    def test_custom_penalty_weights(self):
        from consensusflow.core.protocol import ClaimStatus
        claims = [AtomicClaim(text="A")]
        claims[0].status = ClaimStatus.CORRECTED
        custom = {**DEFAULT_PENALTIES, ClaimStatus.CORRECTED: 5}
        gs = compute_gotcha_score(self._report(claims), penalty_weights=custom)
        # With low CORRECTED penalty, score should be higher
        assert gs.score > 50

    def test_failure_taxonomy_populated(self):
        claims = [AtomicClaim(text="A")]
        claims[0].status = ClaimStatus.CORRECTED
        claims[0].note = "Changed in 2025"
        gs = compute_gotcha_score(self._report(claims))
        assert "OUTDATED_INFO" in gs.failure_taxonomy

    def test_share_text_with_catches(self):
        claims = [AtomicClaim(text="A")]
        claims[0].status = ClaimStatus.REJECTED
        gs = compute_gotcha_score(self._report(claims))
        assert "hallucination" in gs.share_text.lower()

    def test_share_text_no_catches(self):
        gs = compute_gotcha_score(self._report([AtomicClaim(text="A")]))
        assert "100% verified" in gs.share_text or "verified" in gs.share_text.lower()

    def test_penalty_breakdown_keys(self):
        claims = [AtomicClaim(text="A"), AtomicClaim(text="B")]
        claims[0].status = ClaimStatus.DISPUTED
        claims[1].status = ClaimStatus.NUANCED
        gs = compute_gotcha_score(self._report(claims))
        assert "DISPUTED" in gs.penalty_breakdown
        assert "NUANCED" in gs.penalty_breakdown

    def test_rejected_count_in_taxonomy(self):
        claims = [AtomicClaim(text="A")]
        claims[0].status = ClaimStatus.REJECTED
        claims[0].note = "never existed"
        gs = compute_gotcha_score(self._report(claims))
        assert "FABRICATION" in gs.failure_taxonomy


# ── _letter_grade ─────────────────────────────────────────────

class TestLetterGrade:
    @pytest.mark.parametrize("score,expected_grade", [
        (100, "A+"),
        (95,  "A+"),
        (94,  "A"),
        (85,  "A"),
        (84,  "B"),
        (72,  "B"),
        (71,  "C"),
        (55,  "C"),
        (54,  "D"),
        (35,  "D"),
        (34,  "F"),
        (0,   "F"),
    ])
    def test_grade_boundaries(self, score: int, expected_grade: str):
        grade, _, _ = _letter_grade(score)
        assert grade == expected_grade


# ── compute_savings ───────────────────────────────────────────

class TestComputeSavings:
    def _report(self, total_tokens: int, saved_tokens: int = 0, early_exit: bool = False):
        r = VerificationReport(prompt="test")
        r.total_tokens = total_tokens
        r.saved_tokens = saved_tokens
        r.early_exit = early_exit
        return r

    def test_no_savings_no_early_exit(self):
        r = self._report(1000)
        s = compute_savings(r)
        assert s.tokens_saved == 0
        assert s.percent_saved == 0.0
        assert s.early_exit is False

    def test_early_exit_savings(self):
        r = self._report(total_tokens=1500, saved_tokens=500, early_exit=True)
        s = compute_savings(r)
        assert s.tokens_saved == 500
        assert s.tokens_used == 1500
        assert s.percent_saved == pytest.approx(25.0, rel=0.01)
        assert s.early_exit is True

    def test_cost_usd_positive(self):
        r = self._report(total_tokens=2000)
        s = compute_savings(r, chain=["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-5-sonnet"])
        assert s.cost_usd > 0

    def test_zero_tokens_no_division_error(self):
        r = self._report(total_tokens=0)
        s = compute_savings(r)
        assert s.percent_saved == 0.0
        assert s.cost_usd == 0.0


# ── _rate_for_model ───────────────────────────────────────────

class TestRateForModel:
    def test_gpt4o_rate(self):
        rate = _rate_for_model("gpt-4o")
        assert rate > 0

    def test_gemini_flash_cheaper_than_gpt4(self):
        assert _rate_for_model("gemini/gemini-2.0-flash") < _rate_for_model("gpt-4o")

    def test_claude_opus_expensive(self):
        assert _rate_for_model("claude-3-opus-20240229") > _rate_for_model("claude-3-haiku-20240307")

    def test_unknown_model_returns_default(self):
        from consensusflow.core.scoring import _DEFAULT_RATE
        assert _rate_for_model("some-unknown-model-xyz") == _DEFAULT_RATE


# ── _estimate_cost_usd ────────────────────────────────────────

class TestEstimateCostUsd:
    def test_zero_tokens_zero_cost(self):
        assert _estimate_cost_usd(0) == 0.0

    def test_with_chain_uses_blended_rate(self):
        cost_with_chain = _estimate_cost_usd(1000, ["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-5-sonnet"])
        cost_without = _estimate_cost_usd(1000)
        # Both should be positive, values differ by model mix
        assert cost_with_chain > 0
        assert cost_without > 0

    def test_more_tokens_more_cost(self):
        assert _estimate_cost_usd(2000) > _estimate_cost_usd(1000)


# ── SavingsReport.__str__ ─────────────────────────────────────

class TestSavingsReportStr:
    def test_str_with_early_exit(self):
        s = SavingsReport(
            tokens_used=1000, tokens_saved=500,
            percent_saved=33.3, early_exit=True,
            cost_usd=0.01, saved_usd=0.005,
        )
        text = str(s)
        assert "Early Exit" in text
        assert "1,000" in text

    def test_str_without_early_exit(self):
        s = SavingsReport(tokens_used=1000, cost_usd=0.01)
        text = str(s)
        assert "1,000" in text
        assert "Early Exit" not in text
