import os
import re
import streamlit as st
from config.defaults import LLAMA_CPP_DEFAULT_URL, OLLAMA_DEFAULT_URL
from config.scenarios import SCENARIOS
from core.evaluator import run_evaluation
from core import llama_server


_LOG_TAG_MAP = {
    "[INIT]":         "init",
    "[TOOLS]":        "tools",
    "[LLM]":          "llm",
    "[RESPONSE]":     "llm",
    "[THINKING]":     "thinking",
    "[TOOL CALL]":    "tool",
    "[TOOL RESULT]":  "result",
    "[WARN]":         "warn",
    "[ERROR]":        "warn",
    "[DONE]":         "done",
    "[COMPLETE]":     "done",
    "[ABORTED]":      "warn",
    "[VALIDATE]":     "val",
    "[CANCEL]":       "cancel",
    "[TOKENS]":       "tokens",
    "[CLEANUP]":      "init",
    "[SYS]":          "sys",
    "[USR]":          "usr",
}


def _tag(line: str) -> str:
    for prefix, tag in _LOG_TAG_MAP.items():
        if line.startswith(prefix):
            return tag
    return ""


def _render_terminal(placeholder, logs: list[dict]) -> None:
    if not logs:
        placeholder.markdown(
            '<div class="terminal-window">Awaiting run…</div>',
            unsafe_allow_html=True,
        )
        return
    lines_html = []
    for entry in logs:
        tag = entry.get("tag", "")
        css = f' class="log-{tag}"' if tag else ""
        raw = entry["text"]

        # Convert Python repr-escaped newlines (backslash-n) to actual newlines
        # so white-space:pre-wrap renders them correctly.
        raw = raw.replace('\\n', '\n')

        # Collapse 3+ consecutive newlines to 2 (avoids huge blank gaps in RESPONSE)
        raw = re.sub(r'\n{3,}', '\n\n', raw)

        text = (
            raw
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        lines_html.append(f"<span{css}>{text}</span>")
    inner = "<br>".join(lines_html)
    placeholder.markdown(
        f'<div class="terminal-window">{inner}</div>',
        unsafe_allow_html=True,
    )


def _status_pill(label: str, state: str) -> str:
    """Return an HTML status pill. state: 'up' | 'down' | 'wait'"""
    return (
        f'<span class="status-pill status-pill-{state}">{label}</span>'
    )


def render() -> None:
    st.header("Execute Evaluation")

    # Refresh llama-server status so the pill reflects current reality
    _backend_now = st.session_state.get("backend_type", "llama.cpp")
    if _backend_now == "llama.cpp":
        _url_now = (st.session_state.get("llm_url") or LLAMA_CPP_DEFAULT_URL).strip()
        llama_server.poll_ready(_url_now)

    # ── Pre-flight status bar (fix #24) ───────────────────────────────────────
    backend      = st.session_state.get("backend_type", "llama.cpp")
    model_sel    = st.session_state.get("selected_model")
    llm_running  = st.session_state.get("llama_server_running", False)
    mcp_running  = st.session_state.get("mcp_running", False)
    tools_loaded = bool(st.session_state.get("mcp_tools"))

    llm_state  = "up"   if llm_running  else ("wait" if backend == "ollama" else "down")
    # "wait" = not started yet (amber); "down" = started but broken (red)
    mcp_state  = "up"   if mcp_running  else "wait"
    tool_state = (
        "up"   if tools_loaded else
        "down" if mcp_running  else  # MCP running but empty = genuine problem
        "wait"                        # MCP not started = expected state
    )
    mod_state  = "up"   if model_sel    else "wait"

    pills = _status_pill(f"Model: {model_sel or 'not chosen'}", mod_state)
    
    # Only show llama-server status if model has been chosen
    if model_sel:
        pills += _status_pill(f"{'Ollama' if backend == 'ollama' else 'llama-server'}: {'ready' if llm_running else 'not ready'}", llm_state)
    
    pills += (
        _status_pill(f"MCP: {'running' if mcp_running else 'stopped'}", mcp_state)
        + _status_pill(f"Tools: {len(st.session_state.get('mcp_tools', {}))}", tool_state)
    )
    st.markdown(pills, unsafe_allow_html=True)

    # Pre-flight warnings
    _backend   = st.session_state.get("backend_type", "llama.cpp")
    _url       = (st.session_state.get("llm_url") or "").strip()
    _default_u = LLAMA_CPP_DEFAULT_URL if _backend == "llama.cpp" else OLLAMA_DEFAULT_URL
    if not model_sel:
        st.warning(
            "⚠️  No model selected — go to **Configuration → Model Setup** and scan/select a model."
        )
    if not _url:
        st.warning(
            f"⚠️  Server URL is empty — defaulting to `{_default_u}`. "
            "Set it in **Configuration → Model Setup**."
        )
    # Warn if the server is loaded with a different model than the one selected
    if _backend == "llama.cpp" and llm_running and model_sel:
        _info = llama_server.get_server_info((_url or _default_u))
        if _info and _info.get("model_path"):
            _running_base  = os.path.basename(_info["model_path"])
            _selected_base = os.path.basename(st.session_state.get("selected_model_path") or "")
            if _running_base and _selected_base and _running_base != _selected_base:
                st.error(
                    f"🚨  Model mismatch: server is running **{_running_base}** "
                    f"but **{_selected_base}** is selected. "
                    f"Go to **Configuration → llama-server → Restart** to load the correct model."
                )

    # ── Active scenario / tool info ────────────────────────────────────────────
    _active_sc  = st.session_state.get("active_scenario", "")
    _tool_f     = st.session_state.get("tool_focus", "")
    _sc_caption = (
        f"**Active:** {_active_sc}" + (f"  |  **Tool:** `{_tool_f}`" if _tool_f else "")
        if _active_sc else
        "⚠️ No scenario selected — configure in **Configuration → Metrics Setup**."
    )
    st.caption(_sc_caption)
    # Track when user edits the prompt fields (fix #10)

    # ── Prompt editors ────────────────────────────────────────────────────────
    col_sys, col_usr = st.columns(2)
    with col_sys:
        prev_sys = st.session_state.get("sys_prompt", "")
        new_sys  = st.text_area(
            "System Prompt", height=150, key="sys_prompt",
            help="Instructions defining the agent's behaviour and available tools",
        )
        if new_sys != prev_sys:
            st.session_state["_prompts_user_edited"] = True

    with col_usr:
        prev_usr = st.session_state.get("user_prompt", "")
        new_usr  = st.text_area(
            "User Prompt", height=150, key="user_prompt",
            help="The task given to the agent",
        )
        if new_usr != prev_usr:
            st.session_state["_prompts_user_edited"] = True

    # ── Run / Cancel / Clear row ──────────────────────────────────────────────
    run_in_progress = st.session_state.get("_run_in_progress", False)
    model_chosen = bool(st.session_state.get("selected_model"))

    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Run Evaluation",
            type="primary",
            use_container_width=True,
            disabled=run_in_progress or not model_chosen,
        )
    with col_cancel:
        # Cancel button (fix #16)
        if st.button("⏹  Cancel", use_container_width=True, disabled=not run_in_progress):
            st.session_state["cancel_requested"] = True
    with col_clear:
        if st.button("Clear Log", use_container_width=True):
            st.session_state["run_logs"]          = []
            st.session_state["run_completed"]     = False
            st.session_state["telemetry"]         = {}
            st.session_state["_run_in_progress"]  = False
            st.session_state["cancel_requested"]  = False
            st.rerun()

    log_placeholder = st.empty()
    _render_terminal(log_placeholder, st.session_state.get("run_logs", []))

    if run_btn and not run_in_progress:
        st.session_state["run_logs"]         = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True

        logs: list[dict]       = []
        cancel_ref: list[bool] = [False]

        def on_log(msg: str) -> None:
            # Propagate cancel flag into the evaluator via the shared reference
            if st.session_state.get("cancel_requested"):
                cancel_ref[0] = True
            entry = {"text": msg, "tag": _tag(msg)}
            logs.append(entry)
            st.session_state["run_logs"] = list(logs)
            _render_terminal(log_placeholder, logs)

        _backend   = st.session_state.get("backend_type", "llama.cpp")
        _def_url   = LLAMA_CPP_DEFAULT_URL if _backend == "llama.cpp" else OLLAMA_DEFAULT_URL
        _url_val   = (st.session_state.get("llm_url") or _def_url).strip()

        _active_scenario = st.session_state.get("active_scenario", "")
        _scenario_data   = SCENARIOS.get(_active_scenario, {})
        config = {
            "backend_type":        _backend,
            "llm_url":             _url_val,
            "selected_model":      st.session_state.get("selected_model"),
            "context_size":        st.session_state.get("context_size", 4096),
            "sys_prompt":          st.session_state.get("sys_prompt", ""),
            "user_prompt":         st.session_state.get("user_prompt", ""),
            "mcp_url":             st.session_state.get("mcp_url", ""),
            "mcp_server_url":      st.session_state.get("mcp_server_url", ""),
            "mcp_tools":           st.session_state.get("mcp_tools", {}),
            "mcp_running":         st.session_state.get("mcp_running", False),
            "validation_command":  st.session_state.get("validation_command", ""),
            "fail_patterns":       st.session_state.get("fail_patterns", []),
            "active_scenario":     _active_scenario,
            "tool_focus":          st.session_state.get("tool_focus", ""),
            "metrics_matrix":      st.session_state.get("metrics_matrix", []),
            "expected_stdout":     _scenario_data.get("expected_stdout", ""),
            "pre_run_cleanup":     _scenario_data.get("pre_run_cleanup", []),
            "cancel_requested_ref": cancel_ref,
        }

        # Spinner shows during the run (fix #15)
        with st.spinner("Evaluation running…"):
            from core.environment import LocalEnvironment
            # ── SSH execution target — FUTURE RELEASE ─────────────────────────
            # Remote SSH execution is planned for a future release.
            # from core.environment import LocalEnvironment, SSHEnvironment
            # if env_type == "ssh":
            #     env = SSHEnvironment(
            #         host=st.session_state.get("target_ssh_host"),
            #         port=st.session_state.get("target_ssh_port", 22),
            #         username=st.session_state.get("target_ssh_user"),
            #         password=st.session_state.get("target_ssh_password"),
            #         key_path=st.session_state.get("target_ssh_key_path"),
            #     )
            #     on_log(f"[INIT] Target: SSH ({st.session_state.get('target_ssh_user')}@{st.session_state.get('target_ssh_host')})")
            # ──────────────────────────────────────────────────────────────────
            env_type = st.session_state.get("target_env_type", "local")
            env = None
            try:
                env = LocalEnvironment()
                on_log("[INIT] Target: Local")

                telemetry = run_evaluation(env, config, on_log)
            finally:
                if env and hasattr(env, "close"):
                    env.close()

        st.session_state["telemetry"]        = telemetry
        st.session_state["run_completed"]    = True
        st.session_state["_run_in_progress"] = False
        st.session_state["cancel_requested"] = False

        # Append to run history (fix #26)
        history: list = st.session_state.get("run_history", [])
        history.append(telemetry)
        from config.defaults import MAX_RUN_HISTORY
        st.session_state["run_history"] = history[-MAX_RUN_HISTORY:]
