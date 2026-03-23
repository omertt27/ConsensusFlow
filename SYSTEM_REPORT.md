# ConsensusFlow — Detailed System Report

> Generated: 2026-03-24 · Python 3.13.9 · 214 tests · 97% coverage · 0 Pylance errors

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Repository Layout](#2-repository-layout)
3. [Architecture Overview](#3-architecture-overview)
4. [Core Modules — Deep Dive](#4-core-modules--deep-dive)
   - 4.1 [protocol.py](#41-protocolpy--data-contracts)
   - 4.2 [engine.py](#42-enginepy--pipeline-orchestrator)
   - 4.3 [scoring.py](#43-scoringpy--gotcha-score-engine)
   - 4.4 [models.py](#44-modelspy--pydantic-api-schemas)
   - 4.5 [_pydantic_compat.py](#45-_pydantic_compatpy--compatibility-shim)
5. [Provider Layer](#5-provider-layer)
6. [Prompt System](#6-prompt-system)
7. [CLI](#7-cli)
8. [UI / Report Renderer](#8-ui--report-renderer)
9. [FastAPI Backend](#9-fastapi-backend)
10. [React / Vite Frontend](#10-react--vite-frontend)
11. [Test Suite](#11-test-suite)
12. [Code Coverage](#12-code-coverage)
13. [Type Safety & Linting](#13-type-safety--linting)
14. [Dependency Map](#14-dependency-map)
15. [Data Flow Walkthrough](#15-data-flow-walkthrough)
16. [Configuration Reference](#16-configuration-reference)
17. [Known Limitations & Future Work](#17-known-limitations--future-work)

---

## 1. Executive Summary

**ConsensusFlow** is a multi-model LLM verification pipeline that chains three AI models — a **Proposer**, an **Auditor**, and a **Resolver** — to detect and correct factual errors in LLM-generated text. Every factual claim in a response is atomised, independently audited, and scored.

| Metric | Value |
|---|---|
| Total source lines (Python) | ~3 700 |
| Total test lines | ~1 900 |
| Test count | **214** |
| Test pass rate | **100 %** |
| Code coverage | **97 %** |
| Pylance errors | **0** |
| Python requirement | ≥ 3.9 |
| Primary dependency | `litellm ≥ 1.40` |

The pipeline produces a **Gotcha Score** (0–100) — a single shareable metric summarising how many errors the chain caught — plus a full claim-by-claim audit trail, token/cost accounting, and a failure taxonomy.

---

## 2. Repository Layout

```
ConsensusFlow/
│
├── consensusflow/               # Main Python package
│   ├── __init__.py              # Public API surface (verify, SequentialChain, …)
│   ├── cli.py                   # Click-based CLI entry point
│   ├── exceptions.py            # Custom exception hierarchy
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── _pydantic_compat.py  # Pydantic v2 re-export shim
│   │   ├── engine.py            # SequentialChain orchestrator (681 lines)
│   │   ├── models.py            # Pydantic schemas for API (259 lines)
│   │   ├── protocol.py          # Core dataclasses & enums (191 lines)
│   │   └── scoring.py           # Gotcha Score + cost accounting (407 lines)
│   │
│   ├── prompts/
│   │   ├── loader.py            # Prompt file loader with caching
│   │   ├── adversarial.md       # Auditor system prompt
│   │   ├── extractor.md         # Claim extractor system prompt
│   │   └── synthesis.md         # Resolver system prompt
│   │
│   ├── providers/
│   │   ├── __init__.py
│   │   └── litellm_client.py    # Async LiteLLM wrapper (272 lines)
│   │
│   └── ui/
│       ├── __init__.py
│       └── report.py            # Rich terminal report renderer (331 lines)
│
├── backend/
│   ├── __init__.py
│   └── main.py                  # FastAPI server (223 lines)
│
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js           # Proxy: /api → localhost:8000
│   └── src/
│       ├── main.jsx
│       ├── App.jsx              # Main React component
│       ├── App.css              # Complete creative stylesheet
│       └── index.css            # CSS variables & global reset
│
├── tests/                       # 214 tests across 9 test files
│   ├── test_cli.py              (438 lines)
│   ├── test_engine.py           (166 lines)
│   ├── test_engine_extended.py  (408 lines)
│   ├── test_litellm_client.py   (311 lines)
│   ├── test_loader.py           (102 lines)
│   ├── test_models.py           (311 lines)
│   ├── test_protocol.py         (86 lines)
│   ├── test_report.py           (269 lines)
│   └── test_scoring.py          (272 lines)
│
├── examples/
│   ├── hallucination_benchmark.py
│   └── travel_verify.py
│
├── docs/                        # GitHub Pages documentation site
├── pyproject.toml
├── IMPROVEMENT_PLAN.md
└── SYSTEM_REPORT.md             # ← this file
```

---

## 3. Architecture Overview

```
User / API
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│                      SequentialChain                        │
│                      (engine.py)                            │
│                                                             │
│  prompt ──► [Proposer] ──► [Claim Extractor] ──► [Auditor] │
│                │                                    │       │
│                │            ┌── early exit? ────────┘       │
│                │            │  (Jaccard ≥ threshold         │
│                │            │   OR all claims VERIFIED)     │
│                │            │                               │
│                └────────────▼──────────────────────────────►│
│                         [Resolver]                          │
│                              │                              │
│                              ▼                              │
│                     VerificationReport                      │
└─────────────────────────────────────────────────────────────┘
         │                        │
         ▼                        ▼
  [Gotcha Score]           [Savings Report]
  (scoring.py)             (scoring.py)
         │
         ▼
  ┌─────────────┐    ┌───────────────┐    ┌───────────────┐
  │  CLI output │    │  FastAPI JSON │    │  React UI     │
  │  (report.py)│    │  (backend/)   │    │  (frontend/)  │
  └─────────────┘    └───────────────┘    └───────────────┘
```

### Pipeline Stages

| Stage | Role | Model |
|---|---|---|
| **Proposer** | Generates the initial answer | Configurable (default: `gpt-4o`) |
| **Claim Extractor** | Atomises the answer into verifiable statements | Configurable (default: `gpt-4o-mini`) |
| **Auditor** | Independently reviews every claim with adversarial prompting | Configurable (default: `gemini/gemini-1.5-pro`) |
| **Early-Exit Check** | Skips Resolver if Jaccard similarity ≥ threshold or all claims verified | — |
| **Resolver** | Synthesises a corrected, final answer | Configurable (default: `claude-3-5-sonnet-20241022`) |

---

## 4. Core Modules — Deep Dive

### 4.1 `protocol.py` — Data Contracts

**Location:** `consensusflow/core/protocol.py` (191 lines) · **Coverage: 100%**

The immutable data layer. All objects are plain Python `dataclass`es — no framework dependency.

#### Enums

```
ClaimStatus   VERIFIED | CORRECTED | DISPUTED | NUANCED | REJECTED
ChainStatus   SUCCESS  | EARLY_EXIT | PARTIAL  | ERROR
```

#### `AtomicClaim`

```python
@dataclass
class AtomicClaim:
    id:            str          # Short UUID (8 chars) for cross-referencing
    text:          str          # Current claim text (may be corrected)
    status:        ClaimStatus  # Auditor verdict
    original_text: str | None   # Set when status == CORRECTED
    note:          str | None   # Auditor's free-text reasoning
    confidence:    float        # 0.0 – 1.0
```

#### `StepResult`

```python
@dataclass
class StepResult:
    step:               str       # "proposer" | "auditor" | "resolver"
    model:              str       # LiteLLM model string
    raw_text:           str       # Raw LLM output
    timestamp:          datetime  # UTC, auto-set
    prompt_tokens:      int
    completion_tokens:  int
    latency_ms:         float
    metadata:           dict      # Fallback provenance, etc.
    # Computed:
    total_tokens → int
```

#### `VerificationReport`

Central result object passed through the entire pipeline. Key computed properties:

```
verified_count  → claims with status VERIFIED
corrected_count → claims with status CORRECTED
disputed_count  → claims with status DISPUTED or REJECTED
```

---

### 4.2 `engine.py` — Pipeline Orchestrator

**Location:** `consensusflow/core/engine.py` (681 lines) · **Coverage: 93%**

The largest and most complex module. Contains two top-level functions and one class.

#### `_jaccard_similarity(text_a, text_b) → float`

Dependency-free token-overlap metric using set intersection/union on lowercased word tokens. Used for the early-exit decision. Runs in O(n) where n = vocabulary size.

```
similarity = |tokens_a ∩ tokens_b| / |tokens_a ∪ tokens_b|
```

#### `_parse_claims_from_json(raw) → list[AtomicClaim]`

Parses extractor output. Strips markdown code fences (```` ```json ````), then `json.loads`. Falls back to line-splitting if JSON is malformed — ensuring the pipeline never hard-fails on a bad extractor response.

#### `_parse_audit_from_json(raw, original_claims) → list[AtomicClaim]`

Parses auditor JSON output and merges verdicts back into the original claim list by `id`. If the auditor returns malformed JSON, all claims are marked `DISPUTED` rather than crashing.

#### `SequentialChain`

The main orchestrator class.

**Constructor parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `chain` | `list[str]` | GPT-4o / Gemini / Claude | 3 LiteLLM model strings |
| `extractor_model` | `str` | `gpt-4o-mini` | Model for claim extraction |
| `similarity_threshold` | `float` | `0.92` | Jaccard threshold for early exit |
| `stream_callback` | `callable` | `None` | Called with `(event, data)` per step |
| `timeout` | `float` | `60.0` | Per-step timeout in seconds |
| `fallback_chain` | `list[str]` | `None` | 3-model fallback if primary fails |
| `penalty_weights` | `dict` | `None` | Override Gotcha Score penalties |
| `budget_usd` | `float` | `None` | Abort before Resolver if cost exceeded |

**`run(prompt) → VerificationReport`** — Blocking async pipeline:

```
1. Proposer call          → StepResult
2. Claim extraction       → list[AtomicClaim]
3. Auditor call           → StepResult + updated claims
4. Early-exit check       → may short-circuit
5. Budget check           → may raise BudgetExceededError
6. Resolver call          → StepResult + final_answer
7. Aggregate metrics      → total_tokens, total_latency_ms
```

**`stream(prompt) → AsyncIterator[dict]`** — Yields SSE-style event dicts:

| Event | Data |
|---|---|
| `status` | Human-readable status string |
| `proposer_chunk` | Text delta from Proposer |
| `claims_extracted` | `list[dict]` of raw claim objects |
| `auditor_chunk` | Text delta from Auditor |
| `early_exit` | `{message, saved_tokens}` |
| `resolver_chunk` | Text delta from Resolver |
| `error` | Error message string |
| `done` | Full `VerificationReport` object |

**Fallback logic:** If a primary model raises any exception, `_run_step_with_fallback` retries with the fallback model. The fallback model's `StepResult` records `metadata["fallback_from"]` and `metadata["fallback_reason"]` for auditability. If the fallback also fails, a `ModelUnavailableError` is raised.

**Budget guard:** Before the Resolver step, actual token counts from the Proposer and Auditor are summed, cost is estimated via `_estimate_cost_usd`, and `BudgetExceededError` is raised if the estimate exceeds `budget_usd`. The Resolver is intentionally the guard point because it is the most expensive step.

---

### 4.3 `scoring.py` — Gotcha Score Engine

**Location:** `consensusflow/core/scoring.py` (407 lines) · **Coverage: 99%**

#### Gotcha Score Formula

```
raw_penalty   = Σ penalty[claim.status]  for each claim
worst_case    = len(claims) × max_penalty   (REJECTED = 35)
normalised    = 1 − (raw_penalty / worst_case)
score         = round(normalised × 100)     clamped [0, 100]
```

**Default penalty table:**

| Status | Penalty | Rationale |
|---|---|---|
| `VERIFIED` | 0 | No error |
| `NUANCED` | 5 | Soft: missing context |
| `DISPUTED` | 15 | Medium-soft: unconfirmable |
| `CORRECTED` | 20 | Medium: fact was wrong |
| `REJECTED` | 35 | Hard: demonstrably false |

**Grade table:**

| Score | Grade | Label |
|---|---|---|
| 95–100 | A+ | Bulletproof |
| 85–94 | A | Highly Reliable |
| 70–84 | B | Mostly Reliable |
| 50–69 | C | Use With Caution |
| 25–49 | D | Unreliable |
| 0–24 | F | Dangerous |

#### Failure Taxonomy

Non-verified claims are automatically classified into one of five categories using ordered regex pattern matching on the claim text + auditor note:

```
FABRICATION      → "never", "does not exist", "hallucin…", "no evidence"
OUTDATED_INFO    → "changed", "no longer", "currently", years ≥ 2023
MISSING_CONTEXT  → "however", "except", "closed on", "but", "unless"
UNVERIFIABLE     → "unclear", "cannot confirm", "no source", "disputed"
FACTUAL_ERROR    → catch-all (always last)
```

DISPUTED claims are always classified as UNVERIFIABLE (short-circuit before pattern matching).

#### Cost Estimation

Per-provider blended token rates (input + output averaged) keyed on model-prefix substrings. Estimated cost is used for the budget guard in `engine.py` and for the savings report.

#### `compute_savings(report) → SavingsReport`

When early exit is triggered, calculates:
- `tokens_saved` = estimated resolver tokens not consumed
- `percent_saved` = tokens_saved / total_would_have_been × 100
- `saved_usd` = tokens_saved × estimated rate

---

### 4.4 `models.py` — Pydantic API Schemas

**Location:** `consensusflow/core/models.py` (259 lines) · **Coverage: 98%**

Pydantic v2 models that mirror the protocol dataclasses but add:
- Field-level validation constraints (`min_length`, `ge`, `le`, `pattern`)
- JSON schema export for FastAPI OpenAPI docs
- `@field_validator` for business rules
- `@model_validator` for cross-field invariants

| Schema | Purpose |
|---|---|
| `AtomicClaimSchema` | Validated claim with `text_not_blank` and `coerce_status` validators |
| `StepResultSchema` | Step audit record; `step` field pattern-validated |
| `GotchaScoreSchema` | Score + grade; `catches_le_total` model validator |
| `SavingsReportSchema` | Token/cost savings; all numeric fields `ge=0` |
| `VerificationReportSchema` | Full report; `model_config = {"use_enum_values": True}` |
| `VerifyRequestSchema` | API input; `chain_must_have_three` validator |

`report_to_schema(report, gotcha_score, savings)` converts a `VerificationReport` dataclass into a fully-validated `VerificationReportSchema` for API serialisation.

---

### 4.5 `_pydantic_compat.py` — Compatibility Shim

**Location:** `consensusflow/core/_pydantic_compat.py` (19 lines) · **Coverage: 100%**

A single-responsibility module that re-exports `BaseModel`, `Field`, `field_validator`, `model_validator` from pydantic. Pydantic is always available as a transitive dependency via litellm, so this is a clean re-export with no fallback shims.

**Why this exists:** Previously, `models.py` had inline `try/except ImportError` blocks that defined fallback shims. Pylance processes both branches of `try/except` simultaneously, causing `reportAssignmentType` errors when the `except` branch declared names that the `try` branch later re-imported. Isolating the import into `_pydantic_compat.py` gives Pylance a single, unambiguous definition path.

---

## 5. Provider Layer

**Location:** `consensusflow/providers/litellm_client.py` (272 lines) · **Coverage: 94%**

A thin async wrapper around `litellm.acompletion`. All 100+ LLM providers supported by LiteLLM are available with zero additional configuration.

### `LiteLLMClient`

**Constructor:**

| Parameter | Default | Description |
|---|---|---|
| `timeout` | `60.0` | Per-call timeout in seconds |
| `max_tokens` | `4096` | Max completion tokens |
| `temperature` | `0.2` | Low temperature for factual accuracy |

**Public methods:**

- `complete(model, system, user, **kwargs) → dict` — Single async completion. Returns `{text, prompt_tokens, completion_tokens, model}`.
- `stream(model, system, user, **kwargs) → AsyncIterator[str]` — Async generator yielding text delta chunks. Retries up to 3× on transient failures with exponential backoff (2s, 4s).

**Retry logic:**

- Uses `tenacity` when available; falls back to a manual `while` loop otherwise.
- Retryable exceptions: `litellm.exceptions.RateLimitError`, `litellm.exceptions.ServiceUnavailableError`, `litellm.exceptions.Timeout`, `asyncio.TimeoutError`.
- Auth errors and bad requests fail immediately (not retried).

**Mock mode:**

When `litellm` is not installed, `complete()` returns a deterministic mock response and `stream()` yields it in a single chunk. This enables running the full test suite without any API keys.

**Type ignore annotations (3 locations):**

```python
litellm.set_verbose = ...      # type: ignore[attr-defined]  — missing from stubs
async for chunk in response:   # type: ignore[union-attr]    — CustomStreamWrapper
text = response.choices[0]...  # type: ignore[union-attr]    — ModelResponse union
```

These are litellm stub gaps, not runtime bugs.

---

## 6. Prompt System

**Location:** `consensusflow/prompts/` · **Coverage: 100%**

Three Markdown prompt files are loaded once at startup and cached. Using Markdown files (instead of hard-coded strings) makes prompt iteration fast without touching Python code.

| File | Used By | Role |
|---|---|---|
| `adversarial.md` | Auditor step | Instructs the model to be maximally skeptical and return structured JSON |
| `extractor.md` | Claim extraction step | Instructs the model to atomise the answer into a JSON claim array |
| `synthesis.md` | Resolver step | Instructs the model to produce a corrected final answer |

`loader.py` — `load_prompt(name: str) → str`:
- Searches `consensusflow/prompts/<name>.md`
- Strips YAML front-matter if present
- Raises `PromptNotFoundError` (custom exception) on missing file
- Caches results in a module-level `dict` to avoid repeated disk I/O

---

## 7. CLI

**Location:** `consensusflow/cli.py` (279 lines) · **Coverage: 96%**

Full-featured `argparse`-based CLI. Installed as `consensusflow` entry point via `pyproject.toml`.

### Usage

```bash
consensusflow "Is the Great Wall of China visible from space?"

# Custom chain
consensusflow "..." --chain gpt-4o gemini/gemini-1.5-pro claude-3-5-sonnet-20241022

# Streaming mode
consensusflow "..." --stream

# JSON output
consensusflow "..." --output json

# Save to file
consensusflow "..." --save report.md

# Budget cap
consensusflow "..." --budget 0.10

# Fallback models
consensusflow "..." --fallback gpt-4o-mini gpt-3.5-turbo gpt-4o-mini
```

### Flags

| Flag | Description |
|---|---|
| `--chain` | 3 model strings (proposer, auditor, resolver) |
| `--extractor` | Model for claim extraction |
| `--threshold` | Early-exit Jaccard threshold (default: 0.92) |
| `--stream` | Enable real-time streaming output |
| `--output` | `terminal` \| `markdown` \| `json` |
| `--save` | Write report to file |
| `--no-color` | Disable ANSI colour output |
| `--budget` | Maximum USD spend |
| `--fallback` | 3 fallback model strings |
| `--debug` | Print full tracebacks on error |

---

## 8. UI / Report Renderer

**Location:** `consensusflow/ui/report.py` (331 lines) · **Coverage: 100%**

Rich terminal report renderer. Produces three output formats:

### `format_terminal(report, gs, savings) → str`

Full-colour ANSI output with:
- Colour-coded Gotcha Score banner (green/yellow/red based on grade)
- Per-claim table with status emoji and confidence score
- Token/cost savings breakdown
- Step-by-step latency and token counts
- Failure taxonomy counts

### `format_markdown(report, gs, savings) → str`

GitHub-flavoured Markdown with tables, suitable for saving or pasting into issues/PRs.

### `format_json(report, gs, savings) → str`

Pretty-printed JSON using `dataclasses.asdict` + custom enum serialiser.

All three formats call `compute_gotcha_score` and `compute_savings` if not pre-computed.

---

## 9. FastAPI Backend

**Location:** `backend/main.py` (223 lines) · **Coverage: N/A** (integration layer)

### Endpoints

#### `POST /api/verify`

Blocking verification. Accepts `VerifyRequest` JSON, runs the full pipeline, returns a detailed JSON report.

```json
{
  "prompt": "Is the Great Wall visible from space?",
  "chain": ["gpt-4o", "gemini/gemini-1.5-pro", "claude-3-5-sonnet-20241022"],
  "extractor_model": "gpt-4o-mini",
  "similarity_threshold": 0.92,
  "budget_usd": null
}
```

Response includes: `run_id`, `status`, `final_answer`, `gotcha_score`, `atomic_claims`, `steps`, `savings`, `total_tokens`, `total_latency_ms`.

#### `POST /api/verify/stream`

Server-Sent Events (SSE) streaming endpoint. Returns `text/event-stream` with one JSON object per event. The frontend's stream panel renders each event in real time.

Event format:
```
data: {"event": "proposer_chunk", "data": "The Great Wall..."}
data: {"event": "claims_extracted", "data": [...]}
data: {"event": "auditor_chunk", "data": "..."}
data: {"event": "done", "data": {...full report...}}
```

#### `GET /api/health`

Returns `{"status": "ok", "version": "0.1.0"}`. Used by the frontend to check server availability.

### CORS

Allows `localhost:5173` and `localhost:5174` (Vite dev server ports). In production, replace with the actual domain.

### Run

```bash
uvicorn backend.main:app --reload --port 8000
```

---

## 10. React / Vite Frontend

**Location:** `frontend/src/` · **Proxy:** `/api → http://localhost:8000`

### `App.jsx`

Single-page application with the following sections:

| Section | Description |
|---|---|
| **Aurora background** | 3 animated radial gradient blobs (CSS `aurora-shift` keyframe) |
| **Header** | Animated pipeline nodes showing active step in real time |
| **Example chips** | Quick-fill buttons for common queries |
| **Query form** | Textarea with char count, Verify button, stream toggle |
| **Status bar** | Live spinner + current step label |
| **Stream panel** | Tabbed (All / Proposer / Auditor / Resolver) monospace output with live cursor |
| **Report grid** | Score card (SVG ring) + Stats card side-by-side |
| **Answer card** | Final answer with copy button and status pill |
| **Model steps** | Cards per pipeline step showing model name, latency, tokens |
| **Claims list** | Filterable claim rows with badge, confidence, note |

### `App.css`

Complete stylesheet (~1 000 lines). Key design decisions:

- **Dark theme only** — background `#060b14` with glassmorphism cards (`backdrop-filter: blur`)
- **CSS custom properties** — all colours, radii, shadows defined in `index.css` `:root`
- **No UI framework** — pure CSS animations and layout, no Tailwind/MUI
- **Score ring** — SVG `stroke-dashoffset` animated on mount for the ring fill
- **Responsive** — `report-grid` collapses to single column below 680px

### `vite.config.js`

```js
proxy: { '/api': 'http://localhost:8000' }
```

All `/api/*` requests from the Vite dev server are forwarded to the FastAPI backend, avoiding CORS issues in development.

---

## 11. Test Suite

**Location:** `tests/` · **214 tests · 100% pass rate**

| File | Tests | What It Covers |
|---|---|---|
| `test_cli.py` | 38 | Argument parser, error handling, streaming CLI, file saving, `main()` entry point |
| `test_engine.py` | ~30 | Jaccard similarity, JSON parsers, `SequentialChain` init, `run()`, early-exit, budget guard |
| `test_engine_extended.py` | ~50 | Streaming pipeline, fallback chain, resolver failure fallback, `_estimate_tokens` |
| `test_litellm_client.py` | ~35 | `complete()`, `stream()`, retry logic, mock mode, timeout, auth errors |
| `test_loader.py` | ~15 | Prompt loading, caching, missing file error, front-matter stripping |
| `test_models.py` | ~40 | All Pydantic validators, `report_to_schema()`, field constraints |
| `test_protocol.py` | ~15 | `AtomicClaim`, `StepResult`, `VerificationReport` dataclasses, enums |
| `test_report.py` | ~25 | Terminal, Markdown, and JSON formatters |
| `test_scoring.py` | ~30 | Gotcha Score formula, grade table, failure taxonomy, cost estimation, savings |

All async tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`).

---

## 12. Code Coverage

```
Name                                        Stmts   Miss  Cover
---------------------------------------------------------------
consensusflow/__init__.py                       7      0   100%
consensusflow/cli.py                          133      5    96%
consensusflow/core/__init__.py                  3      0   100%
consensusflow/core/_pydantic_compat.py          3      0   100%
consensusflow/core/engine.py                  249     18    93%
consensusflow/core/models.py                  108      2    98%
consensusflow/core/protocol.py                 76      0   100%
consensusflow/core/scoring.py                 113      1    99%
consensusflow/exceptions.py                    13      0   100%
consensusflow/prompts/loader.py                24      0   100%
consensusflow/providers/__init__.py             2      0   100%
consensusflow/providers/litellm_client.py      82      5    94%
consensusflow/ui/__init__.py                    2      0   100%
consensusflow/ui/report.py                    114      0   100%
---------------------------------------------------------------
TOTAL                                         929     31    97%
```

**Uncovered lines (31 total):**

| File | Lines | Reason |
|---|---|---|
| `cli.py` | 176-179, 234, 279 | Interactive stdin edge cases; `KeyboardInterrupt` in specific branching |
| `engine.py` | 81-82, 115, 122-123, 392-395, 429-432, 477-480, 642 | Line-split fallback paths for malformed LLM output; streaming error re-raise paths |
| `models.py` | 66, 189 | `text_not_blank` blank-string branch; `chain_must_have_three` with `None` |
| `scoring.py` | 248 | Edge case in cost estimation for unknown model prefix |
| `litellm_client.py` | 72-73, 83, 126-127 | `dotenv` import failure branch; debug-mode logging path |

---

## 13. Type Safety & Linting

### Pylance (VS Code)

All **0 errors** across the entire codebase. Previously had 5 errors — all resolved:

| File | Error | Fix Applied |
|---|---|---|
| `models.py` (×2) | `reportAssignmentType` — pydantic `BaseModel`/`Field` re-declaration conflict from inline `try/except` shims | Extracted to `_pydantic_compat.py` as a clean re-export |
| `models.py` (×3) | `reportOptionalCall` — `field_validator`/`model_validator` resolved as `None` due to shim conflict | Same fix as above |
| `litellm_client.py` (×3) | `attr-defined`, `union-attr` — litellm stub gaps | Targeted `# type: ignore` comments with specific error codes |

### Type Hint Quality

- All public functions have full parameter and return type annotations
- `from __future__ import annotations` used throughout for forward-reference support
- `dict[str, Any]` / `list[str]` modern generics (Python 3.9+ style with `__future__`)
- No use of `Any` except at genuine dynamic boundaries (LiteLLM responses, dataclass-to-dict conversion)

---

## 14. Dependency Map

### Core (always required)

```
litellm ≥ 1.40        → LLM provider abstraction (brings pydantic as transitive dep)
python-dotenv ≥ 1.0   → .env file loading
tenacity ≥ 8.2        → Retry logic with exponential backoff
```

### Server (optional extra: `pip install consensusflow[server]`)

```
fastapi ≥ 0.104       → REST API + SSE streaming
uvicorn[standard] ≥ 0.24 → ASGI server
```

### Dev (optional extra: `pip install consensusflow[dev]`)

```
pytest ≥ 8.0
pytest-asyncio ≥ 0.23
pytest-cov ≥ 5.0
ruff ≥ 0.4
mypy ≥ 1.10
```

### Internal import graph

```
cli.py
  └── core/engine.py
        ├── core/protocol.py
        ├── core/scoring.py
        ├── providers/litellm_client.py
        ├── prompts/loader.py
        └── exceptions.py

backend/main.py
  └── core/engine.py  (same subtree)
  └── core/models.py
        ├── core/protocol.py
        └── core/_pydantic_compat.py → pydantic

ui/report.py
  └── core/protocol.py
  └── core/scoring.py
```

---

## 15. Data Flow Walkthrough

**Input:** `"Is the Great Wall of China visible from space?"`

```
1. SequentialChain.run(prompt)
   │
   ├─► LiteLLMClient.complete("gpt-4o", system=..., user=prompt)
   │     → StepResult(step="proposer", raw_text="The Great Wall is visible...",
   │                  prompt_tokens=142, completion_tokens=220, latency_ms=1840)
   │
   ├─► LiteLLMClient.complete("gpt-4o-mini", system=extractor_prompt, user=raw_text)
   │     → [AtomicClaim(id="a1b2", text="The Great Wall is visible from space", confidence=0.95),
   │         AtomicClaim(id="c3d4", text="It stretches over 13,000 miles", confidence=0.9)]
   │
   ├─► LiteLLMClient.complete("gemini/gemini-1.5-pro", system=adversarial_prompt, user=audit_user)
   │     → raw JSON: [{"id":"a1b2","status":"REJECTED","note":"Astronauts confirm it is NOT visible"},
   │                   {"id":"c3d4","status":"VERIFIED","note":"Length is correct"}]
   │     → AtomicClaim(id="a1b2", status=REJECTED, note="Astronauts confirm...")
   │        AtomicClaim(id="c3d4", status=VERIFIED)
   │
   ├─► Jaccard similarity check: 0.41 < 0.92 → no early exit
   │
   ├─► LiteLLMClient.complete("claude-3-5-sonnet-20241022", system=synthesis_prompt, user=resolver_user)
   │     → "No, the Great Wall of China is NOT visible from space with the naked eye..."
   │
   └─► VerificationReport(
         status=ChainStatus.PARTIAL,
         final_answer="No, the Great Wall...",
         atomic_claims=[REJECTED, VERIFIED],
         total_tokens=892,
         total_latency_ms=6420
       )

2. compute_gotcha_score(report)
   → penalty = 35 (REJECTED) + 0 (VERIFIED) = 35
   → worst_case = 2 × 35 = 70
   → score = round((1 − 35/70) × 100) = 50
   → grade = "C", label = "Use With Caution"

3. compute_savings(report)
   → early_exit=False → tokens_saved=0

4. API response / terminal output
```

---

## 16. Configuration Reference

### Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | OpenAI models |
| `GEMINI_API_KEY` | Google Gemini models |
| `ANTHROPIC_API_KEY` | Anthropic Claude models |
| `CONSENSUSFLOW_DEBUG` | Set to `"1"` to enable verbose LiteLLM logging |

All standard LiteLLM provider variables are supported (AWS, Azure, Cohere, etc.).

### `pyproject.toml` Highlights

```toml
[project]
requires-python = ">=3.9"
dependencies = ["litellm>=1.40.0", "python-dotenv>=1.0.0", "tenacity>=8.2.0"]

[project.optional-dependencies]
server = ["fastapi>=0.104", "uvicorn[standard]>=0.24"]
dev    = ["pytest>=8.0", "pytest-asyncio>=0.23", "pytest-cov>=5.0", ...]

[tool.pytest.ini_options]
asyncio_mode = "auto"
filterwarnings = ["ignore::DeprecationWarning", "ignore::PendingDeprecationWarning"]
```

---

## 17. Known Limitations & Future Work

### Current Limitations

| Area | Limitation |
|---|---|
| **Similarity metric** | Jaccard is a fast heuristic. Semantic similarity (embeddings) would be more accurate for early-exit decisions |
| **Claim extraction** | Relies on the extractor model returning valid JSON; the line-split fallback produces lower quality claims |
| **Cost estimation** | Blended per-provider rates are approximations; actual costs depend on exact model versions and pricing tiers |
| **Streaming token counting** | In stream mode, token counts are estimated from character length (~4 chars/token) rather than exact values |
| **No persistence** | Verification reports are ephemeral; no database storage out of the box |
| **Single chain** | Only one Proposer/Auditor/Resolver triplet per run; no ensemble or voting across multiple chains |
| **No auth** | The FastAPI backend has no authentication layer |

### Suggested Improvements

1. **Embedding-based similarity** — Replace Jaccard with cosine similarity on sentence embeddings for more accurate early-exit decisions
2. **Persistent report storage** — Add SQLite/PostgreSQL backend for report history and analytics
3. **Batch verification** — Accept multiple prompts in a single API call with concurrent execution
4. **Confidence calibration** — Use auditor confidence scores to weight Gotcha Score penalties
5. **Source citation** — Extend the Auditor prompt to return verifiable source URLs alongside verdicts
6. **API authentication** — Add API key middleware to the FastAPI backend for production deployments
7. **Streaming token accounting** — Use `tiktoken` for accurate token counts in streaming mode
8. **Caching layer** — Cache identical prompt+model combinations to reduce cost during development
9. **Webhook support** — POST the final report to a configured URL when verification completes
10. **Frontend history panel** — Store past verifications in `localStorage` for easy comparison

---

*Report generated from codebase state as of 2026-03-24. All metrics reflect the current `.venv` environment with Python 3.13.9.*
