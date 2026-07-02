import copy
import uuid
import streamlit as st
from config.defaults import DEFAULT_CONTEXT_SIZE, MIN_CONTEXT_SIZE
from config.scenarios import DEFAULT_SCENARIO
from core.state import init_state, sync_scenario, sync_project
from core.models import scan_gguf_models
from core import llama_server
from core.logsetup import configure_logging
from core.settings_store import load_settings, save_settings
from ui.components import status_pill
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab, batch_tab, comparison_tab
# from ui import caf_tab, target_tab  # CAF/Target tabs hidden — re-enable when CAF bot type is implemented

st.set_page_config(
    page_title="ModelScope",
    layout="wide",
    initial_sidebar_state="expanded",
)

configure_logging()

inject()
# Override any stale ::after rule lingering in the Streamlit module cache
st.markdown(
    "<style>"
    ".spark-title::after{content:none!important;display:none!important}"
    ".spark-title::before{content:none!important;display:none!important}"
    "</style>",
    unsafe_allow_html=True,
)
init_state()
st.session_state.setdefault("batch_queue", [])
st.session_state.setdefault("comparison_models", [])

# ── Load persisted settings on first run only ──────────────────────────────────
if not st.session_state.get("_settings_loaded"):
    _saved = load_settings()
    for _k, _v in _saved.items():
        st.session_state[_k] = _v
    # Normalise: settings written by 2.0 stored 'projects' as a dict; 2.1 uses a list.
    if not isinstance(st.session_state.get("projects"), list):
        st.session_state["projects"] = []
    if "active_scenario" in _saved:
        st.session_state["_last_exec_scenario"] = _saved["active_scenario"]
    # Initialise the step-ID counter above any IDs already saved in projects,
    # so that new steps/commands never get an ID that already exists in the data.
    _max_id = 0
    for _proj in st.session_state.get("projects", []):
        for _step in _proj.get("config", {}).get("startup_commands", []):
            _max_id = max(_max_id, _step.get("_id", 0))
            for _cmd in _step.get("commands", []):
                _max_id = max(_max_id, _cmd.get("_id", 0))
        for _step in _proj.get("config", {}).get("completion_commands", []):
            _max_id = max(_max_id, _step.get("_id", 0))
            for _cmd in _step.get("commands", []):
                _max_id = max(_max_id, _cmd.get("_id", 0))
    if _max_id:
        st.session_state["_step_id_counter"] = _max_id
    st.session_state["_settings_loaded"] = True

# ── Scenario state sync ────────────────────────────────────────────────────────
_active = st.session_state.get("active_scenario", DEFAULT_SCENARIO)
if st.session_state.get("_last_exec_scenario") != _active:
    sync_scenario(_active)

# ── Auto-bootstrap: create a default Bash-Bot project if the list is empty ────
def _make_default_project() -> dict:
    return {
        "id":   "default_bash",
        "name": "Bash Project 1",
        "type": "bash_bot",
        "config": {
            "execution_target": "local",
            "ssh_host": "",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_password": "",
            "ssh_key_path": "",
            "startup_commands": [],
            "bash_timeout": 60,
            "completion_commands": [],
            "validation_commands": [],
            "fail_patterns": [],
            "metrics_matrix": [],
            "validation_sets": [],
            "sudo": False,
        },
    }

if not st.session_state.get("projects") and not st.session_state.get("_show_new_project_dialog"):
    _default = _make_default_project()
    st.session_state["projects"] = [_default]
    st.session_state["active_project_id"] = _default["id"]

# ── Project-change sync ────────────────────────────────────────────────────────
_active_proj_id = st.session_state.get("active_project_id")
if st.session_state.get("_last_active_project_id") != _active_proj_id and _active_proj_id:
    sync_project(_active_proj_id)

# ── No automatic model loading ─────────────────────────────────────────────────
llama_server.poll_ready(st.session_state.get("llm_url", ""))


# ── Undo helpers ──────────────────────────────────────────────────────────────
def _push_undo(snapshot: dict) -> None:
    stack = st.session_state.setdefault("_undo_stack", [])
    stack.append(snapshot)
    if len(stack) > 20:
        st.session_state["_undo_stack"] = stack[-20:]


def _apply_undo() -> None:
    stack = st.session_state.get("_undo_stack", [])
    if not stack:
        return
    snapshot = stack.pop()
    st.session_state["_undo_stack"] = stack
    if snapshot["type"] == "project":
        st.session_state["projects"] = snapshot["projects"]
        st.session_state["active_project_id"] = snapshot["active_project_id"]
        if snapshot["active_project_id"]:
            sync_project(snapshot["active_project_id"])
    elif snapshot["type"] == "cmd":
        st.session_state[snapshot["state_key"]] = snapshot["data"]
    st.rerun()


# ── New Project dialog ─────────────────────────────────────────────────────────
@st.dialog("New Project")
def _show_add_project_dialog() -> None:
    from config.bash_templates import BASH_BOT_TEMPLATES
    name = st.text_input("Project Name", placeholder="My Bash Bot")
    bot_type = st.selectbox(
        "Bot Type",
        options=["Bash-Bot"],
        help="Choose the type of bot for this project. Llama-CLI-Bot and AI-Agent are coming soon.",
    )
    st.caption("Llama-CLI-Bot and AI-Agent are coming soon!")
    _TYPE_MAP = {
        "Bash-Bot": "bash_bot",
    }
    _CONFIG_DEFAULTS = {
        "bash_bot": {
            "execution_target": "local",
            "ssh_host": "", "ssh_port": 22, "ssh_user": "root",
            "ssh_password": "", "ssh_key_path": "", "sudo": False,
            "startup_commands": [], "bash_timeout": 60,
            "completion_commands": [], "validation_commands": [],
            "fail_patterns": [], "metrics_matrix": [], "validation_sets": [],
        },
        "llama_cli_bot": {
            "execution_target": "local",
            "ssh_host": "", "ssh_port": 22, "ssh_user": "root",
            "ssh_password": "", "ssh_key_path": "", "sudo": False,
            "backend": "llama.cpp", "binary_path": "", "model_dir": "",
            "model_name": "", "tokens": 2048,
            "openai_base_url": "", "openai_verify_ssl": True, "openai_api_key": "",
            "mcp_config_path": "", "mcp_servers": [],
            "prompts": [], "commands": [], "steps": [], "timeout": 60,
            "validation_commands": [], "fail_patterns": [], "metrics_matrix": [],
        },
        "ai_agent": {},
    }

    _template_key = "blank"
    if bot_type == "Bash-Bot":
        _TEMPLATE_LABELS = {
            "Blank":                    "blank",
            "File Creator (example)":   "file_creator",
            "Nmap Scanner (example)":   "nmap_scanner",
        }
        _tmpl_label = st.selectbox(
            "Template",
            options=list(_TEMPLATE_LABELS.keys()),
            help="Start from a pre-configured ground-truth example or a blank project.",
            key="dlg_bash_template_sel",
        )
        _template_key = _TEMPLATE_LABELS[_tmpl_label]
        if _template_key != "blank":
            st.caption(
                "File Creator: creates `/tmp/test` with numbers 1–10, then validates content."
                if _template_key == "file_creator" else
                "Nmap Scanner: runs `nmap -F 127.0.0.1`, saves output, then validates scan structure."
            )

    col_create, col_cancel = st.columns(2)
    with col_create:
        if st.button("Create", type="primary", use_container_width=True):
            proj_name  = name.strip() or f"Project {len(st.session_state['projects']) + 1}"
            _type_key  = _TYPE_MAP[bot_type]
            _push_undo({"desc": "create project", "type": "project",
                        "projects": copy.deepcopy(st.session_state.get("projects", [])),
                        "active_project_id": st.session_state.get("active_project_id")})
            if _type_key == "bash_bot" and _template_key != "blank":
                base_cfg = copy.deepcopy(BASH_BOT_TEMPLATES[_template_key])
            else:
                base_cfg = dict(_CONFIG_DEFAULTS.get(_type_key, {}))
            new_proj = {
                "id":     str(uuid.uuid4())[:8],
                "name":   proj_name,
                "type":   _type_key,
                "config": base_cfg,
            }
            st.session_state["projects"].append(new_proj)
            st.session_state["active_project_id"] = new_proj["id"]
            st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ── Trigger New Project dialog when the last project was just deleted ──────────
if st.session_state.pop("_show_new_project_dialog", False):
    _show_add_project_dialog()


# ── Sidebar: project list ──────────────────────────────────────────────────────
_BOT_ICON = {"bash_bot": "💻", "llama_cli_bot": "🦙", "ai_agent": "🤖"}

with st.sidebar:
    st.markdown("## ModelScope")
    st.markdown("### Projects")

    _active_pid = st.session_state.get("active_project_id")
    for _proj in st.session_state.get("projects", []):
        _icon    = _BOT_ICON.get(_proj.get("type", ""), "📁")
        _is_active = _proj["id"] == _active_pid
        _label   = f"{'▶ ' if _is_active else '　'}{_icon} {_proj['name']}"
        _btn_type = "primary" if _is_active else "secondary"
        if st.button(
            _label,
            key=f"proj_btn_{_proj['id']}",
            use_container_width=True,
            type=_btn_type,
        ):
            st.session_state["active_project_id"] = _proj["id"]
            st.rerun()



    if st.button("💾 Save Settings", key="btn_save_settings", use_container_width=True,
                 help="Save current configuration to ~/.modelscope/settings.json"):
        _active_proj = next(
            (p for p in st.session_state.get("projects", []) if p["id"] == st.session_state.get("active_project_id")),
            None,
        )
        if _active_proj is not None:
            if _active_proj.get("type") == "bash_bot":
                config_tab._flush_bash_config(_active_proj)
            elif _active_proj.get("type") == "llama_cli_bot":
                config_tab._flush_llama_cli_config(_active_proj)
        save_settings(st.session_state)
        st.toast("Settings saved!", icon="✅")

    if st.button("＋  New Project", use_container_width=True):
        _show_add_project_dialog()


# ── Brand block ────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='brand-block'>"
    "<h1 class='spark-title'>ModelScope</h1>"
    "<span class='app-subtitle'>LLM &amp; MCP Tool Evaluation Platform</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Status bar — conditioned on active bot type ────────────────────────────────
_active_proj = next(
    (p for p in st.session_state.get("projects", []) if p["id"] == _active_pid),
    None,
)
_active_bot_type = _active_proj.get("type", "bash_bot") if _active_proj else "bash_bot"

if _active_bot_type == "bash_bot":
    _target     = st.session_state.get("bash_execution_target", "local")
    _target_lbl = f"Target: {_target.upper()}"
    _ssh_ok     = (
        _target == "local"
        or bool(st.session_state.get("bash_ssh_host", "").strip())
    )
    _pills = status_pill(_target_lbl, "up" if _ssh_ok else "wait")
    if _active_proj:
        _pills += status_pill(f"Project: {_active_proj['name']}", "up")
    st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)
elif _active_bot_type == "llama_cli_bot":
    _backend = st.session_state.get("llama_cli_backend", "llama.cpp")
    _model   = st.session_state.get("llama_cli_model_name") or "not chosen"
    _target  = st.session_state.get("llama_cli_execution_target", "local")
    _pills = (
        status_pill(f"Model: {_model}", "up" if _model != "not chosen" else "wait")
        + status_pill(f"Backend: {_backend}", "up")
        + status_pill(f"Target: {_target.upper()}", "up")
    )
    if _active_proj:
        _pills += status_pill(f"Project: {_active_proj['name']}", "up")
    st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)
else:
    # LLM-based bots: show the full model status bar
    _backend     = st.session_state.get("backend_type", "llama.cpp")
    _model       = st.session_state.get("selected_model") or "not chosen"
    _running     = st.session_state.get("llama_server_running", False)
    _ctx         = st.session_state.get("context_size", DEFAULT_CONTEXT_SIZE)
    _mcp_on      = st.session_state.get("mcp_running", False)
    _tool_foc    = st.session_state.get("tool_focus", "")
    _src_mode    = st.session_state.get("model_source_mode", "pre_compiled_local")
    _is_remote   = _src_mode == "pre_compiled_remote"
    _process     = st.session_state.get("llama_server_process")
    _crashed     = st.session_state.get("llama_server_crashed", False)

    if _is_remote:
        _srv_state = "up";  _srv_label = f"{_backend}: remote"
    elif _running:
        _srv_state = "up";  _srv_label = f"{_backend}: running"
    elif _backend == "ollama":
        _srv_state = "wait"; _srv_label = f"{_backend}: stopped"
    elif _crashed:
        _srv_state = "down"; _srv_label = f"{_backend}: crashed"
    else:
        _srv_state = "wait"; _srv_label = f"{_backend}: stopped"

    _mod_state   = "up" if _model != "not chosen" else "wait"
    _ctx_state   = "up" if _ctx >= MIN_CONTEXT_SIZE else "wait"
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

# ── Tabs — conditioned on active bot type ─────────────────────────────────────
# Removed tabs (CAF & Target hidden — re-enable when those bot types are implemented):
# tab_target = "🎯  Target"      → target_tab.render()
# tab_caf    = "🤖  CyberAgentFlow" → caf_tab.render()

if _active_bot_type == "bash_bot":
    # Bash-Bot: Config + Execute + Dashboard.
    # Batch Evaluation and Model Comparison are LLM-specific and not shown.
    tab_cfg, tab_exec, tab_dash = st.tabs([
        "⚙  Configuration",
        "▶  Execute",
        "📊  Analytical Dashboard",
    ])
    with tab_cfg:
        config_tab.render()
    with tab_exec:
        execute_tab.render()
    with tab_dash:
        dashboard_tab.render()
else:
    tab_cfg, tab_exec, tab_dash, tab_batch, tab_compare = st.tabs([
        "⚙  Configuration",
        "▶  Execute",
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
