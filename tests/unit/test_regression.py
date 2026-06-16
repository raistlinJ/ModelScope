"""
Regression guard tests — one test per bug that was fixed.
Each test should FAIL if the bug is reintroduced.
"""
import pytest
from unittest.mock import patch, MagicMock
import streamlit as st


# ── Regression #1: URL missing http:// scheme causes silent failure ────────────
# Bug: fetch_ollama_models("grain.utep.edu:11434") raised MissingSchema
#      silently and returned None (or crashed). Fixed by _ensure_scheme().

def test_ensure_scheme_prepends_http():
    from core.utils import ensure_http_scheme as _ensure_scheme
    assert _ensure_scheme("grain.utep.edu:11434") == "http://grain.utep.edu:11434"
    assert _ensure_scheme("localhost:11434")       == "http://localhost:11434"


@patch("core.models.requests.get")
def test_fetch_ollama_bare_hostname_no_exception(mock_get):
    from core.models import fetch_ollama_models
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {"models": []}
    mock_get.return_value.raise_for_status = MagicMock()

    models, err = fetch_ollama_models("grain.utep.edu:11434")

    assert err == ""  # no MissingSchema, no silent failure
    called_url = mock_get.call_args[0][0]
    assert called_url.startswith("http://"), f"URL {called_url!r} missing scheme"


# ── Regression #2: fetch_ollama_models returned None on error ─────────────────
# Bug: callers did `for m in fetch_ollama_models(...)` which crashed when None
#      was returned on error. Fixed: always returns (list, str) tuple.

def test_fetch_ollama_always_returns_tuple_on_error():
    from core.models import fetch_ollama_models
    # Empty URL
    result = fetch_ollama_models("")
    assert isinstance(result, tuple) and len(result) == 2
    assert isinstance(result[0], list)
    assert isinstance(result[1], str)

    # Connection error
    with patch("core.models.requests.get", side_effect=Exception("boom")):
        result = fetch_ollama_models("http://localhost:11434")
        assert isinstance(result, tuple) and len(result) == 2


# ── Regression #3: llama-server stuck "Starting…" when process crashes ────────
# Bug: poll_ready() never checked if subprocess died, so the UI showed
#      "Starting — loading model…" indefinitely. Fixed: check proc.poll().

def test_poll_ready_crash_detection():
    from core import llama_server

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1  # subprocess exited with error

    st.session_state["llama_server_process"] = mock_proc
    st.session_state["llama_server_running"] = False

    with patch("requests.get", side_effect=Exception("unreachable")):
        ready = llama_server.poll_ready("http://localhost:8080")

    assert ready is False, "poll_ready must return False when process has crashed"
    assert st.session_state["llama_server_crashed"] is True, \
        "llama_server_crashed must be set True so UI can show crash message"
    assert st.session_state["llama_server_exit_code"] == 1


# ── Regression #4: stop() must clear running state immediately ───────────────
# Bug: after clicking Stop, the UI showed "Running & ready" and "Server stopped"
#      simultaneously because llama_server_running wasn't cleared synchronously.
#      Fixed: stop() sets llama_server_running=False before returning.

@patch("subprocess.run")
@patch("time.sleep")
def test_stop_clears_running_state(mock_sleep, mock_run):
    from core import llama_server

    mock_proc = MagicMock()
    st.session_state["llama_server_process"] = mock_proc
    st.session_state["llama_server_running"] = True

    ok, _ = llama_server.stop()

    assert ok is True
    assert st.session_state["llama_server_running"] is False, \
        "stop() must set llama_server_running=False so UI doesn't show both states"


# ── Regression #5: MCP default path pointed to non-existent directory ─────────
# Bug: MCP_SCRIPT_PATH was set to mcp-nmap-server/index.js which didn't exist.
#      Fixed: defaults.py now uses mcp-server/index.js.

def test_mcp_script_path_correct_directory():
    from config.defaults import MCP_SCRIPT_PATH
    import os

    path_parts = MCP_SCRIPT_PATH.replace("\\", "/").split("/")
    # Must use 'mcp-server', not 'mcp-nmap-server'
    assert "mcp-server" in path_parts, (
        f"MCP_SCRIPT_PATH must contain 'mcp-server' directory, got: {MCP_SCRIPT_PATH}"
    )
    assert "mcp-nmap-server" not in path_parts, (
        f"MCP_SCRIPT_PATH must NOT contain 'mcp-nmap-server', got: {MCP_SCRIPT_PATH}"
    )
    assert MCP_SCRIPT_PATH.endswith("index.js"), (
        f"MCP_SCRIPT_PATH must end with index.js, got: {MCP_SCRIPT_PATH}"
    )


# ── Regression #6: tool_call missing 'type: function' causes HTTP 500 ─────────
# Bug: _stream_llama_cpp assembled tool calls without 'type':'function',
#      which llama.cpp rejected with 500 on the re-sent assistant message.
#      The full test coverage lives in tests/unit/test_tool_call_type.py.
#      This regression guard checks the minimal invariant inline.

def test_stream_llama_cpp_tool_call_has_type_field():
    import json
    import sys, types
    for mod in ("requests", "pandas"):
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)

    from core.streaming import stream_llama_cpp as _stream_llama_cpp

    sse_line = json.dumps({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "abc",
         "function": {"name": "file_creator", "arguments": '{"path":"/tmp/x","content":"y"}'}}
    ]}}]})

    with patch("core.streaming.requests") as mock_req:
        mock_resp = MagicMock()
        mock_resp.iter_lines.return_value = iter([
            f"data: {sse_line}".encode(),
            b"data: [DONE]",
        ])
        mock_resp.raise_for_status = MagicMock()
        mock_req.post.return_value = mock_resp

        result = _stream_llama_cpp(
            "http://localhost:8080", "", [{"role": "user", "content": "test"}],
            [], 4096, lambda _: None,
        )

    tc = result["message"]["tool_calls"][0]
    assert tc.get("type") == "function", \
        "Tool call must have type='function' or llama.cpp returns HTTP 500"
