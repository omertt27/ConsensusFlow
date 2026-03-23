"""
backend/main.py — ConsensusFlow FastAPI server.

Endpoints:
  POST /api/verify          — blocking verification, returns full JSON report
  POST /api/verify/stream   — SSE streaming (event: data: JSON per step)
  GET  /api/health          — health check

Run:
  uvicorn backend.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger("consensusflow.backend")

app = FastAPI(
    title="ConsensusFlow API",
    description="Multi-model verification pipeline — Proposer → Auditor → Resolver",
    version="0.1.0",
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
        "atomic_claims": [
            {
                "id":            c.id,
                "text":          c.text,
                "status":        c.status.value,
                "original_text": c.original_text,
                "note":          c.note,
                "confidence":    round(c.confidence, 3),
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


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "consensusflow"}


@app.post("/api/verify")
async def verify_blocking(req: VerifyRequest) -> dict:
    """Blocking verification — waits for the full pipeline then returns the report."""
    from consensusflow.core.engine import SequentialChain
    from consensusflow.exceptions import (
        BudgetExceededError,
        ChainConfigError,
        ModelUnavailableError,
    )

    try:
        chain = SequentialChain(
            chain=req.chain,
            extractor_model=req.extractor_model,
            similarity_threshold=req.similarity_threshold,
            budget_usd=req.budget_usd,
        )
        report = await chain.run(req.prompt)
        return _report_to_dict(report)

    except ChainConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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


@app.post("/api/verify/stream")
async def verify_stream(req: VerifyRequest) -> StreamingResponse:
    """
    SSE streaming verification.

    Each event is a JSON object:
        data: {"event": "status"|"proposer_chunk"|"claims_extracted"|
                        "auditor_chunk"|"early_exit"|"resolver_chunk"|
                        "done"|"error",
               "data": <payload>}
    """
    from consensusflow.core.engine import SequentialChain
    from consensusflow.core.protocol import VerificationReport
    from consensusflow.exceptions import ChainConfigError

    try:
        chain = SequentialChain(
            chain=req.chain,
            extractor_model=req.extractor_model,
            similarity_threshold=req.similarity_threshold,
            budget_usd=req.budget_usd,
        )
    except ChainConfigError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    async def event_generator():
        try:
            async for event in chain.stream(req.prompt):
                etype = event["event"]
                data  = event["data"]

                # Serialise the final report
                if etype == "done" and isinstance(data, VerificationReport):
                    payload = json.dumps({"event": "done", "data": _report_to_dict(data)})
                else:
                    payload = json.dumps({"event": etype, "data": data})

                yield f"data: {payload}\n\n"

        except Exception as exc:
            log.exception("Streaming error")
            error_payload = json.dumps({"event": "error", "data": str(exc)})
            yield f"data: {error_payload}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
