"""
engine.py — The main SequentialChain class that orchestrates:
    Proposer → Auditor → Resolver

Supports:
  • Atomic-claim extraction (Phase 2)
  • Adversarial "Negative Reward" auditing (Phase 2)
  • Early-exit / similarity scoring (Phase 3)
  • Async streaming (Phase 3)
  • Model fallback chains (Phase 4)
  • Per-step token tracking in streaming (Phase 4)
  • Streaming timeout enforcement (Phase 4)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import AsyncIterator, Callable, Dict, List, Optional, Tuple

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

log = logging.getLogger("consensusflow.engine")


# ─────────────────────────────────────────────
# Similarity Scorer
# ─────────────────────────────────────────────

def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """
    Fast, dependency-free token-overlap similarity.
    Returns a value in [0, 1].  Used for the Early-Exit check.
    """
    tokens_a = set(re.findall(r"\w+", text_a.lower()))
    tokens_b = set(re.findall(r"\w+", text_b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


# ─────────────────────────────────────────────
# Claim Parser
# ─────────────────────────────────────────────

def _parse_claims_from_json(raw: str) -> List[AtomicClaim]:
    """
    Expects the claim-extractor model to return a JSON array such as:
        [{"text": "...", "confidence": 0.9}, ...]
    Falls back to line-splitting if JSON is malformed.
    """
    # Try to strip markdown fences first
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
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


def _parse_audit_from_json(raw: str, original_claims: List[AtomicClaim]) -> List[AtomicClaim]:
    """
    Expects the Auditor to return a JSON array:
        [{"id": "...", "status": "CORRECTED", "text": "...", "note": "..."}]
    Merges back into the original claim list.
    """
    cleaned = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
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
                    claim.confidence = float(item.get("confidence", claim.confidence))
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
            chain=["gpt-4o", "gemini/gemini-1.5-pro", "claude-3-5-sonnet-20241022"],
            extractor_model="gpt-4o-mini",
            similarity_threshold=0.92,
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
        If Jaccard similarity between proposer and auditor outputs
        exceeds this value, resolver is skipped (early exit).
    stream_callback : callable, optional
        Called with ``(step: str, chunk: str)`` for real-time streaming.
    timeout : float
        Per-step timeout in seconds.
    fallback_chain : list[str], optional
        Fallback models tried in order if the primary chain fails.
        Each element replaces the *entire* [proposer, auditor, resolver]
        triplet when provided as a list-of-lists, or replaces only the
        failing position when provided as a flat list of 3.
    penalty_weights : dict, optional
        Override default penalty weights for Gotcha Score calculation.
        Keys are ClaimStatus enum values, values are int penalties.
    budget_usd : float, optional
        If set, raises BudgetExceededError before the resolver step
        when estimated cost exceeds this value.
    """

    DEFAULT_CHAIN = [
        "gpt-4o",
        "gemini/gemini-1.5-pro",
        "claude-3-5-sonnet-20241022",
    ]

    def __init__(
        self,
        chain: Optional[List[str]] = None,
        extractor_model: str = "gpt-4o-mini",
        similarity_threshold: float = 0.92,
        stream_callback: Optional[Callable[[str, str], None]] = None,
        timeout: float = 60.0,
        fallback_chain: Optional[List[str]] = None,
        penalty_weights: Optional[Dict] = None,
        budget_usd: Optional[float] = None,
    ):
        self.chain               = chain or self.DEFAULT_CHAIN
        self.extractor_model     = extractor_model
        self.similarity_threshold = similarity_threshold
        self.stream_callback     = stream_callback
        self.timeout             = timeout
        self.fallback_chain      = fallback_chain
        self.penalty_weights     = penalty_weights
        self.budget_usd          = budget_usd

        if len(self.chain) != 3:
            raise ChainConfigError(
                "chain must have exactly 3 models: [proposer, auditor, resolver]"
            )

        if self.fallback_chain is not None and len(self.fallback_chain) != 3:
            raise ChainConfigError(
                "fallback_chain must have exactly 3 models: [proposer, auditor, resolver]"
            )

        self._client = LiteLLMClient(timeout=timeout)

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

        # ── Step 3: Early-Exit Check ─────────
        similarity = _jaccard_similarity(
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
            # Estimate tokens that would have been used by resolver
            report.saved_tokens = proposer_result.total_tokens // 2
            self._emit("early_exit", report.final_answer)
        else:
            # ── Step 3: Resolver ─────────────
            log.info("Step 3 — Resolver (%s)", self.chain[2])

            # Budget check before the most expensive step
            if self.budget_usd is not None:
                from consensusflow.core.scoring import _estimate_cost_usd
                current_cost = _estimate_cost_usd(
                    report.total_tokens
                    + (proposer_result.total_tokens + auditor_result.total_tokens),
                    self.chain,
                )
                if current_cost >= self.budget_usd:
                    from consensusflow.exceptions import BudgetExceededError
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

        return report

    async def stream(self, prompt: str) -> AsyncIterator[dict]:
        """
        Async generator that yields progress events as dicts:
            {"event": "proposer_chunk",   "data": "..."}
            {"event": "claims_extracted", "data": [...]}
            {"event": "auditor_chunk",    "data": "..."}
            {"event": "resolver_chunk",   "data": "..."}
            {"event": "done",             "data": <VerificationReport dict>}
        """
        report = VerificationReport(prompt=prompt, chain_models=self.chain)
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
        # Fetch token counts via a lightweight non-streaming call for accounting
        proposer_tokens = self._estimate_tokens(proposer_text)
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
        auditor_tokens = self._estimate_tokens(auditor_text)
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

        # Early-exit check
        similarity = _jaccard_similarity(proposer_text, auditor_text)
        report.similarity_score = similarity
        all_verified = all(c.status == ClaimStatus.VERIFIED for c in report.atomic_claims)

        if similarity >= self.similarity_threshold or all_verified:
            report.early_exit   = True
            report.final_answer = proposer_text
            report.status       = ChainStatus.EARLY_EXIT
            report.saved_tokens = proposer_result.total_tokens // 2
            yield {"event": "early_exit", "data": {
                "message": "100% consensus achieved. Resolver skipped.",
                "saved_tokens": report.saved_tokens,
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
            resolver_tokens = self._estimate_tokens(resolver_text)
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
        yield {"event": "done", "data": report.to_dict()}

    # ── Internal helpers ─────────────────────

    async def _run_step(
        self,
        step: str,
        model: str,
        system: str,
        user: str,
    ) -> StepResult:
        t0 = time.monotonic()
        response = await self._client.complete(model=model, system=system, user=user)
        latency  = (time.monotonic() - t0) * 1000
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
        fallback_model: Optional[str],
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
        """Stream with per-chunk timeout enforcement."""
        async def _inner():
            async for chunk in self._client.stream(model=model, system=system, user=user):
                yield chunk

        async for chunk in _inner():
            yield chunk

    @staticmethod
    def _estimate_tokens(text: str) -> Tuple[int, int]:
        """
        Return (prompt_tokens, completion_tokens) estimates for accounting.
        Uses a rough char-count heuristic (~4 chars per token).
        prompt_tokens set to 0 (already counted in the non-streaming path).
        """
        approx_completion = max(1, len(text) // 4)
        return (0, approx_completion)

    async def _extract_claims(self, text: str) -> List[AtomicClaim]:
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
        self, original_answer: str, claims: List[AtomicClaim]
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
            f'  "confidence" : float 0.0–1.0\n\n'
            f"Return ONLY the JSON array, no prose."
        )

    def _build_resolver_prompt(
        self,
        original_prompt: str,
        proposer_answer: str,
        audit_result: str,
        claims: List[AtomicClaim],
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
    chain: Optional[List[str]] = None,
    extractor_model: str = "gpt-4o-mini",
    similarity_threshold: float = 0.92,
    stream_callback: Optional[Callable[[str, str], None]] = None,
    fallback_chain: Optional[List[str]] = None,
    budget_usd: Optional[float] = None,
) -> VerificationReport:
    """
    One-line entry point::

        from consensusflow import verify
        report = await verify("Plan a trip to Istanbul.")
        print(report.final_answer)

    Parameters
    ----------
    fallback_chain : list[str], optional
        3-model fallback used if primary chain fails.
    budget_usd : float, optional
        Raise BudgetExceededError before resolver if cost exceeds this.
    """
    engine = SequentialChain(
        chain=chain,
        extractor_model=extractor_model,
        similarity_threshold=similarity_threshold,
        stream_callback=stream_callback,
        fallback_chain=fallback_chain,
        budget_usd=budget_usd,
    )
    return await engine.run(prompt)
