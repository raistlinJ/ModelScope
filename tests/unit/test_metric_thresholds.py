from core.metric_thresholds import (
    assess_metric_thresholds,
    assess_token_thresholds,
    configured_thresholds,
    metrics_for_bot,
)
from core.bot_types import get_bot_plugin


def _thresholds():
    return {
        "total_tokens": {
            "hard_fail": 100,
            "soft_fail": 75,
            "soft_pass": 50,
            "hard_pass": 25,
        }
    }


def test_thresholds_ignore_blank_values_and_unknown_metrics():
    assert configured_thresholds({
        "total_tokens": {"hard_fail": "100", "soft_pass": "", "unexpected": 5},
        "latency": {"hard_fail": 1},
    }) == {"total_tokens": {"hard_fail": 100.0}}

    assert configured_thresholds({"total_tokens": {"hard_fail": "nan"}}) == {}


def test_threshold_bands_follow_configured_comparisons():
    for value, expected in ((100, "hard_fail"), (76, "soft_fail"), (75, "soft_pass"), (26, "hard_pass"), (25, "unclassified")):
        result = assess_token_thresholds({"total_tokens": value}, _thresholds())
        assert result[0]["level"] == expected


def test_higher_is_better_threshold_bands_reverse_the_comparisons():
    thresholds = {
        "total_tokens": {
            "direction": "higher",
            "hard_fail": 25,
            "soft_fail": 50,
            "soft_pass": 75,
            "hard_pass": 100,
        }
    }

    for value, expected in ((25, "hard_fail"), (49, "soft_fail"), (50, "soft_pass"), (99, "hard_pass"), (100, "unclassified")):
        result = assess_metric_thresholds({"total_tokens": value}, thresholds)
        assert result[0]["level"] == expected
    assert result[0]["direction"] == "higher"
    assert result[0]["operator"] is None

    hard_pass = assess_metric_thresholds({"total_tokens": 99}, thresholds)[0]
    assert hard_pass["operator"] == "<"


def test_server_token_metrics_are_sourced_from_metrics_endpoint():
    server = assess_token_thresholds(
        {"llama_server_metrics": {"available": True, "prompt_tokens": 10, "completion_tokens": 20}},
        {"total_tokens": {"hard_pass": 25}},
    )
    assert server[0]["value"] == 30
    assert server[0]["source"] == "llama-server /metrics"

def test_metrics_config_includes_every_dashboard_card_for_each_llama_bot():
    cli_metrics = {key for key, _ in metrics_for_bot("llama_cli")}
    server_metrics = {key for key, _ in metrics_for_bot("llama_server")}
    caf_metrics = {key for key, _ in metrics_for_bot("caf_cli_run_bot")}

    assert cli_metrics == {"total_latency", "prompts_run", "commands_run"}
    assert caf_metrics == cli_metrics
    assert {"requests_processing", "requests_deferred", "context_high_watermark", "decode_calls", "busy_slots_per_decode"} <= server_metrics
    assert {"prompt_tokens", "completion_tokens", "total_tokens"} <= server_metrics

    # The catalogs live with their backend plugins; threshold code only reads
    # that contract to render/configure them.
    assert get_bot_plugin("llama_cli_bot").metric_specs.keys() == cli_metrics
    assert get_bot_plugin("llama_server_bot").metric_specs.keys() == server_metrics


def test_server_only_dashboard_metrics_can_receive_threshold_bands():
    result = assess_metric_thresholds(
        {
            "llama_server_metrics": {
                "available": True,
                "decode_calls": 120,
                "busy_slots_per_decode": 2.25,
            }
        },
        {
            "decode_calls": {"hard_fail": 100},
            "busy_slots_per_decode": {"soft_pass": 2},
        },
    )
    by_metric = {item["metric"]: item for item in result}
    assert by_metric["decode_calls"]["level"] == "hard_fail"
    assert by_metric["busy_slots_per_decode"]["level"] == "soft_pass"
