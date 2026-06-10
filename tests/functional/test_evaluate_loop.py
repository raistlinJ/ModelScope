"""
Functional tests for evaluator.run_evaluation — loop control, telemetry
accounting, cancellation, abort paths, and backend dispatch.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from core import evaluator


def _logs():
    """Returns a (list, on_log) pair so we can assert on emitted log lines."""
    lines = []
    return lines, lambda m: lines.append(m)


def _tool_resp(tool_name="file_creator", args='{"path":"/tmp/x","content":"1"}'):
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "type": "function",
                            "function": {"name": tool_name, "arguments": args}}],
        },
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _text_resp(content="Task complete."):
    return {
        "message": {"role": "assistant", "content": content},
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


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
    }
    base.update(overrides)
    return base


# ── 8-round hard limit ─────────────────────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
@patch("core.evaluator._run_validation")
def test_max_8_rounds(mock_val, mock_stream):
    mock_stream.return_value = _tool_resp()
    mock_val.return_value = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}

    env = MagicMock()
    env.delete_file.return_value = True
    env.write_file.return_value = {"status": "success", "bytes_written": 1}

    _, on_log = _logs()
    tel = evaluator.run_evaluation(env, _config(), on_log)

    assert tel["llm_rounds"] == 8
    assert len(tel["tool_calls"]) == 8


# ── Early termination when LLM gives final answer ────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_early_termination_on_final_answer(mock_stream):
    mock_stream.return_value = _text_resp("Done!")

    _, on_log = _logs()
    tel = evaluator.run_evaluation(MagicMock(), _config(), on_log)

    assert tel["llm_rounds"] == 1
    assert len(tel["tool_calls"]) == 0
    assert tel["llm_response"] == "Done!"


# ── Cancel before first round ─────────────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_cancel_before_first_round(mock_stream):
    cancel_ref = [True]
    _, on_log = _logs()
    tel = evaluator.run_evaluation(MagicMock(), _config(cancel_requested_ref=cancel_ref), on_log)

    mock_stream.assert_not_called()
    assert tel["run_aborted"] is True
    assert tel["llm_rounds"] == 0


# ── Cancel mid-loop after first round ─────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
@patch("core.evaluator._run_validation")
def test_cancel_after_first_round(mock_val, mock_stream):
    cancel_ref = [False]
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            cancel_ref[0] = True  # flip cancel before round 2 processes
        return _tool_resp()

    mock_stream.side_effect = side_effect

    env = MagicMock()
    env.write_file.return_value = {"status": "success", "bytes_written": 1}

    _, on_log = _logs()
    tel = evaluator.run_evaluation(env, _config(cancel_requested_ref=cancel_ref), on_log)

    assert tel["run_aborted"] is True
    assert tel["llm_rounds"] >= 1


# ── Token accounting across rounds ────────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_token_accumulation(mock_stream):
    responses = [
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "file_creator", "arguments": '{"path":"/tmp/x","content":"a"}'}}
        ]}, "usage": {"prompt_tokens": 20, "completion_tokens": 10}},
        _text_resp("Done!"),
    ]
    mock_stream.side_effect = responses

    env = MagicMock()
    env.write_file.return_value = {"status": "success", "bytes_written": 1}

    _, on_log = _logs()
    tel = evaluator.run_evaluation(env, _config(), on_log)

    # Round 1: 20+10=30, Round 2: 10+5=15 → total 45
    assert tel["prompt_tokens"] == 30
    assert tel["completion_tokens"] == 15
    assert tel["total_tokens"] == 45


# ── tokens_per_second computed when > 0 ───────────────────────────────────────

@patch("core.evaluator.time")
@patch("core.evaluator._stream_llama_cpp")
def test_tokens_per_second_computed(mock_stream, mock_time):
    # Control time so latency = 5.0 s, completion = 50 tokens → 10.0 tok/s
    mock_time.time.side_effect = [0.0, 5.0]

    mock_stream.return_value = {
        "message": {"role": "assistant", "content": "x"},
        "usage": {"prompt_tokens": 10, "completion_tokens": 50},
    }

    _, on_log = _logs()
    tel = evaluator.run_evaluation(MagicMock(), _config(), on_log)

    assert tel["total_latency"] == 5.0
    assert tel["tokens_per_second"] == 10.0


# ── Abort with no activity → validation skipped ───────────────────────────────

@patch("core.evaluator._stream_llama_cpp",
       side_effect=Exception("Network unreachable"))
def test_abort_no_activity_skips_validation(mock_stream):
    _, on_log = _logs()
    tel = evaluator.run_evaluation(
        MagicMock(),
        _config(validation_command="cat /tmp/test"),
        on_log,
    )

    assert tel["run_aborted"] is True
    assert tel["llm_rounds"] == 0
    assert tel["validation_passed"] is None


# ── Abort with prior activity → validation still runs ─────────────────────────

@patch("core.evaluator._stream_llama_cpp")
@patch("core.evaluator._run_validation")
def test_abort_with_activity_runs_validation(mock_val, mock_stream):
    responses = [
        _tool_resp(),
        MagicMock(side_effect=Exception("connection dropped")),
    ]

    call_count = [0]

    def side_effect(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return _tool_resp()
        raise Exception("connection dropped")

    mock_stream.side_effect = side_effect
    mock_val.return_value = {"stdout": "", "stderr": "", "exit_code": 0, "passed": True}

    env = MagicMock()
    env.write_file.return_value = {"status": "success", "bytes_written": 1}

    _, on_log = _logs()
    tel = evaluator.run_evaluation(
        env,
        _config(validation_command="cat /tmp/test"),
        on_log,
    )

    assert tel["run_aborted"] is True
    mock_val.assert_called_once()
    assert tel["validation_passed"] is True


# ── Ollama backend dispatches to _stream_ollama ───────────────────────────────

@patch("core.evaluator._stream_ollama")
def test_ollama_backend_dispatch(mock_ollama):
    mock_ollama.return_value = _text_resp("ollama answer")

    _, on_log = _logs()
    tel = evaluator.run_evaluation(
        MagicMock(),
        _config(backend_type="ollama", llm_url="http://localhost:11434"),
        on_log,
    )

    mock_ollama.assert_called_once()
    assert tel["llm_response"] == "ollama answer"


# ── Connection error marks run aborted ────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_connection_error_aborts(mock_stream):
    import requests
    mock_stream.side_effect = requests.exceptions.ConnectionError("refused")

    logs, on_log = _logs()
    tel = evaluator.run_evaluation(MagicMock(), _config(), on_log)

    assert tel["run_aborted"] is True
    assert any("Cannot connect" in l for l in logs)


# ── Inefficiency detection ────────────────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_repeated_tool_calls_logged_as_inefficiency(mock_stream):
    same_args = '{"path":"/tmp/x","content":"1"}'
    responses = [
        _tool_resp("file_creator", same_args),
        _tool_resp("file_creator", same_args),
        _text_resp("Done"),
    ]
    mock_stream.side_effect = responses

    env = MagicMock()
    env.write_file.return_value = {"status": "success", "bytes_written": 1}

    _, on_log = _logs()
    tel = evaluator.run_evaluation(env, _config(), on_log)

    assert len(tel["inefficiencies"]) >= 1
    assert "file_creator" in tel["inefficiencies"][0]


# ── Pre-run cleanup ────────────────────────────────────────────────────────────

@patch("core.evaluator._stream_llama_cpp")
def test_pre_run_cleanup_called(mock_stream):
    mock_stream.return_value = _text_resp("done")
    env = MagicMock()
    env.delete_file.return_value = True

    _, on_log = _logs()
    evaluator.run_evaluation(
        env,
        _config(pre_run_cleanup=["/tmp/test"]),
        on_log,
    )

    env.delete_file.assert_called_once_with("/tmp/test")
