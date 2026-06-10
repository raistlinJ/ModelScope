import re
import time
import streamlit as st
from config.defaults import (
    LLAMA_CPP_DEFAULT_URL, OLLAMA_DEFAULT_URL,
    GGUF_MODELS_DIR, MIN_CONTEXT_SIZE, MAX_CONTEXT_SIZE, CONTEXT_STEP,
    MCP_SERVER_BASE_URL,
)
from config.metrics import METRIC_TYPES, CATEGORIES, format_criterion
from config.scenarios import SCENARIOS
from core.models import scan_gguf_models, fetch_ollama_models
from core.mcp_manager import start_mcp, stop_mcp, discover_tools
from core import llama_server

# Tool focus → scenario mapping
_TOOL_SCENARIOS = {
    "file_creator":  "Scenario 1 – File Creation",
    "run_nmap_scan": "Scenario 2 – Network Scan",
}
_TOOL_LABELS = {
    "file_creator":  "file_creator — File Creation",
    "run_nmap_scan": "run_nmap_scan — Network Scanner",
}


def render() -> None:
    st.header("Configuration")
    sub_model, sub_metrics, sub_verify = st.tabs(
        ["⚙  Model Setup", "📐  Metrics Setup", "🔬  Platform Verification"]
    )
    with sub_model:
        _model_setup()
    with sub_metrics:
        _metrics_setup()
    with sub_verify:
        _platform_verification()


def _platform_verification() -> None:
    """Pre-flight checks and test suite side by side."""
    col_pf, col_ts = st.columns(2)
    with col_pf:
        st.subheader("Pre-flight Checks")
        st.caption("Verify platform and evaluation pipeline before running benchmarks.")
        from ui.preflight_tab import render as _render_preflight
        _render_preflight()
    with col_ts:
        st.subheader("Test Suite")
        st.caption("Visual regression dashboard for the ModelScope test suite.")
        from ui.test_suite_tab import render as _render_tests
        _render_tests()


# ── Model Setup ────────────────────────────────────────────────────────────────

# ── SSH connection test — FUTURE RELEASE ─────────────────────────────────────
# SSH connections to remote execution targets are planned for a future release.
# The function below is disabled until that support is fully implemented.
#
# def _test_ssh_connection() -> None:
#     """Attempt a quick SSH connection and run a probe command, showing the result inline."""
#     from core.environment import SSHEnvironment
#     host     = st.session_state.get("target_ssh_host", "").strip()
#     port     = st.session_state.get("target_ssh_port", 22)
#     user     = st.session_state.get("target_ssh_user", "").strip()
#     password = st.session_state.get("target_ssh_password", "") or None
#     key_path = st.session_state.get("target_ssh_key_path", "").strip() or None
#
#     if not host:
#         st.error("Host is required.")
#         return
#     if not user:
#         st.error("User is required.")
#         return
#
#     env = None
#     try:
#         with st.spinner(f"Connecting to {user}@{host}:{port}…"):
#             env = SSHEnvironment(host=host, port=port, username=user,
#                                  password=password, key_path=key_path)
#             result = env.execute("hostname && whoami && uname -sr", timeout=10)
#
#         if result["exit_code"] == 0:
#             st.success(f"Connected — `{result['stdout'].strip()}`")
#         else:
#             st.error(f"Command failed (exit {result['exit_code']}): {result['stderr'].strip()}")
#     except Exception as e:
#         st.error(f"SSH error: {e}")
#     finally:
#         if env:
#             env.close()
# ─────────────────────────────────────────────────────────────────────────────


def _model_setup() -> None:
    # Apply any pending backend detection BEFORE the selectbox is created.
    if "_pending_backend" in st.session_state:
        pending = st.session_state.pop("_pending_backend")
        st.session_state["backend_type"] = pending
        st.session_state["_last_backend"] = pending
        st.session_state["llm_models"]    = []
        st.session_state["selected_model"] = None

    # ── Execution Target ───────────────────────────────────────────────────────
    with st.expander("Execution Target", expanded=False):
        env_type = st.selectbox(
            "Mode",
            options=["local"],  # "ssh" option is a future release
            index=0,
            key="target_env_type",
            help="Where evaluation commands and tools will execute.",
        )
        # ── SSH target UI — FUTURE RELEASE ────────────────────────────────────
        # Remote SSH execution targets are planned for a future release.
        # if env_type == "ssh":
        #     c1, c2, c3 = st.columns([4, 1, 4])
        #     c1.text_input("Host", key="target_ssh_host")
        #     c2.number_input("Port", value=22, key="target_ssh_port")
        #     c3.text_input("User", key="target_ssh_user")
        #     c4, c5 = st.columns(2)
        #     c4.text_input("Password (optional)", type="password", key="target_ssh_password")
        #     c5.text_input("SSH Key Path (optional)", key="target_ssh_key_path")
        #     ct, _ = st.columns([2, 5])
        #     with ct:
        #         if st.button("Test Connection", use_container_width=True):
        #             _test_ssh_connection()
        # ──────────────────────────────────────────────────────────────────────

    # ── Backend & Model ────────────────────────────────────────────────────────
    with st.expander("Backend & Model", expanded=True):
        st.subheader("Backend & Connection")
        c_be, c_url = st.columns([2, 5])
        with c_be:
            backend = st.selectbox(
                "Backend",
                options=["llama.cpp", "ollama"],
                index=0 if st.session_state["backend_type"] == "llama.cpp" else 1,
                key="backend_type",
            )
        with c_url:
            if st.session_state.get("_last_backend") != backend:
                st.session_state["llm_url"] = (
                    LLAMA_CPP_DEFAULT_URL if backend == "llama.cpp" else OLLAMA_DEFAULT_URL
                )
                st.session_state["_last_backend"] = backend
                st.session_state["llm_models"]    = []
                st.session_state["selected_model"] = None
            elif not (st.session_state.get("llm_url") or "").strip():
                st.session_state["llm_url"] = (
                    LLAMA_CPP_DEFAULT_URL if backend == "llama.cpp" else OLLAMA_DEFAULT_URL
                )
            st.text_input("Server URL", key="llm_url")

        st.subheader("Model")
        if backend == "llama.cpp":
            _gguf_model_selector()
        else:
            _ollama_model_selector()

        st.subheader("Context Window")
        ctx = st.slider(
            "Tokens",
            min_value=MIN_CONTEXT_SIZE,
            max_value=MAX_CONTEXT_SIZE,
            step=CONTEXT_STEP,
            key="context_size",
            help="Number of tokens in the model context. Restart the server to apply.",
        )
        if backend == "llama.cpp":
            url  = st.session_state.get("llm_url", "")
            info = llama_server.get_server_info(url)
            if info and info["n_ctx"] is not None and info["n_ctx"] != ctx:
                st.warning(
                    f"Running server n_ctx = **{info['n_ctx']}** — "
                    f"slider is **{ctx}**. Restart server to apply."
                )

        if backend == "llama.cpp":
            st.subheader("llama-server")
            _llama_server_controls()
        else:
            st.subheader("Ollama")
            st.info(
                "Ollama manages model loading automatically. "
                "Ensure the Ollama service is running at the configured URL."
            )

    # ── MCP Server ─────────────────────────────────────────────────────────────
    with st.expander("SecOps MCP Server", expanded=True):
        _mcp_server_section()


def _llama_server_controls() -> None:
    """Status indicator, server log, and Start / Stop / Restart controls."""
    url          = st.session_state.get("llm_url", "")
    ready        = llama_server.poll_ready(url)
    proc         = st.session_state.get("llama_server_process")
    model_chosen = bool(st.session_state.get("selected_model_path"))
    crashed      = st.session_state.get("llama_server_crashed", False)
    loading      = proc is not None and not ready and not crashed

    # During loading the live server log is the authoritative status — discard
    # any deferred start message so it can't stack on top of the log output.
    # Outside loading, show deferred messages only when not in a definitive state.
    if loading:
        for _key in ("_autostart_msg", "_srv_msg"):
            st.session_state.pop(_key, None)
    else:
        in_definitive_state = ready or crashed
        for _key in ("_autostart_msg", "_srv_msg"):
            msg_pair = st.session_state.pop(_key, None)
            if not msg_pair or in_definitive_state:
                continue
            level, msg = msg_pair
            if level in ("ok", "success"):
                st.success(msg)
            elif level == "info":
                st.info(msg)
            else:
                st.error(msg)

    if ready:
        st.success("Running & ready")
        info = llama_server.get_server_info(url)
        if info:
            model_name = (info.get("model_path") or "").split("/")[-1] or "?"
            st.caption(f"Model: `{model_name}`  |  n_ctx: `{info.get('n_ctx', '?')}`")
    elif crashed:
        exit_code = st.session_state.get("llama_server_exit_code", "?")
        st.error(f"Process exited (code {exit_code})")
        log = llama_server.get_server_log(tail=30)
        if log:
            with st.expander("Server output", expanded=True):
                st.code(log, language=None)
        else:
            st.caption("No log output captured — check the binary path.")
    elif loading:
        st.markdown(
            '<div style="color:var(--warn);font-family:\'JetBrains Mono\',monospace;'
            'font-size:0.84rem;font-weight:600;padding:4px 0 6px">'
            '⏳ Loading model into memory…</div>',
            unsafe_allow_html=True,
        )
        log = llama_server.get_server_log(tail=12)
        if log:
            st.code(log, language=None)
        # Auto-refresh every 0.5 s until the server is ready or crashes
        time.sleep(0.5)
        st.rerun()
    elif not model_chosen:
        st.warning("No model selected — choose one above, then press Start.")
    else:
        st.warning("Not running — press Start to load the model.")

    st.text_input(
        "Binary path",
        key="llama_server_bin",
        help="Path to the llama-server executable.",
    )

    col_start, col_stop, col_restart = st.columns(3)
    with col_start:
        if st.button("Start", use_container_width=True, key="btn_ls_start",
                     disabled=not model_chosen):
            ok, msg = llama_server.start(
                st.session_state["selected_model_path"],
                context_size=st.session_state.get("context_size", 4096),
            )
            st.session_state["_srv_msg"] = ("success" if ok else "error", msg)
            st.rerun()

    with col_stop:
        if st.button("Stop", use_container_width=True, key="btn_ls_stop",
                     disabled=not (ready or loading)):
            ok, msg = llama_server.stop()
            st.session_state["_srv_msg"] = ("success" if ok else "info", msg)
            st.rerun()

    with col_restart:
        if st.button("Restart", use_container_width=True, key="btn_ls_restart",
                     disabled=not model_chosen):
            llama_server.stop()
            ok, msg = llama_server.start(
                st.session_state["selected_model_path"],
                context_size=st.session_state.get("context_size", 4096),
            )
            st.session_state["_srv_msg"] = ("success" if ok else "error", msg)
            st.rerun()


def _mcp_server_section() -> None:
    """MCP server path, Start/Stop, SSH tunnel, and tool discovery."""
    if msg_pair := st.session_state.pop("_mcp_msg", None):
        level, msg = msg_pair
        if level == "success":
            st.success(msg)
        elif level == "info":
            st.info(msg)
        else:
            st.error(msg)

    # ── Connection Type ───────────────────────────────────────────────────────
    # ── SSH Tunnel option — FUTURE RELEASE ───────────────────────────────────
    # MCP SSH tunneling is planned for a future release.  Only "Local" is
    # available for now.  The "SSH Tunnel" option is disabled below.
    conn_type = st.selectbox(
        "Connection Type",
        options=["Local"],  # "SSH Tunnel" option is a future release
        index=0,
        key="_mcp_conn_type_sel",
        help="Local: start a Node.js MCP server on this machine.",
    )
    # st.session_state["mcp_use_ssh"] = (conn_type == "SSH Tunnel")

    if conn_type == "Local":
        # ── Local MCP ─────────────────────────────────────────────────────────
        col_path, col_url = st.columns([1, 1])
        with col_path:
            st.text_input("Node.js Script Path", key="mcp_url")
        with col_url:
            st.text_input("MCP Server URL", key="mcp_server_url")

        col_start, col_stop, _ = st.columns([1, 1, 4])
        with col_start:
            if st.button("Start MCP", use_container_width=True, key="btn_start_mcp"):
                ok, msg = start_mcp(st.session_state["mcp_url"])
                st.session_state["_mcp_msg"] = ("success" if ok else "error", msg)
                st.rerun()
        with col_stop:
            if st.button("Stop MCP", use_container_width=True, key="btn_stop_mcp"):
                ok, msg = stop_mcp()
                st.session_state["_mcp_msg"] = ("success" if ok else "info", msg)
                st.rerun()

        running = st.session_state.get("mcp_running", False)
        pill_state = "up" if running else "wait"
        pill_label = "Running" if running else "Stopped"
        st.markdown(
            f'<p style="margin:4px 0 8px">'
            f'<span class="status-pill status-pill-{pill_state}">{pill_label}</span>'
            f'</p>',
            unsafe_allow_html=True,
        )

    # ── MCP SSH Tunnel UI — FUTURE RELEASE ───────────────────────────────────
    # The SSH Tunnel branch below is disabled until SSH support ships.
    # else:
    #     # ── SSH Tunnel ────────────────────────────────────────────────────────
    #     st.caption(
    #         "Forward a remote MCP server's port to localhost via SSH. "
    #         "The MCP server must already be running on the remote machine."
    #     )
    #     c1, c2, c3 = st.columns([4, 1, 4])
    #     c1.text_input("SSH Host", key="mcp_ssh_host", placeholder="192.168.1.100")
    #     c2.number_input("SSH Port", value=22, key="mcp_ssh_port")
    #     c3.text_input("SSH User", key="mcp_ssh_user", placeholder="root")
    #     c4, c5 = st.columns(2)
    #     c4.text_input("Password (optional)", type="password", key="mcp_ssh_password")
    #     c5.text_input("SSH Key Path (optional)", key="mcp_ssh_key_path")
    #
    #     col_url, _ = st.columns([1, 1])
    #     with col_url:
    #         st.text_input("MCP Server URL (after tunnel)", key="mcp_server_url")
    #
    #     col_ssh_start, col_ssh_stop, _ = st.columns([1, 1, 4])
    #     with col_ssh_start:
    #         if st.button("Start SSH Tunnel", use_container_width=True, key="btn_mcp_ssh_start"):
    #             from core.mcp_manager import start_mcp_ssh_tunnel
    #             host = st.session_state.get("mcp_ssh_host", "").strip()
    #             port = st.session_state.get("mcp_ssh_port", 22)
    #             user = st.session_state.get("mcp_ssh_user", "").strip()
    #             pwd  = st.session_state.get("mcp_ssh_password", "") or None
    #             key  = st.session_state.get("mcp_ssh_key_path", "").strip() or None
    #             if not host or not user:
    #                 st.session_state["_mcp_msg"] = ("error", "SSH host and user are required.")
    #             else:
    #                 ok, msg = start_mcp_ssh_tunnel(host, port, user, pwd, key)
    #                 st.session_state["_mcp_msg"] = ("success" if ok else "error", msg)
    #             st.rerun()
    #     with col_ssh_stop:
    #         if st.button("Stop SSH Tunnel", use_container_width=True, key="btn_mcp_ssh_stop"):
    #             from core.mcp_manager import stop_mcp_ssh_tunnel
    #             ok, msg = stop_mcp_ssh_tunnel()
    #             st.session_state["_mcp_msg"] = ("success" if ok else "info", msg)
    #             st.rerun()
    #
    #     tunnel_proc = st.session_state.get("mcp_ssh_tunnel_process")
    #     if tunnel_proc is not None:
    #         alive = tunnel_proc.poll() is None
    #         t_state = "up" if alive else "down"
    #         t_label = "Tunnel Active" if alive else "Tunnel Died"
    #         st.markdown(
    #             f'<p style="margin:4px 0 8px">'
    #             f'<span class="status-pill status-pill-{t_state}">{t_label}</span>'
    #             f'</p>',
    #             unsafe_allow_html=True,
    #         )
    # ─────────────────────────────────────────────────────────────────────────

    st.divider()

    # ── Fetch MCP Tools ───────────────────────────────────────────────────────
    col_ft, _ = st.columns([2, 5])
    with col_ft:
        if st.button("Fetch MCP Tools", use_container_width=True):
            _url = st.session_state.get("mcp_server_url", MCP_SERVER_BASE_URL)
            tools = discover_tools(st.session_state["mcp_url"], base_url=_url)
            if tools:
                prev    = st.session_state.get("mcp_tools", {})
                old_keys = set(prev.keys()) - {t["name"] for t in tools}
                for old in old_keys:
                    st.session_state.pop(f"tool_chk_{old}", None)
                st.session_state["mcp_tools"] = {
                    t["name"]: prev.get(t["name"], True)
                    for t in tools
                }
                st.success(f"Found: {', '.join(t['name'] for t in tools)}")
            else:
                st.warning("No tools found — is the MCP server running?")

    tools_dict = st.session_state.get("mcp_tools", {})
    if tools_dict:
        st.write("**Available MCP Tools**")
        from core.mcp_manager import load_tools_from_json
        import os
        mcp_dir     = os.path.dirname(st.session_state.get("mcp_url", ""))
        desc_lookup = {
            t["name"]: t.get("description", "")
            for t in load_tools_from_json(mcp_dir)
        }
        ncols   = min(4, len(tools_dict))
        cols    = st.columns(ncols)
        updated = {}
        for idx, (name, enabled) in enumerate(tools_dict.items()):
            with cols[idx % ncols]:
                updated[name] = st.checkbox(
                    name,
                    value=enabled,
                    key=f"tool_chk_{name}",
                    help=desc_lookup.get(name) or None,
                )
        st.session_state["mcp_tools"] = updated
    else:
        st.info("Click **Fetch MCP Tools** to discover tools from the MCP server.")


def _gguf_model_selector() -> None:
    col_dir, col_scan = st.columns([6, 1])
    with col_dir:
        st.text_input(
            "GGUF Models Directory", key="model_dir",
            help="Root directory scanned recursively — vocab-only files are excluded",
        )
    with col_scan:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Scan", use_container_width=True, key="btn_scan_gguf"):
            found = scan_gguf_models(st.session_state["model_dir"])
            st.session_state["llm_models"] = found
            if found:
                st.success(f"{len(found)} model(s) found")
            else:
                st.warning("No inference GGUF models found")

    models = st.session_state.get("llm_models", [])
    if models:
        labels = [f"{m['name']}  ({m.get('size_gb', '?')} GB)" for m in models]
        names  = [m["name"] for m in models]
        cur    = st.session_state.get("selected_model")
        idx    = names.index(cur) if cur in names else 0
        sel_label = st.selectbox(
            "Select GGUF Model", options=labels, index=idx,
            help="Vocab-only files are filtered out. Size shown next to each model.",
        )
        sel = names[labels.index(sel_label)]
        st.session_state["selected_model"]      = sel
        sel_model = next(m for m in models if m["name"] == sel)
        st.session_state["selected_model_path"] = sel_model["path"]
        st.caption(f"Path: `{sel_model['path']}`  |  Size: `{sel_model.get('size_gb', '?')} GB`")

        url  = st.session_state.get("llm_url", "")
        info = llama_server.get_server_info(url) if llama_server.is_running(url) else None
        if info and info.get("model_path"):
            import os as _os
            running_base  = _os.path.basename(info["model_path"])
            selected_base = _os.path.basename(sel_model["path"])
            if running_base != selected_base:
                st.warning(
                    f"Server has **{running_base}** loaded — "
                    f"**{selected_base}** is selected. Restart to switch."
                )
    else:
        st.info("Click **Scan** to discover GGUF models.")


def _ollama_model_selector() -> None:
    col_btn, _ = st.columns([3, 4])
    with col_btn:
        if st.button("Fetch Ollama Models", use_container_width=True):
            found, err = fetch_ollama_models(st.session_state["llm_url"])
            if found:
                st.session_state["llm_models"] = [
                    {"name": m["name"], "path": m["name"], "size_gb": m["size_gb"]}
                    for m in found
                ]
                st.success(f"{len(found)} model(s) found")
            elif err:
                st.error(err)
            else:
                st.warning("No models returned — Ollama may be running but has no models pulled.")

    models = st.session_state.get("llm_models", [])
    if models:
        labels    = [f"{m['name']}  ({m.get('size_gb', '?')} GB)" for m in models]
        raw_names = [m["name"] for m in models]
        cur       = st.session_state.get("selected_model")
        idx       = raw_names.index(cur) if cur in raw_names else 0
        sel_label = st.selectbox("Select Ollama Model", options=labels, index=idx)
        sel_name  = raw_names[labels.index(sel_label)]
        st.session_state["selected_model"] = sel_name
    else:
        st.info("Click **Fetch Ollama Models** to list available models.")


# ── Metrics Setup ──────────────────────────────────────────────────────────────

_CAT_COLOUR = {
    "Validation":  "#b45309",
    "Tool":        "#c2410c",
    "Content":     "#0e7490",
    "Performance": "#166534",
    "Path":        "#6d28d9",
    "Judge":       "#be123c",
}


def _cat_badge(cat: str) -> str:
    colour = _CAT_COLOUR.get(cat, "#64748b")
    return (
        f'<span style="background:{colour};color:#fff;padding:1px 7px;'
        f'border-radius:2px;font-size:0.68rem;font-weight:700;'
        f'letter-spacing:0.5px;text-transform:uppercase">{cat}</span>'
    )


def _type_badge(type_key: str) -> str:
    """Badge showing the metric type label with a hover tooltip showing the description."""
    info   = METRIC_TYPES.get(type_key, {})
    cat    = info.get("category", "—")
    label  = info.get("label", type_key)
    desc   = info.get("description", "")
    colour = _CAT_COLOUR.get(cat, "#64748b")
    safe_desc = desc.replace('"', '&quot;')
    return (
        f'<span title="{safe_desc}" style="background:{colour};color:#fff;padding:1px 7px;'
        f'border-radius:2px;font-size:0.68rem;font-weight:700;'
        f'letter-spacing:0.5px;cursor:help" data-tooltip="{safe_desc}">'
        f'{label}</span>'
    )


def _on_tool_focus_change() -> None:
    """Callback: sync scenario, validation, fail patterns, and metrics when tool changes."""
    tool     = st.session_state.get("tool_focus", "file_creator")
    scenario = _TOOL_SCENARIOS.get(tool, "Scenario 1 – File Creation")
    _s       = SCENARIOS.get(scenario, {})
    st.session_state["active_scenario"]      = scenario
    st.session_state["validation_command"]   = _s.get("validation_command", "")
    st.session_state["fail_patterns"]        = list(_s.get("fail_patterns", []))
    st.session_state["metrics_matrix"]       = list(_s.get("default_metrics", []))
    st.session_state["_prompts_user_edited"] = False


def _metrics_setup() -> None:
    # ── Tool Focus Selector ────────────────────────────────────────────────────
    st.subheader("Evaluation Tool")
    st.caption(
        "Select the MCP tool this evaluation will focus on. "
        "Validation command, fail patterns, and metrics matrix are automatically configured."
    )
    st.selectbox(
        "Focus Tool",
        options=list(_TOOL_LABELS.keys()),
        format_func=lambda k: _TOOL_LABELS[k],
        key="tool_focus",
        on_change=_on_tool_focus_change,
        help="The tool the AI agent is expected to use in this evaluation scenario.",
    )

    st.divider()

    # ── Validation command ─────────────────────────────────────────────────────
    st.subheader("Validation")
    _tool  = st.session_state.get("tool_focus", "file_creator")
    _vhelp = (
        "Command run after evaluation to verify the task completed. "
        "For nmap: 'nmap 127.0.0.1'. For file creation: 'cat /tmp/test'."
    )
    st.text_input(
        "Validation Command",
        key="validation_command",
        help=_vhelp,
        placeholder="e.g. cat /tmp/test",
    )

    st.write("**Fail Patterns**")
    st.caption("Output strings that indicate failure (checked even when exit code = 0).")
    patterns: list = st.session_state.get("fail_patterns", [])
    col_inp, col_add = st.columns([5, 1])
    with col_inp:
        new_p = st.text_input(
            "New pattern", placeholder='e.g. "file not found"',
            label_visibility="collapsed", key="_new_fail_pattern",
        )
    with col_add:
        if st.button("Add", use_container_width=True, key="btn_add_pattern"):
            p = new_p.strip()
            if p and p not in patterns:
                st.session_state["fail_patterns"] = patterns + [p]
                st.rerun()

    if patterns:
        to_remove = None
        for i, p in enumerate(patterns):
            pc, pd = st.columns([8, 1])
            pc.code(p)
            if pd.button("✕", key=f"del_fp_{i}"):
                to_remove = i
        if to_remove is not None:
            del patterns[to_remove]
            st.session_state["fail_patterns"] = patterns
            st.rerun()

        if st.session_state.get("_confirm_clear_patterns"):
            st.warning("Clear all fail patterns? This cannot be undone.")
            cc1, cc2, _ = st.columns([1, 1, 5])
            if cc1.button("Yes, clear", key="btn_confirm_clear_yes"):
                st.session_state["fail_patterns"] = []
                st.session_state["_confirm_clear_patterns"] = False
                st.rerun()
            if cc2.button("Cancel", key="btn_confirm_clear_no"):
                st.session_state["_confirm_clear_patterns"] = False
                st.rerun()
        else:
            if st.button("Clear All", key="btn_clear_patterns"):
                st.session_state["_confirm_clear_patterns"] = True
                st.rerun()

    st.divider()

    # ── Metrics matrix ─────────────────────────────────────────────────────────
    st.subheader("Metrics Matrix")
    st.caption(
        "Each metric is evaluated against run telemetry after execution. "
        "Inspired by mcp-eval and MCPEval."
    )

    matrix: list = st.session_state.get("metrics_matrix", [])

    if st.session_state.get("_confirm_reset_metrics"):
        st.warning("Reset metrics to scenario defaults? All custom metrics will be lost.")
        cr1, cr2, _ = st.columns([1, 1, 5])
        if cr1.button("Yes, reset", key="btn_confirm_reset_yes"):
            from config.scenarios import SCENARIOS
            active   = st.session_state.get("active_scenario", "")
            defaults = SCENARIOS.get(active, {}).get("default_metrics", [])
            st.session_state["metrics_matrix"] = list(defaults)
            st.session_state["_confirm_reset_metrics"] = False
            st.rerun()
        if cr2.button("Cancel", key="btn_confirm_reset_no"):
            st.session_state["_confirm_reset_metrics"] = False
            st.rerun()
    else:
        col_rst, _ = st.columns([2, 5])
        with col_rst:
            if st.button("Reset to scenario defaults", key="btn_reset_metrics"):
                st.session_state["_confirm_reset_metrics"] = True
                st.rerun()

    # ── Add metric expander ────────────────────────────────────────────────────
    with st.expander("+ Add metric"):
        type_options: list[str] = []
        for cat in CATEGORIES:
            for key, info in METRIC_TYPES.items():
                if info["category"] == cat:
                    type_options.append(f"{cat}: {info['label']}")

        existing_ids = {m.get("id", "") for m in matrix}
        suggested_id = next(
            f"M-{i:03d}" for i in range(1, 999)
            if f"M-{i:03d}" not in existing_ids
        )

        c1, c2, c3 = st.columns([2, 3, 4])
        new_id     = c1.text_input("ID",   value=suggested_id, key="_nm_id")
        new_name   = c2.text_input("Name", placeholder="My Check", key="_nm_name")
        type_label = c3.selectbox("Type",  options=type_options, key="_nm_type")

        selected_type_key = ""
        for key, info in METRIC_TYPES.items():
            if f"{info['category']}: {info['label']}" == type_label:
                selected_type_key = key
                break

        param_values: dict = {}
        if selected_type_key:
            type_info = METRIC_TYPES[selected_type_key]
            if type_info["params"]:
                st.caption(f"*{type_info['description']}*")
                pcols = st.columns(min(3, len(type_info["params"])))
                for i, param in enumerate(type_info["params"]):
                    with pcols[i % 3]:
                        pkey = f"_nm_p_{param['name']}"
                        if param["type"] == "int":
                            param_values[param["name"]] = st.number_input(
                                param["label"], value=int(param.get("default", 0)),
                                step=1, key=pkey,
                            )
                        elif param["type"] == "float":
                            param_values[param["name"]] = st.number_input(
                                param["label"], value=float(param.get("default", 0.0)),
                                step=0.1, format="%.1f", key=pkey,
                            )
                        elif param["type"] == "bool":
                            param_values[param["name"]] = st.checkbox(
                                param["label"],
                                value=bool(param.get("default", True)), key=pkey,
                            )
                        else:
                            param_values[param["name"]] = st.text_input(
                                param["label"],
                                value=str(param.get("default", "")), key=pkey,
                            )

        if st.button("Add Metric", key="btn_add_metric"):
            _errors = []
            _id   = new_id.strip()
            _name = new_name.strip()
            if not _name:
                _errors.append("Name is required.")
            if not _id:
                _errors.append("ID is required.")
            elif not re.match(r"^M-\d{3}$", _id):
                _errors.append("ID must match format M-NNN (e.g. M-001, M-042).")
            elif _id in existing_ids:
                _errors.append(f"ID **{_id}** is already used.")
            if selected_type_key:
                for param in METRIC_TYPES[selected_type_key]["params"]:
                    if param["type"] == "str":
                        val = str(param_values.get(param["name"], "")).strip()
                        if not val:
                            _errors.append(f"Parameter **{param['label']}** is required.")
            if _errors:
                for err in _errors:
                    st.error(err)
            else:
                matrix.append({
                    "id":      _id,
                    "name":    _name,
                    "type":    selected_type_key,
                    "enabled": True,
                    "params":  dict(param_values),
                })
                st.session_state["metrics_matrix"] = matrix
                st.rerun()

    # ── Matrix table ──────────────────────────────────────────────────────────
    if matrix:
        hcols = st.columns([1, 2, 3, 3, 4, 1])
        for lbl, col in zip(["On", "ID", "Name", "Type", "Criterion", "Del"], hcols):
            col.markdown(f"**{lbl}**")
        st.divider()

        to_delete = None
        for i, m in enumerate(matrix):
            rc      = st.columns([1, 2, 3, 3, 4, 1])
            enabled = rc[0].checkbox(
                "Include", value=m.get("enabled", True),
                key=f"me_{i}_{m['id']}", label_visibility="collapsed",
            )
            matrix[i]["enabled"] = enabled
            rc[1].code(m["id"])
            rc[2].write(m["name"])
            rc[3].markdown(_type_badge(m.get("type", "")), unsafe_allow_html=True)
            rc[4].markdown(
                f'<span class="criterion">{format_criterion(m)}</span>',
                unsafe_allow_html=True,
            )
            if rc[5].button("✕", key=f"md_{i}"):
                to_delete = i

        if to_delete is not None:
            del matrix[to_delete]
            st.session_state["metrics_matrix"] = matrix
            st.rerun()

        st.session_state["metrics_matrix"] = matrix
    else:
        st.info("No metrics configured. Click **Reset to scenario defaults** or add one above.")
