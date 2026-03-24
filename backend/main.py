"""
backend/main.py — ConsensusFlow FastAPI server.

Endpoints:
  POST /api/verify          — blocking verification, returns full JSON report
  POST /api/verify/stream   — SSE streaming (event: data: JSON per step)
  POST /api/verify/batch    — batch verification of multiple prompts (concurrent)
  GET  /api/history         — paginated list of past verifications
  GET  /api/history/{run_id} — full report for a specific run
  DELETE /api/history/{run_id} — delete a specific report
  GET  /api/cache/stats     — cache hit/miss statistics
  POST /api/cache/clear     — clear the in-memory cache
  GET  /api/health          — health check

Authentication:
  Set env var CONSENSUSFLOW_API_KEY to enable Bearer token auth.
  Requests without a valid key receive HTTP 401.
  If CONSENSUSFLOW_API_KEY is not set, auth is disabled (dev mode).

Run:
  uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger("consensusflow.backend")

# ─────────────────────────────────────────────
# Configuration from environment
# ─────────────────────────────────────────────

# Per-step LLM call timeout in seconds. Override via env var for slow models.
_STEP_TIMEOUT: float = float(os.environ.get("CONSENSUSFLOW_STEP_TIMEOUT", "60"))

# Regex for valid LiteLLM model name strings: "provider/model-name" or "model-name"
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-./]*$")

# ─────────────────────────────────────────────
# Shared state (process-level singletons)
# ─────────────────────────────────────────────

from consensusflow.core.cache import MemoryCache
from consensusflow.core.storage import ReportStore

_cache = MemoryCache(maxsize=256, ttl_seconds=3600)
_store = ReportStore()   # lazy-init on first use

app = FastAPI(
    title="ConsensusFlow API",
    description="Multi-model verification pipeline — Proposer → Auditor → Resolver",
    version="0.2.0",
)

# Allow the Vite dev server (and any localhost origin) during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────
# API Key Authentication
# ─────────────────────────────────────────────

_API_KEY: str | None = os.environ.get("CONSENSUSFLOW_API_KEY")


async def _verify_api_key(authorization: str | None = Header(default=None)) -> None:
    """
    Dependency that enforces Bearer-token authentication when
    CONSENSUSFLOW_API_KEY is set in the environment.

    If the env var is NOT set, auth is disabled (development mode).
    """
    if _API_KEY is None:
        return  # Auth disabled — dev mode
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header. Expected: Bearer <api-key>",
        )
    token = authorization[len("Bearer "):]
    if token != _API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# ─────────────────────────────────────────────
# Request / Response schemas
# ─────────────────────────────────────────────

class VerifyRequest(BaseModel):
    prompt: str = Field(..., min_length=3, max_length=8000)
    chain: list[str] | None = Field(
        default=None,
        description="Exactly 3 LiteLLM model strings [proposer, auditor, resolver]",
    )
    extractor_model: str = "gpt-4o-mini"
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    budget_usd: float | None = Field(default=None, ge=0.0)
    enable_cache: bool = False
    webhook_url: str | None = None

    @field_validator("chain", mode="before")
    @classmethod
    def validate_chain_models(cls, v: Any) -> Any:
        if v is None:
            return v
        for m in v:
            if not isinstance(m, str) or not _MODEL_NAME_RE.match(m):
                raise ValueError(
                    f"Invalid model name {m!r}. Expected format: 'provider/model' or 'model-name'."
                )
        return v

    @field_validator("extractor_model", mode="before")
    @classmethod
    def validate_extractor_model(cls, v: Any) -> Any:
        if not isinstance(v, str) or not _MODEL_NAME_RE.match(v):
            raise ValueError(
                f"Invalid extractor_model {v!r}. Expected format: 'provider/model' or 'model-name'."
            )
        return v


class BatchVerifyRequest(BaseModel):
    prompts: list[str] = Field(..., min_length=1, max_length=20)
    chain: list[str] | None = None
    extractor_model: str = "gpt-4o-mini"
    similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    budget_usd: float | None = Field(default=None, ge=0.0)
    enable_cache: bool = True   # cache enabled by default for batch
    concurrency: int = Field(default=3, ge=1, le=10)

    @field_validator("chain", mode="before")
    @classmethod
    def validate_chain_models(cls, v: Any) -> Any:
        if v is None:
            return v
        for m in v:
            if not isinstance(m, str) or not _MODEL_NAME_RE.match(m):
                raise ValueError(
                    f"Invalid model name {m!r}. Expected format: 'provider/model' or 'model-name'."
                )
        return v

    @field_validator("extractor_model", mode="before")
    @classmethod
    def validate_extractor_model(cls, v: Any) -> Any:
        if not isinstance(v, str) or not _MODEL_NAME_RE.match(v):
            raise ValueError(
                f"Invalid extractor_model {v!r}. Expected format: 'provider/model' or 'model-name'."
            )
        return v


def _report_to_dict(report: Any) -> dict:
    """Serialise a VerificationReport dataclass to a JSON-safe dict."""
    from consensusflow.core.scoring import compute_gotcha_score, compute_savings

    gs      = compute_gotcha_score(report)
    savings = compute_savings(report)

    def _step(sr: Any) -> dict | None:
        if sr is None:
            return None
        return {
            "step":               sr.step,
            "model":              sr.model,
            "prompt_tokens":      sr.prompt_tokens,
            "completion_tokens":  sr.completion_tokens,
            "total_tokens":       sr.total_tokens,
            "latency_ms":         round(sr.latency_ms, 1),
        }

    return {
        "run_id":            report.run_id,
        "prompt":            report.prompt,
        "chain_models":      report.chain_models,
        "status":            report.status.value,
        "final_answer":      report.final_answer,
        "similarity_score":  round(report.similarity_score, 4),
        "early_exit":        report.early_exit,
        "total_tokens":      report.total_tokens,
        "total_latency_ms":  round(report.total_latency_ms, 1),
        "created_at":        datetime.now(timezone.utc).isoformat(),
        "atomic_claims": [
            {
                "id":            c.id,
                "text":          c.text,
                "status":        c.status.value,
                "original_text": c.original_text,
                "note":          c.note,
                "confidence":    round(c.confidence, 3),
                "sources":       c.sources,
            }
            for c in report.atomic_claims
        ],
        "gotcha_score": {
            "score":            gs.score,
            "grade":            gs.grade,
            "label":            gs.label,
            "emoji":            gs.emoji,
            "total_claims":     gs.total_claims,
            "catches":          gs.catches,
            "penalty_breakdown": gs.penalty_breakdown,
            "failure_taxonomy": gs.failure_taxonomy,
            "share_text":       gs.share_text,
        },
        "savings": {
            "tokens_used":           savings.tokens_used,
            "tokens_saved":          savings.tokens_saved,
            "total_would_have_been": savings.total_would_have_been,
            "percent_saved":         round(savings.percent_saved, 1),
            "early_exit":            savings.early_exit,
            "cost_usd":              round(savings.cost_usd, 6),
            "saved_usd":             round(savings.saved_usd, 6),
        },
        "steps": {
            "proposer":  _step(report.proposer_result),
            "auditor":   _step(report.auditor_result),
            "resolver":  _step(report.resolver_result),
        },
    }


def _make_chain(req: Any) -> Any:
    """Build a SequentialChain from a request, wiring in the shared cache."""
    from consensusflow.core.engine import SequentialChain
    from consensusflow.exceptions import ChainConfigError

    try:
        chain = SequentialChain(
            chain=req.chain,
            extractor_model=req.extractor_model,
            similarity_threshold=req.similarity_threshold,
            budget_usd=req.budget_usd,
            enable_cache=req.enable_cache,
            webhook_url=getattr(req, "webhook_url", None),
            timeout=_STEP_TIMEOUT,
        )
        # Override with the shared process-level cache so all requests benefit
        if req.enable_cache:
            chain._cache = _cache
        return chain
    except ChainConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {
        "status":  "ok",
        "service": "consensusflow",
        "version": "0.2.0",
        "auth":    "enabled" if _API_KEY else "disabled (dev mode)",
    }


@app.post("/api/verify", dependencies=[Depends(_verify_api_key)])
async def verify_blocking(req: VerifyRequest, response: Response) -> dict:
    """Blocking verification — waits for the full pipeline then returns the report."""
    from consensusflow.exceptions import (
        BudgetExceededError,
        ModelUnavailableError,
    )

    chain = _make_chain(req)
    try:
        report = await chain.run(req.prompt)
        result = _report_to_dict(report)
        # Persist to SQLite — catch storage errors specifically so a DB hiccup
        # never masks an otherwise successful verification.
        try:
            await _store.save(result)
        except sqlite3.Error as store_exc:
            log.warning("Failed to persist report to SQLite: %s", store_exc)
        except Exception as store_exc:
            log.error("Unexpected error persisting report: %s", store_exc, exc_info=True)
        response.headers["X-Cache-Hit"] = "true" if chain._cache_hit_count > 0 else "false"
        return result

    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=402,
            detail=f"Budget exceeded: estimated ${exc.cost_usd:.4f} > limit ${exc.budget_usd:.4f}",
        )
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        log.exception("Unexpected error in /api/verify")
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/verify/stream", dependencies=[Depends(_verify_api_key)])
async def verify_stream(req: VerifyRequest) -> StreamingResponse:
    """
    SSE streaming verification.

    Each event is a JSON object:
        data: {"event": "status"|"proposer_chunk"|"claims_extracted"|
                        "auditor_chunk"|"early_exit"|"resolver_chunk"|
                        "done"|"error",
               "data": <payload>}
    """
    from consensusflow.core.protocol import VerificationReport

    chain = _make_chain(req)

    async def event_generator():
        try:
            async for event in chain.stream(req.prompt):
                # Wrap each event individually — a serialization failure on one
                # event should not kill the entire SSE connection.
                try:
                    etype = event["event"]
                    data  = event["data"]

                    # Serialise the final report and persist it
                    if etype == "done" and isinstance(data, VerificationReport):
                        result = _report_to_dict(data)
                        try:
                            await _store.save(result)
                        except sqlite3.Error as store_exc:
                            log.warning("Failed to persist streaming report to SQLite: %s", store_exc)
                        except Exception as store_exc:
                            log.error("Unexpected error persisting streaming report: %s", store_exc, exc_info=True)
                        result["x_cache_hit"] = chain._cache_hit_count > 0
                        payload = json.dumps({"event": "done", "data": result})
                    else:
                        # Use default=str so non-serialisable data emits a string
                        # rather than crashing the generator.
                        payload = json.dumps({"event": etype, "data": data}, default=str)

                    yield f"data: {payload}\n\n"

                except Exception as event_exc:
                    log.warning(
                        "Failed to serialize stream event %r: %s",
                        event.get("event", "?"), event_exc,
                    )
                    warning_payload = json.dumps({
                        "event": "warning",
                        "data": f"An event was skipped due to a serialization error: {event_exc}",
                    })
                    yield f"data: {warning_payload}\n\n"

        except Exception as exc:
            log.exception("Streaming error")
            error_payload = json.dumps({"event": "error", "data": str(exc)})
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":     "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/verify/batch", dependencies=[Depends(_verify_api_key)])
async def verify_batch(req: BatchVerifyRequest) -> dict:
    """
    Batch verification — run multiple prompts concurrently.

    Returns a list of report dicts in the same order as the input prompts.
    Failed individual prompts include an "error" key instead of a report.

    Parameters
    ----------
    concurrency : int
        Maximum number of simultaneous pipeline runs (default 3, max 10).
    """
    semaphore = asyncio.Semaphore(req.concurrency)

    async def _run_one(prompt: str, idx: int) -> dict:
        async with semaphore:
            try:
                chain = _make_chain(req)
                report = await chain.run(prompt)
                result = _report_to_dict(report)
                try:
                    await _store.save(result)
                except sqlite3.Error as store_exc:
                    log.warning("Failed to persist batch report[%d] to SQLite: %s", idx, store_exc)
                except Exception as store_exc:
                    log.error("Unexpected error persisting batch report[%d]: %s", idx, store_exc, exc_info=True)
                return {"index": idx, "prompt": prompt, **result}
            except Exception as exc:
                log.error("Batch item %d failed: %s", idx, exc)
                return {"index": idx, "prompt": prompt, "error": str(exc)}

    tasks   = [_run_one(p, i) for i, p in enumerate(req.prompts)]
    results = await asyncio.gather(*tasks)
    return {
        "batch_size":  len(req.prompts),
        "concurrency": req.concurrency,
        "results":     list(results),
    }


# ─────────────────────────────────────────────
# History endpoints
# ─────────────────────────────────────────────

@app.get("/api/history", dependencies=[Depends(_verify_api_key)])
async def list_history(
    limit:  int = Query(default=50, ge=1,  le=200),
    offset: int = Query(default=0,  ge=0),
    status: str | None = Query(default=None),
) -> dict:
    """Paginated list of past verification summaries."""
    rows  = await _store.list(limit=limit, offset=offset, status_filter=status)
    total = await _store.count()
    return {"total": total, "limit": limit, "offset": offset, "items": rows}


@app.get("/api/history/{run_id}", dependencies=[Depends(_verify_api_key)])
async def get_history_item(run_id: str) -> dict:
    """Retrieve the full report for a specific run_id."""
    record = await _store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Report {run_id!r} not found")
    return record


@app.delete("/api/history/{run_id}", dependencies=[Depends(_verify_api_key)])
async def delete_history_item(run_id: str) -> dict:
    """Delete a specific report."""
    deleted = await _store.delete(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Report {run_id!r} not found")
    return {"deleted": True, "run_id": run_id}


# ─────────────────────────────────────────────
# Cache management endpoints
# ─────────────────────────────────────────────

@app.get("/api/cache/stats", dependencies=[Depends(_verify_api_key)])
async def cache_stats() -> dict:
    """Return current cache hit/miss statistics."""
    return _cache.stats


@app.post("/api/cache/clear", dependencies=[Depends(_verify_api_key)])
async def cache_clear() -> dict:
    """Clear all cached LLM responses."""
    await _cache.clear()
    return {"cleared": True, "message": "Cache cleared successfully"}
