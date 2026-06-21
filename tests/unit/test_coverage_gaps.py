"""
Targeted tests to cover remaining uncovered branches across:
  - core/preflight.py    (TestResult.icon None branch, fail paths, LLM smoke branches)
  - core/models.py       (OSError in getsize, MissingSchema, detect_backend)
  - core/test_runner.py  (_pytest_cmd, run_tests with subprocess)
  - core/evaluator.py    (_load_all_tool_schemas, _execute_tool MCP path, cancel branch)
"""
import json
import os
import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock, call


# ── core/preflight.py ─────────────────────────────────────────────────────────

class TestTestResultIconNone:
    def test_icon_none_returns_circle(self):
        from core.preflight import TestResult
        r = TestResult("check", "platform", None, "detail")
        assert r.icon == "○"

    def test_icon_true_returns_checkmark(self):
        from core.preflight import TestResult
        r = TestResult("check", "platform", True, "detail")
        assert r.icon == "✓"

    def test_icon_false_returns_cross(self):
        from core.preflight import TestResult
        r = TestResult("check", "platform", False, "detail")
        assert r.icon == "✗"


class TestTimeHelper:
    def test_time_function_returns_result_and_ms(self):
        from core.preflight import _time
        result, ms = _time(lambda: 42)
        assert result == 42
        assert ms >= 0.0


class TestBackendConnectivityFailBranches:
    """Hit the HTTP-error and timeout branches in check_backend_connectivity."""

    @patch("requests.get")
    def test_http_non_ok_response_fails(self, mock_get):
        from core.preflight import check_backend_connectivity
        mock_get.return_value.ok = False
        mock_get.return_value.status_code = 503
        state = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080"}
        r = check_backend_connectivity(state)
        assert r.passed is False
        assert "503" in r.detail

    @patch("requests.get", side_effect=__import__("requests").exceptions.Timeout("timed out"))
    def test_timeout_returns_none(self, _):
        from core.preflight import check_backend_connectivity
        state = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080"}
        r = check_backend_connectivity(state)
        assert r.passed is None
        assert "timed out" in r.detail.lower()

    @patch("requests.get", side_effect=__import__("requests").exceptions.ConnectionError("refused"))
    def test_connection_error_returns_none(self, _):
        from core.preflight import check_backend_connectivity
        state = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080"}
        r = check_backend_connectivity(state)
        assert r.passed is None


class TestCheckTimeoutHandlingFail:
    """Cover the False branch of check_timeout_handling (lines 220-224)."""

    def test_false_return_from_is_running_unexpected(self):
        """If is_running returns True (unexpectedly), the check records False."""
        from core.preflight import check_timeout_handling
        with patch("core.llama_server.is_running", return_value=True):
            r = check_timeout_handling()
        # True means "server is running" but the check wants False (unreachable)
        # → the check will report passed=False with "Unexpected: is_running returned True"
        assert r.passed is False
        assert "Unexpected" in r.detail

    def test_exception_in_is_running_caught(self):
        """Exception in is_running gives passed=False."""
        from core.preflight import check_timeout_handling
        with patch("core.llama_server.is_running", side_effect=RuntimeError("boom")):
            r = check_timeout_handling()
        assert r.passed is False
        assert "boom" in r.detail


class TestFilesystemAccessFailBranches:
    """Cover error branches in check_filesystem_access (lines 245, 250, 255, 261-262)."""

    def test_write_failure_returns_false(self):
        from core.preflight import check_filesystem_access
        from core.environment import LocalEnvironment
        with patch.object(LocalEnvironment, "write_file", return_value={"error": "disk full"}):
            r = check_filesystem_access()
        assert r.passed is False
        assert "Write failed" in r.detail

    def test_read_mismatch_returns_false(self):
        from core.preflight import check_filesystem_access
        from core.environment import LocalEnvironment
        with patch.object(LocalEnvironment, "write_file", return_value={"status": "success"}):
            with patch.object(LocalEnvironment, "read_file", return_value="wrong content"):
                r = check_filesystem_access()
        assert r.passed is False
        assert "mismatch" in r.detail.lower() or "Read" in r.detail

    def test_delete_failure_returns_false(self):
        from core.preflight import check_filesystem_access
        from core.environment import LocalEnvironment
        with patch.object(LocalEnvironment, "write_file", return_value={"status": "success"}):
            with patch.object(LocalEnvironment, "read_file", return_value="preflight_ok"):
                with patch.object(LocalEnvironment, "delete_file", return_value=False):
                    r = check_filesystem_access()
        assert r.passed is False
        assert "Delete" in r.detail

    def test_exception_caught_returns_false(self):
        from core.preflight import check_filesystem_access
        from core.environment import LocalEnvironment
        with patch.object(LocalEnvironment, "write_file", side_effect=RuntimeError("crash")):
            r = check_filesystem_access()
        assert r.passed is False
        assert "Exception" in r.detail


class TestKnownGoodTelemetryFailBranch:
    """Cover the fail branch in check_known_good_telemetry (lines 419-428)."""

    def test_metric_failing_on_good_tel_returns_false(self):
        from core.preflight import check_known_good_telemetry
        from config.metrics import make_metric
        # A latency metric with max_seconds=0 will always fail
        metrics = [make_metric("m1", "Strict Latency", "latency", max_seconds=0.0)]
        r = check_known_good_telemetry(metrics, "")
        assert r.passed is False
        assert "m1" in r.detail

    def test_disabled_metric_skipped(self):
        from core.preflight import check_known_good_telemetry
        from config.metrics import make_metric
        m = make_metric("m1", "Strict Latency", "latency", max_seconds=0.0)
        m["enabled"] = False
        r = check_known_good_telemetry([m], "")
        # Disabled metrics are skipped; no failures → passes
        assert r.passed is True


class TestKnownBadTelemetryFailBranch:
    """Cover fail branch in check_known_bad_telemetry (lines 460-464)."""

    def test_gate_metric_not_failing_returns_false(self):
        """Patching evaluate_metric to return True (not False) exposes the error path."""
        from core.preflight import check_known_bad_telemetry
        from config.metrics import make_metric
        gate = make_metric("m1", "TC", "task_completion")

        # evaluate_metric is imported inside check_known_bad_telemetry via
        # "from config.metrics import evaluate_metric", so patch at the source module.
        with patch("config.metrics.evaluate_metric", return_value=True):
            r = check_known_bad_telemetry([gate])
        assert r.passed is False
        assert "m1" in r.detail


class TestValidationLogicAlignmentFailBranches:
    """Cover early-exit failure paths in check_validation_logic_alignment."""

    def test_clean_run_not_passing_returns_false(self):
        from core.preflight import check_validation_logic_alignment
        # _run_validation is imported inside check_validation_logic_alignment
        # via "from core.evaluator import _run_validation", so patch at source.
        with patch("core.evaluator._run_validation") as mock_val:
            mock_val.return_value = {"passed": False, "stdout": "", "stderr": ""}
            r = check_validation_logic_alignment()
        assert r.passed is False
        assert "should pass" in r.detail

    def test_noisy_run_not_failing_returns_false(self):
        from core.preflight import check_validation_logic_alignment
        results = [
            {"passed": True, "stdout": "ok", "stderr": ""},    # clean case → OK
            {"passed": True, "stdout": "ok", "stderr": ""},    # noisy case → should be False
        ]
        with patch("core.evaluator._run_validation", side_effect=results):
            r = check_validation_logic_alignment()
        assert r.passed is False
        assert "pattern" in r.detail.lower() or "caught" in r.detail.lower()

    def test_fail_exit_not_failing_returns_false(self):
        from core.preflight import check_validation_logic_alignment
        results = [
            {"passed": True, "stdout": "ok", "stderr": ""},    # clean → ok
            {"passed": False, "stdout": "err", "stderr": ""},  # noisy → ok
            {"passed": True, "stdout": "", "stderr": ""},      # fail exit → should be False
        ]
        with patch("core.evaluator._run_validation", side_effect=results):
            r = check_validation_logic_alignment()
        assert r.passed is False
        assert "exit" in r.detail.lower() or "fail" in r.detail.lower()

    def test_exception_caught_returns_false(self):
        from core.preflight import check_validation_logic_alignment
        with patch("core.evaluator._run_validation", side_effect=RuntimeError("crash")):
            r = check_validation_logic_alignment()
        assert r.passed is False
        assert "Exception" in r.detail


class TestLlmSmokeAdditionalBranches:
    """Cover LLM smoke test branches not hit by existing tests."""

    @patch("core.evaluator.run_evaluation")
    @patch("core.preflight.requests.get")
    def test_run_evaluation_raises_returns_false(self, mock_get, mock_run):
        from core.preflight import check_llm_smoke
        mock_get.return_value.ok = True
        mock_run.side_effect = RuntimeError("crash")
        state = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080",
                 "selected_model": "", "context_size": 4096, "mcp_url": ""}
        with patch("core.llama_server.is_running", return_value=True):
            r = check_llm_smoke(state, timeout_s=10)
        assert r.passed is False

    @patch("core.evaluator.run_evaluation")
    @patch("core.preflight.requests.get")
    def test_run_aborted_returns_false(self, mock_get, mock_run):
        from core.preflight import check_llm_smoke
        mock_get.return_value.ok = True
        mock_run.return_value = {
            "validation_passed": False,
            "run_aborted": True,
            "llm_rounds": 0,
            "tool_calls": [],
            "validation_stdout": "",
        }
        state = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080",
                 "selected_model": "", "context_size": 4096, "mcp_url": ""}
        with patch("core.llama_server.is_running", return_value=True):
            r = check_llm_smoke(state, timeout_s=10)
        assert r.passed is False
        assert "aborted" in r.detail.lower()

    @patch("core.preflight.requests.get")
    def test_ollama_not_ok_returns_none(self, mock_get):
        from core.preflight import check_llm_smoke
        mock_get.return_value.ok = False
        state = {"backend_type": "ollama", "llm_url": "http://localhost:11434",
                 "selected_model": "", "context_size": 4096, "mcp_url": ""}
        r = check_llm_smoke(state, timeout_s=5)
        assert r.passed is None
        assert "skipped" in r.detail.lower()


# ── core/models.py ────────────────────────────────────────────────────────────

class TestScanGgufModelsOsError:
    def test_oserror_in_getsize_uses_zero(self, tmp_path):
        from core.models import scan_gguf_models
        # Create a real .gguf file so os.walk finds it
        model_file = tmp_path / "test.gguf"
        model_file.write_bytes(b"\x00" * 10)
        with patch("os.path.getsize", side_effect=OSError("permission denied")):
            results = scan_gguf_models(str(tmp_path))
        assert len(results) == 1
        assert results[0]["size_gb"] == 0.0


class TestFetchOllamaModelsMissingSchema:
    def test_missing_schema_error_returns_error_message(self):
        import requests
        from core.models import fetch_ollama_models
        with patch("requests.get", side_effect=requests.exceptions.MissingSchema("bad url")):
            models_list, error = fetch_ollama_models("not-a-url")
        assert models_list == []
        assert "missing scheme" in error.lower() or "invalid" in error.lower()


class TestDetectBackend:
    @patch("requests.get")
    def test_detects_ollama(self, mock_get):
        from core.models import detect_backend
        mock_get.return_value.ok = True
        mock_get.return_value.json.return_value = {"models": []}
        result = detect_backend("http://localhost:11434")
        assert result == "ollama"

    @patch("requests.get")
    def test_detects_llama_cpp(self, mock_get):
        from core.models import detect_backend
        # First call (ollama /api/tags): raise exception
        # Second call (llama.cpp /v1/models): ok
        first = MagicMock()
        first.ok = True
        first.json.return_value = {"not_models": []}  # No "models" key → not ollama

        second = MagicMock()
        second.ok = True

        mock_get.side_effect = [first, second]
        result = detect_backend("http://localhost:8080")
        assert result == "llama.cpp"

    @patch("requests.get", side_effect=Exception("refused"))
    def test_returns_none_when_both_fail(self, _):
        from core.models import detect_backend
        result = detect_backend("http://nowhere:9999")
        assert result is None

    @patch("requests.get")
    def test_ollama_exception_falls_through_to_llama(self, mock_get):
        from core.models import detect_backend
        llama_resp = MagicMock()
        llama_resp.ok = True
        mock_get.side_effect = [Exception("ollama down"), llama_resp]
        result = detect_backend("http://localhost:8080")
        assert result == "llama.cpp"


# ── core/test_runner.py ───────────────────────────────────────────────────────

class TestPytestCmd:
    def test_returns_list_with_pytest(self):
        from core.test_runner import _pytest_cmd
        cmd = _pytest_cmd()
        assert isinstance(cmd, list)
        assert len(cmd) >= 2
        assert "pytest" in cmd[-1] or cmd[-1] == "pytest"

    def test_returns_list_when_python3_not_found(self):
        """When shutil.which returns None for python3, falls back to next option."""
        import shutil
        from core.test_runner import _pytest_cmd
        original_which = shutil.which
        def mock_which(name):
            if name == "python3":
                return None
            return original_which(name)
        with patch("shutil.which", side_effect=mock_which):
            cmd = _pytest_cmd()
        assert isinstance(cmd, list)
        assert len(cmd) >= 2


class TestRunTests:
    def test_returns_test_run_result(self):
        from core.test_runner import run_tests, TestRunResult
        # Point at a non-existent test path — pytest exits quickly with error
        result = run_tests(test_path="tests/unit/test_utils.py", timeout=60)
        assert isinstance(result, TestRunResult)

    def test_timeout_expired_returns_error_msg(self):
        from core.test_runner import run_tests, TestRunResult
        # Use a 0-second timeout to force TimeoutExpired
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["pytest"], timeout=0)):
            result = run_tests(test_path="tests/unit/test_utils.py", timeout=0)
        assert isinstance(result, TestRunResult)
        assert "timed out" in result.error_msg.lower()

    def test_exception_in_subprocess_returns_error_msg(self):
        from core.test_runner import run_tests, TestRunResult

        # _pytest_cmd() calls subprocess.run with --version to probe, then run_tests
        # calls subprocess.run with the actual test command. We allow the --version
        # calls to succeed (returncode=0) and make the actual test run raise OSError.
        call_count = [0]
        version_result = MagicMock()
        version_result.returncode = 0

        def smart_side_effect(*args, **kwargs):
            call_count[0] += 1
            # --version probes: let them succeed
            if "--version" in (args[0] if args else kwargs.get("args", [])):
                return version_result
            # Actual pytest invocation: raise
            raise OSError("no such file")

        with patch("core.test_runner.subprocess.run", side_effect=smart_side_effect):
            result = run_tests(test_path="tests/unit/test_utils.py", timeout=30)
        assert isinstance(result, TestRunResult)
        assert "no such file" in result.error_msg


# ── core/evaluator.py ─────────────────────────────────────────────────────────

class TestLoadAllToolSchemas:
    def test_returns_empty_when_no_tools_json(self, tmp_path):
        from core.evaluator import _load_tool_schemas as _load_all_tool_schemas
        result = _load_all_tool_schemas(str(tmp_path / "index.js"))
        assert result == []

    def test_returns_schemas_from_tools_json(self, tmp_path):
        from core.evaluator import _load_tool_schemas as _load_all_tool_schemas
        tools_data = [
            {"name": "file_creator", "description": "Creates files",
             "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}}
        ]
        tools_file = tmp_path / "tools.json"
        tools_file.write_text(json.dumps(tools_data))
        # mcp_script_path's dirname is tmp_path, so tools.json is found
        result = _load_all_tool_schemas(str(tmp_path / "index.js"))
        assert len(result) == 1
        assert result[0]["function"]["name"] == "file_creator"

    def test_skips_entries_without_name(self, tmp_path):
        from core.evaluator import _load_tool_schemas as _load_all_tool_schemas
        tools_data = [
            {"description": "No name tool"},
            {"name": "valid_tool", "description": "Good"},
        ]
        tools_file = tmp_path / "tools.json"
        tools_file.write_text(json.dumps(tools_data))
        result = _load_all_tool_schemas(str(tmp_path / "index.js"))
        assert len(result) == 1
        assert result[0]["function"]["name"] == "valid_tool"

    def test_invalid_json_returns_empty(self, tmp_path):
        from core.evaluator import _load_tool_schemas as _load_all_tool_schemas
        tools_file = tmp_path / "tools.json"
        tools_file.write_text("NOT JSON {{{")
        result = _load_all_tool_schemas(str(tmp_path / "index.js"))
        assert result == []


class TestExecuteToolMcpPath:
    """Cover the MCP-first path in _execute_tool (lines 116-118)."""

    def test_mcp_success_skips_local(self):
        from core.evaluator import _execute_tool
        from core.environment import LocalEnvironment
        env = MagicMock(spec=LocalEnvironment)
        with patch("core.evaluator.call_mcp_tool", return_value={"stdout": "mcp result"}):
            result = _execute_tool(env, "file_creator", {"path": "/tmp/x"}, mcp_running=True)
        assert result == {"stdout": "mcp result"}
        env.write_file.assert_not_called()

    def test_mcp_error_falls_through_to_local(self):
        from core.evaluator import _execute_tool
        env = MagicMock()
        env.write_file.return_value = {"status": "success"}
        with patch("core.evaluator.call_mcp_tool", return_value={"error": "mcp down"}):
            result = _execute_tool(env, "file_creator", {"path": "/tmp/x", "content": "hi"},
                                   mcp_running=True)
        env.write_file.assert_called_once()


class TestRunEvaluationCancelBranch:
    """Cover cancel_ref=True path in run_evaluation (line 291-292)."""

    @patch("core.evaluator.stream_llama_cpp")
    def test_cancel_before_tool_calls_aborts(self, mock_stream):
        from core.evaluator import run_evaluation
        from core.environment import LocalEnvironment
        # LLM responds with a tool call
        mock_stream.return_value = {
            "message": {"role": "assistant", "content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": "file_creator",
                             "arguments": '{"path": "/tmp/x", "content": "y"}'},
            }]},
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        cancel_ref = [True]  # Already cancelled before tool execution
        env = LocalEnvironment()
        config = {
            "backend_type": "llama.cpp",
            "llm_url": "http://localhost:8080",
            "selected_model": "test.gguf",
            "context_size": 4096,
            "sys_prompt": "You are helpful.",
            "user_prompt": "Create file",
            "mcp_url": "",
            "mcp_tools": {"file_creator": True},
            "mcp_running": False,
            "validation_command": "",
            "fail_patterns": [],
            "active_scenario": "Scenario 1 – File Creation",
            "expected_stdout": "",
            "pre_run_cleanup": [],
            "cancel_requested_ref": cancel_ref,
        }
        tel = run_evaluation(env, config, lambda _: None)
        # Run should be marked aborted since cancel was set
        assert tel["run_aborted"] is True
