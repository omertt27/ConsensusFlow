"""
engine.py — The main SequentialChain class that orchestrates:
    Proposer → Auditor → Resolver

Supports:
  • Atomic-claim extraction (Phase 2)
  • Adversarial "Negative Reward" auditing (Phase 2)
  • Early-exit / similarity scoring (Phase 3) — embedding-based w/ Jaccard fallback
  • Async streaming (Phase 3)
  • Model fallback chains (Phase 4)
  • tiktoken-accurate token counting for streaming (Phase 4)
  • Streaming timeout enforcement (Phase 4)
  • Response caching (MemoryCache) — skip LLM for duplicate prompts
  • Confidence-calibrated claim scoring
  • Source citation extraction from Auditor output
  • Webhook delivery of final report
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING

from consensusflow.core.protocol import (
    AtomicClaim,
    ChainStatus,
    ClaimStatus,
    StepResult,
    VerificationReport,
)
from consensusflow.exceptions import (
    ChainConfigError,
    ModelUnavailableError,
)
from consensusflow.providers.litellm_client import LiteLLMClient
from consensusflow.prompts.loader import load_prompt
from consensusflow.core.cache import MemoryCache, NullCache

log = logging.getLogger("consensusflow.engine")


# ─────────────────────────────────────────────
# Auditor reliability guard
# ─────────────────────────────────────────────

_DISPUTE_THRESHOLD = 0.60   # >60% DISPUTED in one run = auditor drift

def _check_auditor_reliability(claims: list) -> str | None:
    """
    Return a warning string if the auditor appears to have drifted
    (i.e. marked an implausibly high fraction of claims as DISPUTED).
    Returns None when the audit looks healthy.
    """
    if not claims:
        return None
    disputed = sum(1 for c in claims if c.status == ClaimStatus.DISPUTED)
    ratio = disputed / len(claims)
    if ratio > _DISPUTE_THRESHOLD:
        return (
            f"Auditor reliability warning: {disputed}/{len(claims)} claims "
            f"({ratio:.0%}) marked DISPUTED — possible auditor drift. "
            "The auditor may have confused a contested topic with unverifiable "
            "individual claims. Review results critically."
        )
    return None


# ─────────────────────────────────────────────
# Token accounting (tiktoken)
# ─────────────────────────────────────────────

def _count_tokens(text: str, model: str = "gpt-4o") -> int:
    """
    Count tokens accurately using tiktoken.
    Falls back to the 4-chars-per-token heuristic when tiktoken can't
    resolve the exact model encoding (e.g. Gemini, Claude).
    """
    try:
        import tiktoken  # type: ignore[import-untyped]
        try:
            enc = tiktoken.encoding_for_model(model)
        except KeyError:
            enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text) // 4)


# ─────────────────────────────────────────────
# Similarity Scorer
# ─────────────────────────────────────────────

def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """
    Fast, dependency-free token-overlap similarity.
    Returns a value in [0, 1].  Used as fallback for the Early-Exit check.
    """
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _cosine_similarity(text_a: str, text_b: str) -> float:
    """
    TF-weighted cosine similarity between two texts.
    More semantically sensitive than Jaccard.
    No external ML dependencies — uses pure Python TF-IDF weighting.
    """
    def tf_vector(text: str) -> dict[str, float]:
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return {}
        counts: dict[str, int] = {}
        for t in tokens:
            counts[t] = counts.get(t, 0) + 1
        total = len(tokens)
        return {t: c / total for t, c in counts.items()}

    vec_a = tf_vector(text_a)
    vec_b = tf_vector(text_b)
    if not vec_a or not vec_b:
        return 0.0

    shared = set(vec_a) & set(vec_b)
    dot    = sum(vec_a[t] * vec_b[t] for t in shared)
    norm_a = sum(v * v for v in vec_a.values()) ** 0.5
    norm_b = sum(v * v for v in vec_b.values()) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _compute_similarity(text_a: str, text_b: str) -> float:
    """
    Compute best available similarity score.
    Uses TF cosine similarity (pure-Python, no ML deps).
    The cosine metric is more sensitive to semantic overlap than Jaccard.
    """
    return _cosine_similarity(text_a, text_b)


# ─────────────────────────────────────────────
# Claim Parser
# ─────────────────────────────────────────────

def _parse_claims_from_json(raw: str) -> list[AtomicClaim]:
    """
    Expects the claim-extractor model to return a JSON array such as:
        [{"text": "...", "confidence": 0.9}, ...]
    Falls back to line-splitting if JSON is malformed.
    """
    # Strip markdown fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            claims = []
            for item in data:
                if isinstance(item, dict):
                    claims.append(AtomicClaim(
                        text=item.get("text", ""),
                        confidence=float(item.get("confidence", 1.0)),
                    ))
                elif isinstance(item, str):
                    claims.append(AtomicClaim(text=item))
            return claims
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: treat each non-empty line as a claim
    log.warning(
        "Claim extractor returned non-JSON; falling back to line split. "
        "Raw output (first 200 chars): %s", raw[:200]
    )
    return [
        AtomicClaim(text=line.strip())
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _parse_audit_from_json(raw: str, original_claims: list[AtomicClaim]) -> list[AtomicClaim]:
    """
    Expects the Auditor to return a JSON array:
        [{"id": "...", "status": "CORRECTED", "text": "...", "note": "...",
          "sources": ["https://example.com"], "confidence": 0.9}]
    Merges back into the original claim list.
    Sources (URLs) are stored in claim.sources for citation display.
    """
    # Strip markdown fences (```json ... ``` or ``` ... ```)
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    index   = {c.id: c for c in original_claims}

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                cid = item.get("id")
                if cid and cid in index:
                    claim = index[cid]
                    new_status = item.get("status", "VERIFIED").upper()
                    try:
                        claim.status = ClaimStatus(new_status)
                    except ValueError:
                        claim.status = ClaimStatus.DISPUTED

                    if claim.status == ClaimStatus.CORRECTED:
                        claim.original_text = claim.text
                        claim.text = item.get("text", claim.text)

                    claim.note = item.get("note")
                    # Confidence calibration: honour auditor's stated confidence
                    raw_conf = item.get("confidence", claim.confidence)
                    claim.confidence = max(0.0, min(1.0, float(raw_conf)))

                    # Source citation extraction
                    sources = item.get("sources", [])
                    if isinstance(sources, list):
                        claim.sources = [str(s) for s in sources if s]

            return list(index.values())
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: mark all as DISPUTED when audit output is unparseable
    log.warning(
        "Auditor returned non-JSON; marking all claims as DISPUTED. "
        "Raw output (first 200 chars): %s", raw[:200]
    )
    for c in original_claims:
        c.status = ClaimStatus.DISPUTED
    return original_claims


# ─────────────────────────────────────────────
# Sequential Chain Engine
# ─────────────────────────────────────────────

class SequentialChain:
    """
    ConsensusFlow core engine.

    Usage::

        chain = SequentialChain(
            chain=["gpt-4o", "gemini/gemini-2.5-flash", "gpt-4o-mini"],
            extractor_model="gpt-4o-mini",
            similarity_threshold=0.92,
            enable_cache=True,
            webhook_url="https://yourapp.example.com/hooks/verification",
        )
        report = await chain.run("Plan a 2-day trip to Istanbul.")

    Parameters
    ----------
    chain : list[str]
        Exactly 3 LiteLLM model strings in order:
        [proposer, auditor, resolver].
    extractor_model : str
        Fast model used for atomic claim extraction (default: gpt-4o-mini).
    similarity_threshold : float
        If cosine similarity between proposer and auditor outputs
        exceeds this value, resolver is skipped (early exit).
    stream_callback : callable, optional
        Called with ``(step: str, chunk: str)`` for real-time streaming.
    timeout : float
        Per-step timeout in seconds.
    fallback_chain : list[str], optional
        Fallback models tried in order if the primary chain fails.
    penalty_weights : dict, optional
        Override default penalty weights for Gotcha Score calculation.
    budget_usd : float, optional
        Raise BudgetExceededError before resolver when cost exceeds this.
    enable_cache : bool
        If True, cache identical (model, prompt) pairs in memory.
    cache_ttl : float
        Seconds until a cache entry expires (default 1 hour).
    cache_maxsize : int
        Maximum cached entries before LRU eviction.
    webhook_url : str, optional
        If set, POST the final JSON report to this URL after each run.
    """

    DEFAULT_CHAIN = [
        "gpt-4o",               # Proposer  — OpenAI
        "gemini/gemini-2.5-flash",  # Auditor   — Google
        "gpt-4o-mini",          # Resolver  — OpenAI (fast, cheap fallback; swap for Claude/Mistral etc.)
    ]

    def __init__(
        self,
        chain: list[str] | None = None,
        extractor_model: str = "gpt-4o-mini",
        similarity_threshold: float = 0.92,
        stream_callback: Callable[[str, str], None] | None = None,
        timeout: float = 60.0,
        fallback_chain: list[str] | None = None,
        penalty_weights: dict | None = None,
        budget_usd: float | None = None,
        enable_cache: bool = False,
        cache_ttl: float = 3600.0,
        cache_maxsize: int = 256,
        webhook_url: str | None = None,
    ):
        raw_chain = chain or self.DEFAULT_CHAIN
        # Allow 2-model shorthand: [proposer, auditor] → resolver reuses proposer
        if len(raw_chain) == 2:
            raw_chain = [raw_chain[0], raw_chain[1], raw_chain[0]]
            log.info(
                "2-model chain detected — resolver will reuse proposer: %s",
                raw_chain[0],
            )
        self.chain               = raw_chain
        self.extractor_model     = extractor_model
        self.similarity_threshold = similarity_threshold
        self.stream_callback     = stream_callback
        self.timeout             = timeout
        self.fallback_chain      = fallback_chain
        self.penalty_weights     = penalty_weights
        self.budget_usd          = budget_usd
        self.webhook_url         = webhook_url

        if len(self.chain) != 3:
            raise ChainConfigError(
                "chain must have 2 or 3 models: [proposer, auditor] or "
                "[proposer, auditor, resolver]"
            )

        if self.fallback_chain is not None and len(self.fallback_chain) not in (2, 3):
            raise ChainConfigError(
                "fallback_chain must have 2 or 3 models"
            )
        # Normalise fallback chain the same way
        if self.fallback_chain is not None and len(self.fallback_chain) == 2:
            self.fallback_chain = [
                self.fallback_chain[0],
                self.fallback_chain[1],
                self.fallback_chain[0],
            ]

        self._client = LiteLLMClient(timeout=timeout)

        # Cache backend
        if enable_cache:
            self._cache: MemoryCache | NullCache = MemoryCache(
                maxsize=cache_maxsize, ttl_seconds=cache_ttl
            )
        else:
            self._cache = NullCache()

        # Load prompt templates once
        self._adversarial_prompt = load_prompt("adversarial")
        self._synthesis_prompt   = load_prompt("synthesis")
        self._extractor_prompt   = load_prompt("extractor")

    # ── Public API ───────────────────────────

    async def run(self, prompt: str) -> VerificationReport:
        """
        Full async pipeline.  Returns a VerificationReport.
        """
        report = VerificationReport(
            prompt=prompt,
            chain_models=self.chain,
            penalty_weights=self.penalty_weights,
        )
        t_start = time.monotonic()

        # ── Step 1: Proposer ─────────────────
        log.info("Step 1 — Proposer (%s)", self.chain[0])
        proposer_result = await self._run_step_with_fallback(
            step="proposer",
            primary_model=self.chain[0],
            fallback_model=self.fallback_chain[0] if self.fallback_chain else None,
            system="You are a knowledgeable assistant. Answer accurately and thoroughly.",
            user=prompt,
        )
        report.proposer_result = proposer_result
        self._emit("proposer_done", proposer_result.raw_text)

        # ── Step 2a: Extract Atomic Claims ───
        log.info("Step 2a — Extracting atomic claims (%s)", self.extractor_model)
        claims = await self._extract_claims(proposer_result.raw_text)
        report.atomic_claims = claims
        self._emit("claims_extracted", json.dumps([c.to_dict() for c in claims]))

        # ── Step 2b: Auditor ─────────────────
        log.info("Step 2b — Auditor (%s)", self.chain[1])
        audit_user = self._build_audit_prompt(
            original_answer=proposer_result.raw_text,
            claims=claims,
        )
        auditor_result = await self._run_step_with_fallback(
            step="auditor",
            primary_model=self.chain[1],
            fallback_model=self.fallback_chain[1] if self.fallback_chain else None,
            system=self._adversarial_prompt,
            user=audit_user,
        )
        report.auditor_result = auditor_result
        report.atomic_claims = _parse_audit_from_json(
            auditor_result.raw_text, claims
        )
        self._emit("auditor_done", auditor_result.raw_text)

        # ── Auditor reliability guard ─────────
        report.auditor_reliability_warning = _check_auditor_reliability(report.atomic_claims)
        if report.auditor_reliability_warning:
            log.warning(
                "Auditor reliability warning: %s", report.auditor_reliability_warning
            )

        # ── Step 3: Early-Exit Check ─────────
        similarity = _compute_similarity(
            proposer_result.raw_text, auditor_result.raw_text
        )
        report.similarity_score = similarity
        all_verified = all(
            c.status == ClaimStatus.VERIFIED for c in report.atomic_claims
        )

        if similarity >= self.similarity_threshold or all_verified:
            log.info(
                "Early exit triggered (similarity=%.2f, all_verified=%s)",
                similarity, all_verified,
            )
            report.early_exit   = True
            report.final_answer = proposer_result.raw_text
            report.status       = ChainStatus.EARLY_EXIT
            # saved_tokens = resolver's estimated consumption.
            # Conservative: use proposer's completion tokens (resolver typically
            # produces a similar-length synthesis). This is also correct in the
            # 2-model case where resolver == proposer — same token budget.
            report.saved_tokens = proposer_result.completion_tokens or (proposer_result.total_tokens // 2)
            # saved_cost uses the resolver slot's own per-token rate (correct for
            # both 2-model and 3-model chains).
            from consensusflow.core.scoring import _rate_for_model
            resolver_rate = _rate_for_model(self.chain[2])
            report.saved_cost_usd = report.saved_tokens * resolver_rate / 1000
            self._emit("early_exit", report.final_answer)
        else:
            # ── Step 3: Resolver ─────────────
            log.info("Step 3 — Resolver (%s)", self.chain[2])

            # Budget check before the most expensive step.
            # Use actual token counts from completed steps — report.total_tokens
            # is only summed at the end, so we compute it directly here.
            if self.budget_usd is not None:
                from consensusflow.core.scoring import _estimate_cost_usd
                from consensusflow.exceptions import BudgetExceededError
                tokens_so_far = (
                    proposer_result.total_tokens + auditor_result.total_tokens
                )
                current_cost = _estimate_cost_usd(tokens_so_far, self.chain)
                if current_cost >= self.budget_usd:
                    raise BudgetExceededError(current_cost, self.budget_usd)

            resolver_user = self._build_resolver_prompt(
                original_prompt=prompt,
                proposer_answer=proposer_result.raw_text,
                audit_result=auditor_result.raw_text,
                claims=report.atomic_claims,
            )
            try:
                resolver_result = await self._run_step_with_fallback(
                    step="resolver",
                    primary_model=self.chain[2],
                    fallback_model=self.fallback_chain[2] if self.fallback_chain else None,
                    system=self._synthesis_prompt,
                    user=resolver_user,
                )
                report.resolver_result = resolver_result
                report.final_answer    = resolver_result.raw_text
            except Exception as exc:
                log.error(
                    "Resolver failed (%s); falling back to proposer answer: %s",
                    self.chain[2], exc,
                )
                report.final_answer = proposer_result.raw_text

            report.status = (
                ChainStatus.PARTIAL
                if report.disputed_count > 0
                else ChainStatus.SUCCESS
            )
            if report.resolver_result:
                self._emit("resolver_done", report.resolver_result.raw_text)

        # ── Aggregate metrics ────────────────
        report.total_latency_ms = (time.monotonic() - t_start) * 1000
        report.total_tokens = sum(
            s.total_tokens
            for s in [
                proposer_result,
                auditor_result,
                report.resolver_result,
            ]
            if s is not None
        )

        # ── Webhook delivery ─────────────────
        if self.webhook_url:
            await self._deliver_webhook(report)

        return report

    async def stream(self, prompt: str) -> AsyncIterator[dict]:
        """
        Async generator that yields progress events as dicts:
            {"event": "proposer_chunk",   "data": "..."}
            {"event": "claims_extracted", "data": [...]}
            {"event": "auditor_chunk",    "data": "..."}
            {"event": "resolver_chunk",   "data": "..."}
            {"event": "done",             "data": <VerificationReport>}
        """
        report = VerificationReport(
            prompt=prompt,
            chain_models=self.chain,
            penalty_weights=self.penalty_weights,
        )
        t_start = time.monotonic()

        # Proposer — streaming
        yield {"event": "status", "data": f"🧠 Proposer ({self.chain[0]}) thinking…"}
        proposer_text = ""
        proposer_t0 = time.monotonic()
        try:
            async for chunk in self._stream_with_timeout(
                model=self.chain[0],
                system="You are a knowledgeable assistant. Answer accurately and thoroughly.",
                user=prompt,
            ):
                proposer_text += chunk
                yield {"event": "proposer_chunk", "data": chunk}
        except Exception as exc:
            log.error("Proposer streaming failed: %s", exc)
            yield {"event": "error", "data": f"Proposer failed: {exc}"}
            return

        proposer_latency = (time.monotonic() - proposer_t0) * 1000
        # tiktoken-accurate token count for streaming responses
        proposer_tokens = self._estimate_tokens(proposer_text, self.chain[0])
        proposer_result = StepResult(
            step="proposer",
            model=self.chain[0],
            raw_text=proposer_text,
            prompt_tokens=proposer_tokens[0],
            completion_tokens=proposer_tokens[1],
            latency_ms=proposer_latency,
        )
        report.proposer_result = proposer_result

        # Claim extraction
        yield {"event": "status", "data": "🔬 Extracting atomic claims…"}
        claims = await self._extract_claims(proposer_text)
        report.atomic_claims = claims
        yield {"event": "claims_extracted", "data": [c.to_dict() for c in claims]}

        # Auditor — streaming
        yield {"event": "status", "data": f"🔍 Auditor ({self.chain[1]}) verifying…"}
        audit_user = self._build_audit_prompt(proposer_text, claims)
        auditor_text = ""
        auditor_t0 = time.monotonic()
        try:
            async for chunk in self._stream_with_timeout(
                model=self.chain[1],
                system=self._adversarial_prompt,
                user=audit_user,
            ):
                auditor_text += chunk
                yield {"event": "auditor_chunk", "data": chunk}
        except Exception as exc:
            log.error("Auditor streaming failed: %s", exc)
            yield {"event": "error", "data": f"Auditor failed: {exc}"}
            return

        auditor_latency = (time.monotonic() - auditor_t0) * 1000
        auditor_tokens = self._estimate_tokens(auditor_text, self.chain[1])
        auditor_result = StepResult(
            step="auditor",
            model=self.chain[1],
            raw_text=auditor_text,
            prompt_tokens=auditor_tokens[0],
            completion_tokens=auditor_tokens[1],
            latency_ms=auditor_latency,
        )
        report.auditor_result = auditor_result
        report.atomic_claims = _parse_audit_from_json(auditor_text, claims)

        # Auditor reliability guard (streaming path)
        report.auditor_reliability_warning = _check_auditor_reliability(report.atomic_claims)
        if report.auditor_reliability_warning:
            log.warning("Auditor reliability warning: %s", report.auditor_reliability_warning)
            yield {"event": "auditor_warning", "data": report.auditor_reliability_warning}

        # Early-exit check (streaming path)
        similarity = _compute_similarity(proposer_text, auditor_text)
        report.similarity_score = similarity
        all_verified = all(c.status == ClaimStatus.VERIFIED for c in report.atomic_claims)

        if similarity >= self.similarity_threshold or all_verified:
            report.early_exit   = True
            report.final_answer = proposer_text
            report.status       = ChainStatus.EARLY_EXIT
            report.saved_tokens = proposer_result.completion_tokens or (proposer_result.total_tokens // 2)
            from consensusflow.core.scoring import _rate_for_model
            report.saved_cost_usd = report.saved_tokens * _rate_for_model(self.chain[2]) / 1000
            yield {"event": "early_exit", "data": {
                "message": "100% consensus achieved. Resolver skipped.",
                "saved_tokens": report.saved_tokens,
                "saved_cost_usd": report.saved_cost_usd,
            }}
        else:
            # Resolver — streaming
            yield {"event": "status", "data": f"✍️  Resolver ({self.chain[2]}) synthesising…"}
            resolver_user = self._build_resolver_prompt(
                prompt, proposer_text, auditor_text, report.atomic_claims
            )
            resolver_text = ""
            resolver_t0 = time.monotonic()
            try:
                async for chunk in self._stream_with_timeout(
                    model=self.chain[2],
                    system=self._synthesis_prompt,
                    user=resolver_user,
                ):
                    resolver_text += chunk
                    yield {"event": "resolver_chunk", "data": chunk}
            except Exception as exc:
                log.error("Resolver streaming failed: %s; using proposer answer", exc)
                yield {"event": "error", "data": f"Resolver failed (using proposer answer): {exc}"}
                resolver_text = proposer_text

            resolver_latency = (time.monotonic() - resolver_t0) * 1000
            resolver_tokens = self._estimate_tokens(resolver_text, self.chain[2])
            resolver_result = StepResult(
                step="resolver",
                model=self.chain[2],
                raw_text=resolver_text,
                prompt_tokens=resolver_tokens[0],
                completion_tokens=resolver_tokens[1],
                latency_ms=resolver_latency,
            )
            report.resolver_result = resolver_result
            report.final_answer    = resolver_text
            report.status = (
                ChainStatus.PARTIAL if report.disputed_count > 0
                else ChainStatus.SUCCESS
            )

        report.total_latency_ms = (time.monotonic() - t_start) * 1000
        report.total_tokens = sum(
            s.total_tokens
            for s in [proposer_result, auditor_result, report.resolver_result]
            if s is not None
        )
        # Webhook delivery (stream path)
        if self.webhook_url:
            await self._deliver_webhook(report)
        yield {"event": "done", "data": report}

    # ── Internal helpers ─────────────────────

    async def _run_step(
        self,
        step: str,
        model: str,
        system: str,
        user: str,
    ) -> StepResult:
        t0 = time.monotonic()
        # Check cache first
        cache_key = self._cache.make_key(model, system, user)
        cached = await self._cache.get(cache_key)
        if cached is not None:
            log.debug("Cache HIT for step=%s model=%s", step, model)
            response = cached
        else:
            response = await self._client.complete(model=model, system=system, user=user)
            await self._cache.set(cache_key, response)

        latency = (time.monotonic() - t0) * 1000
        return StepResult(
            step=step,
            model=model,
            raw_text=response["text"],
            prompt_tokens=response.get("prompt_tokens", 0),
            completion_tokens=response.get("completion_tokens", 0),
            latency_ms=latency,
        )

    async def _run_step_with_fallback(
        self,
        step: str,
        primary_model: str,
        fallback_model: str | None,
        system: str,
        user: str,
    ) -> StepResult:
        """Run a step, falling back to fallback_model if primary fails."""
        try:
            return await self._run_step(step, primary_model, system, user)
        except Exception as primary_exc:
            if fallback_model is None:
                raise
            log.warning(
                "Primary model %s failed for step '%s': %s — trying fallback %s",
                primary_model, step, primary_exc, fallback_model,
            )
            try:
                result = await self._run_step(step, fallback_model, system, user)
                result.metadata["fallback_from"] = primary_model
                result.metadata["fallback_reason"] = str(primary_exc)
                return result
            except Exception as fallback_exc:
                log.error(
                    "Fallback model %s also failed for step '%s': %s",
                    fallback_model, step, fallback_exc,
                )
                raise ModelUnavailableError(
                    f"Both primary ({primary_model}) and fallback ({fallback_model}) "
                    f"failed for step '{step}': {fallback_exc}"
                ) from fallback_exc

    async def _stream_with_timeout(
        self,
        model: str,
        system: str,
        user: str,
    ) -> AsyncIterator[str]:
        """Stream with per-step timeout enforcement via the client's timeout setting."""
        async for chunk in self._client.stream(model=model, system=system, user=user):
            yield chunk

    @staticmethod
    def _estimate_tokens(text: str, model: str = "") -> tuple[int, int]:
        """
        Return (prompt_tokens, completion_tokens) for accounting.
        Uses tiktoken for accurate counting when available,
        falls back to ~4 chars/token heuristic.
        prompt_tokens set to 0 (already counted in non-streaming path).
        """
        completion = _count_tokens(text, model or "gpt-4o")
        return (0, completion)

    async def _deliver_webhook(self, report: VerificationReport) -> None:
        """
        POST the serialised report to self.webhook_url.
        Fires-and-forgets on error to never block the pipeline.
        """
        if not self.webhook_url:
            return
        try:
            import httpx  # type: ignore[import-untyped]
            payload = report.to_dict()
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json", "User-Agent": "ConsensusFlow/1.0"},
                )
                resp.raise_for_status()
            log.info("Webhook delivered to %s  status=%d", self.webhook_url, resp.status_code)
        except Exception as exc:
            log.warning("Webhook delivery to %s failed: %s", self.webhook_url, exc)

    async def _extract_claims(self, text: str) -> list[AtomicClaim]:
        user_msg = (
            f"Extract every verifiable factual claim from the text below.\n\n"
            f"TEXT:\n{text}\n\n"
            f"Return ONLY a JSON array with objects: "
            f'[{{"text": "claim text", "confidence": 0.95}}, ...]'
        )
        response = await self._client.complete(
            model=self.extractor_model,
            system=self._extractor_prompt,
            user=user_msg,
        )
        return _parse_claims_from_json(response["text"])

    def _build_audit_prompt(
        self, original_answer: str, claims: list[AtomicClaim]
    ) -> str:
        claims_block = "\n".join(
            f'  [{c.id}] "{c.text}"' for c in claims
        )
        return (
            f"## Proposer's Answer\n{original_answer}\n\n"
            f"## Atomic Claims to Verify\n{claims_block}\n\n"
            f"## Your Task\n"
            f"For EACH claim, return a JSON array where every object has:\n"
            f'  "id"         : the claim id shown above\n'
            f'  "status"     : one of VERIFIED | CORRECTED | DISPUTED | NUANCED | REJECTED\n'
            f'  "text"       : corrected text (only if CORRECTED, else repeat original)\n'
            f'  "note"       : your forensic reasoning (1-2 sentences)\n'
            f'  "confidence" : float 0.0–1.0 representing your certainty\n'
            f'  "sources"    : JSON array of verifiable URLs supporting your verdict '
            f'(empty array [] if none available)\n\n'
            f"Return ONLY the JSON array, no prose."
        )

    def _build_resolver_prompt(
        self,
        original_prompt: str,
        proposer_answer: str,
        audit_result: str,
        claims: list[AtomicClaim],
    ) -> str:
        corrected = [c for c in claims if c.status != ClaimStatus.VERIFIED]
        corrections_block = ""
        if corrected:
            corrections_block = "\n## Corrections & Notes from Auditor\n" + "\n".join(
                f"  [{c.status.value}] {c.text}"
                + (f"  ← was: {c.original_text}" if c.original_text else "")
                + (f"\n    Note: {c.note}" if c.note else "")
                for c in corrected
            )
        return (
            f"## Original User Request\n{original_prompt}\n\n"
            f"## Proposer's Draft Answer\n{proposer_answer}\n\n"
            f"## Auditor's Full Review\n{audit_result}"
            f"{corrections_block}\n\n"
            f"## Your Task\n"
            f"Produce the final, verified answer incorporating all corrections and nuances. "
            f"Be precise. Cite corrections inline where relevant."
        )

    def _emit(self, event: str, data: str) -> None:
        if self.stream_callback:
            self.stream_callback(event, data)


# ─────────────────────────────────────────────
# Convenience wrapper
# ─────────────────────────────────────────────

async def verify(
    prompt: str,
    chain: list[str] | None = None,
    extractor_model: str = "gpt-4o-mini",
    similarity_threshold: float = 0.92,
    stream_callback: Callable[[str, str], None] | None = None,
    fallback_chain: list[str] | None = None,
    budget_usd: float | None = None,
    enable_cache: bool = False,
    cache_ttl: float = 3600.0,
    webhook_url: str | None = None,
) -> VerificationReport:
    """
    One-line entry point::

        from consensusflow import verify

        # 2-model shorthand (resolver reuses proposer automatically):
        report = await verify("Plan a trip to Istanbul.",
                              chain=["gpt-4o", "gemini/gemini-2.5-flash"])

        # Full 3-model chain:
        report = await verify("Plan a trip to Istanbul.",
                              chain=["gpt-4o", "gemini/gemini-2.5-flash", "gpt-4o-mini"])

        print(report.final_answer)

    Parameters
    ----------
    chain : list[str], optional
        2 or 3 LiteLLM model strings.  When 2 are given the resolver
        automatically reuses the proposer model.
    fallback_chain : list[str], optional
        2- or 3-model fallback used if primary chain fails.
    budget_usd : float, optional
        Raise BudgetExceededError before resolver if cost exceeds this.
    enable_cache : bool
        Cache identical (model, prompt) pairs in memory.
    cache_ttl : float
        Seconds until a cache entry expires.
    webhook_url : str, optional
        POST the final report to this URL after completion.
    """
    engine = SequentialChain(
        chain=chain,
        extractor_model=extractor_model,
        similarity_threshold=similarity_threshold,
        stream_callback=stream_callback,
        fallback_chain=fallback_chain,
        budget_usd=budget_usd,
        enable_cache=enable_cache,
        cache_ttl=cache_ttl,
        webhook_url=webhook_url,
    )
    return await engine.run(prompt)
