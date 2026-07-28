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


def _by_metric(results):
    """Index an assessment by metric name.

    assess_metric_thresholds iterates the union of configured and observed
    metrics, which is a set — so positional indexing is not stable.
    """
    return {item["metric"]: item for item in results}


def test_threshold_bands_follow_configured_comparisons():
    # Lower is better: hard_fail 100, soft_fail 75, soft_pass 50, hard_pass 25.
    cases = (
        (100, "hard_fail"),     # >= hard_fail
        (76, "soft_fail"),      # > soft_fail
        (50, "soft_pass"),      # <= soft_pass
        (26, "soft_pass"),      # still within the soft-pass band
        (25, "hard_pass"),      # <= hard_pass wins over soft_pass
    )
    for value, expected in cases:
        result = _by_metric(assess_token_thresholds({"total_tokens": value}, _thresholds()))
        assert result["total_tokens"]["level"] == expected, value


def test_values_between_the_pass_and_fail_bands_are_unclassified():
    """Four independent thresholds leave a neutral zone in the middle.

    Note the boundary asymmetry: hard_fail matches with >= but soft_fail with
    >, so a value equal to the soft_fail threshold lands in this zone rather
    than in soft_fail.
    """
    for value in (51, 74, 75):
        result = _by_metric(assess_token_thresholds({"total_tokens": value}, _thresholds()))
        assert result["total_tokens"]["level"] == "unclassified", value


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

    cases = (
        (25, "hard_fail"),       # <= hard_fail
        (49, "soft_fail"),       # < soft_fail
        (50, "unclassified"),    # neutral zone between the bands
        (75, "soft_pass"),       # >= soft_pass
        (99, "soft_pass"),
        (100, "hard_pass"),      # >= hard_pass wins over soft_pass
    )
    for value, expected in cases:
        result = _by_metric(assess_metric_thresholds({"total_tokens": value}, thresholds))
        assert result["total_tokens"]["level"] == expected, value

    top = _by_metric(assess_metric_thresholds({"total_tokens": 100}, thresholds))["total_tokens"]
    assert top["direction"] == "higher"
    assert top["operator"] == ">="


def test_server_token_metrics_are_sourced_from_metrics_endpoint():
    server = _by_metric(assess_token_thresholds(
        {"llama_server_metrics": {"available": True, "prompt_tokens": 10, "completion_tokens": 20}},
        {"total_tokens": {"hard_pass": 25}},
    ))
    # total_tokens is not reported by /metrics; it is derived from the two parts.
    assert server["total_tokens"]["value"] == 30
    assert server["total_tokens"]["source"] == "llama-server /metrics"
    assert server["prompt_tokens"]["value"] == 10
    assert server["completion_tokens"]["value"] == 20

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
            "busy_slots_per_decode": {"soft_pass": 4},
        },
    )
    by_metric = _by_metric(result)
    assert by_metric["decode_calls"]["level"] == "hard_fail"      # 120 >= 100
    assert by_metric["busy_slots_per_decode"]["level"] == "soft_pass"  # 2.25 <= 4
    assert by_metric["decode_calls"]["source"] == "llama-server /metrics"
