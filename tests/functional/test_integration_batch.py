"""
Integration test: BatchRunner + LocalEnvironment + mocked LLM.

Queues a "Scenario 1 – File Creation" job with a mocked LLM that returns a
file_creator tool call, then a terminal text response. Verifies telemetry has
all expected keys and run completed without abort.
"""
import pytest
from unittest.mock import patch, MagicMock
from core.batch_runner import BatchRunner, BatchJob


def _file_creator_resp():
    return {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "c1",
                "type": "function",
                "function": {
                    "name": "file_creator",
                    "arguments": '{"path": "/tmp/integration_test_batch.txt", "content": "1\\n2\\n3"}',
                },
            }],
        },
        "usage": {"prompt_tokens": 20, "completion_tokens": 10},
    }


def _done_resp():
    return {
        "message": {"role": "assistant", "content": "File created successfully."},
        "usage": {"prompt_tokens": 15, "completion_tokens": 8},
    }


@patch("core.evaluator.stream_llama_cpp")
def test_batch_run_file_creation_scenario(mock_stream, tmp_path):
    """Full pipeline: BatchRunner → LocalEnvironment → file_creator tool → telemetry."""
    responses = [_file_creator_resp(), _done_resp()]
    mock_stream.side_effect = responses

    runner = BatchRunner(max_parallel=1)
    job = BatchJob(
        scenario_key="Scenario 1 – File Creation",
        model_config={
            "selected_model": "test-model.gguf",
            "backend_type": "llama.cpp",
            "llm_url": "http://localhost:8080",
            "context_size": 4096,
            "mcp_url": "",
            "mcp_tools": {},
            "mcp_running": False,
        },
    )
    runner.enqueue(job)
    report = runner.run(on_log=lambda _: None)

    # Job completed without failure
    assert report.total_jobs == 1
    assert report.completed == 1
    assert report.failed == 0

    # Telemetry has all required keys
    tel = job.result
    assert tel is not None
    required_keys = [
        "run_timestamp", "run_scenario", "run_model", "run_backend",
        "total_latency", "prompt_tokens", "completion_tokens", "total_tokens",
        "tokens_per_second", "llm_rounds", "tool_calls", "inefficiencies",
        "llm_response", "run_aborted", "validation_passed",
        "caf_trajectory", "caf_config",
    ]
    for key in required_keys:
        assert key in tel, f"Missing telemetry key: {key}"

    # LLM was called twice (tool + final answer)
    assert tel["llm_rounds"] == 2
    # file_creator was called once
    assert len(tel["tool_calls"]) == 1
    assert tel["tool_calls"][0]["tool"] == "file_creator"
    # Run not aborted
    assert tel["run_aborted"] is False
    # Token count is accumulated
    assert tel["prompt_tokens"] == 35
    assert tel["completion_tokens"] == 18


@patch("core.evaluator.stream_llama_cpp")
def test_batch_report_csv_with_real_telemetry(mock_stream, tmp_path):
    """CSV export includes the model and scenario info."""
    mock_stream.side_effect = [_done_resp()]

    runner = BatchRunner()
    job = BatchJob(
        scenario_key="Scenario 1 – File Creation",
        model_config={
            "selected_model": "csv-test-model",
            "backend_type": "llama.cpp",
            "llm_url": "http://localhost:8080",
            "context_size": 4096,
            "mcp_url": "",
            "mcp_tools": {},
            "mcp_running": False,
        },
    )
    runner.enqueue(job)
    report = runner.run()
    csv_text = runner.export_csv(report)

    assert "csv-test-model" in csv_text
    assert "Scenario 1" in csv_text


@patch("core.evaluator.stream_llama_cpp")
def test_multiple_jobs_both_complete(mock_stream, tmp_path):
    """Two concurrent jobs complete independently."""
    mock_stream.side_effect = [_done_resp(), _done_resp()]

    runner = BatchRunner(max_parallel=1)
    runner.enqueue(BatchJob(
        scenario_key="Scenario 1 – File Creation",
        model_config={"selected_model": "m1", "backend_type": "llama.cpp",
                      "llm_url": "http://localhost:8080", "context_size": 4096,
                      "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
    ))
    runner.enqueue(BatchJob(
        scenario_key="Scenario 1 – File Creation",
        model_config={"selected_model": "m2", "backend_type": "llama.cpp",
                      "llm_url": "http://localhost:8080", "context_size": 4096,
                      "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
    ))
    report = runner.run()
    assert report.completed == 2
    assert report.failed == 0
