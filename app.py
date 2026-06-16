import streamlit as st
from config.defaults import DEFAULT_CONTEXT_SIZE
from config.scenarios import DEFAULT_SCENARIO
from core.state import init_state, sync_scenario
from core.models import scan_gguf_models
from core import llama_server
from ui.components import status_pill
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab, batch_tab, comparison_tab

st.set_page_config(
    page_title="ModelScope",
    layout="wide",
    initial_sidebar_state="collapsed",
)

inject()
init_state()
st.session_state.setdefault("batch_queue", [])
st.session_state.setdefault("comparison_models", [])

# ── Scenario state sync — runs BEFORE any widgets are created ─────────────────
_active = st.session_state.get("active_scenario", DEFAULT_SCENARIO)
if st.session_state.get("_last_exec_scenario") != _active:
    sync_scenario(_active)

# ── No automatic model loading ──────────────────────────────────────────────
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
    status_pill(f"Model: {_model.split('/')[-1] if '/' in _model else _model}", _mod_state)
    + status_pill(f"{_backend}: {'running' if _running else 'stopped'}", _srv_state)
    + status_pill(f"ctx: {_ctx:,}", "wait")
    + status_pill(f"MCP: {'on' if _mcp_on else 'off'}", "up" if _mcp_on else "wait")
)
if _tool_foc:
    _pills += status_pill(f"Tool: {_tool_foc}", "up")

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

tab_cfg, tab_exec, tab_dash, tab_batch, tab_compare = st.tabs([
    "⚙  Configuration",
    "▶  Execute Evaluation",
    "📊  Analytical Dashboard",
    "🔄  Batch Evaluation",
    "⚖  Model Comparison",
])

with tab_cfg:
    config_tab.render()

with tab_exec:
    execute_tab.render()

with tab_dash:
    dashboard_tab.render()

with tab_batch:
    batch_tab.render()

with tab_compare:
    comparison_tab.render()
