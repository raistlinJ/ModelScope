"""Validation and selection helpers for ModelScope's bundled MCP manifest."""
from __future__ import annotations

import json
import subprocess
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from config.defaults import MCP_CONFIG_PATH, MCP_SCRIPT_PATH


def load_mcp_tool_config(path: str | None = None) -> list[dict]:
    """Validate a bundled or Claude Desktop-style manifest and list tools."""
    source = Path(path or MCP_CONFIG_PATH).expanduser()
    try:
        stamp = source.stat().st_mtime_ns
    except OSError as exc:
        raise ValueError(f"Could not read MCP config {source}: {exc}") from exc
    return _discover_tools(str(source), stamp)


@lru_cache(maxsize=32)
def _discover_tools(source: str, _stamp: int) -> list[dict]:
    script = Path(MCP_SCRIPT_PATH).with_name("discover_tools.mjs")
    try:
        result = subprocess.run(
            ["node", str(script), source], cwd=script.parent,
            capture_output=True, text=True, timeout=45, check=False,
        )
    except FileNotFoundError as exc:
        raise ValueError("Node.js is required to validate MCP tools") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Timed out while discovering MCP server tools") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise ValueError(f"MCP config validation failed: {detail[-1000:]}")
    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("MCP tool discovery returned invalid JSON") from exc
    if not isinstance(records, list) or not records:
        raise ValueError("MCP config did not expose any tools")
    seen: set[str] = set()
    normalized = []
    for record in records:
        if not isinstance(record, dict) or not all(isinstance(record.get(key), str) and record[key] for key in ("server", "tool_name", "source_name")):
            raise ValueError("MCP discovery returned an invalid tool record")
        if record["tool_name"] in seen:
            raise ValueError(f'MCP config exposes duplicate tool "{record["tool_name"]}"')
        seen.add(record["tool_name"])
        normalized.append({
            **record,
            "name": f"{record.get('label', record['server'])} · {record['source_name']}",
            "enabled": False,
        })
    return normalized


def merge_mcp_tool_selections(declared_tools: list[dict], existing: list[dict] | None) -> list[dict]:
    """Preserve checkbox selection while refreshing a manifest's tool list."""
    enabled = {
        tool.get("tool_name"): bool(tool.get("enabled"))
        for tool in (existing or [])
        if isinstance(tool, dict) and tool.get("tool_name")
    }
    merged = deepcopy(declared_tools)
    for tool in merged:
        tool["enabled"] = enabled.get(tool["tool_name"], tool["enabled"])
    return merged


def enabled_builtin_tool_names(tools: list[dict] | None) -> list[str] | None:
    """Return selected tool names; ``None`` preserves legacy unrestricted runs."""
    if not any(isinstance(tool, dict) and tool.get("tool_name") for tool in (tools or [])):
        return None
    return sorted({
        tool["tool_name"]
        for tool in tools or []
        if isinstance(tool, dict) and tool.get("tool_name") and tool.get("enabled")
    })


def bundled_tool_names(selected_tools: list[str] | None) -> set[str] | None:
    """Return selected names as a set, or ``None`` when all are allowed."""
    return set(selected_tools) if selected_tools is not None else None
