"""
Unit tests for core.comparison — run_comparison, build_metric_table,
winner selection, and summary dict accuracy.

run_evaluation is patched at the import site (core.comparison.run_evaluation).
"""
import pytest
from unittest.mock import patch, MagicMock

from config.metrics import make_metric
from core.comparison import ComparisonConfig, ComparisonResult, run_comparison, build_metric_table


# ── Helpers ───────────────────────────────────────────────────────────────────

def _model(label: str, model: str = None) -> dict:
    return {
        "label": label,
        "selected_model": model or label,
        "backend_type": "llama.cpp",
        "llm_url": "http://localhost:8080",
        "context_size": 4096,
        "mcp_url": "",
        "mcp_tools": {},
        "mcp_running": False,
    }


def _telemetry(passed: bool = True, latency: float = 1.0) -> dict:
    return {
        "total_latency": latency,
        "total_tokens": 100,
        "tokens_per_second": 50.0,
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
        "run_model": "model-a",
        "run_backend": "llama.cpp",
        "run_tool_focus": "",
        "prompt_tokens": 60,
        "completion_tokens": 40,
    }


_METRICS = [
    make_metric("M-1", "Task Completion", "task_completion"),
    make_metric("M-2", "Latency", "latency", max_seconds=5.0),
]


def _config(models=None, metrics=None) -> ComparisonConfig:
    return ComparisonConfig(
        scenario_key="test-scenario",
        models=models or [_model("model-a"), _model("model-b")],
        sys_prompt="sys",
        user_prompt="usr",
        validation_command="",
        fail_patterns=[],
        metrics_matrix=metrics or list(_METRICS),
    )


# ── build_metric_table ────────────────────────────────────────────────────────

class TestBuildMetricTable:
    def test_rows_per_enabled_metric(self):
        labels = ["a", "b"]
        results = [_telemetry(passed=True), _telemetry(passed=False)]
        rows = build_metric_table(labels, results, _METRICS)
        assert len(rows) == len(_METRICS)

    def test_disabled_metric_excluded(self):
        metrics = [
            make_metric("M-1", "TC", "task_completion", enabled=True),
            make_metric("M-2", "Lat", "latency", enabled=False, max_seconds=5.0),
        ]
        rows = build_metric_table(["a"], [_telemetry()], metrics)
        assert len(rows) == 1
        assert rows[0]["metric_type"] == "task_completion"

    def test_scores_dict_has_all_labels(self):
        labels = ["model-a", "model-b", "model-c"]
        results = [_telemetry(passed=True)] * 3
        rows = build_metric_table(labels, results, [make_metric("m", "TC", "task_completion")])
        assert set(rows[0]["scores"].keys()) == set(labels)

    def test_score_values_correct(self):
        labels = ["pass-model", "fail-model"]
        results = [_telemetry(passed=True), _telemetry(passed=False)]
        rows = build_metric_table(labels, results, [make_metric("m", "TC", "task_completion")])
        assert rows[0]["scores"]["pass-model"] is True
        assert rows[0]["scores"]["fail-model"] is False

    def test_row_contains_metric_metadata(self):
        rows = build_metric_table(["a"], [_telemetry()], [make_metric("M-1", "Task Completion", "task_completion")])
        assert rows[0]["metric_id"] == "M-1"
        assert rows[0]["metric_name"] == "Task Completion"
        assert rows[0]["metric_type"] == "task_completion"

    def test_empty_matrix_returns_empty_list(self):
        rows = build_metric_table(["a"], [_telemetry()], [])
        assert rows == []


# ── run_comparison ────────────────────────────────────────────────────────────

class TestRunComparison:
    @patch("core.comparison.run_evaluation")
    def test_returns_comparison_result(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config()
        result = run_comparison(cfg, MagicMock())
        assert isinstance(result, ComparisonResult)

    @patch("core.comparison.run_evaluation")
    def test_model_results_count(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config(models=[_model("a"), _model("b"), _model("c")])
        result = run_comparison(cfg, MagicMock())
        assert len(result.model_results) == 3

    @patch("core.comparison.run_evaluation")
    def test_winner_is_highest_pass_rate(self, mock_eval):
        # model-a passes both metrics; model-b passes none
        def side_effect(env, eval_config, log):
            label = eval_config["selected_model"]
            return _telemetry(passed=(label == "model-a"), latency=0.5)

        mock_eval.side_effect = side_effect
        cfg = _config(
            models=[_model("model-a"), _model("model-b")],
            metrics=[make_metric("m", "TC", "task_completion")],
        )
        result = run_comparison(cfg, MagicMock())
        assert result.winner == "model-a"

    @patch("core.comparison.run_evaluation")
    def test_summary_has_all_model_labels(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config(models=[_model("a"), _model("b")])
        result = run_comparison(cfg, MagicMock())
        assert "a" in result.summary
        assert "b" in result.summary

    @patch("core.comparison.run_evaluation")
    def test_summary_passed_failed_na_counts(self, mock_eval):
        mock_eval.return_value = _telemetry(passed=True, latency=0.5)
        metrics = [
            make_metric("M-1", "TC", "task_completion"),
            make_metric("M-2", "Lat", "latency", max_seconds=999.0),
        ]
        cfg = _config(models=[_model("a")], metrics=metrics)
        result = run_comparison(cfg, MagicMock())
        s = result.summary["a"]
        assert s["passed"] == 2
        assert s["failed"] == 0
        assert s["na"] == 0

    @patch("core.comparison.run_evaluation")
    def test_metric_table_structure(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config(models=[_model("a"), _model("b")])
        result = run_comparison(cfg, MagicMock())
        for row in result.metric_table:
            assert "metric_id" in row
            assert "metric_name" in row
            assert "scores" in row
            assert set(row["scores"].keys()) == {"a", "b"}

    @patch("core.comparison.run_evaluation")
    def test_evaluation_error_marked_aborted(self, mock_eval):
        mock_eval.side_effect = RuntimeError("LLM unreachable")
        cfg = _config(models=[_model("a")])
        result = run_comparison(cfg, MagicMock())
        assert result.model_results[0].get("run_aborted") is True

    @patch("core.comparison.run_evaluation")
    def test_comparison_id_present(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config()
        result = run_comparison(cfg, MagicMock())
        assert result.comparison_id != ""
        assert len(result.comparison_id) == 8

    @patch("core.comparison.run_evaluation")
    def test_scenario_key_preserved(self, mock_eval):
        mock_eval.return_value = _telemetry()
        cfg = _config()
        result = run_comparison(cfg, MagicMock())
        assert result.scenario_key == "test-scenario"

    @patch("core.comparison.run_evaluation")
    def test_pass_rate_calculation(self, mock_eval):
        # 1 of 2 metrics passes → pass_rate = 0.5
        def side(env, eval_config, log):
            return _telemetry(passed=True, latency=999.0)  # latency metric fails

        mock_eval.side_effect = side
        metrics = [
            make_metric("m1", "TC", "task_completion"),
            make_metric("m2", "Lat", "latency", max_seconds=1.0),
        ]
        cfg = _config(models=[_model("a")], metrics=metrics)
        result = run_comparison(cfg, MagicMock())
        assert result.summary["a"]["pass_rate"] == 0.5

    @patch("core.comparison.run_evaluation")
    def test_no_models_winner_empty(self, mock_eval):
        # No models → no evaluations, no summary, winner should be ""
        cfg = ComparisonConfig(
            scenario_key="test-scenario",
            models=[],
            sys_prompt="sys",
            user_prompt="usr",
            metrics_matrix=[],
        )
        result = run_comparison(cfg, MagicMock())
        assert result.winner == ""
        mock_eval.assert_not_called()
