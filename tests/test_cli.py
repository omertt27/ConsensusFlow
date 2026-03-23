"""
tests/test_cli.py — Tests for consensusflow/cli.py

Covers:
  • _build_parser: default values, all flags, and custom args
  • _handle_error: each known exception type + generic fallback + debug mode
  • _save_to_file: writes content and prints file size
  • _print_gotcha_banner: calls scoring helpers and prints output
  • main(): no-prompt path (interactive), empty prompt path, keyboard interrupt
  • _run_standard / _run_streaming via asyncio integration (mocked chain)
"""

from __future__ import annotations

import asyncio
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensusflow.cli import (
    _build_parser,
    _handle_error,
    _save_to_file,
    _print_gotcha_banner,
    main,
    _run_standard,
    _run_streaming,
)
from consensusflow.exceptions import (
    BudgetExceededError,
    ChainConfigError,
    ModelUnavailableError,
)


# ─────────────────────────────────────────────
# _build_parser
# ─────────────────────────────────────────────

class TestBuildParser:
    def test_defaults(self):
        parser = _build_parser()
        args = parser.parse_args(["Hello?"])
        assert args.prompt == "Hello?"
        assert args.chain is None
        assert args.fallback is None
        assert args.extractor == "gpt-4o-mini"
        assert args.threshold == 0.92
        assert args.budget is None
        assert args.output == "terminal"
        assert args.stream is False
        assert args.no_color is False
        assert args.save is None

    def test_chain_three_models(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--chain", "m1", "m2", "m3"])
        assert args.chain == ["m1", "m2", "m3"]

    def test_fallback_chain(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--fallback", "f1", "f2", "f3"])
        assert args.fallback == ["f1", "f2", "f3"]

    def test_output_choices(self):
        parser = _build_parser()
        for choice in ("terminal", "markdown", "json"):
            args = parser.parse_args(["Q?", "--output", choice])
            assert args.output == choice

    def test_stream_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--stream"])
        assert args.stream is True

    def test_no_color_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--no-color"])
        assert args.no_color is True

    def test_budget_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--budget", "0.10"])
        assert args.budget == pytest.approx(0.10)

    def test_save_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--save", "out.md"])
        assert args.save == "out.md"

    def test_extractor_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--extractor", "gpt-4-turbo"])
        assert args.extractor == "gpt-4-turbo"

    def test_threshold_flag(self):
        parser = _build_parser()
        args = parser.parse_args(["Q?", "--threshold", "0.85"])
        assert args.threshold == pytest.approx(0.85)


# ─────────────────────────────────────────────
# _handle_error
# ─────────────────────────────────────────────

class TestHandleError:
    def test_budget_exceeded_error(self, capsys):
        exc = BudgetExceededError(cost_usd=0.15, budget_usd=0.10)
        _handle_error(exc)
        err = capsys.readouterr().err
        assert "Budget exceeded" in err or "budget" in err.lower()
        assert "0.1500" in err or "0.15" in err

    def test_chain_config_error(self, capsys):
        exc = ChainConfigError("chain must have exactly 3 models")
        _handle_error(exc)
        err = capsys.readouterr().err
        assert "Configuration error" in err or "config" in err.lower()

    def test_model_unavailable_error(self, capsys):
        exc = ModelUnavailableError("all models down")
        _handle_error(exc)
        err = capsys.readouterr().err
        assert "unavailable" in err.lower() or "models" in err.lower()

    def test_prompt_not_found_error(self, capsys):
        from consensusflow.exceptions import PromptNotFoundError
        exc = PromptNotFoundError("adversarial.md not found")
        _handle_error(exc)
        err = capsys.readouterr().err
        assert "Prompt file missing" in err or "missing" in err.lower()

    def test_generic_exception(self, capsys):
        exc = RuntimeError("something went wrong")
        _handle_error(exc)
        err = capsys.readouterr().err
        assert "Error" in err or "something went wrong" in err

    def test_debug_mode_prints_traceback(self, capsys):
        with patch.dict(os.environ, {"CONSENSUSFLOW_DEBUG": "1"}):
            exc = RuntimeError("boom")
            try:
                raise exc
            except RuntimeError:
                _handle_error(exc)
        err = capsys.readouterr().err
        assert "boom" in err


# ─────────────────────────────────────────────
# _save_to_file
# ─────────────────────────────────────────────

class TestSaveToFile:
    def test_writes_content(self, tmp_path):
        path = str(tmp_path / "report.md")
        _save_to_file(path, "# Hello\nWorld")
        with open(path) as f:
            assert f.read() == "# Hello\nWorld"

    def test_prints_saved_message(self, tmp_path, capsys):
        path = str(tmp_path / "out.md")
        _save_to_file(path, "content")
        err = capsys.readouterr().err
        assert "saved" in err.lower() or "Report" in err


# ─────────────────────────────────────────────
# _print_gotcha_banner
# ─────────────────────────────────────────────

class TestPrintGotchaBanner:
    def _make_report(self):
        from consensusflow.core.protocol import VerificationReport, AtomicClaim, ClaimStatus
        report = VerificationReport(prompt="test", chain_models=["a", "b", "c"])
        report.atomic_claims = [
            AtomicClaim(text="claim one", status=ClaimStatus.VERIFIED),
            AtomicClaim(text="claim two", status=ClaimStatus.CORRECTED),
        ]
        return report

    def test_outputs_gotcha_score(self, capsys):
        report = self._make_report()
        _print_gotcha_banner(report, width=50)
        out = capsys.readouterr().out
        assert "GOTCHA SCORE" in out

    def test_outputs_cost(self, capsys):
        report = self._make_report()
        _print_gotcha_banner(report)
        out = capsys.readouterr().out
        assert "cost" in out.lower() or "$" in out

    def test_early_exit_shows_savings(self, capsys):
        report = self._make_report()
        report.early_exit = True
        report.saved_tokens = 1000
        _print_gotcha_banner(report)
        out = capsys.readouterr().out
        assert "Early Exit" in out or "saved" in out.lower()


# ─────────────────────────────────────────────
# _run_standard (async, mocked chain)
# ─────────────────────────────────────────────

class TestRunStandard:
    def _make_mock_report(self):
        from consensusflow.core.protocol import VerificationReport, AtomicClaim, ClaimStatus, ChainStatus
        report = VerificationReport(prompt="test", chain_models=["a", "b", "c"])
        report.status = ChainStatus.SUCCESS
        report.final_answer = "The answer is 42."
        report.atomic_claims = [AtomicClaim(text="claim", status=ClaimStatus.VERIFIED)]
        return report

    def _make_args(self, output="terminal", save=None):
        parser = _build_parser()
        argv = ["test prompt", "--output", output]
        if save:
            argv += ["--save", save]
        return parser.parse_args(argv)

    @pytest.mark.asyncio
    async def test_terminal_output(self, capsys):
        args = self._make_args(output="terminal")
        mock_report = self._make_mock_report()
        with patch(
            "consensusflow.core.engine.SequentialChain"
        ) as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_report)
            await _run_standard(args)
        out = capsys.readouterr().out
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_markdown_output(self, capsys):
        args = self._make_args(output="markdown")
        mock_report = self._make_mock_report()
        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_report)
            await _run_standard(args)
        out = capsys.readouterr().out
        assert "#" in out or "##" in out or len(out) > 0

    @pytest.mark.asyncio
    async def test_json_output(self, capsys):
        args = self._make_args(output="json")
        mock_report = self._make_mock_report()
        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_report)
            await _run_standard(args)
        out = capsys.readouterr().out
        assert "{" in out or "run_id" in out

    @pytest.mark.asyncio
    async def test_save_writes_file(self, tmp_path):
        path = str(tmp_path / "saved.md")
        args = self._make_args(output="markdown", save=path)
        mock_report = self._make_mock_report()
        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.run = AsyncMock(return_value=mock_report)
            await _run_standard(args)
        assert os.path.exists(path)


# ─────────────────────────────────────────────
# _run_streaming (async, mocked chain)
# ─────────────────────────────────────────────

class TestRunStreaming:
    def _make_args(self, output="terminal", save=None):
        parser = _build_parser()
        argv = ["test prompt", "--stream"]
        if save:
            argv += ["--save", save]
        return parser.parse_args(argv)

    def _make_mock_report(self):
        from consensusflow.core.protocol import VerificationReport, AtomicClaim, ClaimStatus, ChainStatus
        report = VerificationReport(prompt="test", chain_models=["a", "b", "c"])
        report.status = ChainStatus.SUCCESS
        report.final_answer = "Streamed answer."
        report.atomic_claims = [AtomicClaim(text="claim", status=ClaimStatus.VERIFIED)]
        return report

    async def _mock_stream_gen(self, report):
        """Yield a variety of event types including done."""
        yield {"event": "status", "data": "Starting..."}
        yield {"event": "proposer_chunk", "data": "chunk one "}
        yield {"event": "claims_extracted", "data": [{"text": "claim"}]}
        yield {"event": "auditor_chunk", "data": "audit chunk"}
        yield {"event": "resolver_chunk", "data": "resolver chunk"}
        yield {"event": "done", "data": report}

    @pytest.mark.asyncio
    async def test_streaming_outputs_to_stdout(self, capsys):
        args = self._make_args()
        mock_report = self._make_mock_report()
        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.stream = MagicMock(
                return_value=self._mock_stream_gen(mock_report)
            )
            await _run_streaming(args)
        out = capsys.readouterr().out
        assert len(out) > 0

    @pytest.mark.asyncio
    async def test_early_exit_event(self, capsys):
        args = self._make_args()
        mock_report = self._make_mock_report()

        async def _gen_early_exit(prompt):
            yield {"event": "early_exit", "data": {"message": "Early exit!", "saved_tokens": 500}}
            yield {"event": "done", "data": mock_report}

        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.stream = _gen_early_exit
            await _run_streaming(args)
        out = capsys.readouterr().out
        assert "Early exit" in out or "500" in out

    @pytest.mark.asyncio
    async def test_error_event_goes_to_stderr(self, capsys):
        args = self._make_args()

        async def _gen_error(prompt):
            yield {"event": "error", "data": "something exploded"}

        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.stream = _gen_error
            await _run_streaming(args)
        err = capsys.readouterr().err
        assert "something exploded" in err

    @pytest.mark.asyncio
    async def test_streaming_save_file(self, tmp_path):
        path = str(tmp_path / "stream.md")
        parser = _build_parser()
        args = parser.parse_args(["test prompt", "--stream", "--save", path, "--output", "markdown"])
        mock_report = self._make_mock_report()
        with patch("consensusflow.core.engine.SequentialChain") as MockChain:
            MockChain.return_value.stream = MagicMock(
                return_value=self._mock_stream_gen(mock_report)
            )
            await _run_streaming(args)
        assert os.path.exists(path)


# ─────────────────────────────────────────────
# main() entry point
# ─────────────────────────────────────────────

class TestMain:
    def _make_mock_report(self):
        from consensusflow.core.protocol import VerificationReport, AtomicClaim, ClaimStatus, ChainStatus
        report = VerificationReport(prompt="test", chain_models=["a", "b", "c"])
        report.status = ChainStatus.SUCCESS
        report.final_answer = "Answer."
        report.atomic_claims = [AtomicClaim(text="claim", status=ClaimStatus.VERIFIED)]
        return report

    def test_no_prompt_reads_from_stdin_then_exits(self, capsys):
        """No prompt + empty input → print_help and sys.exit(1)."""
        with patch("sys.argv", ["consensusflow"]):
            with patch("builtins.input", return_value=""):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 1

    def test_keyboard_interrupt_exits_130(self, capsys):
        with patch("sys.argv", ["consensusflow", "test question"]):
            with patch("asyncio.run", side_effect=KeyboardInterrupt()):
                with pytest.raises(SystemExit) as exc_info:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        main()
        assert exc_info.value.code == 130

    def test_exception_exits_1(self, capsys):
        with patch("sys.argv", ["consensusflow", "test question"]):
            with patch("asyncio.run", side_effect=RuntimeError("boom")):
                with pytest.raises(SystemExit) as exc_info:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", RuntimeWarning)
                        main()
        assert exc_info.value.code == 1

    @pytest.mark.filterwarnings("ignore::RuntimeWarning")
    def test_successful_run(self, capsys):
        """main() succeeds end-to-end: verify it exits normally (no SystemExit)."""
        mock_report = self._make_mock_report()

        # Patch _run_standard at module level so the coroutine is ours
        async def _fake_run(args):
            from consensusflow.ui.report import render_terminal
            print(render_terminal(mock_report))

        # patch asyncio.run to simply run the coroutine synchronously
        def _sync_asyncio_run(coro):
            import asyncio as _asyncio
            loop = _asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        with patch("sys.argv", ["consensusflow", "What is 2+2?"]):
            with patch("consensusflow.cli._run_standard", new=_fake_run):
                with patch("consensusflow.cli.asyncio") as mock_asyncio:
                    mock_asyncio.run.side_effect = _sync_asyncio_run
                    main()
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_interactive_mode_prompt(self, capsys):
        """When no prompt given but user types something in interactive mode."""
        mock_report = self._make_mock_report()

        async def _fake_run_standard(args):
            from consensusflow.ui.report import render_terminal
            print(render_terminal(mock_report))

        with patch("sys.argv", ["consensusflow"]):
            with patch("builtins.input", return_value="What is 2+2?"):
                with patch("consensusflow.cli._run_standard", new=_fake_run_standard):
                    main()
        out = capsys.readouterr().out
        assert len(out) > 0

    def test_eof_in_interactive_mode_exits_0(self):
        """Ctrl+D (EOFError) in interactive mode → clean exit 0."""
        with patch("sys.argv", ["consensusflow"]):
            with patch("builtins.input", side_effect=EOFError()):
                with pytest.raises(SystemExit) as exc_info:
                    main()
        assert exc_info.value.code == 0
