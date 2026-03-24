"""
cli.py — ConsensusFlow command-line interface.

Usage:
    consensusflow "Your question here"
    consensusflow "Plan a trip to Istanbul" --chain gpt-4o gemini/gemini-2.5-flash claude-3-5-sonnet-20241022
    consensusflow "..." --output markdown > report.md
    consensusflow "..." --output json
    consensusflow "..." --stream
    consensusflow "..." --budget 0.10
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="consensusflow",
        description="🔍 ConsensusFlow — Multi-model verification pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  consensusflow "Is the Blue Mosque free to enter in 2026?"
  consensusflow "What year did the Berlin Wall fall?" --chain gpt-4o gemini/gemini-2.5-flash
  consensusflow "What time does Topkapi Palace open?" --chain gpt-4o gemini/gemini-2.5-flash claude-3-5-sonnet-20241022
  consensusflow "Plan a 2-day Istanbul trip" --output markdown > report.md
  consensusflow "..." --stream --output terminal
  consensusflow "..." --budget 0.05 --fallback gpt-4o-mini gemini/gemini-2.5-flash
        """,
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="The question or task to verify.",
    )
    parser.add_argument(
        "--chain",
        nargs="+",
        metavar="MODEL",
        default=None,
        help=(
            "2 or 3 LiteLLM model strings for the pipeline. "
            "2-model: PROPOSER AUDITOR (resolver reuses proposer). "
            "3-model: PROPOSER AUDITOR RESOLVER. "
            "Example: --chain gpt-4o gemini/gemini-2.5-flash"
        ),
    )
    parser.add_argument(
        "--fallback",
        nargs="+",
        metavar="MODEL",
        default=None,
        help=(
            "Fallback chain (2 or 3 models) used if the primary chain fails. "
            "Example: --fallback gpt-4o-mini gemini/gemini-2.5-flash"
        ),
    )
    parser.add_argument(
        "--extractor",
        default="gpt-4o-mini",
        metavar="MODEL",
        help="Fast model for atomic claim extraction (default: gpt-4o-mini).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.92,
        metavar="FLOAT",
        help="Similarity threshold for early exit (0.0–1.0, default: 0.92).",
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="USD",
        help="Abort before the resolver step if estimated cost exceeds this value (e.g. 0.10).",
    )
    parser.add_argument(
        "--output",
        choices=["terminal", "markdown", "json"],
        default="terminal",
        help="Output format (default: terminal).",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream output as it is generated.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour codes.",
    )
    parser.add_argument(
        "--save",
        metavar="FILE",
        help="Save report to a file (e.g. report.md).",
    )
    return parser


def _print_gotcha_banner(report, width: int = 70) -> None:
    """Print the Gotcha Score + Savings banner to stdout."""
    from consensusflow.core.scoring import compute_gotcha_score, compute_savings
    gs      = compute_gotcha_score(report)
    savings = compute_savings(report)
    sep = "─" * width
    print()
    print(sep)
    print(f"  {gs.emoji}  GOTCHA SCORE: {gs.score}/100  —  {gs.label}  [Grade: {gs.grade}]")
    print(f"  🎯 Catches: {gs.catches} out of {gs.total_claims} claims")
    if gs.failure_taxonomy:
        taxonomy_str = "  |  ".join(
            f"{cat}: {cnt}" for cat, cnt in gs.failure_taxonomy.items()
        )
        print(f"  🔬 Taxonomy: {taxonomy_str}")
    if savings.early_exit and savings.tokens_saved > 0:
        print(
            f"  ⚡ Early Exit — saved ~{savings.tokens_saved:,} tokens"
            f" ({savings.percent_saved:.0f}%) ≈ ${savings.saved_usd:.4f}"
        )
    print(f"  💵 Est. cost: ${savings.cost_usd:.4f}")
    print(sep)
    print(f"\n  💬 {gs.share_text}\n")


def _save_to_file(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    size_kb = os.path.getsize(path) / 1024
    print(f"✅ Report saved to: {path} ({size_kb:.1f} KB)", file=sys.stderr)


async def _run_streaming(args: argparse.Namespace) -> None:
    """Run in streaming mode — print chunks as they arrive."""
    from consensusflow.core.engine import SequentialChain
    from consensusflow.ui.report import render_markdown, render_terminal, render_json
    from consensusflow.core.protocol import VerificationReport

    chain = SequentialChain(
        chain=args.chain,
        extractor_model=args.extractor,
        similarity_threshold=args.threshold,
        fallback_chain=args.fallback,
        budget_usd=args.budget,
    )

    print("\n🔍 ConsensusFlow — Streaming Verification\n" + "─" * 50)

    final_report: VerificationReport | None = None
    async for event in chain.stream(args.prompt):
        etype = event["event"]
        data  = event["data"]

        if etype == "status":
            print(f"\n{data}", flush=True)
        elif etype == "proposer_chunk":
            print(data, end="", flush=True)
        elif etype == "claims_extracted":
            print(f"\n\n📋 {len(data)} atomic claims extracted for auditing…")
        elif etype == "auditor_chunk":
            print(data, end="", flush=True)
        elif etype == "early_exit":
            print(f"\n\n{data['message']} Tokens saved: ~{data.get('saved_tokens', 0):,}")
        elif etype == "resolver_chunk":
            print(data, end="", flush=True)
        elif etype == "error":
            print(f"\n⚠️  {data}", file=sys.stderr)
        elif etype == "done":
            if isinstance(data, VerificationReport):
                final_report = data
            print("\n")

    if final_report is not None:
        _print_gotcha_banner(final_report, width=50)

        if args.save:
            if args.output == "markdown":
                text = render_markdown(final_report)
            elif args.output == "json":
                text = render_json(final_report)
            else:
                text = render_terminal(final_report)
            _save_to_file(args.save, text)


async def _run_standard(args: argparse.Namespace) -> None:
    """Run in standard (non-streaming) mode — print report after completion."""
    from consensusflow.core.engine import SequentialChain
    from consensusflow.ui.report import render_markdown, render_terminal, render_json

    print("🔍 ConsensusFlow — Verifying…", file=sys.stderr)

    chain = SequentialChain(
        chain=args.chain,
        extractor_model=args.extractor,
        similarity_threshold=args.threshold,
        fallback_chain=args.fallback,
        budget_usd=args.budget,
    )
    report = await chain.run(args.prompt)

    if args.output == "markdown":
        output_text = render_markdown(report)
    elif args.output == "json":
        output_text = render_json(report)
    else:
        output_text = render_terminal(report)

    print(output_text)

    if args.output == "terminal":
        _print_gotcha_banner(report, width=70)

    if args.save:
        _save_to_file(args.save, output_text)


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if not args.prompt:
        print("🔍 ConsensusFlow Interactive Mode")
        print("Enter your question (Ctrl+C to exit):\n")
        try:
            args.prompt = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            sys.exit(0)

    if not args.prompt:
        parser.print_help()
        sys.exit(1)

    # Validate chain / fallback lengths
    if args.chain is not None and len(args.chain) not in (2, 3):
        parser.error("--chain requires 2 or 3 model names (PROPOSER AUDITOR [RESOLVER])")
    if args.fallback is not None and len(args.fallback) not in (2, 3):
        parser.error("--fallback requires 2 or 3 model names (PROPOSER AUDITOR [RESOLVER])")

    try:
        if args.stream:
            asyncio.run(_run_streaming(args))
        else:
            asyncio.run(_run_standard(args))
    except KeyboardInterrupt:
        print("\n\nInterrupted.", file=sys.stderr)
        sys.exit(130)
    except Exception as exc:
        _handle_error(exc)
        sys.exit(1)


def _handle_error(exc: Exception) -> None:
    """Print a user-friendly error message for known exception types."""
    from consensusflow.exceptions import (
        BudgetExceededError,
        ChainConfigError,
        ModelUnavailableError,
        PromptNotFoundError,
    )
    if isinstance(exc, BudgetExceededError):
        print(
            f"\n💸 Budget exceeded: estimated cost ${exc.cost_usd:.4f} "
            f"exceeds limit ${exc.budget_usd:.4f}. "
            f"Use --budget to raise the limit or choose cheaper models.",
            file=sys.stderr,
        )
    elif isinstance(exc, ChainConfigError):
        print(f"\n⚙️  Configuration error: {exc}", file=sys.stderr)
    elif isinstance(exc, ModelUnavailableError):
        print(
            f"\n🔌 All models unavailable: {exc}\n"
            f"Check your API keys and network connection.",
            file=sys.stderr,
        )
    elif isinstance(exc, PromptNotFoundError):
        print(f"\n📄 Prompt file missing: {exc}", file=sys.stderr)
    else:
        print(f"\n❌ Error: {exc}", file=sys.stderr)

    if os.getenv("CONSENSUSFLOW_DEBUG", "0") == "1":
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
