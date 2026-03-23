"""
travel_verify.py — AISTANBUL Demo

Demonstrates ConsensusFlow catching a real hallucination in an Istanbul
travel itinerary. This is the script shown in the website demo section.

Usage:
    python examples/travel_verify.py
    python examples/travel_verify.py --stream
    python examples/travel_verify.py --save report.md
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os

# Allow running from repo root without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


PROMPT = """
Plan a detailed 2-day itinerary for Istanbul, Turkey.
Include:
- The top 3 must-see attractions each day
- Opening hours and any entry fees
- Best neighbourhood to stay in
- One local restaurant recommendation per day
- Practical tips (transport, clothing, etiquette)
""".strip()


async def run_standard(chain: list[str], save: str | None) -> None:
    from consensusflow import verify
    from consensusflow.ui.report import render_terminal, render_markdown

    print("\n🔍 ConsensusFlow — AISTANBUL Itinerary Verification")
    print("=" * 56)
    print(f"📝 Prompt: {PROMPT[:80]}…")
    print(f"🔗 Chain:  {' → '.join(chain)}")
    print("=" * 56 + "\n")
    print("⏳ Running verification pipeline…\n")

    report = await verify(PROMPT, chain=chain)

    print(render_terminal(report))

    if save:
        md = render_markdown(report)
        with open(save, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"\n✅ Markdown report saved to: {save}")


async def run_streaming(chain: list[str]) -> None:
    from consensusflow import SequentialChain

    engine = SequentialChain(chain=chain)

    print("\n🔍 ConsensusFlow — Streaming Demo")
    print("=" * 56)

    async for event in engine.stream(PROMPT):
        etype = event["event"]
        data  = event["data"]

        if etype == "status":
            print(f"\n{data}", flush=True)
        elif etype == "proposer_chunk":
            print(data, end="", flush=True)
        elif etype == "claims_extracted":
            print(f"\n\n📋 Extracted {len(data)} atomic claims for audit…")
        elif etype == "auditor_chunk":
            print(data, end="", flush=True)
        elif etype == "early_exit":
            print(f"\n⚡ {data['message']}")
        elif etype == "resolver_chunk":
            print(data, end="", flush=True)
        elif etype == "done":
            summary = data.get("claim_summary", {})
            print(f"\n\n{'=' * 56}")
            print(f"✅ Status  : {data['status']}")
            print(f"🔬 Claims  : {summary.get('verified',0)} verified, "
                  f"{summary.get('corrected',0)} corrected, "
                  f"{summary.get('disputed',0)} disputed")
            print(f"🪙 Tokens  : {data['total_tokens']:,}")
            print(f"⏱  Latency : {data['total_latency_ms']:.0f} ms")


def main() -> None:
    parser = argparse.ArgumentParser(description="AISTANBUL Demo — ConsensusFlow")
    parser.add_argument("--stream", action="store_true", help="Stream output live")
    parser.add_argument("--save", metavar="FILE", help="Save Markdown report to file")
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

    try:
        if args.stream:
            asyncio.run(run_streaming(args.chain))
        else:
            asyncio.run(run_standard(args.chain, args.save))
    except KeyboardInterrupt:
        print("\n\nInterrupted.")


if __name__ == "__main__":
    main()
