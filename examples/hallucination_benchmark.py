"""
hallucination_benchmark.py — Run ConsensusFlow against 50 known-hallucination queries.

Works out-of-the-box with only OPENAI_API_KEY + GEMINI_API_KEY.
Any third model (Claude, Mistral, Cohere, local Ollama…) can be
plugged in via --chain.

Usage:
    python examples/hallucination_benchmark.py
    python examples/hallucination_benchmark.py --output results.json
    python examples/hallucination_benchmark.py --chain gpt-4o gemini/gemini-2.5-flash claude-3-5-sonnet-20241022
    python examples/hallucination_benchmark.py --concurrency 5
    python examples/hallucination_benchmark.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time

# Support running directly from the examples/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── CF-BENCH-50 dataset ───────────────────────────────────────
# General-domain hallucination benchmark — no travel, no single city.
# Each entry: (query, known_correct_answer_fragment, hallucination_trap)
# expected_fragment is matched case-insensitively as a regex.
BENCHMARK_50 = [
    # ── World Geography (1–8) ─────────────────────────────────
    ("What is the capital of Australia?",
     r"canberra", "hallucination: models say Sydney or Melbourne"),
    ("What is the capital of Canada?",
     r"ottawa", "hallucination: models say Toronto"),
    ("What is the capital of Brazil?",
     r"bras[ií]lia", "hallucination: models say São Paulo or Rio"),
    ("Which country has the largest land area in the world?",
     r"russia", "basic fact"),
    ("What is the smallest country in the world by area?",
     r"vatican", "basic fact"),
    ("What ocean lies to the east of the United States?",
     r"atlantic", "basic geography"),
    ("What continent is Egypt in?",
     r"africa", "hallucination: models sometimes say Middle East as a continent"),
    ("Which two countries share the longest land border in the world?",
     r"canada|united states|us|usa", "hallucination: models say Russia/China"),

    # ── Science & Physics (9–16) ──────────────────────────────
    ("What is the chemical symbol for gold?",
     r"\bau\b", "basic chemistry"),
    ("How many planets are in our solar system?",
     r"8|eight", "hallucination: models say 9 including Pluto"),
    ("What is the most abundant gas in Earth's atmosphere?",
     r"nitrogen", "hallucination: models say oxygen"),
    ("What is the boiling point of water at sea level in Celsius?",
     r"100", "basic physics"),
    ("How many bones are in the adult human body?",
     r"206", "hallucination: models say 207 or 208"),
    ("What gas do plants absorb during photosynthesis?",
     r"co2|carbon dioxide", "basic biology"),
    ("What is the atomic number of carbon?",
     r"\b6\b", "hallucination: models say 12 (confusing with atomic mass)"),
    ("Who proposed the theory of general relativity?",
     r"einstein", "basic fact"),

    # ── Technology & Computing (17–24) ────────────────────────
    ("Who invented the World Wide Web?",
     r"berners.lee|tim berners", "hallucination: models say Al Gore or Vint Cerf"),
    ("In what year was the first iPhone released?",
     r"2007", "hallucination: models say 2006 or 2008"),
    ("In what year was Python first released?",
     r"1991", "hallucination: models say 1995"),
    ("Who founded Microsoft?",
     r"gates|allen|bill gates|paul allen", "hallucination: models omit Allen"),
    ("What does HTTP stand for?",
     r"hypertext transfer protocol", "basic acronym"),
    ("What does CPU stand for?",
     r"central processing unit", "basic acronym"),
    ("Who founded Apple Computer?",
     r"jobs|wozniak", "hallucination: models omit Wozniak or Jobs"),
    ("What programming language is most widely used for data science?",
     r"python", "basic fact"),

    # ── History (25–33) ───────────────────────────────────────
    ("In what year did World War II end?",
     r"1945", "basic history"),
    ("Who was the first person to walk on the Moon?",
     r"armstrong|neil armstrong", "hallucination: models sometimes say Buzz Aldrin"),
    ("In what year did the Berlin Wall fall?",
     r"1989", "basic history"),
    ("Who wrote the United States Declaration of Independence?",
     r"jefferson|thomas jefferson", "hallucination: models say Franklin or Adams"),
    ("In what year did the French Revolution begin?",
     r"1789", "basic history"),
    ("In what year did the Soviet Union officially dissolve?",
     r"1991", "hallucination: models say 1989 or 1990"),
    ("Who was the first President of the United States?",
     r"washington|george washington", "basic history"),
    ("In what year did man first land on the Moon?",
     r"1969", "hallucination: models say 1968 or 1970"),
    ("What was the name of the passenger ship that sank in April 1912?",
     r"titanic", "basic history"),

    # ── Literature & Art (34–40) ──────────────────────────────
    ("Who wrote the novel 1984?",
     r"orwell|george orwell", "basic literature"),
    ("Who wrote Romeo and Juliet?",
     r"shakespeare", "basic literature"),
    ("Who wrote Don Quixote?",
     r"cervantes", "basic literature"),
    ("Who painted the Mona Lisa?",
     r"da vinci|leonardo", "basic art history"),
    ("Who wrote the Harry Potter series?",
     r"rowling|j\.k\.", "basic literature"),
    ("What language has the most native speakers in the world?",
     r"mandarin|chinese", "hallucination: models say English or Spanish"),
    ("What is the most translated book in history?",
     r"bible", "basic fact"),

    # ── Medicine & Biology (41–45) ────────────────────────────
    ("What is the powerhouse of the cell?",
     r"mitochondria", "classic biology fact"),
    ("How many chromosomes do humans have?",
     r"46|23 pairs", "hallucination: models say 48"),
    ("What organ produces insulin in the human body?",
     r"pancreas", "hallucination: models say liver"),
    ("What is the most common blood type in the world?",
     r"o\+|o positive|type o", "hallucination: models say A+"),
    ("What is the average normal human body temperature in Celsius?",
     r"37", "basic medicine"),

    # ── Economics & Business (46–50) ──────────────────────────
    ("What does GDP stand for?",
     r"gross domestic product", "basic acronym"),
    ("Who wrote 'The Wealth of Nations'?",
     r"adam smith", "hallucination: models say Ricardo or Keynes"),
    ("What is the currency of Japan?",
     r"yen", "basic fact"),
    ("What does IPO stand for in finance?",
     r"initial public offering", "basic finance acronym"),
    ("Which stock exchange is the largest in the world by market capitalisation?",
     r"new york|nyse|nasdaq", "hallucination: models sometimes say Tokyo or London"),
]

assert len(BENCHMARK_50) == 50, f"Dataset must have 50 entries, got {len(BENCHMARK_50)}"

# Category boundaries for the breakdown report
CATEGORIES = [
    ("World Geography",       1,   8),
    ("Science & Physics",     9,  16),
    ("Technology & Computing",17,  24),
    ("History",               25,  33),
    ("Literature & Art",      34,  40),
    ("Medicine & Biology",    41,  45),
    ("Economics & Business",  46,  50),
]


async def verify_single(query: str, expected_pattern: str, chain: list[str]) -> dict:
    """Run one query through ConsensusFlow and check if expected pattern matches."""
    from consensusflow import verify
    from consensusflow.core.scoring import compute_savings

    try:
        t0 = time.monotonic()
        report = await verify(query, chain=chain)
        elapsed = time.monotonic() - t0

        final = report.final_answer.lower()
        passed = bool(re.search(expected_pattern.lower(), final))

        savings = compute_savings(report)

        return {
            "query": query,
            "expected_pattern": expected_pattern,
            "passed": passed,
            "early_exit": report.early_exit,
            "similarity_score": report.similarity_score,
            "total_tokens": report.total_tokens,
            "latency_s": round(elapsed, 2),
            "corrected_claims": report.corrected_count,
            "disputed_claims": report.disputed_count,
            "auditor_warning": bool(report.auditor_reliability_warning),
            "status": report.status.value,
            "cost_2model_usd": savings.cost_2model_usd,
            "cost_3model_usd": savings.cost_3model_usd,
            "saved_usd": savings.saved_usd,
        }
    except Exception as exc:
        return {
            "query": query,
            "expected_pattern": expected_pattern,
            "passed": False,
            "error": str(exc),
            "cost_2model_usd": 0.0,
            "cost_3model_usd": 0.0,
            "saved_usd": 0.0,
        }


async def run_benchmark(
    chain: list[str],
    output: str | None,
    concurrency: int = 3,
    dry_run: bool = False,
) -> None:
    dataset = BENCHMARK_50
    print(f"\n🧪 ConsensusFlow Benchmark — {len(dataset)} queries")
    print(f"🔗 Chain: {' → '.join(chain)}")
    print(f"⚡ Concurrency: {concurrency}")
    if dry_run:
        print("🔍 DRY RUN — no API calls will be made")
    print("=" * 70)

    if dry_run:
        for i, (query, pattern, trap) in enumerate(dataset, 1):
            print(f"[{i:3}] {query[:60]}  ← {trap}")
        print(f"\nTotal: {len(dataset)} queries ready to run.")
        return

    results: list[dict] = [{}] * len(dataset)
    passed_total = 0
    semaphore = asyncio.Semaphore(concurrency)

    async def bounded_verify(idx: int, query: str, pattern: str) -> None:
        async with semaphore:
            result = await verify_single(query, pattern, chain)
            results[idx] = result
            status = "✅" if result["passed"] else ("⚠️ " if result.get("auditor_warning") else "❌")
            err = f"  ERR: {result['error'][:60]}" if "error" in result else ""
            print(
                f"[{idx+1:3}/{len(dataset)}] {query[:52]:<52} {status}"
                f"  {result.get('latency_s', '?')}s{err}"
            )

    tasks = [
        bounded_verify(i, query, pattern)
        for i, (query, pattern, *_) in enumerate(dataset)
    ]
    await asyncio.gather(*tasks)

    # ── Summary ──────────────────────────────────────────────
    total = len(results)
    passed_total = sum(1 for r in results if r.get("passed"))
    accuracy = passed_total / total * 100
    early_exits = sum(1 for r in results if r.get("early_exit"))
    avg_tokens = sum(r.get("total_tokens", 0) for r in results) / total
    avg_latency = sum(r.get("latency_s", 0) for r in results) / total
    errors = [r for r in results if "error" in r]
    warnings = sum(1 for r in results if r.get("auditor_warning"))

    total_cost_2model = sum(r.get("cost_2model_usd", 0.0) for r in results)
    total_cost_3model = sum(r.get("cost_3model_usd", 0.0) for r in results)
    avg_cost_2model   = total_cost_2model / total
    avg_cost_3model   = total_cost_3model / total

    print("\n" + "=" * 70)
    print(f"📊 OVERALL: {passed_total}/{total} passed  ({accuracy:.1f}% accuracy)")
    print(f"⚡ Early exits: {early_exits} ({early_exits/total*100:.0f}%)")
    print(f"🪙 Avg tokens/query: {avg_tokens:.0f}")
    print(f"⏱  Avg latency/query: {avg_latency:.1f}s")
    print(f"💰 Cost per query  — 2-model (lightweight): ${avg_cost_2model:.4f}"
          f"   |   3-model (full): ${avg_cost_3model:.4f}")
    print(f"💰 Total run cost  — 2-model: ${total_cost_2model:.4f}"
          f"   |   3-model: ${total_cost_3model:.4f}")
    if warnings:
        print(f"⚠️  Auditor drift warnings: {warnings}")
    if errors:
        print(f"❌ Errors: {len(errors)}")
        for e in errors[:5]:
            print(f"   • {e['query'][:55]}: {e.get('error','?')[:80]}")

    # ── Per-category breakdown ────────────────────────────────
    print("\n📂 Per-category breakdown:")
    print(f"  {'Category':<28} {'Pass':>5}  {'Total':>5}  {'%':>6}")
    print("  " + "-" * 48)
    for cat_name, start, end in CATEGORIES:
        cat_results = results[start - 1 : end]
        cat_pass = sum(1 for r in cat_results if r.get("passed"))
        cat_total = len(cat_results)
        pct = cat_pass / cat_total * 100 if cat_total else 0
        bar = "🟢" if pct >= 80 else ("🟡" if pct >= 60 else "🔴")
        print(f"  {bar} {cat_name:<26} {cat_pass:>5}  {cat_total:>5}  {pct:>5.0f}%")

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "accuracy": accuracy,
                    "passed": passed_total,
                    "total": total,
                    "chain": chain,
                    "early_exits": early_exits,
                    "avg_tokens": avg_tokens,
                    "avg_latency_s": avg_latency,
                    "auditor_warnings": warnings,
                    "cost_2model_usd_total": round(total_cost_2model, 6),
                    "cost_3model_usd_total": round(total_cost_3model, 6),
                    "avg_cost_2model_usd":   round(avg_cost_2model, 6),
                    "avg_cost_3model_usd":   round(avg_cost_3model, 6),
                    "results": results,
                },
                fh,
                indent=2,
            )
        print(f"\n✅ Full results saved to: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ConsensusFlow Hallucination Benchmark")
    parser.add_argument("--output", metavar="FILE", help="Save results as JSON")
    parser.add_argument(
        "--chain",
        nargs="+",
        metavar="MODEL",
        default=[
            "gpt-4o",                   # Proposer  — OpenAI
            "gemini/gemini-2.5-flash",  # Auditor   — Google
            # Resolver defaults to proposer when only 2 models are given
        ],
        help=(
            "2 or 3 LiteLLM model strings: proposer auditor [resolver]. "
            "When only 2 are given the resolver automatically reuses the proposer. "
            "Examples: --chain gpt-4o gemini/gemini-2.5-flash "
            "          --chain gpt-4o gemini/gemini-2.5-flash claude-3-5-sonnet-20241022"
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Number of queries to run in parallel (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print all queries without making API calls",
    )
    args = parser.parse_args()
    if len(args.chain) not in (2, 3):
        parser.error("--chain requires exactly 2 or 3 model strings")
    asyncio.run(run_benchmark(args.chain, args.output, args.concurrency, args.dry_run))


if __name__ == "__main__":
    main()
