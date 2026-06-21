"""
Extended unit tests for core/evaluator.py covering previously-uncovered lines:
  - Lines 303-304: malformed JSON tool arguments fallback
  - Lines 444-449: tool schema auto-load fallback (mcp_tools empty)
  - Lines 458-459: pre_run_cleanup exception handling
  - Lines 483-485: HTTPError abort path
  - Lines 509-512: inline tool_call parsing path with log message
  - Line 555: validation stdout log line
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from core import evaluator
from core.judge import JudgeScore


def _config(**overrides):
    base = {
        "backend_type":        "llama.cpp",
        "llm_url":             "http://localhost:8080",
        "selected_model":      "m.gguf",
        "context_size":        4096,
        "sys_prompt":          "sys",
        "user_prompt":         "user",
        "mcp_url":             "",
        "mcp_tools":           {},
        "validation_command":  "",
        "fail_patterns":       [],
        "mcp_running":         False,
        "cancel_requested_ref":[False],
        "execution_mode":      "local",
    }
    base.update(overrides)
    return base


def _text_resp(content="done"):
    return {
        "message": {"role": "assistant", "content": content},
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }


def _tool_resp(tool="file_creator", args='{"path":"/tmp/x","content":"1"}'):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": tool, "arguments": args}}],
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


class TestMalformedToolArguments:
    """Line 303-304: malformed JSON in tool arguments falls back to {}."""

    @patch("core.evaluator.stream_llama_cpp")
    def test_malformed_json_args_fallback(self, mock_stream):
        resp = {
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "file_creator",
                                             "arguments": "NOT JSON"}}],
            },
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        # Round 1: malformed args, Round 2: final answer
        mock_stream.side_effect = [resp, _text_resp("done")]

        env = MagicMock()
        env.write_file.return_value = {"error": "missing path"}
        logs = []
        tel = evaluator.run_evaluation(env, _config(), lambda m: logs.append(m))
        # Should not raise; tool was called with empty args
        assert tel["llm_rounds"] >= 1


class TestToolSchemaAutoLoad:
    """Lines 444-449: when mcp_tools is empty but mcp_url is set, auto-load schemas."""

    @patch("core.evaluator._load_tool_schemas")
    @patch("core.evaluator.stream_llama_cpp")
    def test_auto_load_fallback_when_mcp_tools_empty(self, mock_stream, mock_load):
        # First call (with enabled_tools) → empty; second call (auto-load) → schemas
        schema = [{"type": "function", "function": {"name": "file_creator", "description": "", "parameters": {}}}]
        mock_load.side_effect = [[], schema]
        mock_stream.return_value = _text_resp("done")

        logs = []
        tel = evaluator.run_evaluation(
            MagicMock(),
            _config(mcp_url="/path/to/mcp/index.js", mcp_tools={}),
            lambda m: logs.append(m),
        )
        assert any("auto-loaded" in l for l in logs)

    @patch("core.evaluator._load_tool_schemas")
    @patch("core.evaluator.stream_llama_cpp")
    def test_no_auto_load_when_no_mcp_url(self, mock_stream, mock_load):
        mock_load.return_value = []
        mock_stream.return_value = _text_resp("done")

        logs = []
        evaluator.run_evaluation(MagicMock(), _config(mcp_url=""), lambda m: logs.append(m))
        # Should only be called once (no auto-load without URL)
        assert mock_load.call_count == 1


class TestPreRunCleanupException:
    """Lines 458-459: exception in delete_file during cleanup is logged."""

    @patch("core.evaluator.stream_llama_cpp")
    def test_cleanup_exception_logged_as_warn(self, mock_stream):
        mock_stream.return_value = _text_resp("done")

        env = MagicMock()
        env.delete_file.side_effect = Exception("permission denied")

        logs = []
        tel = evaluator.run_evaluation(
            env,
            _config(pre_run_cleanup=["/tmp/test_file"]),
            lambda m: logs.append(m),
        )
        assert any("[WARN]" in l and "Cleanup" in l for l in logs)


class TestHttpErrorAbort:
    """Lines 483-485: HTTPError causes run abort."""

    @patch("core.evaluator.stream_llama_cpp")
    def test_http_error_aborts(self, mock_stream):
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        error = requests.exceptions.HTTPError(response=mock_response)
        mock_stream.side_effect = error

        logs = []
        tel = evaluator.run_evaluation(MagicMock(), _config(), lambda m: logs.append(m))
        assert tel["run_aborted"] is True
        assert any("HTTP" in l for l in logs)


class TestInlineToolCallParsedLog:
    """Lines 509-512: inline tool_call parsing logs the parsed tool names."""

    @patch("core.evaluator.stream_llama_cpp")
    def test_inline_tool_call_logs_parsed_names(self, mock_stream):
        inline_content = '<tool_call>{"name": "file_creator", "arguments": {"path": "/tmp/x", "content": "hi"}}</tool_call>'
        responses = [
            {
                "message": {"role": "assistant", "content": inline_content},
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
            _text_resp("done"),
        ]
        mock_stream.side_effect = responses

        env = MagicMock()
        env.write_file.return_value = {"status": "success", "bytes_written": 2}

        logs = []
        tel = evaluator.run_evaluation(env, _config(), lambda m: logs.append(m))
        assert any("Parsed" in l and "inline" in l for l in logs)


class TestValidationStdoutLog:
    """Line 555: validation stdout is logged when non-empty."""

    @patch("core.evaluator.stream_llama_cpp")
    @patch("core.evaluator._run_validation")
    def test_validation_stdout_logged(self, mock_val, mock_stream):
        mock_stream.return_value = _text_resp("done")
        mock_val.return_value = {
            "stdout": "test file exists\n",
            "stderr": "",
            "exit_code": 0,
            "passed": True,
        }

        logs = []
        evaluator.run_evaluation(
            MagicMock(),
            _config(validation_command="test -f /tmp/x"),
            lambda m: logs.append(m),
        )
        assert any("[VALIDATE OUTPUT]" in l for l in logs)
        assert any("test file exists" in l for l in logs)


class TestAiJudgeIntegration:
    @patch("core.judge.FrontierJudge")
    @patch("core.evaluator.stream_llama_cpp")
    def test_enabled_judge_populates_telemetry(self, mock_stream, mock_judge_cls):
        mock_stream.return_value = _text_resp("final answer")
        judge = mock_judge_cls.return_value
        judge.provider = "anthropic"
        judge.model = "claude-test"
        judge.score_response.return_value = JudgeScore(
            correctness=88,
            coherence=77,
            goal_alignment=91,
            safety=95,
            efficiency=82,
            justifications={"correctness": "good"},
            aggregate_score=86.6,
            raw_response="{}",
        )

        tel = evaluator.run_evaluation(
            MagicMock(),
            _config(
                judge_enabled=True,
                judge_provider="anthropic",
                judge_model="claude-test",
                judge_api_key="test-key",
                judge_temperature=0.0,
                judge_mode="Score all responses",
            ),
            lambda _: None,
        )

        assert tel["judge_scores"]["correctness"]["score"] == 88
        assert tel["judge_scores"]["correctness"]["justification"] == "good"
        assert tel["judge_aggregate_score"] == 86.6
        judge.score_response.assert_called_once()
