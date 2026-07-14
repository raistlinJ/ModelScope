"""Run-scoped severity bands for Llama dashboard metrics.

Thresholds are deliberately observational: they label a completed run in the
dashboard and do not alter validation or process exit status.
"""
from __future__ import annotations

import math
from numbers import Real
from typing import Any, Mapping


def _plugin_type_id(bot: str) -> str:
    """Accept both UI prefixes (``llama_server``) and plugin type ids."""
    return bot if bot.endswith("_bot") else f"{bot}_bot"


def metrics_for_bot(bot: str) -> tuple[tuple[str, Mapping[str, str]], ...]:
    """Return a bot plugin's own configurable dashboard metric catalog."""
    from core.bot_types import get_bot_plugin

    plugin = get_bot_plugin(_plugin_type_id(bot))
    if plugin is None:
        return ()
    return tuple(plugin.metric_specs.items())


def all_metric_specs() -> dict[str, Mapping[str, str]]:
    """Return the union of catalogs registered by installed bot plugins."""
    from core.bot_types import iter_bot_plugins

    specs: dict[str, Mapping[str, str]] = {}
    for plugin in iter_bot_plugins():
        specs.update(plugin.metric_specs)
    return specs


def metric_spec(metric: str) -> Mapping[str, str] | None:
    """Look up a metric across registered plugin catalogs."""
    return all_metric_specs().get(metric)


# Order matters: the first matching configured band wins.
THRESHOLD_LEVELS: tuple[tuple[str, str, str], ...] = (
    ("hard_fail", "Hard Fail", ">="),
    ("soft_fail", "Soft Fail", ">"),
    ("soft_pass", "Soft Pass", ">"),
    ("hard_pass", "Hard Pass", ">"),
)


def _number(value: Any) -> float | None:
    """Return a finite numeric value, excluding booleans and malformed data."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Real):
        number = float(value)
        return number if math.isfinite(number) else None
    try:
        number = float(str(value).strip())
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def configured_thresholds(raw: Any) -> dict[str, dict[str, float]]:
    """Normalize persisted threshold data and omit blank/invalid values."""
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, float]] = {}
    valid_levels = {key for key, _, _ in THRESHOLD_LEVELS}
    for metric, values in raw.items():
        if metric_spec(metric) is None or not isinstance(values, dict):
            continue
        cleaned = {
            level: value
            for level, raw_value in values.items()
            if level in valid_levels and (value := _number(raw_value)) is not None
        }
        if cleaned:
            normalized[metric] = cleaned
    return normalized


def _add(observed: dict[str, dict[str, Any]], key: str, value: Any, source: str) -> None:
    number = _number(value)
    spec = metric_spec(key)
    if number is not None and spec is not None:
        observed[key] = {"value": number, "source": source, **spec}


def observed_dashboard_metrics(telemetry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Find all numeric values rendered by Llama analytical dashboards."""
    observed: dict[str, dict[str, Any]] = {}
    _add(observed, "total_latency", telemetry.get("total_latency"), "run telemetry")
    if isinstance(telemetry.get("prompt_responses"), list):
        _add(observed, "prompts_run", len(telemetry["prompt_responses"]), "run telemetry")
    if isinstance(telemetry.get("tool_calls"), list):
        _add(
            observed, "commands_run",
            sum(1 for call in telemetry["tool_calls"] if call.get("tool") == "bash"),
            "run telemetry",
        )

    # Standard evaluator telemetry is authoritative when it has token data.
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        if key in telemetry:
            _add(observed, key, telemetry.get(key), "run telemetry")

    server = telemetry.get("llama_server_metrics", {})
    if isinstance(server, dict) and server.get("available"):
        for key in (
            "prompt_tokens", "completion_tokens", "prompt_tokens_per_second",
            "completion_tokens_per_second", "prompt_seconds", "completion_seconds",
            "requests_processing", "requests_deferred", "context_high_watermark",
            "decode_calls", "busy_slots_per_decode",
        ):
            if key not in observed:
                _add(observed, key, server.get(key), "llama-server /metrics")

    if "total_tokens" not in observed:
        prompt = observed.get("prompt_tokens", {}).get("value")
        completion = observed.get("completion_tokens", {}).get("value")
        if prompt is not None and completion is not None:
            _add(observed, "total_tokens", prompt + completion, observed["prompt_tokens"]["source"])
    return observed


def format_metric_value(value: float | None, unit: str) -> str:
    """Format numerical metric values consistently in cards, tables, and tooltips."""
    if value is None:
        return "—"
    precision = 2 if unit in {"s", "tok/s", "slots"} else 0
    formatted = f"{value:,.{precision}f}"
    return f"{formatted} {unit}" if unit else formatted


def assess_metric_thresholds(telemetry: dict[str, Any], raw_thresholds: Any) -> list[dict[str, Any]]:
    """Return configured dashboard metrics with their observed severity band."""
    thresholds = configured_thresholds(raw_thresholds)
    measurements = observed_dashboard_metrics(telemetry)
    results: list[dict[str, Any]] = []
    for metric, metric_thresholds in thresholds.items():
        measurement = measurements.get(metric)
        spec = metric_spec(metric)
        if spec is None:
            continue
        result: dict[str, Any] = {
            "metric": metric,
            "label": spec["label"],
            "unit": spec["unit"],
            "thresholds": metric_thresholds,
            "value": measurement.get("value") if measurement else None,
            "source": measurement.get("source") if measurement else None,
            "level": "not_available" if measurement is None else "unclassified",
            "threshold": None,
        }
        if measurement is not None:
            for level, _, operator in THRESHOLD_LEVELS:
                threshold = metric_thresholds.get(level)
                if threshold is None:
                    continue
                if (operator == ">=" and measurement["value"] >= threshold) or (
                    operator == ">" and measurement["value"] > threshold
                ):
                    result["level"] = level
                    result["threshold"] = threshold
                    break
        results.append(result)
    return results


# Backward-compatible import for projects/plugins written while this module was
# token-only. New code should use assess_metric_thresholds.
assess_token_thresholds = assess_metric_thresholds
