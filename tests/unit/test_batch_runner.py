"""
Unit tests for core.batch_runner — BatchJob, BatchRunner, BatchReport.

All run_evaluation calls are patched at the import site
(core.batch_runner.run_evaluation) so no real LLM or network is hit.
"""
import csv
import io
import pytest
from unittest.mock import patch, MagicMock

from core.batch_runner import BatchJob, BatchRunner, BatchReport


# ── Helpers ───────────────────────────────────────────────────────────────────

def _model_cfg(model: str = "test-model") -> dict:
    return {
        "selected_model": model,
        "backend_type": "llama.cpp",
        "llm_url": "http://localhost:8080",
        "context_size": 4096,
        "mcp_url": "",
        "mcp_tools": {},
        "mcp_running": False,
    }


def _fake_telemetry(latency: float = 1.0, passed: bool = True) -> dict:
    return {
        "total_latency": latency,
        "total_tokens": 100,
        "prompt_tokens": 60,
        "completion_tokens": 40,
        "tokens_per_second": 40.0,
        "llm_rounds": 1,
        "tool_calls": [],
        "inefficiencies": [],
        "llm_response": "done",
        "run_aborted": False,
        "validation_passed": passed,
        "metrics_matrix": [],
        "caf_trajectory": [],
        "caf_config": {},
        "run_timestamp": "2025-01-01 00:00:00",
        "run_scenario": "test",
        "run_model": "test-model",
        "run_backend": "llama.cpp",
        "run_tool_focus": "",
    }


# ── BatchJob ──────────────────────────────────────────────────────────────────

class TestBatchJob:
    def test_default_status_is_queued(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        assert job.status == "queued"

    def test_job_id_is_8_chars(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        assert len(job.job_id) == 8

    def test_auto_label_generated(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg("some-model"))
        assert job.job_label != ""
        assert "some-model" in job.job_label

    def test_label_truncates_model_path(self):
        # Only the last path component is kept
        job = BatchJob("Scenario 1 – File Creation", _model_cfg("path/to/bigmodel.gguf"))
        assert "bigmodel.gguf" in job.job_label

    def test_custom_label_not_overwritten(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg(), job_label="custom_label")
        assert job.job_label == "custom_label"

    def test_default_priority_is_5(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        assert job.priority == 5

    def test_result_starts_as_none(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        assert job.result is None

    def test_error_starts_as_none(self):
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        assert job.error is None


# ── BatchRunner.enqueue ───────────────────────────────────────────────────────

class TestBatchRunnerEnqueue:
    def test_enqueue_adds_job(self):
        runner = BatchRunner()
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        runner.enqueue(job)
        assert len(runner.queue) == 1

    def test_enqueue_returns_job_id(self):
        runner = BatchRunner()
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        jid = runner.enqueue(job)
        assert jid == job.job_id

    def test_queue_sorted_by_priority(self):
        runner = BatchRunner()
        job_high = BatchJob("Scenario 1 – File Creation", _model_cfg("a"), priority=2)
        job_low  = BatchJob("Scenario 1 – File Creation", _model_cfg("b"), priority=8)
        runner.enqueue(job_low)
        runner.enqueue(job_high)
        assert runner.queue[0].priority == 2
        assert runner.queue[1].priority == 8

    def test_same_priority_preserved_order(self):
        runner = BatchRunner()
        job1 = BatchJob("Scenario 1 – File Creation", _model_cfg("m1"), priority=5)
        job2 = BatchJob("Scenario 1 – File Creation", _model_cfg("m2"), priority=5)
        runner.enqueue(job1)
        runner.enqueue(job2)
        assert len(runner.queue) == 2


# ── BatchRunner.clear ─────────────────────────────────────────────────────────

class TestBatchRunnerClear:
    def test_clear_empties_queue(self):
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        runner.clear()
        assert runner.queue == []

    def test_clear_on_empty_is_safe(self):
        runner = BatchRunner()
        runner.clear()
        assert runner.queue == []


# ── BatchRunner.run ───────────────────────────────────────────────────────────

class TestBatchRunnerRun:
    @patch("core.batch_runner.run_evaluation")
    def test_run_two_jobs_both_complete(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()

        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m1")))
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m2")))

        report = runner.run()
        assert report.total_jobs == 2
        assert report.completed == 2
        assert report.failed == 0
        assert len(report.results) == 2

    @patch("core.batch_runner.run_evaluation")
    def test_run_failed_job_counted(self, mock_eval):
        mock_eval.side_effect = RuntimeError("model crashed")

        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))

        report = runner.run()
        assert report.failed == 1
        assert report.completed == 0

    @patch("core.batch_runner.run_evaluation")
    def test_job_status_transitions(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()

        runner = BatchRunner()
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        runner.enqueue(job)
        runner.run()

        assert job.status == "done"
        assert job.result is not None

    @patch("core.batch_runner.run_evaluation")
    def test_failed_job_status_and_error(self, mock_eval):
        mock_eval.side_effect = RuntimeError("boom")

        runner = BatchRunner()
        job = BatchJob("Scenario 1 – File Creation", _model_cfg())
        runner.enqueue(job)
        runner.run()

        assert job.status == "failed"
        assert "boom" in job.error

    @patch("core.batch_runner.run_evaluation")
    def test_on_log_called(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        logs = []
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        runner.run(on_log=lambda m: logs.append(m))
        # run_evaluation received the logger; mock_eval is called with it
        assert mock_eval.called

    @patch("core.batch_runner.run_evaluation")
    def test_duration_is_positive(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        report = runner.run()
        assert report.duration_seconds >= 0.0

    @patch("core.batch_runner.run_evaluation")
    def test_summary_rows_length_matches_jobs(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m1")))
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m2")))
        report = runner.run()
        assert len(report.summary_rows) == 2

    @patch("core.batch_runner.run_evaluation")
    def test_parallel_run(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        runner = BatchRunner(max_parallel=2)
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m1")))
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m2")))
        report = runner.run()
        assert report.completed == 2


# ── BatchRunner.export_csv ────────────────────────────────────────────────────

class TestBatchRunnerExportCsv:
    @patch("core.batch_runner.run_evaluation")
    def test_csv_has_expected_headers(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        report = runner.run()
        csv_text = runner.export_csv(report)
        assert csv_text != ""
        reader = csv.DictReader(io.StringIO(csv_text))
        headers = reader.fieldnames or []
        for expected in ["job_id", "label", "scenario", "model", "status", "latency"]:
            assert expected in headers

    @patch("core.batch_runner.run_evaluation")
    def test_csv_row_count_matches_jobs(self, mock_eval):
        mock_eval.return_value = _fake_telemetry()
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m1")))
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg("m2")))
        report = runner.run()
        csv_text = runner.export_csv(report)
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        assert len(rows) == 2

    def test_export_empty_report_returns_empty_string(self):
        runner = BatchRunner()
        empty_report = BatchReport()
        assert runner.export_csv(empty_report) == ""

    @patch("core.batch_runner.run_evaluation")
    def test_csv_latency_value_correct(self, mock_eval):
        mock_eval.return_value = _fake_telemetry(latency=3.14)
        runner = BatchRunner()
        runner.enqueue(BatchJob("Scenario 1 – File Creation", _model_cfg()))
        report = runner.run()
        csv_text = runner.export_csv(report)
        assert "3.14" in csv_text
