"""
test_engine.py — Unit tests for the SequentialChain engine.
Uses mock LiteLLM responses (no real API calls).
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import patch

from consensusflow.core.engine import (
    SequentialChain,
    _jaccard_similarity,
    _parse_claims_from_json,
    _parse_audit_from_json,
    _extract_json_array,
)
from consensusflow.core.protocol import AtomicClaim, ChainStatus, ClaimStatus


# ── Similarity scorer ─────────────────────────────────────────

class TestJaccardSimilarity:
    def test_identical_texts(self):
        assert _jaccard_similarity("hello world", "hello world") == 1.0

    def test_empty_strings(self):
        assert _jaccard_similarity("", "") == 1.0

    def test_no_overlap(self):
        score = _jaccard_similarity("cat dog", "fish bird")
        assert score == 0.0

    def test_partial_overlap(self):
        score = _jaccard_similarity("cat dog fish", "cat bird snake")
        assert 0 < score < 1

    def test_case_insensitive(self):
        assert _jaccard_similarity("Hello World", "hello world") == 1.0


# ── Claim parser ─────────────────────────────────────────────

class TestParseClaimsFromJson:
    def test_valid_json_array(self):
        raw = '[{"text": "Istanbul is in Turkey.", "confidence": 0.99}]'
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 1
        assert claims[0].text == "Istanbul is in Turkey."
        assert claims[0].confidence == 0.99

    def test_markdown_fenced_json(self):
        raw = '```json\n[{"text": "Hagia Sophia is free.", "confidence": 0.9}]\n```'
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 1

    def test_plain_string_array(self):
        raw = '[{"text": "Claim A"}, {"text": "Claim B"}]'
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 2

    def test_fallback_line_split(self):
        raw = "Claim one\nClaim two\nClaim three"
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 3
        assert claims[0].text == "Claim one"


# ── Audit parser ─────────────────────────────────────────────

class TestParseAuditFromJson:
    def test_corrected_claim(self):
        original = [AtomicClaim(id="abc123", text="Blue Mosque costs €10")]
        raw = json.dumps([{
            "id": "abc123",
            "status": "CORRECTED",
            "text": "Blue Mosque is free.",
            "note": "Always been free.",
            "confidence": 0.98,
        }])
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.CORRECTED
        assert result[0].text == "Blue Mosque is free."
        assert result[0].original_text == "Blue Mosque costs €10"

    def test_verified_claim(self):
        original = [AtomicClaim(id="xyz789", text="Istanbul is in Turkey.")]
        raw = json.dumps([{
            "id": "xyz789", "status": "VERIFIED",
            "text": "Istanbul is in Turkey.", "note": "Correct.", "confidence": 1.0,
        }])
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.VERIFIED

    def test_fallback_on_invalid_json(self):
        original = [AtomicClaim(text="Some claim")]
        result = _parse_audit_from_json("not valid json {{", original)
        assert result[0].status == ClaimStatus.DISPUTED

    # ── Gemini output pattern robustness ────────────────────────────
    def _make_claims(self, ids=("c1",)):
        return [AtomicClaim(id=cid, text=f"claim {cid}") for cid in ids]

    def test_fenced_json_is_parsed(self):
        """Gemini wraps output in ```json ... ``` fences."""
        original = self._make_claims(["c1"])
        original[0].id = "c1"
        raw = '```json\n[{"id": "c1", "status": "VERIFIED", "note": "ok", "confidence": 1.0}]\n```'
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.VERIFIED

    def test_prose_before_fence_is_parsed(self):
        """Gemini sometimes outputs prose then a fenced block."""
        original = self._make_claims(["c1"])
        raw = 'Here is my audit:\n```json\n[{"id": "c1", "status": "CORRECTED", "text": "fixed", "note": "n", "confidence": 0.9}]\n```'
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.CORRECTED

    def test_trailing_prose_after_fence_is_parsed(self):
        """Gemini sometimes appends prose after the closing fence."""
        original = self._make_claims(["c1"])
        raw = '```json\n[{"id": "c1", "status": "NUANCED", "note": "ctx", "confidence": 0.8}]\n```\nHope this helps!'
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.NUANCED

    def test_unfenced_array_in_prose_is_parsed(self):
        """No fences, but array is embedded in prose."""
        original = self._make_claims(["c1"])
        raw = 'My assessment: [{"id": "c1", "status": "VERIFIED", "note": "ok", "confidence": 1.0}] — done.'
        result = _parse_audit_from_json(raw, original)
        assert result[0].status == ClaimStatus.VERIFIED


# ── _extract_json_array ──────────────────────────────────────

class TestExtractJsonArray:
    def test_plain_array(self):
        assert _extract_json_array('[1,2,3]') == '[1,2,3]'

    def test_fenced_block(self):
        raw = '```json\n[1,2,3]\n```'
        assert json.loads(_extract_json_array(raw)) == [1, 2, 3]

    def test_prose_before_fence(self):
        raw = 'Here:\n```json\n[1]\n```'
        assert json.loads(_extract_json_array(raw)) == [1]

    def test_trailing_text_after_fence(self):
        raw = '```json\n[1]\n```\nDone.'
        assert json.loads(_extract_json_array(raw)) == [1]

    def test_no_fence_array_in_prose(self):
        raw = 'Result: [42] end'
        assert json.loads(_extract_json_array(raw)) == [42]

    def test_nested_array(self):
        raw = '```json\n[[1,2],[3,4]]\n```'
        assert json.loads(_extract_json_array(raw)) == [[1, 2], [3, 4]]


# ── SequentialChain ───────────────────────────────────────────

MOCK_PROPOSER_RESPONSE = {
    "text": "The Blue Mosque is free to enter. It was built in 1616.",
    "prompt_tokens": 50,
    "completion_tokens": 30,
    "model": "gpt-4o",
}

MOCK_EXTRACTOR_RESPONSE = {
    "text": json.dumps([
        {"text": "The Blue Mosque is free to enter.", "confidence": 0.99},
        {"text": "The Blue Mosque was built in 1616.", "confidence": 0.95},
    ]),
    "prompt_tokens": 40,
    "completion_tokens": 20,
    "model": "gpt-4o-mini",
}

MOCK_AUDITOR_RESPONSE = {
    "text": json.dumps([
        {"id": None, "status": "VERIFIED",
         "text": "The Blue Mosque is free to enter.",
         "note": "Confirmed.", "confidence": 1.0},
        {"id": None, "status": "VERIFIED",
         "text": "The Blue Mosque was built in 1616.",
         "note": "Confirmed.", "confidence": 1.0},
    ]),
    "prompt_tokens": 60,
    "completion_tokens": 40,
    "model": "gemini",
}


class TestSequentialChain:
    def _make_chain(self):
        return SequentialChain(
            chain=["gpt-4o", "gemini/gemini-2.5-flash", "claude-3-7-sonnet"],
            extractor_model="gpt-4o-mini",
            similarity_threshold=0.92,
        )

    @pytest.mark.asyncio
    async def test_early_exit_on_high_similarity(self):
        chain = self._make_chain()

        async def mock_complete(model, system, user, **_):
            if "gpt-4o-mini" in model or "extractor" in system.lower():
                return MOCK_EXTRACTOR_RESPONSE
            elif "gemini" in model:
                return MOCK_AUDITOR_RESPONSE
            else:
                return MOCK_PROPOSER_RESPONSE

        with patch.object(chain._client, "complete", side_effect=mock_complete):
            report = await chain.run("Is the Blue Mosque free?")

        # Auditor and proposer have very similar text → early exit
        assert report.status == ChainStatus.EARLY_EXIT
        assert report.early_exit is True
        assert report.final_answer != ""

    def test_invalid_chain_length_raises(self):
        from consensusflow.exceptions import ChainConfigError
        # 2-model chains are now valid; only 0 or 4+ models should raise
        with pytest.raises(ChainConfigError):
            SequentialChain(chain=["gpt-4o", "gemini", "claude", "extra"])

    def test_two_model_chain_expands_to_three(self):
        """2-model [proposer, auditor] should auto-expand resolver = proposer."""
        sc = SequentialChain(chain=["gpt-4o", "gemini/gemini-2.5-flash"])
        assert sc.chain == ["gpt-4o", "gemini/gemini-2.5-flash", "gpt-4o"]
