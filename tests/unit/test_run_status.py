from core.run_status import run_status_fingerprint, sidebar_status_indicators


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
    indicators = sidebar_status_indicators(_telemetry(config), config)

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
    indicators = sidebar_status_indicators(_telemetry(config, total_tokens=25), config)

    assert [item["key"] for item in indicators] == ["validation"]
