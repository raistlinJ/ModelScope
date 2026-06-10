import streamlit as st
from config.defaults import GGUF_MODELS_DIR, DEFAULT_CONTEXT_SIZE
from config.scenarios import SCENARIOS, DEFAULT_SCENARIO
from core.state import init_state
from core.models import scan_gguf_models
from core import llama_server
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab

_STATUS_PILL = (
    '<span class="status-pill status-pill-{state}" '
    'style="font-size:0.68rem">{label}</span>'
)

st.set_page_config(
    page_title="ModelScope",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject()
init_state()

# ── Scenario state sync — runs BEFORE any widgets are created ─────────────────
# Syncs prompts, validation, fail_patterns, and metrics when scenario changes.
# Guards against overwriting user-edited prompts (fix #10).
_active = st.session_state.get("active_scenario", DEFAULT_SCENARIO)
if st.session_state.get("_last_exec_scenario") != _active:
    _s = SCENARIOS.get(_active, SCENARIOS[DEFAULT_SCENARIO])
    _edited = st.session_state.get("_prompts_user_edited", False)
    if not _edited:
        st.session_state["sys_prompt"]  = _s["system_prompt"]
        st.session_state["user_prompt"] = _s["user_prompt"]
    # Always sync validation / metrics (these aren't free-text authored by user)
    st.session_state["validation_command"] = _s["validation_command"]
    st.session_state["fail_patterns"]      = list(_s["fail_patterns"])
    st.session_state["metrics_matrix"]     = list(_s["default_metrics"])
    st.session_state["_last_exec_scenario"] = _active
    st.session_state["_prompts_user_edited"] = False

# ── No automatic model loading ──────────────────────────────────────────────
# User must explicitly choose a model before any server starts.
# Poll running server only (fix #7: don't auto-start, just check if already running).
llama_server.poll_ready(st.session_state.get("llm_url", ""))

st.markdown(
    "<div class='brand-block'>"
    "<h1 class='spark-title'>ModelScope</h1>"
    "<span class='app-subtitle'>LLM &amp; MCP Tool Evaluation Platform</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Persistent model status bar ────────────────────────────────────────────────
_backend  = st.session_state.get("backend_type", "llama.cpp")
_model    = st.session_state.get("selected_model") or "not chosen"
_running  = st.session_state.get("llama_server_running", False)
_ctx      = st.session_state.get("context_size", DEFAULT_CONTEXT_SIZE)
_mcp_on   = st.session_state.get("mcp_running", False)
_tool_foc = st.session_state.get("tool_focus", "")

_srv_state = "up" if _running else ("wait" if _backend == "ollama" else "down")
_mod_state = "up" if _model != "not chosen" else "wait"

_pills = (
    _STATUS_PILL.format(state=_mod_state,
                        label=f"Model: {_model.split('/')[-1] if '/' in _model else _model}")
    + _STATUS_PILL.format(state=_srv_state,
                          label=f"{_backend}: {'running' if _running else 'stopped'}")
    + _STATUS_PILL.format(state="wait", label=f"ctx: {_ctx:,}")
    + _STATUS_PILL.format(state="up" if _mcp_on else "wait",
                          label=f"MCP: {'on' if _mcp_on else 'off'}")
)
if _tool_foc:
    _pills += _STATUS_PILL.format(state="up", label=f"Tool: {_tool_foc}")

_bar_col, _restart_col = st.columns([8, 1])
with _bar_col:
    st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)
with _restart_col:
    if _backend == "llama.cpp" and st.button(
        "↺ Restart", key="btn_global_restart",
        use_container_width=True,
        help="Stop and restart the llama-server with the current model and context size",
    ):
        llama_server.stop()
        _mp = st.session_state.get("selected_model_path")
        if _mp:
            ok, msg = llama_server.start(_mp, context_size=_ctx)
            st.session_state["_srv_msg"] = ("success" if ok else "error", msg)
        st.rerun()

tab_cfg, tab_exec, tab_dash = st.tabs([
    "⚙  Configuration",
    "▶  Execute Evaluation",
    "📊  Analytical Dashboard",
])

with tab_cfg:
    config_tab.render()

with tab_exec:
    execute_tab.render()

with tab_dash:
    dashboard_tab.render()
