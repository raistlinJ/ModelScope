import pytest
from core.environment import BaseEnvironment
from core.evaluator import _run_validation
from unittest.mock import MagicMock

def test_run_validation_large_output():
    env = MagicMock(spec=BaseEnvironment)
    # 1MB of output
    large_stdout = "A" * 1024 * 1024
    env.execute.return_value = {
        "stdout": large_stdout,
        "stderr": "",
        "exit_code": 0
    }
    
    res = _run_validation(env, "cmd", [], expected_stdout=large_stdout)
    assert res["passed"] is True
    assert len(res["stdout"]) == 1024 * 1024

def test_run_validation_mismatch_with_whitespace():
    env = MagicMock(spec=BaseEnvironment)
    env.execute.return_value = {
        "stdout": "  output  \n",
        "stderr": "",
        "exit_code": 0
    }
    
    # Should pass because of strip()
    res = _run_validation(env, "cmd", [], expected_stdout="output")
    assert res["passed"] is True

def test_run_validation_fail_pattern_at_start():
    env = MagicMock(spec=BaseEnvironment)
    env.execute.return_value = {
        "stdout": "ERROR: something went wrong",
        "stderr": "",
        "exit_code": 0
    }
    
    res = _run_validation(env, "cmd", ["ERROR"], expected_stdout="")
    assert res["passed"] is False

def test_run_validation_empty_command():
    env = MagicMock(spec=BaseEnvironment)
    res = _run_validation(env, "  ", [])
    assert res["exit_code"] is None
    assert res["passed"] is None
