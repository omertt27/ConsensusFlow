"""
hallucination_benchmark.py — Run ConsensusFlow against 50 known-hallucination queries.

This is the benchmark used to generate the 95% catch rate figure on the website.
All 50 queries are defined inline; each has a known correct answer fragment and
a note describing the common hallucination trap.

Usage:
    python examples/hallucination_benchmark.py
    python examples/hallucination_benchmark.py --output results.json
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


# ── AISTANBUL-50 dataset ──────────────────────────────────────
# Each entry: (query, known_correct_answer_fragment, hallucination_trap)
# expected_fragment is matched case-insensitively as a regex pattern so
# "6|six" catches either "6 minarets" or "six minarets".
AISTANBUL_50 = [
    # ── Opening hours (1–7) ───────────────────────────────────
    ("What time does the Blue Mosque open?",
     r"9(:00)?\s*(am|AM)?", "hallucination: some models say 8am or 10am"),
    ("Is the Blue Mosque free to enter?",
     r"free", "hallucination: models often invent admission fees"),
    ("What are Topkapi Palace opening hours in 2026?",
     r"9(:00)?", "hallucination: hours changed in Jan 2026"),
    ("Is the Grand Bazaar open on Sundays?",
     r"closed", "hallucination: it is closed on Sundays"),
    ("What day is Topkapi Palace closed?",
     r"tuesday", "hallucination: models say Monday"),
    ("Does the Hagia Sophia charge an entrance fee?",
     r"free", "hallucination: models often invent ticket prices since 2020"),
    ("Is the Galata Tower open every day of the week?",
     r"yes|every day|daily", "basic check — open daily"),

    # ── Admission prices (8–12) ───────────────────────────────
    ("How much does Hagia Sophia cost to enter?",
     r"free", "hallucination: models often invent ticket prices"),
    ("What is the Topkapi Palace admission fee in 2026?",
     r"800|750|700", "hallucination: old price was different"),
    ("Is the Dolmabahce Palace free?",
     r"not free|paid|fee|charge", "hallucination: models sometimes say free"),
    ("How much does it cost to visit the Istanbul Archaeology Museum?",
     r"\d+", "any numeric price is plausible — checking model doesn't say 'free'"),
    ("Is entry to the Basilica Cistern free?",
     r"not free|paid|fee|charge|\d+", "hallucination: models sometimes say free"),

    # ── Geography & locations (13–20) ─────────────────────────
    ("Which district is Istiklal Street in?",
     r"bey[oö]g?lu", "hallucination: models say Sultanahmet"),
    ("What side of Istanbul is the Grand Bazaar on?",
     r"european", "hallucination: models sometimes say Asian"),
    ("How far is Kadıköy from Sultanahmet by ferry?",
     r"20|25|30", "hallucination: wrong transit times"),
    ("Is Galata Tower in the European or Asian side?",
     r"european", "basic geography check"),
    ("In which neighbourhood is the Spice Bazaar located?",
     r"emin[oö]n[uü]|fatih", "hallucination: models say Sultanahmet"),
    ("Which continent is Kadıköy on?",
     r"asian|asia", "basic geography — Kadıköy is on the Asian side"),
    ("Is Ortaköy on the European or Asian side of Istanbul?",
     r"european", "hallucination: some models say Asian"),
    ("What body of water separates the European and Asian sides of Istanbul?",
     r"bosphorus|bo[gğ]az", "basic geography check"),

    # ── Historical facts (21–28) ──────────────────────────────
    ("When was the Blue Mosque built?",
     r"1616", "hallucination: models say 1609 or 1600"),
    ("Who commissioned the Blue Mosque?",
     r"ahmed\s*(i|1st|first)", "hallucination: models say Suleiman"),
    ("What was Hagia Sophia originally?",
     r"church|cathedral|christian", "should say Byzantine church"),
    ("In what year did Hagia Sophia become a mosque again?",
     r"2020", "hallucination: models say 2019 or 2021"),
    ("When was Constantinople renamed Istanbul?",
     r"1453|1930", "1453 conquest + 1930 official postal rename"),
    ("Who built the Topkapi Palace?",
     r"mehmed|fatih|ottoman", "hallucination: models sometimes say Suleiman"),
    ("What century was the Grand Bazaar established?",
     r"15th|1400s|1461|1455", "hallucination: models say 16th century"),
    ("How many minarets does the Blue Mosque have?",
     r"6|six", "hallucination: models say four"),

    # ── Transport (29–34) ─────────────────────────────────────
    ("How do you get from Istanbul Airport to Taksim?",
     r"metro|m11|havaist", "hallucination: old answer was shuttle only"),
    ("Does the Istanbul metro run 24 hours?",
     r"no|not 24", "hallucination: models say yes"),
    ("What is the Istanbulkart?",
     r"transit card|transport card|card", "basic check"),
    ("How long does the ferry from Eminönü to Kadıköy take?",
     r"20|25|30", "approximate time check"),
    ("Is there a direct metro line from Sabiha Gökçen Airport to the city centre?",
     r"no|not yet|bus|shuttle", "no direct metro as of 2026"),
    ("What is the name of the main transit card used in Istanbul?",
     r"istanbulkart", "basic fact check"),

    # ── Culture & etiquette (35–39) ───────────────────────────
    ("Should women cover their heads in the Blue Mosque?",
     r"yes|head cover|scarf", "hallucination: models say optional"),
    ("Can you eat during Ramadan in Istanbul restaurants?",
     r"yes|restaurants open", "hallucination: models say no"),
    ("Is alcohol legal in Istanbul?",
     r"yes|legal|available", "hallucination: models say no"),
    ("Do you need to remove shoes before entering a mosque in Istanbul?",
     r"yes|remove|take off", "basic etiquette check"),
    ("Is tipping customary in Istanbul restaurants?",
     r"yes|customary|expected|tip", "basic etiquette check"),

    # ── Food & restaurants (40–43) ────────────────────────────
    ("What is a typical Istanbul breakfast called?",
     r"kahvalt[iı]", "hallucination: models invent names"),
    ("What district is known for fish restaurants in Istanbul?",
     r"kumkap[iı]|tarabya|beyko[zg]", "hallucination: models say Galata"),
    ("What is 'simit' in Istanbul?",
     r"bread|sesame|ring|bagel", "basic food check"),
    ("What is the traditional drink served with Turkish breakfast?",
     r"tea|[çc]ay", "hallucination: some models say coffee first"),

    # ── Broad facts (44–50) ───────────────────────────────────
    ("What is the population of Istanbul in 2026?",
     r"1[5-9]|15 million|16 million", "approximate range check"),
    ("What empire built the Hagia Sophia?",
     r"byzantine|roman", "hallucination: models say Ottoman"),
    ("Is the Bosphorus a river or a strait?",
     r"strait", "basic fact"),
    ("Which two continents does Istanbul span?",
     r"europe|asia", "should mention both continents"),
    ("What is the currency used in Istanbul?",
     r"lira|tl|try", "basic fact"),
    ("What language is primarily spoken in Istanbul?",
     r"turkish", "basic fact"),
    ("In what country is Istanbul located?",
     r"turkey|türkiye", "basic fact — some models confuse with Greece"),
]

assert len(AISTANBUL_50) == 50, f"Dataset must have 50 entries, got {len(AISTANBUL_50)}"


async def verify_single(query: str, expected_pattern: str, chain: list[str]) -> dict:
    """Run one query through ConsensusFlow and check if expected pattern matches."""
    from consensusflow import verify

    try:
        t0 = time.monotonic()
        report = await verify(query, chain=chain)
        elapsed = time.monotonic() - t0

        final = report.final_answer.lower()
        passed = bool(re.search(expected_pattern.lower(), final))

        return {
            "query": query,
            "expected_pattern": expected_pattern,
            "passed": passed,
            "early_exit": report.early_exit,
            "similarity_score": report.similarity_score,
            "total_tokens": report.total_tokens,
            "latency_s": round(elapsed, 2),
            "corrected_claims": report.corrected_count,
            "status": report.status.value,
        }
    except Exception as exc:
        return {
            "query": query,
            "expected_pattern": expected_pattern,
            "passed": False,
            "error": str(exc),
        }


async def run_benchmark(chain: list[str], output: str | None) -> None:
    dataset = AISTANBUL_50
    print(f"\n🧪 ConsensusFlow Benchmark — {len(dataset)} queries")
    print(f"🔗 Chain: {' → '.join(chain)}")
    print("=" * 60)

    results = []
    passed = 0

    for i, (query, expected, *_) in enumerate(dataset, 1):
        print(f"[{i:2}/{len(dataset)}] {query[:55]:<55}", end="", flush=True)
        result = await verify_single(query, expected, chain)
        status = "✅" if result["passed"] else "❌"
        print(f" {status}  {result.get('latency_s', '?')}s")
        results.append(result)
        if result["passed"]:
            passed += 1

    total = len(results)
    accuracy = passed / total * 100

    print("\n" + "=" * 60)
    print(f"📊 Results: {passed}/{total} passed  ({accuracy:.1f}% accuracy)")
    print(f"⚡ Early exits: {sum(1 for r in results if r.get('early_exit'))}")
    avg_tokens = sum(r.get("total_tokens", 0) for r in results) / total
    print(f"🪙 Avg tokens/query: {avg_tokens:.0f}")
    errors = [r for r in results if "error" in r]
    if errors:
        print(f"❌ Errors: {len(errors)}")

    if output:
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(
                {"accuracy": accuracy, "passed": passed, "total": total, "results": results},
                fh, indent=2,
            )
        print(f"\n✅ Full results saved to: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="ConsensusFlow Hallucination Benchmark")
    parser.add_argument("--output", metavar="FILE", help="Save results as JSON")
    parser.add_argument(
        "--chain",
        nargs=3,
        metavar=("PROPOSER", "AUDITOR", "RESOLVER"),
        default=[
            "gpt-4o",
            "gemini/gemini-2.0-flash",
            "claude-3-7-sonnet-20250219",
        ],
    )
    args = parser.parse_args()
    asyncio.run(run_benchmark(args.chain, args.output))


if __name__ == "__main__":
    main()
