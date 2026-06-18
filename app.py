import streamlit as st
from config.defaults import DEFAULT_CONTEXT_SIZE, MIN_CONTEXT_SIZE
from config.scenarios import DEFAULT_SCENARIO
from core.state import init_state, sync_scenario
from core.models import scan_gguf_models
from core import llama_server
from core.logsetup import configure_logging
from ui.components import status_pill
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab, batch_tab, comparison_tab, caf_tab, target_tab

st.set_page_config(
    page_title="ModelScope",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Configure terminal logging so `streamlit run app.py` surfaces progress and
# errors on stdout (issue #3). Idempotent — safe to call on every rerun.
configure_logging()

inject()
init_state()
st.session_state.setdefault("batch_queue", [])
st.session_state.setdefault("comparison_models", [])

# ── Load persisted settings on first run only (not on every Streamlit rerun) ──
if not st.session_state.get("_settings_loaded"):
    from core.settings_store import load_settings, save_settings  # noqa: F401
    _saved = load_settings()
    for _k, _v in _saved.items():
        st.session_state[_k] = _v
    # Prevent the scenario-sync block from clobbering scenario-derived keys that
    # were just restored (e.g. validation_command, fail_patterns, caf_*).
    if "active_scenario" in _saved:
        st.session_state["_last_exec_scenario"] = _saved["active_scenario"]
    st.session_state["_settings_loaded"] = True

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
_backend     = st.session_state.get("backend_type", "llama.cpp")
_model       = st.session_state.get("selected_model") or "not chosen"
_running     = st.session_state.get("llama_server_running", False)
_ctx         = st.session_state.get("context_size", DEFAULT_CONTEXT_SIZE)
_mcp_on      = st.session_state.get("mcp_running", False)
_tool_foc    = st.session_state.get("tool_focus", "")
_src_mode    = st.session_state.get("model_source_mode", "pre_compiled_local")
_is_remote   = _src_mode == "pre_compiled_remote"

_process  = st.session_state.get("llama_server_process")
_crashed  = st.session_state.get("llama_server_crashed", False)

if _is_remote:
    # Remote server — we don't manage it locally; show a static "remote" pill
    _srv_state = "up"
    _srv_label = f"{_backend}: remote"
elif _running:
    _srv_state = "up"
    _srv_label = f"{_backend}: running"
elif _backend == "ollama":
    _srv_state = "wait"
    _srv_label = f"{_backend}: stopped"
elif _crashed:
    _srv_state = "down"
    _srv_label = f"{_backend}: crashed"
else:
    _srv_state = "wait"
    _srv_label = f"{_backend}: stopped"

_mod_state = "up" if _model != "not chosen" else "wait"
_ctx_state = "up" if _ctx >= MIN_CONTEXT_SIZE else "wait"

_model_label = _model.split("/")[-1] if "/" in _model else _model
_pills = (
    status_pill(f"Model: {_model_label}", _mod_state)
    + status_pill(_srv_label, _srv_state)
    + status_pill(f"ctx: {_ctx:,}", _ctx_state)
    + status_pill(f"MCP: {'on' if _mcp_on else 'off'}", "up" if _mcp_on else "wait")
)
if _is_remote:
    _pills += status_pill("source: remote", "up")
if _tool_foc:
    _pills += status_pill(f"Tool: {_tool_foc}", "up")

_bar_col, _restart_col = st.columns([8, 1])
with _bar_col:
    st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)
with _restart_col:
    # Show Restart only for local llama.cpp — remote servers are not managed here.
    _show_restart = _backend == "llama.cpp" and not _is_remote
    if _show_restart and st.button(
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

tab_cfg, tab_target, tab_exec, tab_caf, tab_dash, tab_batch, tab_compare = st.tabs([
    "⚙  Configuration",
    "🎯  Target",
    "▶  Execute Evaluation",
    "🤖  CyberAgentFlow",
    "📊  Analytical Dashboard",
    "🔄  Batch Evaluation",
    "⚖  Model Comparison",
])

with tab_cfg:
    config_tab.render()

with tab_target:
    target_tab.render()

with tab_exec:
    execute_tab.render()

with tab_caf:
    caf_tab.render()

with tab_dash:
    dashboard_tab.render()

with tab_batch:
    batch_tab.render()

with tab_compare:
    comparison_tab.render()
