"""
MCP tool schema registry — import third-party schemas and auto-generate
starter metric matrices based on argument and return-value types.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


class SchemaRegistry:
    @staticmethod
    def generate_metrics_from_schema(tool_name: str, schema: dict) -> list[dict]:
        from config.metrics import make_metric

        metrics: list[dict] = []
        counter = [1]

        def mid() -> str:
            v = f"M-AUTO-{counter[0]:03d}"
            counter[0] += 1
            return v

        properties = schema.get("properties", {}) or {}
        required   = schema.get("required", []) or []

        for arg_name, arg_spec in properties.items():
            if not isinstance(arg_spec, dict):
                continue

            if arg_name in required:
                metrics.append(make_metric(
                    mid(),
                    f"{tool_name}: '{arg_name}' required",
                    "tool_success_rate",
                    min_rate=1.0,
                ))

            if arg_spec.get("type") == "string" and "enum" in arg_spec:
                first_enum = arg_spec["enum"][0] if arg_spec["enum"] else ""
                if first_enum:
                    metrics.append(make_metric(
                        mid(),
                        f"{tool_name}: '{arg_name}' enum valid",
                        "content_contains",
                        text=first_enum,
                    ))

            if arg_spec.get("pattern"):
                metrics.append(make_metric(
                    mid(),
                    f"{tool_name}: '{arg_name}' pattern match",
                    "content_regex",
                    pattern=arg_spec["pattern"],
                ))

        returns = schema.get("returns", {}) or {}
        if returns.get("type") == "object":
            metrics.append(make_metric(
                mid(),
                f"{tool_name}: return schema valid",
                "tool_success_rate",
                min_rate=1.0,
            ))

        if not metrics:
            metrics.append(make_metric(
                mid(),
                f"{tool_name}: execution success",
                "tool_success_rate",
                min_rate=0.9,
            ))

        return metrics

    @staticmethod
    def parse_schema_from_json(schema_json: str) -> dict:
        return json.loads(schema_json)

    @staticmethod
    def save_to_registry(tool_name: str, schema: dict, registry_dir: str) -> str:
        Path(registry_dir).mkdir(parents=True, exist_ok=True)
        path = os.path.join(registry_dir, f"{tool_name}.json")
        with open(path, "w") as fh:
            json.dump(schema, fh, indent=2)
        return path

    @staticmethod
    def load_from_registry(tool_name: str, registry_dir: str) -> dict | None:
        path = os.path.join(registry_dir, f"{tool_name}.json")
        if not os.path.exists(path):
            return None
        with open(path) as fh:
            return json.load(fh)

    @staticmethod
    def list_registry_tools(registry_dir: str) -> list[str]:
        if not os.path.isdir(registry_dir):
            return []
        return [f[:-5] for f in os.listdir(registry_dir) if f.endswith(".json")]

    @staticmethod
    def generate_preset_metrics_for_category(category: str) -> list[dict]:
        from config.metrics import MCPMetricPresets
        presets = {
            "web_search":      MCPMetricPresets.web_search,
            "code_execution":  MCPMetricPresets.code_execution,
            "database_query":  MCPMetricPresets.database_query,
            "calendar_email":  MCPMetricPresets.calendar_email,
            "file_system":     MCPMetricPresets.file_system,
        }
        fn = presets.get(category)
        return fn() if fn else []
