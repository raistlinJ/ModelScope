from core.run_status import (
    batch_execution_indicator,
    run_status_fingerprint,
    sidebar_status_indicators,
)


def _config(**overrides):
    config = {
        "validation_sets": [{"name": "check", "steps": []}],
        "validation_commands": [],
        "fail_patterns": [],
        "metrics_matrix": [],
        "metric_thresholds": {"total_tokens": {"hard_fail": 100, "hard_pass": 25}},
    }
    config.update(overrides)
    return config


def _telemetry(config, **overrides):
    telemetry = {
        "run_status_fingerprint": run_status_fingerprint(config),
        "validation_passed": True,
        "total_tokens": 30,
        "metric_thresholds": config["metric_thresholds"],
    }
    telemetry.update(overrides)
    return telemetry


def test_sidebar_indicators_show_validation_and_configured_metric_bands():
    config = _config()
    # Fewer tokens is better, so 20 clears the hard_pass bar of 25.
    indicators = sidebar_status_indicators(_telemetry(config, total_tokens=20), config)

    assert [(item["key"], item["level"]) for item in indicators] == [
        ("validation", "hard_pass"),
        ("total_tokens", "hard_pass"),
    ]


def test_sidebar_indicators_clear_when_validation_or_metrics_change():
    config = _config()
    telemetry = _telemetry(config)

    assert sidebar_status_indicators(telemetry, _config(validation_sets=[])) == []
    assert sidebar_status_indicators(
        telemetry,
        _config(metric_thresholds={"total_tokens": {"hard_fail": 200}}),
    ) == []


def test_sidebar_indicators_hide_unclassified_and_missing_metrics():
    config = _config(metric_thresholds={"total_tokens": {"hard_pass": 100}})
    # 150 misses the only configured band (hard_pass at <= 100), so the metric
    # has no verdict to show and is left out rather than shown as neutral.
    indicators = sidebar_status_indicators(_telemetry(config, total_tokens=150), config)

    assert [item["key"] for item in indicators] == ["validation"]


# ── Batch projects report execution coverage, not pass/fail ───────────────────

def _levels(telemetry):
    return [item["level"] for item in batch_execution_indicator(telemetry)]


def test_batch_indicator_reports_not_started_without_a_run():
    assert _levels({}) == ["not_started"]
    assert _levels(None) == ["not_started"]
    assert _levels({"batch_containers": [{"state": "pending"}, {"state": "pending"}]}) == ["not_started"]


def test_batch_indicator_distinguishes_partial_from_complete():
    partial = batch_execution_indicator(
        {"batch_containers": [{"state": "passed"}, {"state": "pending"}]}
    )
    assert partial[0]["level"] == "partial"
    assert "1 of 2" in partial[0]["label"]

    complete = batch_execution_indicator(
        {"batch_containers": [{"state": "passed"}, {"state": "failed"}]}
    )
    assert complete[0]["level"] == "complete"


def test_batch_indicator_ignores_the_config_fingerprint():
    """How far a run got stays true even after the validation config changes."""
    telemetry = {"batch_containers": [{"state": "passed"}], "run_status_fingerprint": "stale"}

    assert _levels(telemetry) == ["complete"]
