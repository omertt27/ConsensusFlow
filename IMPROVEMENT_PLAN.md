# ConsensusFlow — Codebase Analysis & Improvement Plan

> Audit date: March 23, 2026  
> **Updated: March 23, 2026 (implementation complete)**  
> Test suite: **214 tests · 0 failures · 0 warnings**  
> Coverage: **95 %** total (up from 65 %; all key gaps closed)

---

## Status: COMPLETED ✅

All critical bugs, type-hint modernisation, and coverage improvements from this plan have been
implemented. See the section headers below for status.

---

## 1 · Executive Summary

The SDK is architecturally sound. The Proposer → Auditor → Resolver chain, atomic claim extraction,
adversarial auditing, early-exit, Gotcha Score, savings report, and Pydantic schemas are all
implemented and now pass 214 automated tests at 95% coverage. All critical bugs have been fixed,
type hints modernised, and developer-experience gaps addressed.

---

## 2 · Issue Register

### 2.1 Critical (Correctness Bugs)

| # | File | Issue | Impact |
|---|------|-------|--------|
| C1 | `engine.py` L68–70 | `.strip("```")` strips individual characters not the substring — multi-char strip is misleading (ruff B005). Replace with `re.sub` already in scope. | Malformed JSON from extractor/auditor not cleaned → parse fallback triggered unnecessarily |
| C2 | `engine.py` streaming `done` event | `yield {"event": "done", "data": report.to_dict()}` sends a raw **dict**; `cli.py` checks `isinstance(data, VerificationReport)` which is always `False` in streaming mode → `_print_gotcha_banner` never runs during `--stream` | Gotcha Score silent in streaming mode |
| C3 | `engine.py` `_stream_with_timeout` | The `timeout` parameter (`self.timeout`) is **never passed to `acompletion`** in streaming mode. Hanging streams can block indefinitely. | Silent hang on slow/unavailable models during streaming |
| C4 | `ui/report.py` L70, 107–108, 145–146, 177–178 | Spurious `f""` f-strings with no placeholders (ruff F541). Not a crash, but indicates accidental copy-paste patterns that obscure intent. | Minor: cosmetic but signals incomplete refactor |
| C5 | `scoring.py` | `compute_gotcha_score` silently ignores `SequentialChain.penalty_weights` because the engine stores `self.penalty_weights` but **never passes it to `compute_gotcha_score`** during `run()` or `stream()`. | Custom penalty weights set on the chain have zero effect |

### 2.2 Coverage Gaps

| # | File | Current Coverage | Missing Lines |
|---|------|-----------------|---------------|
| G1 | `cli.py` | **0 %** | All 279 lines — no test covers argument parsing, `_run_standard`, `_run_streaming`, `_print_gotcha_banner`, `_handle_error`, interactive mode |
| G2 | `models.py` | **0 %** | All Pydantic validators, `report_to_schema()` conversion helper — zero tests |
| G3 | `litellm_client.py` | **49 %** | Live `acompletion` path, streaming retry loop, mock responses |
| G4 | `prompts/loader.py` | **72 %** | `register_prompt_override`, `clear_prompt_overrides`, `PromptNotFoundError` raise path |

### 2.3 Type-Hint Modernisation (Python 3.9+ compatible)

| # | Location | Issue |
|---|----------|-------|
| T1 | `engine.py`, `scoring.py`, `models.py`, `providers/`, `prompts/loader.py` | `typing.List`, `typing.Dict`, `typing.Tuple`, `typing.Optional` still imported — deprecated since Python 3.9. Should be `list`, `dict`, `tuple`, `X \| None`. |
| T2 | `engine.py` | `from typing import AsyncIterator` should be `from collections.abc import AsyncIterator` |
| T3 | `engine.py` L17 | `import asyncio` unused (only `asyncio.TimeoutError` needed, which is `TimeoutError` in Py3.11+) |
| T4 | `providers/litellm_client.py` | `Optional` imported but unused |
| T5 | `ui/report.py` | `Optional` imported but unused |

### 2.4 Architecture & Design

| # | File | Issue |
|---|------|-------|
| A1 | `engine.py` | `_estimate_tokens` uses a naive `len(text)//4` heuristic for streaming token accounting. Should prefer `litellm.token_counter()` when available. |
| A2 | `engine.py` | Jaccard similarity is compared against *proposer prose* vs *auditor JSON* — the auditor always outputs JSON, so similarity will always be low even when all claims are VERIFIED. The `all_verified` check rescues this, but the `similarity_score` stored in the report is misleading. |
| A3 | `engine.py` | `budget_usd` check calls `_estimate_cost_usd(report.total_tokens + proposer + auditor)` — double-counts tokens already in `report.total_tokens` |
| A4 | `scoring.py` | `_PROVIDER_RATES` is a linear scan `O(n)` list. A dict keyed on prefix with longest-match lookup would be cleaner and faster. |
| A5 | `ui/report.py` | `render_json` calls `compute_gotcha_score` + `compute_savings` every time it is called; these are pure functions but still re-compute on every render. Consider memoising or accepting pre-computed values. |
| A6 | `__init__.py` | `compute_gotcha_score`, `compute_savings`, `GotchaScore`, `SavingsReport` not exported from the top-level package — users can't do `from consensusflow import compute_gotcha_score`. |

### 2.5 Code Quality

| # | File | Issue |
|---|------|-------|
| Q1 | `tests/test_engine.py` | `AsyncMock`, `MagicMock` imported but unused |
| Q2 | `tests/test_engine_extended.py` | `AsyncMock`, `MagicMock`, `_jaccard_similarity` imported but unused; `call_count` variable unused in `test_fallback_used_when_primary_fails` |
| Q3 | `tests/test_protocol.py` | `pytest` imported but unused |
| Q4 | `tests/test_report.py` | `pytest` imported but unused |
| Q5 | `tests/test_scoring.py` | `ChainStatus`, `GotchaScore` imported but unused |
| Q6 | `prompts/loader.py` | `open(path, "r")` — explicit `"r"` mode is unnecessary (ruff UP015) |
| Q7 | `__init__.py` | Import order is un-sorted (ruff I001) |

---

## 3 · Improvement Plan

### Phase 1 — Bug Fixes (Implement Now)

| Priority | Fix |
|----------|-----|
| 🔴 P0 | **C2** — Fix streaming `done` event: yield the `VerificationReport` object in `stream()`, not `.to_dict()`. Update `cli.py` `done` handler accordingly. |
| 🔴 P0 | **C5** — Pass `penalty_weights` through `engine.run()` and `engine.stream()` to `compute_gotcha_score`. |
| 🔴 P0 | **C3** — Pass `timeout` to `acompletion` in the streaming path inside `_stream_with_timeout`. |
| 🟠 P1 | **C1** — Fix `.strip("```")` → use `re.sub(r"^\`\`\`(?:json)?\s*|\s*\`\`\`$", "", cleaned)` pattern (already imported). |
| 🟠 P1 | **A3** — Fix double-counting in budget check. |

### Phase 2 — Coverage (Implement Now)

| Priority | Fix |
|----------|-----|
| 🟠 P1 | **G1** — Add `tests/test_cli.py` covering argument parsing, `_handle_error`, `_print_gotcha_banner`, `_save_to_file`, and the `main()` routing logic (using `subprocess` or direct function calls). |
| 🟠 P1 | **G2** — Add `tests/test_models.py` covering `AtomicClaimSchema` validators, `VerificationReportSchema`, `VerifyRequestSchema`, and `report_to_schema()`. |
| 🟡 P2 | **G3** — Add `tests/test_litellm_client.py` covering mock/offline paths, retry logic, `_build_messages`, `_mock_response`. |
| 🟡 P2 | **G4** — Add tests for `register_prompt_override`, `clear_prompt_overrides`, and `PromptNotFoundError` in a `test_loader.py`. |

### Phase 3 — Type Modernisation (Implement Now)

- Auto-fix all `typing.List/Dict/Tuple/Optional` → builtin types across `engine.py`, `scoring.py`, `models.py`, `providers/litellm_client.py`, `prompts/loader.py`.
- Fix `AsyncIterator` import source.
- Remove unused imports in all files.

### Phase 4 — Architecture Improvements (Next Iteration)

| Priority | Improvement |
|----------|-------------|
| 🟡 P2 | **A1** — Use `litellm.token_counter()` when available for streaming token accounting; fall back to heuristic. |
| 🟡 P2 | **A2** — Store `proposer_answer_similarity` separately from `auditor_json_similarity`; compute claim-based consensus ratio instead for the report. |
| 🟡 P2 | **A4** — Convert `_PROVIDER_RATES` to a prefix-keyed dict with sorted longest-first matching. |
| 🟢 P3 | **A5** — Accept optional `gotcha_score` / `savings` kwargs in `render_json` / `render_markdown` to avoid re-computation. |
| 🟢 P3 | **A6** — Export `compute_gotcha_score`, `compute_savings`, `GotchaScore`, `SavingsReport` from `__init__.py`. |

### Phase 5 — Developer Experience (Next Iteration)

| Improvement | Value |
|------------|-------|
| Add `consensusflow score` sub-command: takes a saved JSON report and prints/re-computes the Gotcha Score | Enables post-hoc scoring |
| Add `--verbose` flag to CLI that shows the full chain trace inline | Debugging |
| Add `CONTRIBUTING.md` with dev setup, testing, and PR guidelines | Open-source health |
| Add GitHub Actions CI workflow (`.github/workflows/ci.yml`) | Automated test gate |
| Add `py.typed` marker file for PEP 561 compliance | Type-checker support |
| Consider `async def verify_many(prompts)` for batch verification | Power user feature |

---

## 4 · Metrics Summary

| Category | Before | After Phase 1–3 Target |
|----------|--------|----------------------|
| Tests passing | 120 / 120 | 160+ / 160+ |
| Coverage | 65 % | 85 %+ |
| Ruff errors | 114 | < 5 (style-only) |
| Critical bugs | 5 | 0 |
| Unused imports | 12 | 0 |
