"""
Unit tests for cli.py.

Covers:
  - _use_color, _c, _colorize_log_line
  - _box_table, _print_run_summary
  - main() dispatch: project, sessions list/show
  - _cmd_project: dry-run, secret redaction, plugin dispatch, exit codes
  - _cmd_sessions_list, _cmd_sessions_show
  - _find_session, _read_telemetry
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import types
from io import StringIO
from unittest.mock import MagicMock, patch, mock_open

import pytest

# --------------------------------------------------------------------------- #
#  Module-level helpers (no side-effects on import)                           #
# --------------------------------------------------------------------------- #

import cli


# ── _use_color / _c ────────────────────────────────────────────────────────────

class TestUseColor:
    def test_no_color_when_env_set(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        # Reload to see env change — just call directly
        assert cli._use_color() is False

    def test_c_returns_plain_text_without_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = cli._c("hello", cli._BOLD)
        assert result == "hello"

    def test_c_wraps_with_codes_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        # isatty may be False in test; just check no exception
        result = cli._c("hello", cli._BOLD)
        assert "hello" in result


# ── _colorize_log_line ─────────────────────────────────────────────────────────

class TestColorizeLogLine:
    def test_no_color_returns_unchanged(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        line = "[LLM] Agent turn 1"
        assert cli._colorize_log_line(line) == line

    def test_error_tag_recognized(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("cli._use_color", return_value=True):
            result = cli._colorize_log_line("[ERROR] something failed")
            # The tag should be wrapped in ANSI codes
            assert "[ERROR]" in result

    def test_plain_line_unchanged_with_color(self, monkeypatch):
        with patch("cli._use_color", return_value=True):
            line = "no tag here"
            result = cli._colorize_log_line(line)
            assert "no tag here" in result


# ── _load_config_file ──────────────────────────────────────────────────────────

# ── _box_table ─────────────────────────────────────────────────────────────────

class TestBoxTable:
    def test_returns_no_data_for_empty(self):
        assert cli._box_table([]) == "(no data)"

    def test_contains_header_and_data(self):
        rows = [{"Name": "Alice", "Score": "95"}]
        table = cli._box_table(rows)
        assert "Name" in table
        assert "Alice" in table
        assert "Score" in table
        assert "95" in table

    def test_title_appears(self):
        rows = [{"A": "1"}]
        table = cli._box_table(rows, title="My Table")
        assert "My Table" in table

    def test_multiple_rows(self):
        rows = [{"X": str(i)} for i in range(5)]
        table = cli._box_table(rows)
        for i in range(5):
            assert str(i) in table

    def test_box_drawing_characters(self):
        rows = [{"Col": "val"}]
        table = cli._box_table(rows)
        assert "┌" in table
        assert "┘" in table



# ── _make_env ──────────────────────────────────────────────────────────────────

# ── _apply_config_file_defaults ───────────────────────────────────────────────

# ── _print_run_summary ─────────────────────────────────────────────────────────

class TestPrintRunSummary:
    def _tel(self, **kw):
        base = {
            "run_scenario": "Scenario 1",
            "run_model": "testmodel",
            "run_backend": "llama.cpp",
            "run_aborted": False,
            "validation_passed": None,
            "total_latency": 1.5,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "llm_rounds": 2,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        base.update(kw)
        return base

    def test_no_exception_on_basic_telemetry(self, capsys):
        cli._print_run_summary(self._tel())
        # Any output is fine — just no exception

    def test_passed_shows_PASSED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(validation_passed=True))
        out = capsys.readouterr().out
        assert "PASSED" in out

    def test_failed_shows_FAILED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(validation_passed=False))
        out = capsys.readouterr().out
        assert "FAILED" in out

    def test_aborted_shows_ABORTED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(run_aborted=True))
        out = capsys.readouterr().out
        assert "ABORTED" in out

    def test_metrics_matrix_evaluated(self, capsys):
        matrix = [{"type": "validation_passed", "enabled": True}]
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(metrics_matrix=matrix, validation_passed=True))
        # No exception is the key assertion here


# ── _maybe_inject_run_subcommand ───────────────────────────────────────────────

# ── main() dispatch ────────────────────────────────────────────────────────────

class TestMainDispatch:
    def test_no_subcommand_prints_help(self, capsys):
        ret = cli.main([])
        out = capsys.readouterr().out + capsys.readouterr().err
        assert ret == 0

    # Scenario tests removed - scenarios command no longer exists

    def test_sessions_no_dir_exits_zero(self, tmp_path, capsys):
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path / "nonexistent")])
        assert ret == 0

    def test_sessions_list_empty_dir(self, tmp_path, capsys):
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_list_with_data(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd1234"
        session_dir.mkdir()
        tel = {"run_scenario": "Test", "run_model": "model", "total_latency": 1.0, "validation_passed": True, "total_tokens": 100}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert ret == 0
        assert "abcd1234" in out or "2026" in out

    def test_sessions_list_limit(self, tmp_path, capsys):
        for i in range(5):
            d = tmp_path / f"2026-06-18_12-00-0{i}_abcd000{i}"
            d.mkdir()
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path), "-n", "2"])
        assert ret == 0

    def test_sessions_show_not_found(self, tmp_path, capsys):
        ret = cli.main(["sessions", "show", "nonexistent", "--sessions-dir", str(tmp_path)])
        assert ret == 2

    def test_sessions_show_found(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd1234"
        session_dir.mkdir()
        tel = {"run_scenario": "Test", "run_model": "m", "total_latency": 1.0,
               "validation_passed": True, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        (session_dir / "run.log").write_text("[INIT] started")
        ret = cli.main(["sessions", "show", "abcd1234", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_show_uses_telemetry_0(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd5678"
        session_dir.mkdir()
        tel = {"run_scenario": "CAF", "run_model": "m", "total_latency": 2.0,
               "validation_passed": None, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry_0.json").write_text(json.dumps(tel))
        ret = cli.main(["sessions", "show", "abcd5678", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_no_action_defaults_to_list(self, tmp_path, capsys):
        # 'sessions' with no sub-action should default to list using the default sessions dir
        with patch("cli._default_sessions_dir", return_value=tmp_path / "noexist"):
            ret = cli.main(["sessions"])
        assert ret == 0




    # Scenario tests removed - scenarios concept deleted

    # Scenario tests removed - scenarios concept deleted


# ── _default_sessions_dir ─────────────────────────────────────────────────────

class TestDefaultSessionsDir:
    def test_returns_path(self):
        result = cli._default_sessions_dir()
        assert isinstance(result, pathlib.Path)
        assert "sessions" in str(result)


# ── backward-compat flat invocation ───────────────────────────────────────────



# ── project subcommand ────────────────────────────────────────────────────────

class TestCmdProject:
    """`project` is the only CLI path that runs a bot type.

    It resolves the project's `type` through the bot-type registry and hands
    the run to that plugin, so unlike the removed `run` subcommand it produces
    the same behaviour as the Execute tab.
    """

    def _project_file(self, tmp_path, bot_type="bash_bot", **config):
        from core.bot_types import require_bot_plugin

        plugin = require_bot_plugin(bot_type)
        proj = plugin.make_project(f"p-{bot_type}", f"Test {bot_type}")
        proj["config"].update(config)
        path = tmp_path / f"{bot_type}.json"
        path.write_text(json.dumps(proj, default=str))
        return str(path)

    def test_dry_run_prints_config_without_running(self, tmp_path, capsys):
        with patch("core.evaluator.run_evaluation") as ran:
            ret = cli.main(["project", "-f", self._project_file(tmp_path), "--dry-run"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "Dry-run config" in out
        ran.assert_not_called()

    def test_dry_run_redacts_secrets(self, tmp_path, capsys):
        path = self._project_file(tmp_path, ssh_password="hunter2", openai_api_key="sk-secret")
        ret = cli.main(["project", "-f", path, "--dry-run"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "hunter2" not in out
        assert "sk-secret" not in out
        assert "REDACTED" in out

    def test_a_missing_file_is_reported(self, tmp_path, capsys):
        ret = cli.main(["project", "-f", str(tmp_path / "nope.json"), "--dry-run"])
        assert ret != 0

    def test_the_run_is_handed_to_that_bot_s_plugin(self, tmp_path):
        """Every bot type reaches its own plugin, not the generic evaluator."""
        from core.bot_types import iter_bot_plugins

        for plugin in iter_bot_plugins():
            path = self._project_file(tmp_path, bot_type=plugin.type_id)
            with patch.object(type(plugin), "run_evaluation", return_value={
                "validation_passed": True, "run_aborted": False, "total_latency": 0.0,
            }) as ran:
                ret = cli.main(["project", "-f", path])
            assert ran.called, f"{plugin.type_id} did not reach its plugin"
            assert ret == 0, f"{plugin.type_id} exited {ret}"

    def _exit_code_for(self, tmp_path, telemetry):
        from core.bot_types import require_bot_plugin

        path = self._project_file(tmp_path)
        plugin = require_bot_plugin("bash_bot")
        with patch.object(type(plugin), "run_evaluation", return_value=telemetry):
            return cli.main(["project", "-f", path])

    def test_the_exit_code_carries_the_verdict(self, tmp_path):
        """So a project run can gate a script or a CI step."""
        base = {"run_aborted": False, "total_latency": 0.0}
        assert self._exit_code_for(tmp_path, {**base, "validation_passed": True}) == 0
        assert self._exit_code_for(tmp_path, {**base, "validation_passed": False}) == 1
        assert self._exit_code_for(tmp_path, {**base, "run_aborted": True}) == 1

    def test_no_validation_configured_is_not_a_failure(self, tmp_path):
        """validation_passed is None when a project configures no checks."""
        assert self._exit_code_for(
            tmp_path, {"validation_passed": None, "run_aborted": False, "total_latency": 0.0},
        ) == 0

    def test_a_secret_can_come_from_the_environment(self, tmp_path, monkeypatch, capsys):
        """Exported projects carry no secrets, so the env supplies them."""
        monkeypatch.setenv("MODELSCOPE_SSH_PASSWORD", "from-env")
        path = self._project_file(tmp_path, execution_target="ssh", ssh_host="10.0.0.1")
        ret = cli.main(["project", "-f", path, "--dry-run"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "from-env" not in out   # redacted, but it was resolved
        assert "REDACTED" in out
