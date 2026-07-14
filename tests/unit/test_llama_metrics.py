from core.llama_metrics import (
    accumulate_llama_cli_performance,
    llama_server_metrics_delta,
    parse_llama_cli_performance,
    parse_prometheus_metrics,
    strip_user_metrics_flag,
)


def test_parse_prometheus_metrics_extracts_llama_server_samples():
    values = parse_prometheus_metrics("""
        # HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
        llamacpp:prompt_tokens_total 42
        llamacpp:prompt_seconds_total 1.5
        llamacpp:prompt_tokens_seconds 28
        llamacpp:tokens_predicted_total 17
        llamacpp:tokens_predicted_seconds_total 2
        llamacpp:predicted_tokens_seconds 8.5
        llamacpp:requests_processing 0
        llamacpp:requests_deferred 1
        llamacpp:n_tokens_max 2048
        llamacpp:n_decode_total 91
        llamacpp:n_busy_slots_per_decode 1.75
    """)

    assert values["prompt_tokens"] == 42
    assert values["completion_tokens"] == 17
    assert values["completion_tokens_per_second"] == 8.5
    assert values["context_high_watermark"] == 2048
    assert values["decode_calls"] == 91
    assert values["busy_slots_per_decode"] == 1.75


def test_server_delta_uses_counter_difference_and_final_gauges():
    before = {"available": True, "prompt_tokens": 10, "prompt_seconds": 1, "completion_tokens": 2, "completion_seconds": 0.5, "decode_calls": 12}
    after = {"available": True, "prompt_tokens": 30, "prompt_seconds": 4, "completion_tokens": 12, "completion_seconds": 2.5, "decode_calls": 31, "completion_tokens_per_second": 5, "requests_deferred": 0, "busy_slots_per_decode": 1.5}

    result = llama_server_metrics_delta(before, after)

    assert result["available"] is True
    assert result["prompt_tokens"] == 20
    assert result["completion_tokens"] == 10
    assert result["decode_calls"] == 19
    assert result["completion_tokens_per_second"] == 5
    assert result["busy_slots_per_decode"] == 1.5


def test_parse_and_accumulate_llama_cli_performance():
    sample = parse_llama_cli_performance("""
        llama_perf_context_print: prompt eval time = 100.00 ms / 10 tokens (10.00 ms per token)
        llama_perf_context_print: eval time = 2.00 s / 20 runs (100.00 ms per token)
    """)
    total = {"available": False, "samples": 0}
    accumulate_llama_cli_performance(total, sample)

    assert total == {
        "available": True,
        "samples": 1,
        "prompt_tokens": 10,
        "prompt_seconds": 0.1,
        "prompt_tokens_per_second": 100.0,
        "completion_tokens": 20,
        "completion_seconds": 2.0,
        "completion_tokens_per_second": 10.0,
    }


def test_user_metrics_flag_is_removed_without_losing_other_custom_flags():
    assert strip_user_metrics_flag('--metrics --temp 0.2 --chat-template "chat ml"') == (
        "--temp 0.2 --chat-template 'chat ml'"
    )
