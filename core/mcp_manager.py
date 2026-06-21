"""MCP (Model Context Protocol) server lifecycle and tool invocation.

Starts/stops the local MCP tool server and proxies tool calls to it over HTTP.
The evaluator calls ``call_mcp_tool`` to run a tool when an MCP server is up,
falling back to in-environment execution otherwise. Server state is held in
Streamlit session state, so this module is UI-coupled.
"""
import json
import os
import subprocess
import streamlit as st
import requests
from config.defaults import MCP_SERVER_BASE_URL


def poll_mcp_process() -> bool:
    """Synchronise session-state with the tracked MCP child process."""
    proc = st.session_state.get("mcp_process")
    if proc is None:
        st.session_state["mcp_running"] = False
        return False
    try:
        running = proc.poll() is None
    except Exception:
        running = False
    st.session_state["mcp_running"] = running
    if not running:
        st.session_state.pop("mcp_process", None)
    return running


def start_mcp(script_path: str) -> tuple[bool, str]:
    if not os.path.exists(script_path):
        return False, f"Script not found: {script_path}"
    try:
        proc = subprocess.Popen(
            ["node", script_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True,
        )
        if proc.poll() is not None:
            return False, "MCP server exited immediately"
        st.session_state["mcp_process"] = proc
        st.session_state["mcp_running"] = True
        return True, f"Started (pid {proc.pid})"
    except FileNotFoundError:
        return False, "node not found — is Node.js installed?"
    except Exception as e:
        return False, str(e)


def stop_mcp() -> tuple[bool, str]:
    proc = st.session_state.get("mcp_process")
    if not proc:
        return False, "No MCP server running"
    try:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except Exception:
            pass
        st.session_state.pop("mcp_process", None)
        st.session_state["mcp_running"] = False
        return True, "Stopped"
    except Exception as e:
        return False, str(e)


def load_tools_from_json(mcp_dir: str) -> list[dict]:
    """Read tools from tools.json. Returns list of {name, description} dicts."""
    path = os.path.join(mcp_dir, "tools.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return [
                {"name": t["name"], "description": t.get("description", "")}
                for t in data if isinstance(t, dict) and "name" in t
            ]
        if isinstance(data, dict):
            return [{"name": k, "description": ""} for k in data]
    except Exception:
        pass
    return []


# ── JSON-RPC session helper ───────────────────────────────────────────────────

def _mcp_rpc_session(base_url: str, timeout: float = 1.0) -> tuple[bool, dict]:
    """
    Open a JSON-RPC 2.0 session with the MCP server.
    Returns (ok, headers) where headers contains the session-id if provided.
    """
    try:
        resp = requests.post(
            f"{base_url}/message",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "spark-eval", "version": "1.0"},
                },
            },
            timeout=timeout,
        )
        if not resp.ok:
            return False, {}
        session_id = resp.headers.get("mcp-session-id", "")
        headers    = {"mcp-session-id": session_id} if session_id else {}
        requests.post(
            f"{base_url}/message",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
            timeout=1,
        )
        return True, headers
    except Exception:
        return False, {}


def fetch_tools_from_server(base_url: str = MCP_SERVER_BASE_URL) -> list[str]:
    """Ask the running MCP server for its tool list via JSON-RPC."""
    ok, headers = _mcp_rpc_session(base_url, timeout=1.0)
    if not ok:
        return []
    try:
        resp = requests.post(
            f"{base_url}/message",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
            timeout=1,
        )
        if not resp.ok:
            return []
        result = resp.json().get("result", {})
        return [t["name"] for t in result.get("tools", []) if isinstance(t, dict)]
    except Exception:
        return []


def discover_tools(mcp_script_path: str, base_url: str = MCP_SERVER_BASE_URL) -> list[dict]:
    """Try live server first; fall back to tools.json. Returns {name, description} dicts."""
    live_names = fetch_tools_from_server(base_url)
    mcp_dir    = os.path.dirname(mcp_script_path)
    if live_names:
        json_tools = {t["name"]: t.get("description", "") for t in load_tools_from_json(mcp_dir)}
        return [{"name": n, "description": json_tools.get(n, "")} for n in live_names]
    return load_tools_from_json(mcp_dir)


def call_mcp_tool(
    tool_name: str,
    tool_args: dict,
    base_url: str = MCP_SERVER_BASE_URL,
) -> dict:
    """Call a tool on the MCP server and return its result dict."""
    ok, headers = _mcp_rpc_session(base_url, timeout=3.0)
    if not ok:
        return {"error": "MCP session initialization failed"}
    try:
        resp = requests.post(
            f"{base_url}/message",
            json={
                "jsonrpc": "2.0", "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": tool_args},
            },
            headers=headers,
            timeout=30,
        )
        data = resp.json()
        if "error" in data:
            return {"error": data["error"].get("message", str(data["error"]))}
        return data.get("result", {})
    except Exception as e:
        return {"error": str(e)}
