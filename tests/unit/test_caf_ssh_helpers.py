"""
Unit tests for the CAF SSH helper functions in core.evaluator:
  - _strip_ansi
  - _pull_caf_artifacts
  - _telemetry_from_caf
  - run_caf_ssh_evaluation
"""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from core.evaluator import (
    _strip_ansi,
    _pull_caf_artifacts,
    _telemetry_from_caf,
    run_caf_ssh_evaluation,
    _init_telemetry,
)


# ── _strip_ansi ───────────────────────────────────────────────────────────────

class TestStripAnsi:
    def test_removes_color_codes(self):
        raw = "\x1b[38;5;246m[status]\x1b[0m Starting session..."
        assert _strip_ansi(raw) == "[status] Starting session..."

    def test_removes_cursor_control_sequences(self):
        # ESC[1;21r  ESC[22;1H  ESC[K
        raw = "\x1b[1;21r\x1b[22;1H\x1b[K waiting"
        assert _strip_ansi(raw) == " waiting"

    def test_removes_bold_and_reset(self):
        raw = "\x1b[1mbold text\x1b[0m normal"
        assert _strip_ansi(raw) == "bold text normal"

    def test_plain_text_unchanged(self):
        text = "[run] Transcript: runs/2026-06-18_14-41-10_cli/transcript.md"
        assert _strip_ansi(text) == text

    def test_empty_string(self):
        assert _strip_ansi("") == ""

    def test_run_id_still_parseable_after_strip(self):
        """Stripping ANSI from CAF output must not destroy the run_id line."""
        from core.evaluator import _parse_caf_run_id
        raw = (
            "\x1b[1m[run]\x1b[0m Transcript: \x1b[32mruns/2026-06-18_14-41-10_cli/"
            "transcript.md\x1b[0m"
        )
        cleaned = _strip_ansi(raw)
        run_id = _parse_caf_run_id(cleaned)
        assert run_id == "2026-06-18_14-41-10_cli"

    def test_multiple_sequences_in_one_string(self):
        raw = "\x1b[38;5;246m[status]\x1b[0m \x1b[1;21r\x1b[22;1H\x1b[K \x1b[2mwaiting\x1b[0m"
        result = _strip_ansi(raw)
        assert "\x1b" not in result
        assert "status" in result
        assert "waiting" in result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env_with_files(files: dict, ls_result: str = ""):
    """
    Build a mock SSHEnvironment whose read_file returns content from `files`
    and execute returns ls output for tool_calls listing.
    """
    env = MagicMock()
    env.host = "10.0.0.1"
    env.username = "kali"
    env.remote_cwd = "/opt/caf"
    env.is_remote_caf = True

    def read_file(path):
        if path in files:
            return files[path]
        raise FileNotFoundError(f"no file: {path}")

    env.read_file.side_effect = read_file
    env.execute.return_value = {"stdout": ls_result, "stderr": "", "exit_code": 0}
    return env


def _base_config(**kw):
    cfg = {
        "active_scenario": "CAF – Guardrail Test",
        "selected_model": "devstral",
        "backend_type": "ollama",
        "llm_url": "http://localhost:11434",
        "tool_focus": "",
        "metrics_matrix": [],
        "caf_scope": "Narrow",
        "caf_urgency": "Stealthy",
        "caf_allowed_subnets": ["192.168.1.0/24"],
        "caf_target_credentials": [],
        "validation_command": "",
        "fail_patterns": [],
        "expected_stdout": "",
        "user_prompt": "Scan 192.168.1.1",
    }
    cfg.update(kw)
    return cfg


# ── _pull_caf_artifacts ───────────────────────────────────────────────────────

class TestPullCafArtifacts:
    def test_pulls_transcript_and_metadata(self, tmp_path):
        metadata_dict = {"start_time": "2025-01-01 12:00:00", "status": "completed"}
        files = {
            "runs/abc123/transcript.md": "# Run transcript",
            "runs/abc123/metadata.json": json.dumps(metadata_dict),
        }
        env = _env_with_files(files)
        logs = []

        metadata = _pull_caf_artifacts(env, "abc123", str(tmp_path), lambda m: logs.append(m))

        assert metadata == metadata_dict
        assert (tmp_path / "abc123" / "transcript.md").exists()
        assert (tmp_path / "abc123" / "metadata.json").exists()

    def test_creates_dest_directory(self, tmp_path):
        env = _env_with_files({})
        dest = str(tmp_path / "new_dir")
        _pull_caf_artifacts(env, "run_id", dest, lambda _: None)
        assert Path(dest).is_dir()

    def test_missing_file_logged_as_warn(self, tmp_path):
        env = _env_with_files({})  # no files → read_file raises
        logs = []
        _pull_caf_artifacts(env, "abc123", str(tmp_path), lambda m: logs.append(m))
        assert any("[WARN]" in l for l in logs)

    def test_pulls_tool_call_json_files(self, tmp_path):
        files = {
            "runs/r1/transcript.md": "# T",
            "runs/r1/metadata.json": "{}",
            "runs/r1/tool_calls/001.json": json.dumps({"tool": "nmap", "exit_code": 0}),
        }
        env = _env_with_files(files, ls_result="001.json\n")
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda _: None)
        assert (tmp_path / "r1" / "tool_calls" / "001.json").exists()

    def test_non_json_ls_entries_skipped(self, tmp_path):
        files = {"runs/r1/transcript.md": "# T", "runs/r1/metadata.json": "{}"}
        # ls output contains a non-.json file
        env = _env_with_files(files, ls_result="notes.txt\n001.json\n")
        # Only 001.json should be pulled; notes.txt should be skipped
        env.read_file.side_effect = lambda path: files.get(path, "{}")
        _pull_caf_artifacts(env, "r1", str(tmp_path), lambda _: None)
        # notes.txt should NOT be created
        assert not (tmp_path / "r1" / "tool_calls" / "notes.txt").exists()

    def test_returns_metadata_dict(self, tmp_path):
        meta = {"model": "devstral", "status": "completed"}
        files = {
            "runs/r1/transcript.md": "# T",
            "runs/r1/metadata.json": json.dumps(meta),
        }
        env = _env_with_files(files)
        result = _pull_caf_artifacts(env, "r1", str(tmp_path), lambda _: None)
        assert result["model"] == "devstral"


# ── _telemetry_from_caf ────────────────────────────────────────────────────────

class TestTelemetryFromCaf:
    def test_required_keys_present(self, tmp_path):
        metadata = {"start_time": "2025-01-01", "status": "completed"}
        val_result = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf(metadata, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        for key in ("run_timestamp", "run_model", "total_latency", "tool_calls",
                    "validation_passed", "caf_trajectory", "caf_config"):
            assert key in tel

    def test_metadata_start_time_used(self, tmp_path):
        metadata = {"start_time": "2024-12-01 10:00:00"}
        val_result = {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
        tel = _telemetry_from_caf(metadata, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert tel["run_timestamp"] == "2024-12-01 10:00:00"

    def test_non_completed_status_marks_aborted(self, tmp_path):
        metadata = {"status": "error"}
        val_result = {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
        tel = _telemetry_from_caf(metadata, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert tel["run_aborted"] is True

    def test_completed_status_not_aborted(self, tmp_path):
        metadata = {"status": "completed"}
        val_result = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf(metadata, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert tel["run_aborted"] is False

    def test_tool_calls_parsed_from_json_files(self, tmp_path):
        run_dir = tmp_path / "r1" / "tool_calls"
        run_dir.mkdir(parents=True)
        tc = {"tool": "nmap", "args": {"target": "192.168.1.1"}, "result": "open", "exit_code": 0, "duration_ms": 500}
        (run_dir / "001.json").write_text(json.dumps(tc))
        val_result = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert len(tel["tool_calls"]) == 1
        assert tel["tool_calls"][0]["tool"] == "nmap"

    def test_transcript_read_for_llm_response(self, tmp_path):
        run_dir = tmp_path / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "transcript.md").write_text("# Agent transcript\nFinal answer: done.")
        val_result = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert "transcript" in tel["llm_response"].lower()

    def test_validation_fields_from_val_result(self, tmp_path):
        val_result = {"stdout": "1\n2", "stderr": "err", "exit_code": 0, "passed": True}
        tel = _telemetry_from_caf({}, str(tmp_path), "r1", 0.0, _base_config(), val_result)
        assert tel["validation_stdout"] == "1\n2"
        assert tel["validation_passed"] is True


# ── run_caf_ssh_evaluation ────────────────────────────────────────────────────

class TestRunCafSshEvaluation:
    def _make_env(self, exit_code=0, stdout="[run] Transcript: runs/abc123/transcript.md\n"):
        env = MagicMock()
        env.host = "10.0.0.1"
        env.username = "kali"
        env.remote_cwd = "/opt/caf"
        env.execute.return_value = {
            "stdout": stdout,
            "stderr": "",
            "exit_code": exit_code,
        }
        env.read_file.side_effect = lambda path: json.dumps({"status": "completed"}) \
            if "metadata.json" in path else "# transcript"
        return env

    @patch("core.evaluator._pull_caf_artifacts")
    @patch("core.evaluator._telemetry_from_caf")
    def test_delegates_to_pull_and_telemetry(self, mock_tel, mock_pull, tmp_path):
        mock_pull.return_value = {"status": "completed"}
        mock_tel.return_value = _init_telemetry(_base_config())

        env = self._make_env()
        logs = []
        run_caf_ssh_evaluation(env, _base_config(), lambda m: logs.append(m),
                               local_run_history_dir=str(tmp_path))

        mock_pull.assert_called_once()
        mock_tel.assert_called_once()

    @patch("core.evaluator._pull_caf_artifacts")
    def test_no_run_id_returns_basic_telemetry(self, mock_pull, tmp_path):
        env = self._make_env(stdout="No transcript line here")
        logs = []
        tel = run_caf_ssh_evaluation(env, _base_config(), lambda m: logs.append(m),
                                     local_run_history_dir=str(tmp_path))
        assert "run_aborted" in tel
        assert any("[WARN]" in l for l in logs)
        mock_pull.assert_not_called()

    @patch("core.evaluator._pull_caf_artifacts")
    def test_nonzero_exit_code_marks_aborted(self, mock_pull, tmp_path):
        env = self._make_env(exit_code=1, stdout="")
        tel = run_caf_ssh_evaluation(env, _base_config(), lambda _: None,
                                     local_run_history_dir=str(tmp_path))
        assert tel["run_aborted"] is True
        mock_pull.assert_not_called()

    @patch("core.evaluator._pull_caf_artifacts")
    @patch("core.evaluator._run_validation")
    @patch("core.evaluator._telemetry_from_caf")
    def test_validation_run_when_command_set(self, mock_tel, mock_val, mock_pull, tmp_path):
        mock_pull.return_value = {}
        mock_val.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0, "passed": True}
        mock_tel.return_value = _init_telemetry(_base_config())

        env = self._make_env()
        run_caf_ssh_evaluation(
            env,
            _base_config(validation_command="cat /tmp/test"),
            lambda _: None,
            local_run_history_dir=str(tmp_path),
        )
        mock_val.assert_called_once()

    @patch("core.evaluator._pull_caf_artifacts")
    @patch("core.evaluator._run_validation")
    @patch("core.evaluator._telemetry_from_caf")
    def test_no_validation_when_command_empty(self, mock_tel, mock_val, mock_pull, tmp_path):
        mock_pull.return_value = {}
        mock_tel.return_value = _init_telemetry(_base_config())

        env = self._make_env()
        run_caf_ssh_evaluation(env, _base_config(validation_command=""), lambda _: None,
                               local_run_history_dir=str(tmp_path))
        mock_val.assert_not_called()
