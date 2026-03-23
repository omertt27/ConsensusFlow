"""
tests/test_models.py — Tests for consensusflow/core/models.py

Covers:
  • AtomicClaimSchema validation (blank text, coerce status)
  • StepResultSchema.total_tokens property
  • GotchaScoreSchema catches ≤ total_claims constraint
  • SavingsReportSchema bounds
  • VerificationReportSchema computed properties
  • VerifyRequestSchema chain-length validation
  • report_to_schema conversion helper (Pydantic + shim paths)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from consensusflow.core.models import (
    AtomicClaimSchema,
    StepResultSchema,
    GotchaScoreSchema,
    SavingsReportSchema,
    VerificationReportSchema,
    VerifyRequestSchema,
    report_to_schema,
    _PYDANTIC_AVAILABLE,
)
from consensusflow.core.protocol import ClaimStatus, ChainStatus


# ─────────────────────────────────────────────
# AtomicClaimSchema
# ─────────────────────────────────────────────

class TestAtomicClaimSchema:
    def test_basic_construction(self):
        c = AtomicClaimSchema(text="The Eiffel Tower is 330 m tall.")
        assert c.text == "The Eiffel Tower is 330 m tall."
        assert c.status == ClaimStatus.VERIFIED
        assert c.confidence == 1.0

    def test_defaults(self):
        c = AtomicClaimSchema(text="Some claim.")
        assert c.id is None
        assert c.original_text is None
        assert c.note is None

    def test_confidence_bounds(self):
        c = AtomicClaimSchema(text="claim", confidence=0.75)
        assert c.confidence == 0.75

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_blank_text_rejected(self):
        with pytest.raises(Exception):
            AtomicClaimSchema(text="   ")

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_text_stripped(self):
        c = AtomicClaimSchema(text="  hello world  ")
        assert c.text == "hello world"

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_status_coerced_from_string(self):
        c = AtomicClaimSchema(text="claim", status="corrected")
        assert c.status == ClaimStatus.CORRECTED

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_invalid_status_becomes_disputed(self):
        c = AtomicClaimSchema(text="claim", status="nonsense")
        assert c.status == ClaimStatus.DISPUTED


# ─────────────────────────────────────────────
# StepResultSchema
# ─────────────────────────────────────────────

class TestStepResultSchema:
    def test_total_tokens(self):
        s = StepResultSchema(
            step="proposer",
            model="gpt-4o",
            raw_text="answer",
            prompt_tokens=100,
            completion_tokens=50,
        )
        assert s.total_tokens == 150

    def test_defaults(self):
        s = StepResultSchema(step="auditor", model="gpt-4o-mini", raw_text="ok")
        assert s.prompt_tokens == 0
        assert s.completion_tokens == 0
        assert s.latency_ms == 0.0
        assert s.timestamp is None

    def test_with_timestamp(self):
        ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
        s = StepResultSchema(step="resolver", model="claude-3", raw_text="x", timestamp=ts)
        assert s.timestamp == ts


# ─────────────────────────────────────────────
# GotchaScoreSchema
# ─────────────────────────────────────────────

class TestGotchaScoreSchema:
    def _make(self, total=5, catches=3):
        return GotchaScoreSchema(
            score=70,
            grade="B",
            label="Good",
            emoji="🟢",
            total_claims=total,
            catches=catches,
        )

    def test_basic(self):
        gs = self._make()
        assert gs.score == 70
        assert gs.grade == "B"
        assert gs.catches == 3

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_catches_exceeds_total_rejected(self):
        with pytest.raises(Exception):
            GotchaScoreSchema(
                score=50, grade="C", label="Meh", emoji="🟡",
                total_claims=3, catches=5,
            )

    def test_penalty_breakdown_default(self):
        gs = self._make()
        assert gs.penalty_breakdown == {}

    def test_failure_taxonomy_default(self):
        gs = self._make()
        assert gs.failure_taxonomy == {}


# ─────────────────────────────────────────────
# SavingsReportSchema
# ─────────────────────────────────────────────

class TestSavingsReportSchema:
    def test_defaults(self):
        s = SavingsReportSchema()
        assert s.tokens_used == 0
        assert s.tokens_saved == 0
        assert s.percent_saved == 0.0
        assert s.early_exit is False
        assert s.cost_usd == 0.0
        assert s.saved_usd == 0.0

    def test_custom_values(self):
        s = SavingsReportSchema(tokens_used=1000, tokens_saved=500, percent_saved=50.0,
                                early_exit=True, cost_usd=0.05, saved_usd=0.02)
        assert s.tokens_saved == 500
        assert s.early_exit is True


# ─────────────────────────────────────────────
# VerificationReportSchema
# ─────────────────────────────────────────────

class TestVerificationReportSchema:
    def _make_report(self):
        claims = [
            AtomicClaimSchema(text="claim one", status=ClaimStatus.VERIFIED),
            AtomicClaimSchema(text="claim two", status=ClaimStatus.CORRECTED),
            AtomicClaimSchema(text="claim three", status=ClaimStatus.REJECTED),
        ]
        return VerificationReportSchema(
            run_id="test-run",
            prompt="test prompt",
            chain_models=["m1", "m2", "m3"],
            status=ChainStatus.SUCCESS,
            atomic_claims=claims,
        )

    def test_verified_count(self):
        r = self._make_report()
        assert r.verified_count == 1

    def test_corrected_count(self):
        r = self._make_report()
        assert r.corrected_count == 1

    def test_rejected_count(self):
        r = self._make_report()
        assert r.rejected_count == 1

    def test_defaults(self):
        r = VerificationReportSchema(
            run_id="x", prompt="p", chain_models=["a", "b", "c"],
            status=ChainStatus.SUCCESS,
        )
        assert r.final_answer == ""
        assert r.similarity_score == 0.0
        assert r.early_exit is False
        assert r.atomic_claims == []
        assert r.gotcha_score is None
        assert r.savings is None
        assert r.steps == {}


# ─────────────────────────────────────────────
# VerifyRequestSchema
# ─────────────────────────────────────────────

class TestVerifyRequestSchema:
    def test_basic(self):
        req = VerifyRequestSchema(prompt="What is the capital of France?")
        assert req.prompt == "What is the capital of France?"
        assert req.extractor_model == "gpt-4o-mini"
        assert req.similarity_threshold == 0.92
        assert req.stream is False

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_chain_wrong_length_rejected(self):
        with pytest.raises(Exception):
            VerifyRequestSchema(prompt="test", chain=["m1", "m2"])

    @pytest.mark.skipif(not _PYDANTIC_AVAILABLE, reason="Pydantic not installed")
    def test_chain_correct_length_accepted(self):
        req = VerifyRequestSchema(prompt="test", chain=["m1", "m2", "m3"])
        assert req.chain == ["m1", "m2", "m3"]

    def test_none_chain_allowed(self):
        req = VerifyRequestSchema(prompt="test", chain=None)
        assert req.chain is None


# ─────────────────────────────────────────────
# report_to_schema conversion
# ─────────────────────────────────────────────

class TestReportToSchema:
    def _make_protocol_report(self):
        """Build a real protocol.VerificationReport for conversion."""
        from consensusflow.core.protocol import (
            VerificationReport,
            AtomicClaim,
            StepResult,
            ChainStatus,
            ClaimStatus,
        )
        report = VerificationReport(
            prompt="test",
            chain_models=["a", "b", "c"],
        )
        report.run_id = "fixed-run-id"
        report.status = ChainStatus.SUCCESS
        report.final_answer = "The answer is 42."
        report.atomic_claims = [
            AtomicClaim(text="claim A", status=ClaimStatus.VERIFIED),
            AtomicClaim(text="claim B", status=ClaimStatus.CORRECTED,
                        original_text="old B", note="corrected note"),
        ]
        step = StepResult(step="proposer", model="gpt-4o", raw_text="raw text")
        step.prompt_tokens = 100
        step.completion_tokens = 50
        step.latency_ms = 100.0
        report.proposer_result = step
        return report

    def test_converts_successfully(self):
        report = self._make_protocol_report()
        schema = report_to_schema(report)
        assert schema.run_id == "fixed-run-id"
        assert schema.prompt == "test"
        assert schema.chain_models == ["a", "b", "c"]
        assert schema.status == ChainStatus.SUCCESS
        assert schema.final_answer == "The answer is 42."

    def test_claims_converted(self):
        report = self._make_protocol_report()
        schema = report_to_schema(report)
        assert len(schema.atomic_claims) == 2
        assert schema.atomic_claims[0].text == "claim A"
        assert schema.atomic_claims[1].note == "corrected note"

    def test_step_converted(self):
        report = self._make_protocol_report()
        schema = report_to_schema(report)
        proposer = schema.steps["proposer"]
        assert proposer is not None
        assert proposer.model == "gpt-4o"
        assert proposer.prompt_tokens == 100
        assert proposer.total_tokens == 150

    def test_none_steps_handled(self):
        report = self._make_protocol_report()
        schema = report_to_schema(report)
        assert schema.steps["auditor"] is None
        assert schema.steps["resolver"] is None

    def test_with_gotcha_score(self):
        from consensusflow.core.scoring import compute_gotcha_score
        report = self._make_protocol_report()
        gs = compute_gotcha_score(report)
        schema = report_to_schema(report, gotcha_score=gs)
        assert schema.gotcha_score is not None
        assert 0 <= schema.gotcha_score.score <= 100

    def test_with_savings(self):
        from consensusflow.core.scoring import compute_savings
        report = self._make_protocol_report()
        savings = compute_savings(report)
        schema = report_to_schema(report, savings=savings)
        assert schema.savings is not None
        assert schema.savings.cost_usd >= 0.0
