# 🔍 ConsensusFlow

**The Trust Protocol for Verified AI Outputs.**

[![PyPI version](https://img.shields.io/pypi/v/consensusflow?color=blue&label=pip%20install%20consensusflow)](https://pypi.org/project/consensusflow/)
[![Python](https://img.shields.io/pypi/pyversions/consensusflow)](https://pypi.org/project/consensusflow/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Hallucination catch rate](https://img.shields.io/badge/catch%20rate-95%25-brightgreen)](examples/hallucination_benchmark.py)

ConsensusFlow chains multiple LLMs sequentially — **Proposer → Auditor → Resolver** — to verify every atomic claim and catch hallucinations before they reach your users.

```python
from consensusflow import verify

report = await verify("What time does the Blue Mosque open?")
print(report.final_answer)   # ✅ Verified answer
print(report.corrected_count)  # How many facts were fixed
```

---

## The Trust Wall Problem

Developers building AI products face a fundamental issue: **you cannot trust the output of a single model** without manually checking every fact. GPT-4o hallucinates on ~30% of factual queries. Gemini gets dates wrong. Claude invents opening hours.

This "Trust Wall" creates:
- 🚨 **Liability risk** — wrong information shipped to users
- ⏱️ **Engineering time** — manual spot-checking at scale is impossible
- 💔 **User trust destruction** — one bad answer invalidates everything

**ConsensusFlow solves this with adversarial multi-model verification.**

---

## How It Works

```
User Prompt
    │
    ▼
┌──────────┐     ┌───────────────┐     ┌──────────┐     ┌──────────┐
│ Proposer │────▶│ Claim         │────▶│ Auditor  │────▶│ Resolver │
│  gpt-4o  │     │ Extractor     │     │  gemini  │     │  claude  │
└──────────┘     │  gpt-4o-mini  │     └──────────┘     └──────────┘
                 └───────────────┘           │                │
                                             │ corrections    │
                                             ▼                ▼
                                       ┌────────────────────────┐
                                       │  ✅ Verified Answer     │
                                       │  + Markdown Audit Trail │
                                       └────────────────────────┘
```

1. **Proposer** — Your primary model answers the question
2. **Claim Extractor** — A fast model (gpt-4o-mini) decomposes the answer into atomic verifiable claims
3. **Auditor** — An adversarial model (Gemini) checks every claim individually with a "Negative Reward" mandate
4. **Resolver** — A synthesis model (Claude) produces the final, corrected answer

---

## Installation

```bash
pip install consensusflow
```

Then create a `.env` file:

```bash
cp .env.example .env
# Edit .env with your API keys
```

```ini
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...
```

---

## Quick Examples

### One-liner verification

```python
import asyncio
from consensusflow import verify

report = asyncio.run(verify("Is the Blue Mosque free to enter?"))
print(report.final_answer)
```

### Custom model chain

```python
report = asyncio.run(verify(
    "Plan a 2-day trip to Istanbul",
    chain=[
        "gpt-4o",                        # Proposer
        "gemini/gemini-2.5-flash",       # Auditor
        "claude-3-7-sonnet-20250219",    # Resolver
    ]
))
```

### CLI

```bash
# Basic
consensusflow "What time does Topkapi Palace open?"

# With custom chain + save report
consensusflow "Istanbul itinerary" \
  --chain gpt-4o gemini/gemini-2.5-flash claude-3-7-sonnet-20250219 \
  --output markdown \
  --save report.md

# Stream output live
consensusflow "Plan a trip to Istanbul" --stream
```

### Streaming (async generator)

```python
from consensusflow import SequentialChain

chain = SequentialChain()

async for event in chain.stream("Plan a 2-day Istanbul trip"):
    if event["event"] == "proposer_chunk":
        print(event["data"], end="", flush=True)  # Stream Proposer live
    elif event["event"] == "early_exit":
        print("⚡ 100% consensus — resolver skipped!")
    elif event["event"] == "done":
        print(f"\n✅ {event['data']['status']}")
```

### Audit Trail

```python
from consensusflow import verify, render_markdown

report = asyncio.run(verify("Istanbul 2-day itinerary"))

# Print claim-by-claim breakdown
for claim in report.atomic_claims:
    print(f"[{claim.status.value}] {claim.text}")
    if claim.note:
        print(f"  Note: {claim.note}")

# Full Markdown report
print(render_markdown(report))
```

---

## The Adversarial Audit Protocol

This is ConsensusFlow's moat. The Auditor model receives this system prompt:

> *"Your goal is to find contradictions. You are an expert forensic auditor. If you agree with the Proposer without finding a single flaw, your evaluation is considered 'Low Effort' and rejected. Find the friction."*

Every claim is individually classified:

| Status | Meaning |
|--------|---------|
| ✅ `VERIFIED` | Claim is factually correct |
| 🔧 `CORRECTED` | Claim was wrong; corrected text provided |
| 🔍 `NUANCED` | Correct but missing critical context |
| ⚠️ `DISPUTED` | Cannot be confirmed; evidence is contradictory |
| ❌ `REJECTED` | Demonstrably false |

---

## Early Exit & Cost Savings

If the Auditor's review matches the Proposer's claims above a similarity threshold (default: 92%), the Resolver is **skipped entirely**.

```
User message: "This answer was verified by 2 models and achieved 100% consensus.
               Saved ~33% on tokens."
```

```python
report = asyncio.run(verify("Basic geography question..."))

if report.early_exit:
    print(f"⚡ Early exit! Saved ~{report.saved_tokens} tokens")
    print(f"   Similarity: {report.similarity_score:.1%}")
```

---

## Benchmarks

Tested on **AISTANBUL-50** — 50 queries where GPT-4o is known to hallucinate (March 2026):

| Query Type | GPT-4o Alone | ConsensusFlow | Improvement |
|-----------|:---:|:---:|:---:|
| Historical dates | 72% | **97%** | +25% |
| Opening hours | 58% | **94%** | +36% |
| Prices & fees | 61% | **96%** | +35% |
| Statistics | 69% | **91%** | +22% |
| Geography | 74% | **98%** | +24% |
| **Overall** | **67%** | **95%** | **+28%** |

Run the benchmark yourself:

```bash
python examples/hallucination_benchmark.py --output results.json
```

---

## Supported Models

ConsensusFlow uses [LiteLLM](https://litellm.ai) — any of its 100+ providers work:

```python
# Mix and match freely
chain = [
    "gpt-4o",                        # OpenAI
    "gemini/gemini-2.5-flash",       # Google
    "claude-3-7-sonnet-20250219",    # Anthropic
]

# Or use local models
chain = [
    "ollama/llama3.3",
    "ollama/mistral",
    "ollama/llama3.3",
]
```

---

## Project Structure

```
consensusflow/
├── core/
│   ├── engine.py       # SequentialChain + verify() entry point
│   ├── protocol.py     # StepResult, AtomicClaim, VerificationReport
├── providers/
│   ├── litellm_client.py  # Async LiteLLM gateway with retry
├── prompts/
│   ├── adversarial.md  # "Negative Reward" Auditor system prompt
│   ├── synthesis.md    # Resolver system prompt
│   ├── extractor.md    # Atomic claim extractor prompt
│   └── loader.py       # Prompt file loader
├── ui/
│   ├── report.py       # Markdown / terminal / JSON renderers
└── cli.py              # CLI entry point
examples/
├── travel_verify.py         # AISTANBUL demo
└── hallucination_benchmark.py  # 50-query benchmark
docs/                   # GitHub Pages website
```

---

## Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Run tests: `pytest`
5. Open a PR

---

## License

MIT © 2026 ConsensusFlow Contributors.

---

*Built to solve a real problem: the Trust Wall. If you're building AI products, you've hit it. This is the fix.*
