"""
Unit tests for core/caf_runner.py.

Covers:
  - _strip_ansi (via caf_runner)
  - _caf_provider_flags
  - _parse_caf_run_id
  - _pull_caf_artifacts: diagnostic ls path, dir-not-found path
  - _telemetry_from_caf: all branches
  - run_caf_ssh_evaluation: streaming path, non-streaming path, no run_id
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from core.caf_runner import (
    _strip_ansi,
    _caf_provider_flags,
    _parse_caf_run_id,
    _pull_caf_artifacts,
    _telemetry_from_caf,
    run_caf_ssh_evaluation,
)
from core.evaluator import _init_telemetry


# ── _strip_ansi ────────────────────────────────────────────────────────────────

class TestStripAnsi:
    def test_removes_color_codes(self):
        raw = "\x1b[32mgreen text\x1b[0m"
        assert _strip_ansi(raw) == "green text"

    def test_plain_text_unchanged(self):
        text = "plain text no escape codes"
        assert _strip_ansi(text) == text

    def test_empty_string(self):
        assert _strip_ansi("") == ""


# ── _caf_provider_flags ────────────────────────────────────────────────────────

class TestCafProviderFlags:
    def test_ollama_backend(self):
        cfg = {"backend_type": "ollama", "llm_url": "http://localhost:11434"}
        flags = _caf_provider_flags(cfg)
        assert "--provider ollama_direct" in flags
        assert "11434" in flags

    def test_llama_cpp_default(self):
        cfg = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080"}
        flags = _caf_provider_flags(cfg)
        assert "--provider openai" in flags

    def test_trailing_slash_stripped(self):
        cfg = {"backend_type": "ollama", "llm_url": "http://localhost:11434/"}
        flags = _caf_provider_flags(cfg)
        url_part = flags.split("--url")[1].strip()
        # shlex.quote adds quotes; check no trailing slash inside quotes
        assert not url_part.strip("'\"").endswith("/")


# ── _parse_caf_run_id ──────────────────────────────────────────────────────────

class TestParseCafRunId:
    def test_extracts_simple_id(self):
        output = "[run] Transcript: runs/abc123/transcript.md"
        assert _parse_caf_run_id(output) == "abc123"

    def test_extracts_timestamp_id(self):
        output = "[run] Transcript: runs/2026-06-18_14-41-10_cli/transcript.md"
        assert _parse_caf_run_id(output) == "2026-06-18_14-41-10_cli"

    def test_no_match_returns_none(self):
        assert _parse_caf_run_id("no transcript here") is None

    def test_empty_returns_none(self):
        assert _parse_caf_run_id("") is None

    def test_ansi_stripped_output_parseable(self):
        raw = "\x1b[1m[run]\x1b[0m Transcript: runs/myrun123/transcript.md"
        cleaned = _strip_ansi(raw)
        assert _parse_caf_run_id(cleaned) == "myrun123"


# ── _pull_caf_artifacts ────────────────────────────────────────────────────────

def _make_env_with_files(files: dict, ls_result: str = "") -> MagicMock:
    env = MagicMock(spec=["host", "username", "remote_cwd", "execute", "read_file"])
    env.host = "10.0.0.1"

    def read_file(path):
        if path in files:
            return files[path]
        raise FileNotFoundError(f"not found: {path}")

    env.read_file.side_effect = read_file
    env.execute.return_value = {"stdout": ls_result, "stderr": "", "exit_code": 0}
    return env


class TestPullCafArtifacts:
    def test_pulls_transcript_and_metadata(self, tmp_path):
        meta = {"status": "completed", "model": "devstral"}
        files = {
            "runs/r1/transcript.md": "# transcript",
            "runs/r1/metadata.json": json.dumps(meta),
        }
        env = _make_env_with_files(files)
        logs = []
        result = _pull_caf_artifacts(env, "r1", str(tmp_path), lambda m: logs.append(m))
        assert result == meta
        assert (tmp_path / "r1" / "transcript.md").exists()

    def test_missing_file_logged_as_warn(self, tmp_path):
        env = _make_env_with_files({})
        logs = []
        _pull_caf_artifacts(env, "missing_run", str(tmp_path), lambda m: logs.append(m))
        assert any("[WARN]" in l for l in logs)

    def test_dir_not_found_triggers_find(self, tmp_path):
        # ls returns "DIR_NOT_FOUND" — should trigger a find command
        env = _make_env_with_files({}, ls_result="DIR_NOT_FOUND")
        logs = []
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda m: logs.append(m))
        # execute should have been called at least twice (ls + find)
        assert env.execute.call_count >= 2

    def test_pulls_tool_call_json(self, tmp_path):
        tc = json.dumps({"tool": "nmap", "exit_code": 0, "result": "open", "duration_ms": 500})
        files = {
            "runs/r1/transcript.md": "# T",
            "runs/r1/metadata.json": "{}",
            "runs/r1/tool_calls/001.json": tc,
        }
        env = _make_env_with_files(files, ls_result="001.json\n")
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda _: None)
        assert (tmp_path / "r1" / "tool_calls" / "001.json").exists()

    def test_non_json_ls_entries_skipped(self, tmp_path):
        files = {
            "runs/r1/transcript.md": "# T",
            "runs/r1/metadata.json": "{}",
        }
        env = _make_env_with_files(files, ls_result="notes.txt\n001.json\n")
        env.read_file.side_effect = lambda p: files.get(p, "{}")
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda _: None)
        assert not (tmp_path / "r1" / "tool_calls" / "notes.txt").exists()

    def test_creates_dest_directory(self, tmp_path):
        dest = str(tmp_path / "new_dir")
        env = _make_env_with_files({})
        _pull_caf_artifacts(env, "r1", dest, lambda _: None)
        assert pathlib.Path(dest).is_dir()

    def test_execute_exception_logged(self, tmp_path):
        env = MagicMock(spec=["host", "username", "remote_cwd", "execute", "read_file"])
        env.execute.side_effect = Exception("SSH failure")
        env.read_file.side_effect = FileNotFoundError
        logs = []
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda m: logs.append(m))
        # Should log the error but not raise
        assert any("[CAF]" in l or "[WARN]" in l for l in logs)


# ── _telemetry_from_caf ────────────────────────────────────────────────────────

def _base_config(**kw):
    cfg = {
        "active_scenario": "CAF Test",
        "selected_model": "devstral",
        "backend_type": "ollama",
        "llm_url": "http://localhost:11434",
        "tool_focus": "",
        "metrics_matrix": [],
        "caf_scope": "Narrow",
        "caf_urgency": "Speed",
        "caf_allowed_subnets": [],
        "caf_target_credentials": [],
        "validation_command": "",
        "fail_patterns": [],
        "expected_stdout": "",
        "user_prompt": "Scan 192.168.1.1",
    }
    cfg.update(kw)
    return cfg


class TestTelemetryFromCaf:
    def _val_result(self, passed=True):
        return {"stdout": "", "stderr": "", "exit_code": 0, "passed": passed}

    def test_required_keys_present(self, tmp_path):
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        for key in ("run_timestamp", "run_model", "total_latency", "tool_calls",
                    "validation_passed", "caf_trajectory", "caf_config"):
            assert key in tel

    def test_metadata_start_time_used(self, tmp_path):
        meta = {"start_time": "2025-01-01 12:00:00"}
        tel = _telemetry_from_caf(meta, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert tel["run_timestamp"] == "2025-01-01 12:00:00"

    def test_non_completed_status_marks_aborted(self, tmp_path):
        meta = {"status": "error"}
        tel = _telemetry_from_caf(meta, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert tel["run_aborted"] is True

    def test_completed_status_not_aborted(self, tmp_path):
        meta = {"status": "completed"}
        tel = _telemetry_from_caf(meta, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert tel["run_aborted"] is False

    def test_tool_calls_parsed(self, tmp_path):
        run_dir = tmp_path / "r1" / "tool_calls"
        run_dir.mkdir(parents=True)
        tc = {"tool": "nmap", "args": {}, "result": "scan output", "exit_code": 0, "duration_ms": 500}
        (run_dir / "001.json").write_text(json.dumps(tc))
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert len(tel["tool_calls"]) == 1
        assert tel["tool_calls"][0]["tool"] == "nmap"

    def test_transcript_read_for_llm_response(self, tmp_path):
        run_dir = tmp_path / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "transcript.md").write_text("# Agent transcript\nFinal answer: done.")
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert "transcript" in tel["llm_response"].lower()

    def test_validation_fields(self, tmp_path):
        val = {"stdout": "1\n2", "stderr": "err", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), val)
        assert tel["validation_stdout"] == "1\n2"
        assert tel["validation_passed"] is True

    def test_caf_trajectory_populated(self, tmp_path):
        run_dir = tmp_path / "r1" / "tool_calls"
        run_dir.mkdir(parents=True)
        tc = {"tool": "nmap", "args": {}, "result": "22/tcp open", "exit_code": 0, "duration_ms": 200}
        (run_dir / "001.json").write_text(json.dumps(tc))
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert len(tel["caf_trajectory"]) == 1
        assert tel["caf_trajectory"][0]["tool_called"] == "nmap"

    def test_malformed_tool_call_json_skipped(self, tmp_path):
        run_dir = tmp_path / "r1" / "tool_calls"
        run_dir.mkdir(parents=True)
        (run_dir / "001.json").write_text("NOT JSON")
        # Should not raise, just skip the bad file
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), self._val_result())
        assert tel["tool_calls"] == []


# ── run_caf_ssh_evaluation ────────────────────────────────────────────────────

class TestRunCafSshEvaluationCafRunner:
    def _make_env(self, stdout="[run] Transcript: runs/abc123/transcript.md\n", exit_code=0):
        env = MagicMock(spec=["host", "username", "remote_cwd", "execute", "read_file"])
        env.host = "10.0.0.1"
        env.username = "kali"
        env.remote_cwd = "/opt/caf"
        env.execute.return_value = {"stdout": stdout, "stderr": "", "exit_code": exit_code}
        env.read_file.side_effect = lambda p: (
            json.dumps({"status": "completed"}) if "metadata.json" in p else "# transcript"
        )
        return env

    @patch("core.caf_runner._pull_caf_artifacts")
    @patch("core.caf_runner._telemetry_from_caf")
    def test_successful_run_calls_pull_and_telemetry(self, mock_tel, mock_pull, tmp_path):
        mock_pull.return_value = {"status": "completed"}
        mock_tel.return_value = _init_telemetry(_base_config())
        env = self._make_env()
        logs = []
        run_caf_ssh_evaluation(env, _base_config(), lambda m: logs.append(m),
                               local_run_history_dir=str(tmp_path))
        mock_pull.assert_called_once()
        mock_tel.assert_called_once()

    @patch("core.caf_runner._pull_caf_artifacts")
    def test_no_run_id_returns_basic_telemetry(self, mock_pull, tmp_path):
        env = self._make_env(stdout="No transcript line here")
        logs = []
        tel = run_caf_ssh_evaluation(env, _base_config(), lambda m: logs.append(m),
                                     local_run_history_dir=str(tmp_path))
        assert "run_aborted" in tel
        assert any("[WARN]" in l for l in logs)
        mock_pull.assert_not_called()

    @patch("core.caf_runner._pull_caf_artifacts")
    def test_nonzero_exit_code_marks_aborted(self, mock_pull, tmp_path):
        env = self._make_env(stdout="", exit_code=1)
        tel = run_caf_ssh_evaluation(env, _base_config(), lambda _: None,
                                     local_run_history_dir=str(tmp_path))
        assert tel["run_aborted"] is True
        mock_pull.assert_not_called()

    @patch("core.caf_runner._pull_caf_artifacts")
    @patch("core.evaluator._run_validation")
    @patch("core.caf_runner._telemetry_from_caf")
    def test_validation_runs_when_configured(self, mock_tel, mock_val, mock_pull, tmp_path):
        mock_pull.return_value = {}
        mock_val.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0, "passed": True}
        mock_tel.return_value = _init_telemetry(_base_config())
        env = self._make_env()
        run_caf_ssh_evaluation(env, _base_config(validation_command="cat /tmp/test"),
                               lambda _: None, local_run_history_dir=str(tmp_path))
        mock_val.assert_called_once()

    @patch("core.caf_runner._pull_caf_artifacts")
    @patch("core.evaluator._run_validation")
    @patch("core.caf_runner._telemetry_from_caf")
    def test_no_validation_when_empty(self, mock_tel, mock_val, mock_pull, tmp_path):
        mock_pull.return_value = {}
        mock_tel.return_value = _init_telemetry(_base_config())
        env = self._make_env()
        run_caf_ssh_evaluation(env, _base_config(validation_command=""), lambda _: None,
                               local_run_history_dir=str(tmp_path))
        mock_val.assert_not_called()

    def test_streaming_path_used_when_available(self, tmp_path):
        """When env has execute_streaming, it should be used."""
        env = MagicMock()  # Full MagicMock has execute_streaming
        env.host = "10.0.0.1"
        env.username = "kali"
        env.remote_cwd = "/opt/caf"
        # execute_streaming returns a proper dict
        env.execute_streaming.return_value = {
            "stdout": "[run] Transcript: runs/r1/transcript.md\n",
            "stderr": "",
            "exit_code": 0,
        }
        env.read_file.side_effect = lambda p: (
            json.dumps({"status": "completed"}) if "metadata" in p else "# t"
        )
        logs = []
        with patch("core.caf_runner._pull_caf_artifacts", return_value={}):
            with patch("core.caf_runner._telemetry_from_caf",
                       return_value=_init_telemetry(_base_config())):
                run_caf_ssh_evaluation(env, _base_config(), lambda m: logs.append(m),
                                       local_run_history_dir=str(tmp_path))
        env.execute_streaming.assert_called_once()
        env.execute.assert_not_called()
