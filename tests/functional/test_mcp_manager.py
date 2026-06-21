"""
Functional tests for core.mcp_manager — start/stop, tool loading,
live-server discovery, and call_mcp_tool protocol.
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from core import mcp_manager
import streamlit as st


@pytest.fixture(autouse=True)
def clean_mcp_state():
    for key in ("mcp_process", "mcp_running"):
        st.session_state.pop(key, None)
    yield
    for key in ("mcp_process", "mcp_running"):
        st.session_state.pop(key, None)


# ── start_mcp ─────────────────────────────────────────────────────────────────

@patch("subprocess.Popen")
@patch("os.path.exists")
def test_start_mcp_success(mock_exists, mock_popen):
    mock_exists.return_value = True
    mock_proc = MagicMock()
    mock_proc.pid = 5678
    mock_proc.poll.return_value = None
    mock_popen.return_value = mock_proc

    ok, msg = mcp_manager.start_mcp("server.js")

    assert ok is True
    assert "5678" in msg
    assert st.session_state["mcp_process"] == mock_proc
    assert st.session_state["mcp_running"] is True


@patch("os.path.exists", return_value=False)
def test_start_mcp_script_not_found(mock_exists):
    ok, msg = mcp_manager.start_mcp("missing.js")
    assert ok is False
    assert "not found" in msg.lower()


@patch("subprocess.Popen", side_effect=FileNotFoundError("node not found"))
@patch("os.path.exists", return_value=True)
def test_start_mcp_node_missing(mock_exists, mock_popen):
    ok, msg = mcp_manager.start_mcp("server.js")
    assert ok is False
    assert "node" in msg.lower()


# ── stop_mcp ──────────────────────────────────────────────────────────────────

def test_stop_mcp_success():
    mock_proc = MagicMock()
    st.session_state["mcp_process"] = mock_proc
    st.session_state["mcp_running"] = True

    ok, msg = mcp_manager.stop_mcp()

    assert ok is True
    mock_proc.terminate.assert_called_once()
    assert st.session_state["mcp_running"] is False
    assert "mcp_process" not in st.session_state


def test_poll_mcp_process_marks_exited_process_stopped():
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1
    st.session_state["mcp_process"] = mock_proc
    st.session_state["mcp_running"] = True

    assert mcp_manager.poll_mcp_process() is False
    assert st.session_state["mcp_running"] is False
    assert "mcp_process" not in st.session_state


def test_stop_mcp_no_process():
    ok, msg = mcp_manager.stop_mcp()
    assert ok is False
    assert "no mcp" in msg.lower() or "running" in msg.lower()


# ── load_tools_from_json ──────────────────────────────────────────────────────

def test_load_tools_list_format(tmp_path):
    data = [
        {"name": "file_creator", "description": "Creates files"},
        {"name": "run_nmap_scan", "description": "Scans ports"},
    ]
    (tmp_path / "tools.json").write_text(json.dumps(data))

    tools = mcp_manager.load_tools_from_json(str(tmp_path))

    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert "file_creator" in names
    assert "run_nmap_scan" in names


def test_load_tools_dict_format(tmp_path):
    data = {"file_creator": {}, "file_deleter": {}}
    (tmp_path / "tools.json").write_text(json.dumps(data))

    tools = mcp_manager.load_tools_from_json(str(tmp_path))

    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert "file_creator" in names


def test_load_tools_missing_file(tmp_path):
    tools = mcp_manager.load_tools_from_json(str(tmp_path))
    assert tools == []


def test_load_tools_invalid_json(tmp_path):
    (tmp_path / "tools.json").write_text("not valid json {{")
    tools = mcp_manager.load_tools_from_json(str(tmp_path))
    assert tools == []


def test_fetch_tools_from_json_returns_names(tmp_path):
    data = [{"name": "tool_a"}, {"name": "tool_b"}]
    (tmp_path / "tools.json").write_text(json.dumps(data))

    tools = mcp_manager.load_tools_from_json(str(tmp_path))
    assert [t["name"] for t in tools] == ["tool_a", "tool_b"]


# ── discover_tools ────────────────────────────────────────────────────────────

@patch("core.mcp_manager.fetch_tools_from_server")
def test_discover_tools_prefers_live_server(mock_live, tmp_path):
    mock_live.return_value = ["run_nmap_scan", "file_creator"]
    data = [
        {"name": "run_nmap_scan", "description": "Scans ports"},
        {"name": "file_creator",  "description": "Creates files"},
    ]
    (tmp_path / "tools.json").write_text(json.dumps(data))

    script = str(tmp_path / "index.js")
    tools = mcp_manager.discover_tools(script)

    assert len(tools) == 2
    names = {t["name"] for t in tools}
    assert "run_nmap_scan" in names
    mock_live.assert_called_once()


@patch("core.mcp_manager.fetch_tools_from_server", return_value=[])
def test_discover_tools_falls_back_to_json(mock_live, tmp_path):
    data = [{"name": "file_creator", "description": "Creates files"}]
    (tmp_path / "tools.json").write_text(json.dumps(data))

    script = str(tmp_path / "index.js")
    tools = mcp_manager.discover_tools(script)

    assert len(tools) == 1
    assert tools[0]["name"] == "file_creator"


# ── call_mcp_tool ──────────────────────────────────────────────────────────────

@patch("requests.post")
def test_call_mcp_tool_success(mock_post):
    mock_init = MagicMock()
    mock_init.headers = {"mcp-session-id": "sess-123"}

    mock_notify = MagicMock()

    mock_call = MagicMock()
    mock_call.json.return_value = {"result": {"output": "hello"}}

    mock_post.side_effect = [mock_init, mock_notify, mock_call]

    result = mcp_manager.call_mcp_tool("my-tool", {"arg": 1})

    assert result == {"output": "hello"}
    assert mock_post.call_count == 3

    calls = mock_post.call_args_list
    assert calls[0][1]["json"]["method"] == "initialize"
    assert calls[1][1]["json"]["method"] == "notifications/initialized"
    assert calls[2][1]["json"]["method"] == "tools/call"
    assert calls[2][1]["json"]["params"]["name"] == "my-tool"


@patch("requests.post")
def test_call_mcp_tool_error_response(mock_post):
    mock_init = MagicMock()
    mock_init.headers = {}

    mock_notify = MagicMock()

    mock_call = MagicMock()
    mock_call.json.return_value = {"error": {"message": "Tool not found"}}

    mock_post.side_effect = [mock_init, mock_notify, mock_call]

    result = mcp_manager.call_mcp_tool("bad-tool", {})

    assert "error" in result
    assert "Tool not found" in result["error"]


@patch("requests.post", side_effect=Exception("connection refused"))
def test_call_mcp_tool_network_failure(mock_post):
    result = mcp_manager.call_mcp_tool("any-tool", {})
    assert "error" in result
