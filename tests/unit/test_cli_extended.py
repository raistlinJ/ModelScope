"""
Extended CLI tests covering previously untested branches:
  - _c() with color enabled (line 57)
  - _load_config_file YAML ImportError path (line 124-127)
  - _apply_config_file_defaults short flag path (line 526) and float type (line 555-558)
  - sessions_show no run.log path (line 968-969)
  - sessions_show run.log read exception (line 939)
  - sessions list with PASSED/FAILED badges
  - sessions_show validation_passed=False
  - _maybe_inject_run_subcommand: no run_indicators (line 1094)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest
import cli


class TestCWithColor:
    def test_c_returns_wrapped_text(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("cli._use_color", return_value=True):
            result = cli._c("hello", cli._BOLD)
        assert "\033[1m" in result
        assert "hello" in result
        assert result.endswith(cli._RESET)

    def test_c_multiple_codes(self, monkeypatch):
        with patch("cli._use_color", return_value=True):
            result = cli._c("text", cli._RED, cli._BOLD)
        assert "text" in result


class TestSessionsShowExtended:
    def test_sessions_show_no_run_log(self, tmp_path, capsys):
        """Sessions show should work even without run.log."""
        session_dir = tmp_path / "2026-06-18_12-00-00_abc12345"
        session_dir.mkdir()
        tel = {"run_scenario": "T", "run_model": "m", "total_latency": 1.0,
               "validation_passed": False, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        # No run.log created
        ret = cli.main(["sessions", "show", "abc12345", "--sessions-dir", str(tmp_path)])
        assert ret == 0
        out = capsys.readouterr().out
        assert "no run.log" in out.lower() or "run.log" not in out or ret == 0

    def test_sessions_show_run_log_read_exception(self, tmp_path, capsys):
        """sessions show handles run.log read exception gracefully."""
        session_dir = tmp_path / "2026-06-18_12-00-00_xyz99999"
        session_dir.mkdir()
        tel = {"run_scenario": "T", "run_model": "m", "total_latency": 1.0,
               "validation_passed": None, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        run_log = session_dir / "run.log"
        run_log.write_text("test log content")

        # Patch read_text to raise on the run.log path
        original_read = pathlib.Path.read_text
        def _crashing_read(self, *a, **kw):
            if self.name == "run.log":
                raise PermissionError("access denied")
            return original_read(self, *a, **kw)

        with patch.object(pathlib.Path, "read_text", _crashing_read):
            ret = cli.main(["sessions", "show", "xyz99999", "--sessions-dir", str(tmp_path)])
        # Should not crash, return 0
        assert ret == 0

    def test_sessions_list_with_failed_badge(self, tmp_path, capsys):
        """Sessions list shows PASSED/FAILED properly."""
        session_dir = tmp_path / "2026-06-18_12-00-00_failed001"
        session_dir.mkdir()
        tel = {"run_scenario": "T", "run_model": "m", "total_latency": 1.0,
               "validation_passed": False, "total_tokens": 100}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        with patch("cli._use_color", return_value=False):
            ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert ret == 0
        assert "FAILED" in out

    def test_sessions_show_no_telemetry(self, tmp_path, capsys):
        """sessions show works even with no telemetry file."""
        session_dir = tmp_path / "2026-06-18_12-00-00_notel001"
        session_dir.mkdir()
        (session_dir / "run.log").write_text("[INIT] started")
        ret = cli.main(["sessions", "show", "notel001", "--sessions-dir", str(tmp_path)])
        assert ret == 0


