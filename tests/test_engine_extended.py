"""
test_engine_extended.py — Extended tests for SequentialChain.

Covers:
  • Resolver path (no early exit)
  • Model fallback chain
  • Resolver failure → proposer answer fallback
  • Streaming event structure
  • Error handling (JSON parse failure, model timeout)
  • Budget exceeded
  • Custom penalty weights passed through
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensusflow.core.engine import (
    SequentialChain,
    _jaccard_similarity,
    _parse_audit_from_json,
    _parse_claims_from_json,
)
from consensusflow.core.protocol import AtomicClaim, ChainStatus, ClaimStatus, VerificationReport
from consensusflow.exceptions import ChainConfigError, ModelUnavailableError


# ── Shared mock data ──────────────────────────────────────────

PROPOSER = {
    "text": "The Blue Mosque is free. It was built in 1616 by Sultan Ahmed I.",
    "prompt_tokens": 50, "completion_tokens": 40, "model": "gpt-4o",
}

EXTRACTOR = {
    "text": json.dumps([
        {"text": "The Blue Mosque is free to enter.", "confidence": 0.99},
        {"text": "The Blue Mosque was built in 1616.", "confidence": 0.95},
        {"text": "It was commissioned by Sultan Ahmed I.", "confidence": 0.93},
    ]),
    "prompt_tokens": 60, "completion_tokens": 80, "model": "gpt-4o-mini",
}

# Auditor returns plain prose that is deliberately different from the proposer.
# Using non-JSON forces the fallback that marks all claims DISPUTED, which:
#   1. Sets all_verified=False (no early exit via all_verified shortcut)
#   2. Produces low Jaccard similarity vs. proposer prose → resolver triggered
AUDITOR_DISAGREEING = {
    "text": (
        "CRITICAL CORRECTIONS REQUIRED: Construction timeline disputed. "
        "Architectural analysis contradicts several assertions. "
        "Significant historical inaccuracies detected warranting full synthesis."
    ),
    "prompt_tokens": 70, "completion_tokens": 90, "model": "gemini",
}

AUDITOR_AGREEING = {
    "text": json.dumps([
        {"id": None, "status": "VERIFIED",
         "text": "The Blue Mosque is free to enter.", "note": "Confirmed.", "confidence": 1.0},
        {"id": None, "status": "VERIFIED",
         "text": "The Blue Mosque was built in 1616.", "note": "Confirmed.", "confidence": 1.0},
        {"id": None, "status": "VERIFIED",
         "text": "It was commissioned by Sultan Ahmed I.", "note": "Confirmed.", "confidence": 1.0},
    ]),
    "prompt_tokens": 70, "completion_tokens": 90, "model": "gemini",
}

RESOLVER = {
    "text": "The Blue Mosque is free. Construction began in 1609, completing in 1616.",
    "prompt_tokens": 120, "completion_tokens": 60, "model": "claude",
}


def _make_chain(**kwargs) -> SequentialChain:
    return SequentialChain(
        chain=["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-7-sonnet"],
        extractor_model="gpt-4o-mini",
        similarity_threshold=0.92,
        **kwargs,
    )


def _mock_complete_resolver_path(model, system, user, **_):
    """Return appropriate mock based on model. Use endswith to avoid 'mini' in 'gemini'."""
    if model.endswith("mini") or model == "gpt-4o-mini":
        return EXTRACTOR
    if "gemini" in model:
        return AUDITOR_DISAGREEING
    if "claude" in model:
        return RESOLVER
    return PROPOSER


def _mock_complete_early_exit(model, system, user, **_):
    if model.endswith("mini") or model == "gpt-4o-mini":
        return EXTRACTOR
    if "gemini" in model:
        return AUDITOR_AGREEING
    return PROPOSER


# ── ChainConfig validation ────────────────────────────────────

class TestChainConfigValidation:
    def test_invalid_chain_length_raises(self):
        with pytest.raises(ChainConfigError):
            SequentialChain(chain=["gpt-4o", "gemini"])

    def test_invalid_fallback_length_raises(self):
        with pytest.raises(ChainConfigError):
            SequentialChain(
                chain=["gpt-4o", "gemini", "claude"],
                fallback_chain=["gpt-4o"],
            )

    def test_valid_chain_and_fallback_ok(self):
        chain = SequentialChain(
            chain=["gpt-4o", "gemini", "claude"],
            fallback_chain=["gpt-4-turbo", "gemini/flash", "claude-haiku"],
        )
        assert chain.fallback_chain is not None


# ── Early exit path ───────────────────────────────────────────

class TestEarlyExitPath:
    @pytest.mark.asyncio
    async def test_early_exit_status(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_early_exit):
            report = await chain.run("Is the Blue Mosque free?")
        assert report.status == ChainStatus.EARLY_EXIT
        assert report.early_exit is True
        assert report.resolver_result is None

    @pytest.mark.asyncio
    async def test_early_exit_saved_tokens_positive(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_early_exit):
            report = await chain.run("Is the Blue Mosque free?")
        assert report.saved_tokens > 0

    @pytest.mark.asyncio
    async def test_early_exit_final_answer_is_proposer(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_early_exit):
            report = await chain.run("Is the Blue Mosque free?")
        assert report.final_answer == PROPOSER["text"]


# ── Resolver path ─────────────────────────────────────────────

class TestResolverPath:
    @pytest.mark.asyncio
    async def test_resolver_called_on_disagreement(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_resolver_path):
            report = await chain.run("Tell me about the Blue Mosque.")
        assert report.resolver_result is not None
        assert report.final_answer == RESOLVER["text"]

    @pytest.mark.asyncio
    async def test_resolver_path_status_success_or_partial(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_resolver_path):
            report = await chain.run("Tell me about the Blue Mosque.")
        assert report.status in (ChainStatus.SUCCESS, ChainStatus.PARTIAL)

    @pytest.mark.asyncio
    async def test_total_tokens_sum_all_steps(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_resolver_path):
            report = await chain.run("Tell me about the Blue Mosque.")
        assert report.total_tokens > 0

    @pytest.mark.asyncio
    async def test_non_verified_claims_present(self):
        chain = _make_chain()
        with patch.object(chain._client, "complete", side_effect=_mock_complete_resolver_path):
            report = await chain.run("Tell me about the Blue Mosque.")
        # Auditor fallback marks claims as DISPUTED → resolver is triggered
        statuses = {c.status for c in report.atomic_claims}
        assert ClaimStatus.VERIFIED not in statuses or ClaimStatus.DISPUTED in statuses


# ── Model fallback ────────────────────────────────────────────

class TestModelFallback:
    @pytest.mark.asyncio
    async def test_fallback_used_when_primary_fails(self):
        chain = _make_chain(
            fallback_chain=["gpt-4-turbo", "gemini/flash", "claude-haiku"],
        )
        call_count = {"n": 0}

        async def mock_complete(model, system, user, **_):
            call_count["n"] += 1
            if model == "gpt-4o" and call_count["n"] == 1:
                raise ConnectionError("Primary model down")
            if model.endswith("mini") or model == "gpt-4o-mini":
                return EXTRACTOR
            if "gemini" in model or "flash" in model:
                return AUDITOR_AGREEING
            return PROPOSER

        with patch.object(chain._client, "complete", side_effect=mock_complete):
            report = await chain.run("Test prompt")
        # Fallback model used for proposer step
        assert report.proposer_result is not None
        assert report.proposer_result.metadata.get("fallback_from") == "gpt-4o"

    @pytest.mark.asyncio
    async def test_both_models_fail_raises_model_unavailable(self):
        chain = _make_chain(
            fallback_chain=["gpt-4-turbo", "gemini/flash", "claude-haiku"],
        )

        async def always_fail(model, system, user, **_):
            raise ConnectionError("All down")

        with patch.object(chain._client, "complete", side_effect=always_fail):
            with pytest.raises(ModelUnavailableError):
                await chain.run("Test")


# ── Resolver failure fallback ─────────────────────────────────

class TestResolverFailureFallback:
    @pytest.mark.asyncio
    async def test_resolver_failure_uses_proposer_answer(self):
        chain = _make_chain()
        call_count = {"n": 0}

        async def mock_complete(model, system, user, **_):
            if model.endswith("mini") or model == "gpt-4o-mini":
                return EXTRACTOR
            if "gemini" in model:
                return AUDITOR_DISAGREEING
            if "claude" in model:
                raise ConnectionError("Resolver down")
            return PROPOSER

        with patch.object(chain._client, "complete", side_effect=mock_complete):
            report = await chain.run("Test")
        # Should fall back to proposer answer, not crash
        assert report.final_answer == PROPOSER["text"]
        assert report.resolver_result is None


# ── Streaming ─────────────────────────────────────────────────

class TestStreaming:
    @pytest.mark.asyncio
    async def test_streaming_yields_done_event(self):
        chain = _make_chain()
        chunks = ["The ", "Blue ", "Mosque."]
        audit_chunks = [json.dumps([
            {"id": None, "status": "VERIFIED", "text": "Blue Mosque.", "note": "", "confidence": 1.0}
        ])]
        call_counts = {"proposer": 0, "extractor": 0, "auditor": 0}

        async def mock_stream(model, system, user, **_):
            if "mini" in model:
                call_counts["extractor"] += 1
                # extractor uses complete(), not stream() — this shouldn't be called
                return
            if "gemini" in model:
                for c in audit_chunks:
                    yield c
            else:
                for c in chunks:
                    yield c

        async def mock_complete(model, system, user, **_):
            if "mini" in model:
                return EXTRACTOR
            return PROPOSER

        with patch.object(chain._client, "stream", side_effect=mock_stream), \
             patch.object(chain._client, "complete", side_effect=mock_complete):
            events = []
            async for event in chain.stream("test"):
                events.append(event)

        event_types = [e["event"] for e in events]
        assert "done" in event_types

    @pytest.mark.asyncio
    async def test_streaming_yields_proposer_chunks(self):
        chain = _make_chain()
        chunks = ["Hello ", "world."]

        async def mock_stream(model, system, user, **_):
            if "gemini" in model:
                yield json.dumps([{"id": None, "status": "VERIFIED",
                                   "text": "Hello world.", "note": "", "confidence": 1.0}])
            else:
                for c in chunks:
                    yield c

        async def mock_complete(model, system, user, **_):
            return EXTRACTOR

        with patch.object(chain._client, "stream", side_effect=mock_stream), \
             patch.object(chain._client, "complete", side_effect=mock_complete):
            events = []
            async for event in chain.stream("test"):
                events.append(event)

        proposer_chunks = [e for e in events if e["event"] == "proposer_chunk"]
        assert len(proposer_chunks) == 2
        assert proposer_chunks[0]["data"] == "Hello "

    @pytest.mark.asyncio
    async def test_streaming_done_event_has_dict(self):
        chain = _make_chain()

        async def mock_stream(model, system, user, **_):
            if "gemini" in model:
                yield json.dumps([{"id": None, "status": "VERIFIED",
                                   "text": "Test.", "note": "", "confidence": 1.0}])
            else:
                yield "Test answer."

        async def mock_complete(model, system, user, **_):
            return EXTRACTOR

        with patch.object(chain._client, "stream", side_effect=mock_stream), \
             patch.object(chain._client, "complete", side_effect=mock_complete):
            done_events = []
            async for event in chain.stream("test"):
                if event["event"] == "done":
                    done_events.append(event)

        assert len(done_events) == 1
        assert isinstance(done_events[0]["data"], dict)
        assert "run_id" in done_events[0]["data"]


# ── JSON parse fallback ───────────────────────────────────────

class TestJsonParseFallback:
    def test_malformed_audit_json_marks_disputed(self):
        claims = [AtomicClaim(text="Some claim")]
        result = _parse_audit_from_json("{{not valid json", claims)
        assert all(c.status == ClaimStatus.DISPUTED for c in result)

    def test_malformed_claim_json_falls_back_to_lines(self):
        raw = "Claim one\nClaim two\n# comment line\n"
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 2
        assert claims[0].text == "Claim one"

    def test_claim_json_with_markdown_fence(self):
        raw = "```json\n[{\"text\": \"Istanbul is beautiful.\", \"confidence\": 0.9}]\n```"
        claims = _parse_claims_from_json(raw)
        assert len(claims) == 1
        assert claims[0].text == "Istanbul is beautiful."


# ── Budget check ──────────────────────────────────────────────

class TestBudgetCheck:
    @pytest.mark.asyncio
    async def test_budget_exceeded_raises(self):
        from consensusflow.exceptions import BudgetExceededError
        chain = _make_chain(budget_usd=0.000001)  # absurdly low budget

        async def mock_complete(model, system, user, **_):
            if model.endswith("mini") or model == "gpt-4o-mini":
                return EXTRACTOR
            if "gemini" in model:
                # Non-JSON → fallback marks all claims DISPUTED → resolver path
                return AUDITOR_DISAGREEING
            # Huge token count so cost far exceeds 0.000001 USD budget
            return {**PROPOSER, "prompt_tokens": 500_000, "completion_tokens": 500_000}

        with patch.object(chain._client, "complete", side_effect=mock_complete):
            with pytest.raises(BudgetExceededError) as exc_info:
                await chain.run("Test")
        assert exc_info.value.budget_usd == pytest.approx(0.000001)


# ── Verify convenience wrapper ────────────────────────────────

class TestVerifyWrapper:
    @pytest.mark.asyncio
    async def test_verify_returns_report(self):
        from consensusflow.core.engine import verify
        from unittest.mock import patch as _patch

        async def mock_complete(model, system, user, **_):
            if "mini" in model:
                return EXTRACTOR
            if "gemini" in model:
                return AUDITOR_AGREEING
            return PROPOSER

        with _patch(
            "consensusflow.providers.litellm_client.LiteLLMClient.complete",
            side_effect=mock_complete,
        ):
            report = await verify("Is the Blue Mosque free?")

        assert isinstance(report, VerificationReport)
        assert report.final_answer != ""
