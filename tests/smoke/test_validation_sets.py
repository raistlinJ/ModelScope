import pytest
import re
from unittest.mock import MagicMock
from core.evaluator import _run_validation_sets
from core.environment import LocalEnvironment
from config.scenarios import SCENARIOS

def _env(stdout="", stderr="", exit_code=0):
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = False
    return env

def test_validation_sets_ignore_success():
    """Verify that Ignore type check passes on exit code 0 regardless of stdout."""
    env = _env(stdout="any random output", exit_code=0)
    vsets = [{
        "name": "IgnoreTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "echo test",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Ignore",
                "expected_output": ""
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["steps"][0]["passed"] is True

def test_validation_sets_ignore_failure():
    """Verify that Ignore type check fails if exit code is non-zero."""
    env = _env(stdout="output", exit_code=1)
    vsets = [{
        "name": "IgnoreTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "echo test",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Ignore",
                "expected_output": ""
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is False
    assert results[0]["passed"] is False
    assert results[0]["steps"][0]["passed"] is False
    assert "Exit code" in results[0]["steps"][0]["reason"]

def test_validation_sets_exact_string_success():
    """Verify Exact String passes when output matches exactly after stripping whitespace."""
    env = _env(stdout="  hello world  \n", exit_code=0)
    vsets = [{
        "name": "ExactTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "echo hello world",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Exact String",
                "expected_output": "hello world"
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["steps"][0]["passed"] is True

def test_validation_sets_exact_string_failure():
    """Verify Exact String fails when output doesn't match."""
    env = _env(stdout="different output", exit_code=0)
    vsets = [{
        "name": "ExactTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "echo hello world",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Exact String",
                "expected_output": "hello world"
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is False
    assert results[0]["passed"] is False
    assert results[0]["steps"][0]["passed"] is False
    assert "Output did not match exact string" in results[0]["steps"][0]["reason"]

def test_validation_sets_regex_success():
    """Verify Regex pattern match passes when regex pattern is found in stdout."""
    env = _env(stdout="Nmap scan report for localhost (127.0.0.1)\nPORT STATE SERVICE\n22/tcp open ssh", exit_code=0)
    vsets = [{
        "name": "RegexTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "nmap",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Regex",
                "expected_output": "PORT\\s+STATE\\s+SERVICE"
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["steps"][0]["passed"] is True

def test_validation_sets_regex_failure():
    """Verify Regex fails when pattern doesn't match stdout."""
    env = _env(stdout="Nmap failed", exit_code=0)
    vsets = [{
        "name": "RegexTest",
        "description": "desc",
        "steps": [{
            "delay_seconds": 0.0,
            "commands": [{
                "command": "nmap",
                "enabled": True,
                "timeout_seconds": 5,
                "expected_output_type": "Regex",
                "expected_output": "PORT\\s+STATE\\s+SERVICE"
            }]
        }]
    }]
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is False
    assert results[0]["passed"] is False
    assert results[0]["steps"][0]["passed"] is False
    assert "Output did not match regex pattern" in results[0]["steps"][0]["reason"]

def test_scenario_1_defaults_success():
    """Verify Scenario 1 default validation sets pass with correct mock outputs."""
    sc = SCENARIOS["Scenario 1 – File Creation"]
    vsets = sc["default_validation_sets"]
    
    env = MagicMock(spec=LocalEnvironment)
    env.is_remote_caf = False
    
    # ls /tmp/test exits 0
    # cat /tmp/test exits 0 with numbers 1 through 10
    env.execute.side_effect = [
        {"stdout": "/tmp/test", "stderr": "", "exit_code": 0},
        {"stdout": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10", "stderr": "", "exit_code": 0}
    ]
    
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["steps"][0]["passed"] is True
    assert results[0]["steps"][1]["passed"] is True

def test_scenario_2_defaults_success():
    """Verify Scenario 2 default validation sets pass with correct mock outputs."""
    sc = SCENARIOS["Scenario 2 – Network Scan"]
    vsets = sc["default_validation_sets"]
    
    env = MagicMock(spec=LocalEnvironment)
    env.is_remote_caf = False
    
    # nmap exits 0 with standard output
    env.execute.return_value = {
        "stdout": "Nmap scan report for 127.0.0.1\nPORT STATE SERVICE\n80/tcp open http",
        "stderr": "",
        "exit_code": 0
    }
    
    passed, results = _run_validation_sets(env, vsets, lambda m: None)
    assert passed is True
    assert results[0]["passed"] is True
    assert results[0]["steps"][0]["passed"] is True
