"""Collect and normalize performance data emitted by llama.cpp backends."""
from __future__ import annotations

import re
import shlex
from typing import Any

import requests


# Names exported by llama-server's opt-in Prometheus endpoint.  Keep the
# llama.cpp spelling at the boundary and use JSON-friendly names internally.
_PROMETHEUS_FIELDS = {
    "prompt_tokens": "llamacpp:prompt_tokens_total",
    "prompt_seconds": "llamacpp:prompt_seconds_total",
    "prompt_tokens_per_second": "llamacpp:prompt_tokens_seconds",
    "completion_tokens": "llamacpp:tokens_predicted_total",
    "completion_seconds": "llamacpp:tokens_predicted_seconds_total",
    "completion_tokens_per_second": "llamacpp:predicted_tokens_seconds",
    "requests_processing": "llamacpp:requests_processing",
    "requests_deferred": "llamacpp:requests_deferred",
    "context_high_watermark": "llamacpp:n_tokens_max",
    "decode_calls": "llamacpp:n_decode_total",
    "busy_slots_per_decode": "llamacpp:n_busy_slots_per_decode",
}
_COUNTER_FIELDS = {
    "prompt_tokens", "prompt_seconds", "completion_tokens", "completion_seconds", "decode_calls",
}


def strip_user_metrics_flag(flags: str) -> str:
    """Remove a redundant user-supplied ``--metrics`` server flag.

    ModelScope always owns this flag for managed servers. Re-serializing the
    remaining shell words also preserves quoted custom-flag values safely for
    the SSH launch path.
    """
    if not flags.strip():
        return ""
    try:
        tokens = shlex.split(flags)
        if "--metrics" not in tokens:
            return flags.strip()
        return " ".join(shlex.quote(token) for token in tokens if token != "--metrics")
    except ValueError:
        # Let the existing launcher report malformed flags; still handle a
        # simple redundant occurrence without trying to reinterpret the rest.
        return re.sub(r"(?<!\S)--metrics(?!\S)", "", flags).strip()


def parse_prometheus_metrics(text: str) -> dict[str, float]:
    """Extract the llama.cpp samples we display from Prometheus text format."""
    wanted = {name: field for field, name in _PROMETHEUS_FIELDS.items()}
    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^([^\s{]+)(?:\{[^}]*\})?\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)$", line)
        if not match:
            continue
        field = wanted.get(match.group(1))
        if field:
            values[field] = float(match.group(2))
    return values


def fetch_llama_server_metrics(base_url: str, *, verify_ssl: bool = True) -> dict[str, Any]:
    """Return one `/metrics` snapshot without allowing observability to fail a run."""
    url = base_url.rstrip("/") + "/metrics"
    try:
        response = requests.get(url, timeout=3, verify=verify_ssl)
        if not response.ok:
            return {"available": False, "error": f"HTTP {response.status_code}"}
        values = parse_prometheus_metrics(response.text)
        if not values:
            return {"available": False, "error": "No llama.cpp metrics in response"}
        return {"available": True, **values}
    except requests.RequestException as exc:
        return {"available": False, "error": str(exc)}


def llama_server_metrics_delta(
    before: dict[str, Any] | None, after: dict[str, Any] | None,
) -> dict[str, Any]:
    """Convert two server snapshots into run-scoped counters and final gauges."""
    if not before or not before.get("available"):
        return {
            "available": False,
            "error": (before or {}).get("error", "Metrics were unavailable before the run"),
        }
    if not after or not after.get("available"):
        return {
            "available": False,
            "error": (after or {}).get("error", "Metrics were unavailable after the run"),
        }

    result: dict[str, Any] = {"available": True}
    for field in _COUNTER_FIELDS:
        if field in before and field in after:
            # A restart/reset must not turn a dashboard counter negative.
            result[field] = max(0.0, after[field] - before[field])
    for field in set(_PROMETHEUS_FIELDS) - _COUNTER_FIELDS:
        if field in after:
            result[field] = after[field]
    return result


def parse_llama_cli_performance(stderr: str) -> dict[str, Any]:
    """Parse llama-cli's stderr performance summary when it is available.

    llama-cli writes this summary itself, so it is useful for one-shot CLI
    runs even though CLI has no HTTP `/metrics` endpoint.
    """
    result: dict[str, Any] = {"available": False}
    patterns = {
        "prompt": r"prompt eval time\s*=\s*([\d.]+)\s*(ms|s|sec|seconds)?\s*/\s*(\d+)\s+tokens",
        "completion": r"(?<!prompt )eval time\s*=\s*([\d.]+)\s*(ms|s|sec|seconds)?\s*/\s*(\d+)\s+(?:runs|tokens)",
    }
    for kind, pattern in patterns.items():
        match = re.search(pattern, stderr, flags=re.IGNORECASE)
        if not match:
            continue
        duration = float(match.group(1))
        if (match.group(2) or "").lower() == "ms":
            duration /= 1000
        result[f"{kind}_tokens"] = int(match.group(3))
        result[f"{kind}_seconds"] = duration
        result[f"{kind}_tokens_per_second"] = round(int(match.group(3)) / duration, 3) if duration else 0.0
        result["available"] = True
    return result


def accumulate_llama_cli_performance(total: dict[str, Any], sample: dict[str, Any]) -> None:
    """Add a CLI performance sample to a run's aggregate in-place."""
    if not sample.get("available"):
        return
    total["available"] = True
    total["samples"] = total.get("samples", 0) + 1
    for prefix in ("prompt", "completion"):
        for suffix in ("tokens", "seconds"):
            key = f"{prefix}_{suffix}"
            total[key] = total.get(key, 0) + sample.get(key, 0)
        seconds = total.get(f"{prefix}_seconds", 0)
        tokens = total.get(f"{prefix}_tokens", 0)
        total[f"{prefix}_tokens_per_second"] = round(tokens / seconds, 3) if seconds else 0.0
