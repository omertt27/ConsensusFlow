# ConsensusFlow

**Multi-model hallucination detection for production AI.**

[![PyPI](https://img.shields.io/pypi/v/consensusflow?color=blue&label=pip%20install%20consensusflow)](https://pypi.org/project/consensusflow/)
[![Python](https://img.shields.io/pypi/pyversions/consensusflow)](https://pypi.org/project/consensusflow/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Benchmark](https://img.shields.io/badge/CF--BENCH--50-50%2F50%20100%25-brightgreen)](#benchmarks)
[![Tests](https://img.shields.io/badge/tests-214%20passing-brightgreen)](#)

ConsensusFlow chains multiple LLMs in sequence — **Proposer → Auditor → Resolver** — to verify every atomic claim and catch hallucinations before they reach your users.

> **50/50 on CF-BENCH-50** — catches 100% of hallucination traps across 7 general-knowledge domains with only OpenAI + Gemini. [See benchmarks →](#benchmarks)

```python
from consensusflow import verify

report = await verify("Who founded Microsoft?")
print(report.final_answer)      # Verified answer
print(report.corrected_count)   # How many facts were fixed
```

> **This is a read-only open-source release.**  
> Issues and pull requests are not monitored. See [License](#license).

---

## The problem

A single LLM cannot verify its own output. GPT-4o hallucinates on factual queries. Gemini gets dates wrong. Claude invents details. Checking everything manually does not scale.

ConsensusFlow solves this with an adversarial multi-model pipeline where each model's job is to find flaws in the previous one.

---

## How it works

```
Prompt
  │
  ▼
┌───────────┐    ┌──────────────────┐    ┌──────────┐    ┌──────────┐
│ Proposer  │───▶│ Claim Extractor  │───▶│ Auditor  │───▶│ Resolver │
│  gpt-4o   │    │   gpt-4o-mini    │    │  gemini  │    │  gpt-4o  │
└───────────┘    └──────────────────┘    └──────────┘    └──────────┘
                                               │               │
                                          corrections    final answer
                                               └───────────────┘
                                                       │
                                               ┌───────────────┐
                                               │ VerificationReport │
                                               │ + claim audit trail│
                                               └───────────────┘
```

1. **Proposer** — primary model answers the question
2. **Claim Extractor** — fast model breaks the answer into individual verifiable claims
3. **Auditor** — adversarial model checks every claim with a "Negative Reward" mandate: *find flaws or be penalised for low effort*
4. **Resolver** — synthesis model writes the final corrected answer

The pipeline runs with just **2 models** out of the box (OpenAI + Gemini). The resolver defaults to reusing the proposer, so no Anthropic key is required.

---

## Installation

```bash
pip install consensusflow
```

Create a `.env` file in your project root:

```ini
# Required (minimum — works with 2 models)
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AIza...

# Optional — unlocks a distinct resolver model
ANTHROPIC_API_KEY=sk-ant-...
```

ConsensusFlow loads `.env` automatically via `python-dotenv`.

---

## Quick start

### One-liner

```python
import asyncio
from consensusflow import verify

report = asyncio.run(verify("What is the capital of Australia?"))
print(report.final_answer)
# → "The capital of Australia is Canberra."
```

### 2-model chain (OpenAI + Gemini only)

```python
report = asyncio.run(verify(
    "Who invented the World Wide Web?",
    chain=["gpt-4o", "gemini/gemini-2.5-flash"],
    # resolver automatically reuses gpt-4o
))
```

### 3-model chain (add a distinct resolver)

```python
report = asyncio.run(verify(
    "How many bones are in the adult human body?",
    chain=[
        "gpt-4o",                        # Proposer  — OpenAI
        "gemini/gemini-2.5-flash",       # Auditor   — Google
        "claude-3-7-sonnet-20250219",    # Resolver  — Anthropic
    ]
))
```

### Inspect the audit trail

```python
for claim in report.atomic_claims:
    print(f"[{claim.status.value}] {claim.text}")
    if claim.note:
        print(f"  → {claim.note}")

# Example output:
# [VERIFIED]  The adult human body has 206 bones.
# [CORRECTED] The number does not change after age 25.
#   → Earlier draft said "207–213 depending on age" which is incorrect.
```

### CLI

```bash
# Basic (uses default chain: gpt-4o → gemini/gemini-2.5-flash → gpt-4o-mini)
consensusflow "Who wrote Don Quixote?"

# 2-model chain (resolver automatically reuses the proposer)
consensusflow "Explain general relativity" \
  --chain gpt-4o gemini/gemini-2.5-flash \
  --output markdown \
  --save report.md

# 3-model chain (explicit resolver)
consensusflow "Summarise the French Revolution" \
  --chain gpt-4o gemini/gemini-2.5-flash claude-3-7-sonnet-20250219

# Streaming
consensusflow "Summarise the French Revolution" --stream

# With budget cap
consensusflow "..." --budget 0.05 --fallback gpt-4o-mini gemini/gemini-2.5-flash
```

### Streaming API

```python
from consensusflow import SequentialChain

chain = SequentialChain(chain=["gpt-4o", "gemini/gemini-2.5-flash"])

async for event in chain.stream("What year did the Berlin Wall fall?"):
    if event["event"] == "proposer_chunk":
        print(event["data"], end="", flush=True)
    elif event["event"] == "early_exit":
        print("\n⚡ 100% consensus — resolver skipped!")
    elif event["event"] == "done":
        report = event["data"]
        print(f"\nScore: {report.gotcha_score}/100")
```

---

## Claim statuses

Every claim the Auditor inspects receives one of five verdicts:

| Status | Meaning |
|--------|---------|
| `VERIFIED` | Factually correct and confirmed |
| `CORRECTED` | Wrong — corrected text provided |
| `NUANCED` | Correct but missing critical context |
| `DISPUTED` | Cannot be confirmed; evidence contradictory |
| `REJECTED` | Demonstrably false |

---

## Gotcha Score

Every report gets a **Gotcha Score** (0–100) — a single shareable metric:

```python
from consensusflow.core.scoring import compute_gotcha_score

score = compute_gotcha_score(report)
print(score.score)    # e.g. 84
print(score.grade)    # e.g. "A"
print(score.label)    # e.g. "Highly Reliable"
print(score.share_text)
# "ConsensusFlow caught 2 hallucinations! Score: 84/100 🟢"
```

| Score | Grade | Label |
|-------|-------|-------|
| 95–100 | A+ | Fully Verified |
| 85–94 | A | Highly Reliable |
| 72–84 | B | Mostly Reliable |
| 55–71 | C | Use With Caution |
| 35–54 | D | Significant Errors |
| 0–34 | F | Do Not Trust |

---

## Early exit & cost savings

When the Auditor agrees with the Proposer above a configurable similarity threshold (default 92%), the Resolver is **skipped entirely** — saving ~33% of tokens on that query.

```python
report = asyncio.run(verify("What is the chemical symbol for gold?"))

if report.early_exit:
    print(f"⚡ Resolver skipped — saved {report.saved_tokens} tokens")
    print(f"   Estimated savings: ${report.saved_cost_usd:.5f}")
```

From the benchmark run:

| Mode | Cost/query |
|------|-----------|
| 2-model lightweight | **$0.036** |
| 3-model full run | **$0.036** |

*(Early exits at 4% in the benchmark; higher on simple/well-known facts.)*

---

## Benchmarks

**CF-BENCH-50** — 50 general-domain hallucination traps across 7 categories.  
Run: **24 March 2026** · Chain: `gpt-4o → gemini/gemini-2.5-flash` (2 models, no Claude required)  
Full results: [`examples/benchmark_results.json`](examples/benchmark_results.json)

> **ConsensusFlow catches 100% of hallucination traps in CF-BENCH-50 across 7 general-knowledge domains.**

| Category | Pass | Total | % |
|----------|:----:|:-----:|:-:|
| World Geography | 8 | 8 | 100% |
| Science & Physics | 8 | 8 | 100% |
| Technology & Computing | 8 | 8 | 100% |
| History | 9 | 9 | 100% |
| Literature & Art | 7 | 7 | 100% |
| Medicine & Biology | 5 | 5 | 100% |
| Economics & Business | 5 | 5 | 100% |
| **Overall** | **50** | **50** | **100%** |

**Run stats:**

| Metric | Value |
|--------|-------|
| Accuracy | **100%** (50/50) |
| Early exits (resolver skipped) | 2/50 (4%) |
| Avg tokens / query | 5,242 |
| Avg latency / query | 16.3 s |
| Avg cost / query — 2-model | **$0.0356** |
| Avg cost / query — 3-model est. | $0.0357 |
| Total benchmark cost | $1.78 |

Reproduce it yourself:

```bash
python examples/hallucination_benchmark.py --output results.json
```

Add a third model (e.g. Claude as resolver) with `--chain`:

```bash
python examples/hallucination_benchmark.py \
  --chain gpt-4o gemini/gemini-2.5-flash claude-3-7-sonnet-20250219 \
  --output results_3model.json
```

---

## Advanced features

### Response caching

```python
chain = SequentialChain(
    chain=["gpt-4o", "gemini/gemini-2.5-flash"],
    enable_cache=True,
    cache_ttl=3600,       # 1 hour
    cache_maxsize=256,
)
```

### Budget guard

```python
# Raise BudgetExceededError if cost exceeds $0.10 before resolver
report = await verify("...", budget_usd=0.10)
```

### Webhook delivery

```python
# POST the full JSON report to your endpoint after every run
chain = SequentialChain(
    chain=["gpt-4o", "gemini/gemini-2.5-flash"],
    webhook_url="https://yourapp.example.com/hooks/verification",
)
```

### Fallback chains

```python
# If gpt-4o fails, fall back to gpt-4o-mini
report = await verify(
    "...",
    chain=["gpt-4o", "gemini/gemini-2.5-flash"],
    fallback_chain=["gpt-4o-mini", "gemini/gemini-2.5-flash"],
)
```

### Local models (Ollama)

```python
report = await verify(
    "...",
    chain=["ollama/llama3.3", "ollama/mistral"],
)
```

---

## Supported models

ConsensusFlow uses [LiteLLM](https://litellm.ai) — any of its 100+ providers work as a drop-in chain member:

```
OpenAI      gpt-4o, gpt-4o-mini, o1, o3-mini …
Google      gemini/gemini-2.5-flash, gemini/gemini-1.5-pro …
Anthropic   claude-3-7-sonnet-20250219, claude-3-5-sonnet-20241022 …
Cohere      command-r-plus, command-r …
Mistral     mistral/mistral-large-latest …
Ollama      ollama/llama3.3, ollama/phi3 … (local, no API key)
Azure       azure/gpt-4o …
```

---

## Project structure

```
consensusflow/
├── core/
│   ├── engine.py          # SequentialChain, verify(), stream()
│   ├── protocol.py        # VerificationReport, AtomicClaim, StepResult
│   ├── scoring.py         # GotchaScore, SavingsReport, compute_savings()
│   ├── models.py          # Pydantic models
│   ├── cache.py           # MemoryCache / NullCache
│   └── storage.py         # Persistent run storage
├── providers/
│   └── litellm_client.py  # Async LiteLLM gateway with retry + timeout
├── prompts/
│   ├── adversarial.md     # Auditor "Negative Reward" system prompt
│   ├── synthesis.md       # Resolver system prompt
│   ├── extractor.md       # Atomic claim extractor prompt
│   └── loader.py
├── ui/
│   └── report.py          # Markdown / terminal / JSON renderers
└── cli.py                 # CLI entry point
backend/
└── main.py                # FastAPI server (REST + streaming)
frontend/
└── src/                   # React dashboard (Vite)
examples/
├── hallucination_benchmark.py   # CF-BENCH-50 benchmark runner
└── travel_verify.py
tests/                     # 214 passing tests
```

---

## Running the backend & frontend

```bash
# Backend (FastAPI)
pip install -e ".[server]"
uvicorn backend.main:app --reload
# → http://localhost:8000

# Frontend (React + Vite)
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

---

## License

MIT © 2026 ConsensusFlow.

Permission is hereby granted, free of charge, to any person obtaining a copy of this software to use, copy, modify, merge, publish, and distribute it, subject to the following conditions: the above copyright notice and this permission notice shall be included in all copies or substantial portions of the software.

**This repository does not accept issues, pull requests, or merge requests.** The source code is published for transparency and personal/commercial use under the MIT licence. No support is provided.
