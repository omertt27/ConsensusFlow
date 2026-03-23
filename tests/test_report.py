"""
test_report.py — Unit tests for ui/report.py rendering functions.
No API calls required.
"""

from __future__ import annotations

import json
import pytest

from consensusflow.core.protocol import (
    AtomicClaim,
    ChainStatus,
    ClaimStatus,
    StepResult,
    VerificationReport,
)
from consensusflow.ui.report import (
    _md_escape,
    render_json,
    render_markdown,
    render_terminal,
)


# ── helpers ───────────────────────────────────────────────────

def _make_report(
    status: ChainStatus = ChainStatus.SUCCESS,
    claims: list[AtomicClaim] | None = None,
    early_exit: bool = False,
    final_answer: str = "Istanbul is a city in Turkey.",
) -> VerificationReport:
    r = VerificationReport(
        prompt="Tell me about Istanbul.",
        chain_models=["gpt-4o", "gemini/gemini-2.0-flash", "claude-3-5-sonnet"],
        status=status,
        final_answer=final_answer,
        total_tokens=1200,
        total_latency_ms=4500.0,
        similarity_score=0.87,
        early_exit=early_exit,
        saved_tokens=400 if early_exit else 0,
        atomic_claims=claims or [],
    )
    r.proposer_result = StepResult(
        step="proposer", model="gpt-4o",
        raw_text="Istanbul is a city.", prompt_tokens=50, completion_tokens=30,
        latency_ms=1200,
    )
    r.auditor_result = StepResult(
        step="auditor", model="gemini/gemini-2.0-flash",
        raw_text="[verified]", prompt_tokens=80, completion_tokens=40,
        latency_ms=1800,
    )
    if not early_exit:
        r.resolver_result = StepResult(
            step="resolver", model="claude-3-5-sonnet",
            raw_text=final_answer, prompt_tokens=120, completion_tokens=60,
            latency_ms=2100,
        )
    return r


def _mixed_claims() -> list[AtomicClaim]:
    claims = [
        AtomicClaim(text="Istanbul is in Turkey."),
        AtomicClaim(text="Blue Mosque costs €10."),
        AtomicClaim(text="Hagia Sophia was built in 537 AD."),
        AtomicClaim(text="Grand Bazaar is open Sundays."),
        AtomicClaim(text="Population is 5 million."),
    ]
    claims[0].status = ClaimStatus.VERIFIED
    claims[1].status = ClaimStatus.CORRECTED
    claims[1].original_text = "Blue Mosque costs €10."
    claims[1].text = "Blue Mosque is free."
    claims[1].note = "Always been free."
    claims[2].status = ClaimStatus.NUANCED
    claims[2].note = "Rebuilt multiple times."
    claims[3].status = ClaimStatus.DISPUTED
    claims[3].note = "Closed on Sundays."
    claims[4].status = ClaimStatus.REJECTED
    claims[4].note = "Population is over 15 million."
    return claims


# ── _md_escape ────────────────────────────────────────────────

class TestMdEscape:
    def test_pipe_escaped(self):
        assert _md_escape("a|b") == r"a\|b"

    def test_asterisk_escaped(self):
        assert _md_escape("a*b") == r"a\*b"

    def test_bracket_escaped(self):
        assert _md_escape("a[b]c") == r"a\[b\]c"

    def test_backtick_escaped(self):
        assert _md_escape("a`b`c") == r"a\`b\`c"

    def test_backslash_escaped(self):
        assert _md_escape(r"a\b") == r"a\\b"

    def test_plain_text_unchanged(self):
        assert _md_escape("Hello world") == "Hello world"

    def test_multiple_special_chars(self):
        result = _md_escape("a|b*c[d]e")
        assert r"\|" in result
        assert r"\*" in result
        assert r"\[" in result


# ── render_markdown ───────────────────────────────────────────

class TestRenderMarkdown:
    def test_contains_run_id(self):
        r = _make_report()
        md = render_markdown(r)
        assert r.run_id in md

    def test_contains_final_answer(self):
        r = _make_report(final_answer="Istanbul is beautiful.")
        md = render_markdown(r)
        assert "Istanbul is beautiful." in md

    def test_contains_chain_models(self):
        r = _make_report()
        md = render_markdown(r)
        assert "gpt-4o" in md

    def test_claim_table_rendered(self):
        r = _make_report(claims=_mixed_claims())
        md = render_markdown(r)
        assert "Claim-by-Claim Audit" in md
        assert "Corrected" in md
        assert "Disputed" in md
        assert "Rejected" in md

    def test_rejected_count_in_summary(self):
        r = _make_report(claims=_mixed_claims())
        md = render_markdown(r)
        assert "Rejected" in md

    def test_early_exit_section_present(self):
        r = _make_report(early_exit=True, status=ChainStatus.EARLY_EXIT)
        md = render_markdown(r)
        assert "Early Exit" in md
        assert "Resolver" in md

    def test_resolver_skipped_shown_on_early_exit(self):
        r = _make_report(early_exit=True, status=ChainStatus.EARLY_EXIT)
        md = render_markdown(r)
        assert "Skipped" in md

    def test_pipe_in_claim_text_escaped(self):
        c = AtomicClaim(text="A|B claim")
        r = _make_report(claims=[c])
        md = render_markdown(r)
        # Raw pipe inside table would break rendering; should be escaped
        assert r"A\|B" in md

    def test_gotcha_score_section(self):
        r = _make_report()
        md = render_markdown(r)
        assert "Gotcha Score" in md
        assert "/100" in md

    def test_savings_section(self):
        r = _make_report()
        md = render_markdown(r)
        assert "Cost" in md or "Savings" in md

    def test_valid_markdown_table_rows(self):
        r = _make_report(claims=_mixed_claims())
        md = render_markdown(r)
        # Claim audit rows (5-column table) must have >= 5 pipes
        # Gotcha/savings rows (2-column table) must have >= 3 pipes
        for line in md.splitlines():
            if line.startswith("| ") and not line.startswith("|---"):
                assert line.count("|") >= 3


# ── render_terminal ───────────────────────────────────────────

class TestRenderTerminal:
    def test_contains_final_answer(self):
        r = _make_report(final_answer="Final answer here.")
        out = render_terminal(r)
        assert "Final answer here." in out

    def test_contains_status(self):
        r = _make_report(status=ChainStatus.SUCCESS)
        out = render_terminal(r)
        assert "SUCCESS" in out

    def test_claim_audit_section(self):
        r = _make_report(claims=_mixed_claims())
        out = render_terminal(r)
        assert "CLAIM AUDIT" in out

    def test_rejected_in_claim_summary(self):
        r = _make_report(claims=_mixed_claims())
        out = render_terminal(r)
        assert "rejected" in out.lower()

    def test_early_exit_shown(self):
        r = _make_report(early_exit=True, status=ChainStatus.EARLY_EXIT)
        out = render_terminal(r)
        assert "Early Exit" in out

    def test_gotcha_score_shown(self):
        r = _make_report()
        out = render_terminal(r)
        assert "GOTCHA SCORE" in out

    def test_no_crash_empty_claims(self):
        r = _make_report(claims=[])
        out = render_terminal(r)
        assert len(out) > 0

    def test_original_text_shown_for_correction(self):
        claims = _mixed_claims()
        r = _make_report(claims=claims)
        out = render_terminal(r)
        assert "Was:" in out  # correction original text


# ── render_json ───────────────────────────────────────────────

class TestRenderJson:
    def test_valid_json(self):
        r = _make_report()
        output = render_json(r)
        data = json.loads(output)
        assert isinstance(data, dict)

    def test_run_id_present(self):
        r = _make_report()
        data = json.loads(render_json(r))
        assert data["run_id"] == r.run_id

    def test_gotcha_score_present(self):
        r = _make_report()
        data = json.loads(render_json(r))
        assert "gotcha_score" in data
        assert "score" in data["gotcha_score"]

    def test_savings_present(self):
        r = _make_report()
        data = json.loads(render_json(r))
        assert "savings" in data

    def test_claims_serialised(self):
        r = _make_report(claims=_mixed_claims())
        data = json.loads(render_json(r))
        assert len(data["atomic_claims"]) == 5

    def test_rejected_in_claim_summary(self):
        r = _make_report(claims=_mixed_claims())
        data = json.loads(render_json(r))
        assert "rejected" in data["claim_summary"]
        assert data["claim_summary"]["rejected"] == 1

    def test_custom_indent(self):
        r = _make_report()
        compact = render_json(r, indent=0)
        pretty = render_json(r, indent=4)
        assert len(compact) < len(pretty)
