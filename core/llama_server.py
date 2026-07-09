"""Lifecycle management for a local llama.cpp server process.

Starts, stops, polls the health of, and tracks the running llama-server
subprocess. Process handles and readiness flags are stashed in Streamlit session
state, so this module is UI-coupled and must not be imported by the CLI path.
"""
import os
import socket
import subprocess
import tempfile
import time
import requests
import streamlit as st
from urllib.parse import urlsplit
from config.defaults import LLAMA_SERVER_BIN, LLAMA_CPP_DEFAULT_URL
from core.utils import ensure_http_scheme, effective_verify_ssl


def is_running(url: str = LLAMA_CPP_DEFAULT_URL, timeout: float = 2.0) -> bool:
    """Return True if llama-server is responding at url/health."""
    try:
        r = requests.get(ensure_http_scheme(url).rstrip("/") + "/health", timeout=timeout)
        return r.ok
    except Exception:
        return False


def port_open(url: str, timeout: float = 1.5) -> bool:
    """Return True if a bare TCP connection succeeds to url's host:port.

    Used to tell "nothing is listening here" (managed server not started yet)
    apart from "something is listening but didn't answer the HTTP probe
    correctly" — the two failure modes need very different user-facing
    messages in get_server_info() callers.
    """
    parsed = urlsplit(ensure_http_scheme(url))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def get_server_info(url: str = LLAMA_CPP_DEFAULT_URL, verify_ssl: bool = True) -> dict | None:
    """
    Return {n_ctx, model_path} from a running OpenAI-compatible server, or None.

    llama.cpp exposes detailed metadata at /props. Servers such as vLLM expose
    only the OpenAI-compatible /v1/models endpoint, so fall back to that before
    reporting the server as unreachable.
    """
    base = ensure_http_scheme(url).rstrip("/")
    if not base:
        return None
    verify = effective_verify_ssl(base, verify_ssl)

    def _coerce_int(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    try:
        r = requests.get(base + "/props", timeout=3, verify=verify)
        if r.ok:
            d = r.json()
            return {
                "n_ctx":      _coerce_int(d.get("default_generation_settings", {}).get("n_ctx")),
                "model_path": d.get("model_path", ""),
                "source":     "props",
            }
    except Exception:
        pass

    try:
        r = requests.get(base + "/v1/models", timeout=3, verify=verify)
        if not r.ok:
            return None
        d = r.json()
        raw_models = d.get("data") or d.get("models") or []
        model_path = ""
        n_ctx = None
        for model in raw_models:
            if not isinstance(model, dict):
                continue
            model_path = model.get("id") or model.get("name") or ""
            meta = model.get("meta") or {}
            n_ctx = (
                _coerce_int(meta.get("n_ctx"))
                or _coerce_int(meta.get("context_length"))
                or _coerce_int(model.get("n_ctx"))
                or _coerce_int(model.get("context_length"))
                or _coerce_int(model.get("max_model_len"))
            )
            if model_path:
                break
        return {
            "n_ctx":      n_ctx,
            "model_path": model_path,
            "source":     "v1/models",
        }
    except Exception:
        return None


def get_n_ctx(url: str = LLAMA_CPP_DEFAULT_URL) -> int | None:
    """Return the actual n_ctx of the running server, or None."""
    info = get_server_info(url)
    return info["n_ctx"] if info else None


def _kill_port(port: int) -> None:
    """Kill any process currently listening on the given TCP port."""
    try:
        subprocess.run(["fuser", "-k", f"{port}/tcp"],
                       capture_output=True, timeout=5)
        time.sleep(0.8)
    except Exception:
        pass


def start(
    model_path:   str,
    context_size: int = 4096,
    host:         str = "127.0.0.1",
    port:         int = 8080,
    binary:       str | None = None,
) -> tuple[bool, str]:
    """
    Launch llama-server as a background subprocess.

    Checks the running server's n_ctx if one is already up:
    - n_ctx >= context_size → reuse
    - n_ctx <  context_size → kill and restart with correct size

    binary defaults to LLAMA_SERVER_BIN but can be overridden (fix #29).
    """
    url = f"http://{host}:{port}"
    bin_path = binary or st.session_state.get("llama_server_bin", LLAMA_SERVER_BIN)

    if is_running(url):
        info        = get_server_info(url)
        current_ctx = info["n_ctx"] if info else None
        running_model = (info or {}).get("model_path", "")
        # Compare by basename so absolute-path differences don't matter
        model_matches = (
            not model_path
            or os.path.basename(running_model) == os.path.basename(model_path)
        )
        if current_ctx is not None and current_ctx >= context_size and model_matches:
            st.session_state["llama_server_running"] = True
            return True, f"Already running at {url} (n_ctx={current_ctx}, model matches)"
        if not model_matches:
            action = (
                f"Restarting: loaded model is '{os.path.basename(running_model)}', "
                f"requested '{os.path.basename(model_path)}'"
            )
        else:
            action = (
                f"Restarting: n_ctx={current_ctx} < requested {context_size}"
                if current_ctx is not None else "Restarting: could not read server state (n_ctx/model)"
            )
        proc = st.session_state.get("llama_server_process")
        if proc:
            try:
                proc.terminate()
            except Exception:
                pass
            st.session_state.pop("llama_server_process", None)
        _kill_port(port)
    else:
        action = "Starting"

    try:
        # Write stdout+stderr to a temp file so we can tail it non-blocking
        log_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", prefix="llama_server_", delete=False
        )
        log_path = log_file.name

        proc = subprocess.Popen(
            [
                bin_path,
                "--model",    model_path,
                "--ctx-size", str(context_size),
                "--host",     host,
                "--port",     str(port),
                "--jinja",
                "--parallel", "1",
            ],
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()  # our handle — process keeps its own fd open

        st.session_state["llama_server_process"]  = proc
        st.session_state["llama_server_running"]  = False
        st.session_state["llama_server_crashed"]  = False
        st.session_state["llama_server_log_path"] = log_path
        st.session_state["llama_server_url"]      = url
        return True, f"{action} (pid {proc.pid}) — loading model…"
    except FileNotFoundError:
        return False, f"llama-server not found: {bin_path}"
    except Exception as e:
        return False, str(e)


def stop(host: str = "127.0.0.1", port: int = 8080) -> tuple[bool, str]:
    """Terminate the tracked process and kill the port so orphaned servers are removed too."""
    url  = f"http://{host}:{port}"
    proc = st.session_state.get("llama_server_process")
    if proc:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            pass
        st.session_state.pop("llama_server_process", None)
    # Kill the port regardless — handles externally-started or orphaned servers
    _kill_port(port)
    st.session_state["llama_server_running"] = False
    return True, "Server stopped"


def get_server_log(tail: int = 30) -> str:
    """Return the last `tail` lines from the server's log file, or '' if unavailable."""
    path = st.session_state.get("llama_server_log_path", "")
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-tail:]).strip()
    except Exception:
        return ""


def poll_ready(url: str = LLAMA_CPP_DEFAULT_URL) -> bool:
    """
    Non-blocking readiness check.
    Updates llama_server_running in session_state and returns True when healthy.
    Also detects whether the tracked subprocess has crashed.
    """
    proc = st.session_state.get("llama_server_process")

    # If the process we launched has exited, mark it as crashed
    if proc is not None and proc.poll() is not None:
        exit_code = proc.poll()
        st.session_state["llama_server_running"] = False
        st.session_state["llama_server_crashed"]    = True
        st.session_state["llama_server_exit_code"]  = exit_code
        return False

    ready = is_running(url)
    st.session_state["llama_server_running"] = ready
    if ready:
        st.session_state["llama_server_crashed"] = False
        st.session_state.pop("llama_server_exit_code", None)
    return ready
