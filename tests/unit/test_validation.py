"""
Unit tests for evaluator._run_validation and evaluator._check_inefficiencies.
"""
import pytest
from unittest.mock import MagicMock, patch
from core.evaluator import _run_validation, _check_inefficiencies


def _mock_env(stdout="", stderr="", exit_code=0):
    env = MagicMock()
    env.execute.return_value = {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
    }
    return env


# ── _run_validation ────────────────────────────────────────────────────────────

class TestRunValidation:
    def test_empty_command_returns_none_passed(self):
        env = _mock_env()
        result = _run_validation(env, "", [])
        assert result["passed"] is None
        assert result["exit_code"] is None
        env.execute.assert_not_called()

    def test_exit_zero_no_patterns_passes(self):
        env = _mock_env(stdout="1\n2\n3", exit_code=0)
        result = _run_validation(env, "cat /tmp/test", [])
        assert result["passed"] is True
        assert result["exit_code"] == 0

    def test_nonzero_exit_fails(self):
        env = _mock_env(stdout="", stderr="no such file", exit_code=1)
        result = _run_validation(env, "cat /tmp/missing", [])
        assert result["passed"] is False

    def test_fail_pattern_in_stdout_fails(self):
        env = _mock_env(stdout="no such file or directory", exit_code=0)
        result = _run_validation(env, "cat /tmp/test", ["no such file"])
        assert result["passed"] is False

    def test_fail_pattern_in_stderr_fails(self):
        env = _mock_env(stdout="", stderr="permission denied", exit_code=0)
        result = _run_validation(env, "cat /tmp/test", ["permission denied"])
        assert result["passed"] is False

    def test_fail_pattern_case_insensitive(self):
        env = _mock_env(stdout="PERMISSION DENIED", exit_code=0)
        result = _run_validation(env, "cmd", ["permission denied"])
        assert result["passed"] is False

    def test_unrelated_pattern_does_not_fail(self):
        env = _mock_env(stdout="All files created successfully.", exit_code=0)
        result = _run_validation(env, "ls", ["no such file", "permission denied"])
        assert result["passed"] is True

    def test_expected_stdout_exact_match_passes(self):
        env = _mock_env(stdout="1\n2\n3\n4\n5\n6\n7\n8\n9\n10", exit_code=0)
        result = _run_validation(
            env, "cat /tmp/test", [],
            expected_stdout="1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
        )
        assert result["passed"] is True

    def test_expected_stdout_mismatch_fails(self):
        env = _mock_env(stdout="1\n2\n3", exit_code=0)
        result = _run_validation(
            env, "cat /tmp/test", [],
            expected_stdout="1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
        )
        assert result["passed"] is False

    def test_expected_stdout_strips_whitespace(self):
        env = _mock_env(stdout="hello\n", exit_code=0)
        result = _run_validation(env, "cmd", [], expected_stdout="hello")
        assert result["passed"] is True

    def test_returns_stdout_and_stderr(self):
        env = _mock_env(stdout="out", stderr="err", exit_code=0)
        result = _run_validation(env, "cmd", [])
        assert result["stdout"] == "out"
        assert result["stderr"] == "err"


# ── _check_inefficiencies ─────────────────────────────────────────────────────

class TestCheckInefficiencies:
    def _tc(self, tool: str, args: dict = None):
        return {"tool": tool, "args": args or {}}

    def test_no_duplicates(self):
        calls = [
            self._tc("file_creator", {"path": "/a", "content": "1"}),
            self._tc("file_creator", {"path": "/b", "content": "2"}),
        ]
        assert _check_inefficiencies(calls) == []

    def test_duplicate_detected(self):
        calls = [
            self._tc("file_creator", {"path": "/a"}),
            self._tc("file_creator", {"path": "/a"}),
        ]
        issues = _check_inefficiencies(calls)
        assert len(issues) == 1
        assert "file_creator" in issues[0]

    def test_same_tool_different_args_is_fine(self):
        calls = [
            self._tc("run_nmap_scan", {"target": "10.0.0.1"}),
            self._tc("run_nmap_scan", {"target": "10.0.0.2"}),
        ]
        assert _check_inefficiencies(calls) == []

    def test_triple_duplicate_reports_once(self):
        calls = [self._tc("file_creator", {"path": "/x"})] * 3
        issues = _check_inefficiencies(calls)
        # Second call triggers the report; third call increments count but doesn't re-report
        assert len(issues) == 1

    def test_empty_list(self):
        assert _check_inefficiencies([]) == []

    def test_different_tools_no_issue(self):
        calls = [
            self._tc("file_creator", {"path": "/x"}),
            self._tc("run_nmap_scan", {"target": "127.0.0.1"}),
        ]
        assert _check_inefficiencies(calls) == []
