"""Freshness-aware status indicators for the active project sidebar."""
from __future__ import annotations

import json
from typing import Any

from core.metric_thresholds import assess_metric_thresholds, format_metric_value


_STATUS_CONFIG_FIELDS = (
    "validation_command",
    "validation_commands",
    "validation_sets",
    "fail_patterns",
    "metrics_matrix",
    "metric_thresholds",
)


def run_status_fingerprint(config: dict[str, Any]) -> str:
    """Stable fingerprint of settings that determine status-box validity."""
    snapshot = {key: config.get(key) for key in _STATUS_CONFIG_FIELDS}
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str)


def sidebar_status_indicators(
    telemetry: dict[str, Any] | None, current_config: dict[str, Any],
) -> list[dict[str, str]]:
    """Return sidebar boxes only when a run matches the current configuration."""
    if not isinstance(telemetry, dict) or not telemetry:
        return []
    if telemetry.get("run_status_fingerprint") != run_status_fingerprint(current_config):
        return []

    indicators: list[dict[str, str]] = []
    validation = telemetry.get("validation_passed")
    if validation is True:
        indicators.append({"key": "validation", "icon": "✓", "level": "hard_pass", "label": "Validation: pass"})
    elif validation is False:
        indicators.append({"key": "validation", "icon": "✕", "level": "hard_fail", "label": "Validation: fail"})

    metric_icons = {
        "total_latency": "L", "prompts_run": "R", "commands_run": "C",
        "prompt_tokens": "PT", "completion_tokens": "CT", "total_tokens": "TT",
        "prompt_tokens_per_second": "P/s", "completion_tokens_per_second": "C/s",
        "prompt_seconds": "Ps", "completion_seconds": "Cs", "cli_invocations": "I",
        "requests_processing": "A", "requests_deferred": "D", "context_high_watermark": "W",
        "decode_calls": "DC", "busy_slots_per_decode": "BS",
    }
    for assessment in assess_metric_thresholds(telemetry, telemetry.get("metric_thresholds", {})):
        level = assessment["level"]
        if level not in {"hard_fail", "soft_fail", "soft_pass", "hard_pass"}:
            continue
        indicators.append({
            "key": assessment["metric"],
            "icon": metric_icons[assessment["metric"]],
            "level": level,
            "label": (
                f"{assessment['label']}: {level.replace('_', ' ')} "
                f"({format_metric_value(assessment['value'], assessment['unit'])})"
            ),
        })
    return indicators
