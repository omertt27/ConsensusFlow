"""
litellm_client.py — Thin async wrapper around LiteLLM.

Supports:
  • Single completion  (complete)
  • Async streaming    (stream) with retry
  • Automatic retries  (tenacity, retryable exceptions only)
  • Token usage extraction
  • Retry attempt logging
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, AsyncIterator, Dict, Optional

log = logging.getLogger("consensusflow.litellm_client")

# LiteLLM is imported lazily so the SDK remains importable even before
# the package is installed (useful for type-checking).
try:
    import litellm
    from litellm import acompletion          # async completion
    _LITELLM_AVAILABLE = True
except ImportError:  # pragma: no cover
    _LITELLM_AVAILABLE = False
    log.warning(
        "litellm is not installed. Run `pip install litellm` to enable "
        "live model calls."
    )

try:
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
        before_sleep_log,
    )
    _TENACITY_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TENACITY_AVAILABLE = False


# ─────────────────────────────────────────────
# Retryable exception types
# ─────────────────────────────────────────────

# Only retry transient failures. Auth errors and invalid-request errors
# are not retryable — retrying them just wastes quota and adds latency.
_RETRYABLE_BASE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
)

try:
    # LiteLLM-specific transient errors (available when litellm is installed)
    from litellm.exceptions import (
        RateLimitError,
        ServiceUnavailableError,
        Timeout as LiteLLMTimeout,
    )
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_BASE_EXCEPTIONS + (
        RateLimitError,
        ServiceUnavailableError,
        LiteLLMTimeout,
    )
except ImportError:
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_BASE_EXCEPTIONS


# ─────────────────────────────────────────────
# Retry decorator (graceful fallback if tenacity absent)
# ─────────────────────────────────────────────

def _with_retry(fn):
    """Wrap an async function with exponential back-off retry if tenacity available."""
    if not _TENACITY_AVAILABLE:
        return fn
    return retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=before_sleep_log(log, logging.WARNING),
    )(fn)



# ─────────────────────────────────────────────
# Client
# ─────────────────────────────────────────────

class LiteLLMClient:
    """
    Async gateway to 100+ LLM providers via LiteLLM.

    Environment variables recognised (loaded from .env automatically
    when python-dotenv is installed):
        OPENAI_API_KEY
        ANTHROPIC_API_KEY
        GEMINI_API_KEY  (or GOOGLE_API_KEY)
        COHERE_API_KEY
        … (any key supported by LiteLLM)
    """

    def __init__(
        self,
        timeout: float = 60.0,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        self.timeout     = timeout
        self.max_tokens  = max_tokens
        self.temperature = temperature

        # Try to load .env if python-dotenv is available
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
            log.debug(".env loaded successfully.")
        except ImportError:
            pass  # optional dependency

        if _LITELLM_AVAILABLE:
            # Suppress verbose LiteLLM logging unless debug mode is on
            litellm.set_verbose = os.getenv("CONSENSUSFLOW_DEBUG", "0") == "1"
            # Drop unsupported params silently (e.g. when swapping providers)
            litellm.drop_params = True

    # ── Public interface ─────────────────────

    async def complete(
        self,
        model: str,
        system: str,
        user: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Single async completion.

        Returns::
            {
                "text": str,
                "prompt_tokens": int,
                "completion_tokens": int,
                "model": str,
            }
        """
        return await self._complete_with_retry(model, system, user, **kwargs)

    async def stream(
        self,
        model: str,
        system: str,
        user: str,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        """
        Async generator that yields text delta chunks as they arrive.
        Retries the entire stream on transient failures (not auth errors).
        """
        if not _LITELLM_AVAILABLE:
            yield self._mock_response(user)
            return

        messages = self._build_messages(system, user)

        attempt = 0
        max_attempts = 3 if _TENACITY_AVAILABLE else 1

        while attempt < max_attempts:
            attempt += 1
            try:
                response = await acompletion(
                    model=model,
                    messages=messages,
                    stream=True,
                    timeout=self.timeout,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                    **kwargs,
                )
                async for chunk in response:
                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        yield content
                return  # success — exit retry loop
            except _RETRYABLE_EXCEPTIONS as exc:
                if attempt >= max_attempts:
                    log.error(
                        "Streaming model %s failed after %d attempts: %s",
                        model, attempt, exc,
                    )
                    raise
                wait = min(2 ** attempt, 10)
                log.warning(
                    "Streaming model %s failed (attempt %d/%d): %s — retrying in %ds",
                    model, attempt, max_attempts, exc, wait,
                )
                await asyncio.sleep(wait)
            except Exception as exc:
                # Non-retryable (auth, bad request, etc.) — fail immediately
                log.error("Streaming error for model %s: %s", model, exc)
                raise

    # ── Private helpers ──────────────────────

    @_with_retry
    async def _complete_with_retry(
        self,
        model: str,
        system: str,
        user: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        if not _LITELLM_AVAILABLE:
            return {
                "text": self._mock_response(user),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "model": model,
            }

        messages = self._build_messages(system, user)
        response = await acompletion(
            model=model,
            messages=messages,
            stream=False,
            timeout=self.timeout,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            **kwargs,
        )

        text = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)

        return {
            "text": text,
            "prompt_tokens":     getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "model": getattr(response, "model", model),
        }

    @staticmethod
    def _build_messages(system: str, user: str) -> list:
        return [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ]

    @staticmethod
    def _mock_response(user: str) -> str:
        """
        Deterministic stub returned when LiteLLM is not installed.
        Returns structured-looking mock data useful for offline testing.
        """
        return (
            f"[MOCK RESPONSE — litellm not installed]\n"
            f"The Blue Mosque opens at 9:00 AM and is free to enter. "
            f"It was built in 1616 by Sultan Ahmed I. "
            f"It has six minarets and is located in the Sultanahmet district "
            f"on the European side of Istanbul.\n"
            f"(echoing prompt fragment: {user[:80]}…)"
        )
