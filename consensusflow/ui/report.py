"""
report.py — Renders a VerificationReport as a rich Markdown or
            terminal-formatted Audit Trail.
"""

from __future__ import annotations

import json
import os
import re

from consensusflow.core.protocol import ClaimStatus, ChainStatus, VerificationReport
from consensusflow.core.scoring import compute_gotcha_score, compute_savings

# Status → emoji mapping
_EMOJI = {
    ClaimStatus.VERIFIED:  "✅",
    ClaimStatus.CORRECTED: "🔧",
    ClaimStatus.NUANCED:   "🔍",
    ClaimStatus.DISPUTED:  "⚠️",
    ClaimStatus.REJECTED:  "❌",
}

_STATUS_LABEL = {
    ClaimStatus.VERIFIED:  "Verified",
    ClaimStatus.CORRECTED: "Corrected",
    ClaimStatus.NUANCED:   "Nuance Added",
    ClaimStatus.DISPUTED:  "Disputed",
    ClaimStatus.REJECTED:  "Rejected",
}

_CHAIN_STATUS_EMOJI = {
    ChainStatus.SUCCESS:    "🟢",
    ChainStatus.EARLY_EXIT: "⚡",
    ChainStatus.PARTIAL:    "🟡",
    ChainStatus.ERROR:      "🔴",
}

# Markdown characters that break table cells if unescaped
_MD_ESCAPE_RE = re.compile(r"([|*_\[\]\\`])")


def _md_escape(text: str) -> str:
    """Escape Markdown special characters for safe use inside table cells."""
    return _MD_ESCAPE_RE.sub(r"\\\1", text)


def _terminal_width() -> int:
    """Return current terminal width, defaulting to 70 if unavailable."""
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 70


def render_markdown(report: VerificationReport) -> str:
    """
    Returns a full Markdown Verification Report suitable for:
    - README display
    - CLI output (with a Markdown renderer)
    - Saving as a .md file
    """
    lines = []

    # ── Header ──────────────────────────────────────────────
    chain_emoji = _CHAIN_STATUS_EMOJI.get(report.status, "❓")
    lines += [
        f"# {chain_emoji} ConsensusFlow Verification Report",
        "",
        f"**Run ID:** `{report.run_id}`  ",
        f"**Status:** `{report.status.value}`  ",
        f"**Models:** {' → '.join(f'`{m}`' for m in report.chain_models)}  ",
        f"**Total Tokens:** {report.total_tokens:,}  ",
        f"**Total Latency:** {report.total_latency_ms:.0f} ms  ",
        "",
    ]

    if report.early_exit:
        lines += [
            "## ⚡ Early Exit — 100% Consensus",
            f"> The Auditor found no material differences. "
            f"Resolver was skipped, saving ~{report.saved_tokens:,} tokens (≈33%).",
            "",
        ]

    # ── Original Prompt ──────────────────────────────────────
    lines += [
        "## 📝 Original Prompt",
        f"> {report.prompt}",
        "",
    ]

    # ── Final Answer ─────────────────────────────────────────
    lines += [
        "## 💡 Final Verified Answer",
        "",
        report.final_answer,
        "",
    ]

    # ── Claim Audit Table ────────────────────────────────────
    if report.atomic_claims:
        lines += [
            "## 🔬 Claim-by-Claim Audit",
            "",
            "| # | Status | Claim | Note |",
            "|---|--------|-------|------|",
        ]
        for i, claim in enumerate(report.atomic_claims, 1):
            emoji  = _EMOJI.get(claim.status, "❓")
            label  = _STATUS_LABEL.get(claim.status, claim.status.value)
            text   = _md_escape(claim.text)
            note   = _md_escape(claim.note or "")
            if claim.original_text:
                note = f"*Was:* ~~{_md_escape(claim.original_text)}~~ {note}"
            lines.append(f"| {i} | {emoji} {label} | {text} | {note} |")
        lines.append("")

        # Summary bar
        v = report.verified_count
        c = report.corrected_count
        d = report.disputed_count
        n = report.nuanced_count
        r = report.rejected_count
        total = len(report.atomic_claims)
        lines += [
            "### Claim Summary",
            f"- ✅ Verified: **{v}/{total}**",
            f"- 🔧 Corrected: **{c}**",
            f"- 🔍 Nuanced: **{n}**",
            f"- ⚠️ Disputed: **{d}**",
            f"- ❌ Rejected: **{r}**",
            f"- 🎯 Consensus score: **{report.similarity_score:.1%}**",
            "",
        ]

    # ── Gotcha Score ─────────────────────────────────────────
    gs = compute_gotcha_score(report, penalty_weights=report.penalty_weights)
    savings = compute_savings(report)

    lines += [
        "## 🎯 Gotcha Score",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| **Score** | **{gs.score}/100** — {gs.label} {gs.emoji} |",
        f"| **Grade** | `{gs.grade}` |",
        f"| **Claims checked** | {gs.total_claims} |",
        f"| **Gotchas caught** | {gs.catches} |",
    ]

    if gs.failure_taxonomy:
        taxonomy_str = ", ".join(
            f"{cat}: {cnt}" for cat, cnt in gs.failure_taxonomy.items()
        )
        lines.append(f"| **Failure taxonomy** | {taxonomy_str} |")

    if gs.penalty_breakdown:
        breakdown_str = " · ".join(
            f"{status} −{pts}pts" for status, pts in gs.penalty_breakdown.items() if pts > 0
        )
        if breakdown_str:
            lines.append(f"| **Penalty breakdown** | {breakdown_str} |")

    lines += [
        "",
        f"> {gs.share_text}",
        "",
    ]

    # ── Savings Report ────────────────────────────────────────
    if savings.tokens_saved > 0 or savings.tokens_used > 0:
        lines += [
            "## 💰 Cost & Savings",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| **Tokens used** | {savings.tokens_used:,} |",
        ]
        if savings.early_exit and savings.tokens_saved > 0:
            lines += [
                f"| **Tokens saved** | {savings.tokens_saved:,} (Early Exit — Resolver skipped) |",
                f"| **Savings %** | {savings.percent_saved:.0f}% |",
                f"| **Est. cost** | ${savings.cost_usd:.4f} (saved ${savings.saved_usd:.4f}) |",
            ]
        else:
            lines.append(f"| **Est. cost** | ${savings.cost_usd:.4f} |")
        lines.append("")

    # ── Step-by-Step Trace ───────────────────────────────────
    lines += ["## 🔗 Chain Trace", ""]

    for step_name, step_result in [
        ("Proposer",  report.proposer_result),
        ("Auditor",   report.auditor_result),
        ("Resolver",  report.resolver_result),
    ]:
        if step_result is None:
            if step_name == "Resolver" and report.early_exit:
                lines += [
                    f"### ⚡ {step_name} — Skipped (Early Exit)",
                    "",
                ]
            continue
        lines += [
            f"### {step_name} — `{step_result.model}`",
            f"*Latency: {step_result.latency_ms:.0f} ms | "
            f"Tokens: {step_result.total_tokens:,}*",
            "",
            "<details>",
            "<summary>View raw output</summary>",
            "",
            "```",
            step_result.raw_text,
            "```",
            "",
            "</details>",
            "",
        ]

    # ── Footer ───────────────────────────────────────────────
    lines += [
        "---",
        "*Generated by [ConsensusFlow](https://github.com/omer/consensusflow) — "
        "Multi-model verification pipeline.*",
    ]

    return "\n".join(lines)


def render_terminal(report: VerificationReport) -> str:
    """
    Compact plain-text summary for CLI output (no markdown renderer needed).
    Adapts to the current terminal width.
    """
    W = min(_terminal_width(), 100)  # cap at 100 to stay readable
    sep = "─" * W

    def box(title: str) -> str:
        return f"┌{'─' * (W - 2)}┐\n│ {title:<{W-3}}│\n└{'─' * (W - 2)}┘"

    lines = ["", box("  ConsensusFlow — Verification Report"), ""]

    chain_emoji = _CHAIN_STATUS_EMOJI.get(report.status, "❓")
    lines += [
        f"  {chain_emoji} Status      : {report.status.value}",
        f"  🆔 Run ID      : {report.run_id}",
        f"  🔗 Chain       : {' → '.join(report.chain_models)}",
        f"  🪙 Tokens      : {report.total_tokens:,}",
        f"  ⏱  Latency     : {report.total_latency_ms:.0f} ms",
    ]

    if report.early_exit:
        lines += [
            f"  ⚡ Early Exit  : YES — saved ~{report.saved_tokens:,} tokens",
        ]

    lines += ["", sep, "  FINAL ANSWER", sep, "", report.final_answer, ""]

    if report.atomic_claims:
        lines += [sep, "  CLAIM AUDIT", sep, ""]
        for i, claim in enumerate(report.atomic_claims, 1):
            emoji = _EMOJI.get(claim.status, "❓")
            lines.append(f"  {i:>2}. {emoji} {claim.text}")
            if claim.original_text:
                lines.append(f"       ↳ Was: {claim.original_text}")
            if claim.note:
                lines.append(f"       ℹ  {claim.note}")

        r = report.rejected_count
        lines += [
            "",
            f"  ✅ {report.verified_count} verified  "
            f"🔧 {report.corrected_count} corrected  "
            f"⚠️  {report.disputed_count} disputed  "
            f"❌ {r} rejected  "
            f"🎯 {report.similarity_score:.1%} consensus",
        ]

    # ── Gotcha Score ─────────────────────────────────────────
    gs = compute_gotcha_score(report, penalty_weights=report.penalty_weights)
    savings = compute_savings(report)

    lines += [
        "",
        sep,
        "  GOTCHA SCORE",
        sep,
        "",
        f"  {gs.emoji}  Score   : {gs.score}/100  —  {gs.label}",
        f"  📊 Grade   : {gs.grade}",
        f"  🎯 Catches : {gs.catches} out of {gs.total_claims} claims",
    ]

    if gs.failure_taxonomy:
        taxonomy_str = "  |  ".join(
            f"{cat}: {cnt}" for cat, cnt in gs.failure_taxonomy.items()
        )
        lines.append(f"  🔬 Taxonomy: {taxonomy_str}")

    lines += [
        "",
        f"  💬 {gs.share_text}",
        "",
    ]

    # ── Savings ───────────────────────────────────────────────
    if savings.tokens_used > 0:
        lines += [sep, "  COST & SAVINGS", sep, ""]
        lines.append(f"  🪙 Tokens used   : {savings.tokens_used:,}")
        if savings.early_exit and savings.tokens_saved > 0:
            lines += [
                f"  ⚡ Tokens saved  : {savings.tokens_saved:,}  (Early Exit)",
                f"  💵 Savings       : {savings.percent_saved:.0f}%"
                f"  |  Est. saved ${savings.saved_usd:.4f}",
            ]
        lines.append(f"  💵 Est. cost     : ${savings.cost_usd:.4f}")
        lines.append("")

    lines += ["", sep]
    return "\n".join(lines)


def render_json(report: VerificationReport, indent: int = 2) -> str:
    """Serialise the full report (with Gotcha Score and Savings) as pretty-printed JSON."""
    gs = compute_gotcha_score(report, penalty_weights=report.penalty_weights)
    savings = compute_savings(report)
    data = report.to_dict()
    data["gotcha_score"] = gs.to_dict()
    data["savings"] = savings.to_dict()
    return json.dumps(data, indent=indent, ensure_ascii=True)
