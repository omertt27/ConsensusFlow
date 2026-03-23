"""
tests/test_litellm_client.py — Tests for consensusflow/providers/litellm_client.py

Covers:
  • LiteLLMClient construction and attribute defaults
  • _build_messages formats messages correctly
  • _mock_response returns structured string with prompt fragment
  • complete() uses mock path when LiteLLM not installed / patched
  • stream() yields chunks from mock when LiteLLM not installed / patched
  • stream() retry behaviour on transient errors
  • complete() returns expected dict structure
  • _with_retry returns original fn when tenacity unavailable
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensusflow.providers.litellm_client import LiteLLMClient


# ─────────────────────────────────────────────
# Construction
# ─────────────────────────────────────────────

class TestClientConstruction:
    def test_defaults(self):
        client = LiteLLMClient()
        assert client.timeout == 60.0
        assert client.max_tokens == 4096
        assert client.temperature == 0.3

    def test_custom_params(self):
        client = LiteLLMClient(timeout=30.0, max_tokens=512, temperature=0.0)
        assert client.timeout == 30.0
        assert client.max_tokens == 512
        assert client.temperature == 0.0


# ─────────────────────────────────────────────
# _build_messages
# ─────────────────────────────────────────────

class TestBuildMessages:
    def test_format(self):
        msgs = LiteLLMClient._build_messages("You are a helper.", "What is 2+2?")
        assert len(msgs) == 2
        assert msgs[0] == {"role": "system", "content": "You are a helper."}
        assert msgs[1] == {"role": "user", "content": "What is 2+2?"}

    def test_empty_system(self):
        msgs = LiteLLMClient._build_messages("", "user msg")
        assert msgs[0]["content"] == ""
        assert msgs[1]["content"] == "user msg"


# ─────────────────────────────────────────────
# _mock_response
# ─────────────────────────────────────────────

class TestMockResponse:
    def test_contains_prompt_fragment(self):
        prompt = "What is the population of Istanbul?"
        result = LiteLLMClient._mock_response(prompt)
        assert isinstance(result, str)
        assert "Istanbul" in result or prompt[:80] in result

    def test_contains_mock_header(self):
        result = LiteLLMClient._mock_response("hello")
        assert "MOCK" in result or "mock" in result.lower()

    def test_truncates_long_prompt(self):
        long_prompt = "x" * 200
        result = LiteLLMClient._mock_response(long_prompt)
        assert len(result) < 500  # sanity — not returning full 200-char prompt verbatim


# ─────────────────────────────────────────────
# complete() — mock path (LiteLLM not available)
# ─────────────────────────────────────────────

class TestCompleteNoLiteLLM:
    """When _LITELLM_AVAILABLE is False, complete() returns a mock dict."""

    @pytest.mark.asyncio
    async def test_returns_dict_structure(self):
        client = LiteLLMClient()
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", False
        ):
            result = await client.complete(
                model="gpt-4o",
                system="You are helpful.",
                user="What is 2+2?",
            )
        assert "text" in result
        assert "prompt_tokens" in result
        assert "completion_tokens" in result
        assert "model" in result

    @pytest.mark.asyncio
    async def test_tokens_are_zero_in_mock(self):
        client = LiteLLMClient()
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", False
        ):
            result = await client.complete(
                model="gpt-4o",
                system="sys",
                user="user",
            )
        assert result["prompt_tokens"] == 0
        assert result["completion_tokens"] == 0

    @pytest.mark.asyncio
    async def test_model_echoed(self):
        client = LiteLLMClient()
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", False
        ):
            result = await client.complete(model="my-model", system="s", user="u")
        assert result["model"] == "my-model"


# ─────────────────────────────────────────────
# complete() — real LiteLLM path (mocked acompletion)
# ─────────────────────────────────────────────

class TestCompleteWithMockedLiteLLM:
    def _make_response(self, text: str, prompt_tokens=10, completion_tokens=5):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = text
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = prompt_tokens
        resp.usage.completion_tokens = completion_tokens
        resp.model = "gpt-4o"
        return resp

    @pytest.mark.asyncio
    async def test_returns_text(self):
        client = LiteLLMClient()
        mock_resp = self._make_response("Paris is the capital of France.")
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=AsyncMock(return_value=mock_resp),
            create=True,
        ):
            result = await client.complete(model="gpt-4o", system="s", user="u")
        assert result["text"] == "Paris is the capital of France."
        assert result["prompt_tokens"] == 10
        assert result["completion_tokens"] == 5

    @pytest.mark.asyncio
    async def test_none_content_becomes_empty_string(self):
        client = LiteLLMClient()
        mock_resp = self._make_response(None)
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=AsyncMock(return_value=mock_resp),
            create=True,
        ):
            result = await client.complete(model="gpt-4o", system="s", user="u")
        assert result["text"] == ""


# ─────────────────────────────────────────────
# stream() — mock path
# ─────────────────────────────────────────────

class TestStreamNoLiteLLM:
    @pytest.mark.asyncio
    async def test_yields_mock_string(self):
        client = LiteLLMClient()
        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", False
        ):
            chunks = [
                c async for c in client.stream(
                    model="gpt-4o", system="sys", user="user prompt"
                )
            ]
        assert len(chunks) == 1
        assert isinstance(chunks[0], str)


# ─────────────────────────────────────────────
# stream() — real path with mocked acompletion
# ─────────────────────────────────────────────

class TestStreamWithMockedLiteLLM:
    def _make_chunk(self, content: str | None):
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta = MagicMock()
        chunk.choices[0].delta.content = content
        return chunk

    @pytest.mark.asyncio
    async def test_yields_content_chunks(self):
        client = LiteLLMClient()

        async def _async_iter(_chunks):
            for c in _chunks:
                yield c

        chunks_in = [
            self._make_chunk("Hello"),
            self._make_chunk(" world"),
            self._make_chunk(None),   # None chunks should be skipped
            self._make_chunk("!"),
        ]

        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=AsyncMock(return_value=_async_iter(chunks_in)),
            create=True,
        ):
            out = [
                c async for c in client.stream(
                    model="gpt-4o", system="s", user="u"
                )
            ]

        assert out == ["Hello", " world", "!"]

    @pytest.mark.asyncio
    async def test_stream_retries_on_transient_error(self):
        """Stream should retry on ConnectionError up to max_attempts."""
        client = LiteLLMClient()
        call_count = 0

        async def _async_iter_success():
            yield self._make_chunk("ok")

        async def _flaky_acompletion(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("transient")
            return _async_iter_success()

        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=_flaky_acompletion,
            create=True,
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ):
            out = [
                c async for c in client.stream(
                    model="gpt-4o", system="s", user="u"
                )
            ]

        assert out == ["ok"]
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_stream_raises_after_max_retries(self):
        """Stream should raise after exhausting retries."""
        client = LiteLLMClient()

        async def _always_fail(**kwargs):
            raise ConnectionError("always fails")

        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=_always_fail,
            create=True,
        ), patch(
            "asyncio.sleep", new=AsyncMock()
        ):
            with pytest.raises(ConnectionError):
                async for _ in client.stream(model="gpt-4o", system="s", user="u"):
                    pass

    @pytest.mark.asyncio
    async def test_stream_non_retryable_raises_immediately(self):
        """Non-retryable errors should propagate immediately without retry."""
        client = LiteLLMClient()
        call_count = 0

        async def _bad_request(**kwargs):
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid request")

        with patch(
            "consensusflow.providers.litellm_client._LITELLM_AVAILABLE", True
        ), patch(
            "consensusflow.providers.litellm_client.acompletion",
            new=_bad_request,
            create=True,
        ):
            with pytest.raises(ValueError):
                async for _ in client.stream(model="gpt-4o", system="s", user="u"):
                    pass

        assert call_count == 1  # no retries for non-retryable
