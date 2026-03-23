"""
test_protocol.py — Unit tests for protocol.py data structures.
No API calls required.
"""

import pytest
from consensusflow.core.protocol import (
    AtomicClaim,
    ChainStatus,
    ClaimStatus,
    StepResult,
    VerificationReport,
)


class TestAtomicClaim:
    def test_default_id_generated(self):
        c = AtomicClaim(text="The Blue Mosque is free.")
        assert len(c.id) == 8

    def test_to_dict(self):
        c = AtomicClaim(
            text="Hagia Sophia opens at 9 AM.",
            status=ClaimStatus.CORRECTED,
            original_text="Hagia Sophia opens at 8 AM.",
            note="Corrected opening time.",
            confidence=0.95,
        )
        d = c.to_dict()
        assert d["status"] == "CORRECTED"
        assert d["original_text"] == "Hagia Sophia opens at 8 AM."
        assert d["confidence"] == 0.95

    def test_default_status_is_verified(self):
        c = AtomicClaim(text="Istanbul is in Turkey.")
        assert c.status == ClaimStatus.VERIFIED


class TestStepResult:
    def test_total_tokens(self):
        s = StepResult(
            step="proposer",
            model="gpt-4o",
            raw_text="Some text",
            prompt_tokens=100,
            completion_tokens=200,
        )
        assert s.total_tokens == 300

    def test_to_dict_has_timestamp(self):
        s = StepResult(step="auditor", model="gemini", raw_text="Audit")
        d = s.to_dict()
        assert "timestamp" in d
        assert "T" in d["timestamp"]  # ISO format


class TestVerificationReport:
    def _make_report(self):
        claims = [
            AtomicClaim(text="A", status=ClaimStatus.VERIFIED),
            AtomicClaim(text="B", status=ClaimStatus.CORRECTED),
            AtomicClaim(text="C", status=ClaimStatus.DISPUTED),
            AtomicClaim(text="D", status=ClaimStatus.NUANCED),
            AtomicClaim(text="E", status=ClaimStatus.VERIFIED),
        ]
        return VerificationReport(
            prompt="Test",
            atomic_claims=claims,
            status=ChainStatus.PARTIAL,
        )

    def test_counts(self):
        r = self._make_report()
        assert r.verified_count == 2
        assert r.corrected_count == 1
        assert r.disputed_count == 1
        assert r.nuanced_count == 1

    def test_to_dict_claim_summary(self):
        r = self._make_report()
        d = r.to_dict()
        assert d["claim_summary"]["verified"] == 2
        assert d["claim_summary"]["corrected"] == 1

    def test_run_id_is_uuid(self):
        r = VerificationReport()
        assert len(r.run_id) == 36  # UUID4 string length
