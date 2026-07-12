from core.mcp_catalog import (
    bundled_tool_names,
    enabled_builtin_tool_names,
    load_mcp_tool_config,
    merge_mcp_tool_selections,
)
import pytest


def test_default_manifest_exposes_each_bundled_tool_disabled():
    tools = load_mcp_tool_config()

    assert [tool["tool_name"] for tool in tools] == [
        "file_creator", "read_file", "write_file", "terminal_execute", "run_nmap_scan",
    ]
    assert all(tool["enabled"] is False for tool in tools)


def test_selected_tools_limit_advertised_tools():
    tools = load_mcp_tool_config()
    tools[1]["enabled"] = True

    selected = enabled_builtin_tool_names(tools)

    assert selected == ["read_file"]
    assert bundled_tool_names(selected) == {"read_file"}


def test_legacy_server_list_keeps_all_bundled_tools_available():
    assert enabled_builtin_tool_names([{"name": "legacy", "enabled": True}]) is None
    assert bundled_tool_names(None) is None


def test_merge_preserves_selection_by_tool_name():
    merged = merge_mcp_tool_selections(
        load_mcp_tool_config(), [{"tool_name": "terminal_execute", "enabled": True}],
    )
    assert next(tool for tool in merged if tool["tool_name"] == "terminal_execute")["enabled"] is True


def test_override_manifest_rejects_unknown_or_misgrouped_tools(tmp_path):
    manifest = tmp_path / "mcp.json"
    manifest.write_text(
        '{"mcpServers": {"filesystem": {"transport": "bundled", "tools": ["terminal_execute"]}}}'
    )

    with pytest.raises(ValueError, match="belongs to"):
        load_mcp_tool_config(str(manifest))
