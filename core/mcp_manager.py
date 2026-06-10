import json
import os
import subprocess
import streamlit as st
import requests
from config.defaults import MCP_SERVER_BASE_URL


# ── SSH TUNNEL FUNCTIONS — FUTURE RELEASE ────────────────────────────────────
# SSH connections and MCP SSH tunneling are planned for a future release.
# The functions below are disabled until that support is fully implemented.
#
# def start_mcp_ssh_tunnel(
#     host: str, port: int, user: str,
#     password: str | None = None,
#     key_path: str | None = None,
#     local_port: int = 9191,
#     remote_port: int = 9191,
# ) -> tuple[bool, str]:
#     """
#     Create an SSH tunnel forwarding local_port → remote_host:remote_port.
#     Stores the tunnel process in session state as 'mcp_ssh_tunnel_process'.
#     """
#     import time
#     ssh_cmd = [
#         "ssh", "-N", "-L", f"{local_port}:localhost:{remote_port}",
#         "-p", str(port), f"{user}@{host}",
#         "-o", "StrictHostKeyChecking=no",
#         "-o", "BatchMode=yes" if not password else "StrictHostKeyChecking=no",
#     ]
#     if key_path:
#         ssh_cmd += ["-i", key_path]
#     try:
#         proc = subprocess.Popen(
#             ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
#         )
#         time.sleep(0.8)
#         if proc.poll() is not None:
#             err = (proc.stderr.read() or b"").decode()
#             return False, f"SSH tunnel failed: {err or 'process exited immediately'}"
#         st.session_state["mcp_ssh_tunnel_process"] = proc
#         return True, f"SSH tunnel active (pid {proc.pid})  localhost:{local_port} → {host}:{remote_port}"
#     except FileNotFoundError:
#         return False, "ssh binary not found — is OpenSSH installed?"
#     except Exception as e:
#         return False, str(e)
#
#
# def stop_mcp_ssh_tunnel() -> tuple[bool, str]:
#     """Terminate the SSH tunnel process."""
#     proc = st.session_state.get("mcp_ssh_tunnel_process")
#     if not proc:
#         return False, "No SSH tunnel running"
#     try:
#         proc.terminate()
#         st.session_state.pop("mcp_ssh_tunnel_process", None)
#         return True, "SSH tunnel stopped"
#     except Exception as e:
#         return False, str(e)
# ─────────────────────────────────────────────────────────────────────────────


def start_mcp(script_path: str) -> tuple[bool, str]:
    if not os.path.exists(script_path):
        return False, f"Script not found: {script_path}"
    try:
        proc = subprocess.Popen(
            ["node", script_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
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
        st.session_state.pop("mcp_process", None)
        st.session_state["mcp_running"] = False
        return True, "Stopped"
    except Exception as e:
        return False, str(e)


def load_tools_from_json(mcp_dir: str) -> list[dict]:
    """
    Read tools from tools.json.  Returns list of {name, description} dicts. (fix #25)
    """
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


def fetch_tools_from_json(mcp_dir: str) -> list[str]:
    """Return just tool names (backwards compat)."""
    return [t["name"] for t in load_tools_from_json(mcp_dir)]


def fetch_tools_from_server(base_url: str = MCP_SERVER_BASE_URL) -> list[str]:
    """
    Ask the running MCP server for its tool list via JSON-RPC.
    Uses a short 1 s timeout so Fetch Tools doesn't hang when MCP is down.
    """
    try:
        init_resp = requests.post(
            f"{base_url}/message",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "spark-eval", "version": "1.0"},
                },
            },
            timeout=1,
        )
        if not init_resp.ok:
            return []
        session_id = init_resp.headers.get("mcp-session-id", "")
        headers    = {"mcp-session-id": session_id} if session_id else {}

        # Notify that initialization is complete (Bug 2)
        requests.post(
            f"{base_url}/message",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
            timeout=1,
        )

        list_resp = requests.post(
            f"{base_url}/message",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers=headers,
            timeout=1,
        )
        if not list_resp.ok:
            return []
        result = list_resp.json().get("result", {})
        return [t["name"] for t in result.get("tools", []) if isinstance(t, dict)]
    except Exception:
        return []


def discover_tools(mcp_script_path: str, base_url: str = MCP_SERVER_BASE_URL) -> list[dict]:
    """
    Try live server first, fall back to tools.json.
    Returns list of {name, description} dicts. (fix #25)
    """
    live_names = fetch_tools_from_server(base_url)
    if live_names:
        # Got live names — try to enrich with descriptions from tools.json
        mcp_dir   = os.path.dirname(mcp_script_path)
        json_tools = {t["name"]: t.get("description", "") for t in load_tools_from_json(mcp_dir)}
        return [
            {"name": n, "description": json_tools.get(n, "")}
            for n in live_names
        ]
    # Fall back to tools.json entirely
    return load_tools_from_json(os.path.dirname(mcp_script_path))


def call_mcp_tool(
    tool_name: str,
    tool_args: dict,
    base_url: str = MCP_SERVER_BASE_URL,
) -> dict:
    """Call a tool on the MCP server and return its result dict."""
    try:
        init_resp = requests.post(
            f"{base_url}/message",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "spark-eval", "version": "1.0"},
                },
            },
            timeout=3,
        )
        session_id = init_resp.headers.get("mcp-session-id", "")
        headers    = {"mcp-session-id": session_id} if session_id else {}

        # Notify that initialization is complete (Bug 2)
        requests.post(
            f"{base_url}/message",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
            timeout=1,
        )

        call_resp = requests.post(
            f"{base_url}/message",
            json={
                "jsonrpc": "2.0", "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": tool_args},
            },
            headers=headers,
            timeout=30,
        )
        data = call_resp.json()
        if "error" in data:
            return {"error": data["error"].get("message", str(data["error"]))}
        return data.get("result", {})
    except Exception as e:
        return {"error": str(e)}
