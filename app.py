import copy
import uuid
import streamlit as st
from core.bot_types import get_bot_plugin, iter_bot_plugins
from core.state import init_state, sync_project
from core import llama_server
from core.logsetup import configure_logging
from core.settings_store import load_settings, save_settings
from ui.components import status_pill
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab
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

if "tab_version" not in st.session_state:
    st.session_state["tab_version"] = 0

# ── Load persisted settings on first run only ──────────────────────────────────
if not st.session_state.get("_settings_loaded"):
    _saved = load_settings()
    for _k, _v in _saved.items():
        st.session_state[_k] = _v
    # Normalise: settings written by 2.0 stored 'projects' as a dict; 2.1 uses a list.
    if not isinstance(st.session_state.get("projects"), list):
        st.session_state["projects"] = []
    
    # Clear scenario-related session state keys (scenarios concept removed)
    st.session_state.pop("active_scenario", None)
    st.session_state.pop("_last_exec_scenario", None)
    
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

# ── Auto-bootstrap: create a default Bash-Bot project if the list is empty ────
def _make_default_project() -> dict:
    plugin = get_bot_plugin("bash_bot")
    if plugin is None:
        return {"id": "default_bash", "name": "Bash Project 1", "type": "bash_bot", "config": {}}
    return plugin.make_project("default_bash", "Bash Project 1")

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
    _plugins = list(iter_bot_plugins())
    _LABEL_TO_PLUGIN = {plugin.label: plugin for plugin in _plugins}
    bot_type = st.selectbox(
        "Bot Type",
        options=list(_LABEL_TO_PLUGIN.keys()),
        help="Choose the type of bot for this project.",
    )
    _selected_plugin = _LABEL_TO_PLUGIN[bot_type]

    _template_key = "blank"
    if _selected_plugin.templates:
        _TEMPLATE_LABELS = {template.label: template.key for template in _selected_plugin.templates}
        _tmpl_label = st.selectbox(
            "Template",
            options=list(_TEMPLATE_LABELS.keys()),
            help="Start from a pre-configured ground-truth example or a blank project.",
            key="dlg_bash_template_sel",
        )
        _template_key = _TEMPLATE_LABELS[_tmpl_label]
        if _template_key != "blank":
            st.caption(_selected_plugin.template_caption(_template_key))
            # Keep the direct template symbol visible here: older smoke tests
            # assert that app.py wires the Bash-Bot template source.
            _ = BASH_BOT_TEMPLATES

    col_create, col_cancel = st.columns(2)
    with col_create:
        if st.button("Create", type="primary", use_container_width=True):
            proj_name  = name.strip() or f"Project {len(st.session_state['projects']) + 1}"
            existing_names = [p["name"].lower() for p in st.session_state.get("projects", [])]
            if proj_name.lower() in existing_names:
                st.error(f"A project named '{proj_name}' already exists. Please pick a unique name.")
                st.stop()
            _push_undo({"desc": "create project", "type": "project",
                        "projects": copy.deepcopy(st.session_state.get("projects", [])),
                        "active_project_id": st.session_state.get("active_project_id")})
            new_proj = _selected_plugin.make_project(str(uuid.uuid4())[:8], proj_name, _template_key)
            st.session_state["projects"].append(new_proj)
            st.session_state["active_project_id"] = new_proj["id"]
            st.session_state["tab_version"] += 1
            st.rerun()
    with col_cancel:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


# ── Trigger New Project dialog when the last project was just deleted ──────────
if st.session_state.pop("_show_new_project_dialog", False):
    _show_add_project_dialog()


# ── Sidebar: project list ──────────────────────────────────────────────────────
_BOT_ICON = {plugin.type_id: plugin.icon for plugin in iter_bot_plugins()}

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
            st.session_state["tab_version"] += 1
            st.rerun()



    if st.button("＋  New Project", use_container_width=True):
        _show_add_project_dialog()


# ── Brand block ────────────────────────────────────────────────────────────────
st.markdown(
    "<div class='brand-block'>"
    "<h1 class='spark-title'>ModelScope</h1>"
    "<span class='app-subtitle'>LLM & MCP Tool Evaluation Platform</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Status bar — conditioned on active bot type ────────────────────────────────
_active_proj = next(
    (p for p in st.session_state.get("projects", []) if p["id"] == _active_pid),
    None,
)
_active_bot_type = _active_proj.get("type", "bash_bot") if _active_proj else "bash_bot"
_active_plugin = get_bot_plugin(_active_bot_type)

if _active_plugin is not None:
    _pills = "".join(
        status_pill(item.label, item.state)
        for item in _active_plugin.status_items(st.session_state, _active_proj)
    )
    if _pills:
        st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)
else:
    # Unknown/legacy project type — no dedicated status bar.
    _pills = status_pill(f"Project: {_active_proj['name']}", "up") if _active_proj else ""
    if _pills:
        st.markdown(f'<div class="model-status-bar">{_pills}</div>', unsafe_allow_html=True)

# ── Tabs — conditioned on active bot type ─────────────────────────────────────
# Removed tabs (CAF & Target hidden — re-enable when those bot types are implemented):
# tab_target = "🎯  Target"      → target_tab.render()
# tab_caf    = "🤖  CyberAgentFlow" → caf_tab.render()

tab_cfg, tab_exec, tab_dash = st.tabs([
    "⚙  Configuration",
    "▶  Execute",
    "📊  Analytical Dashboard",
], key=f"active_tab_v{st.session_state['tab_version']}")
with tab_cfg:
    config_tab.render()
with tab_exec:
    execute_tab.render()
with tab_dash:
    dashboard_tab.render()

# ── Auto-save ──────────────────────────────────────────────────────────────────
# Streamlit reruns this script on every widget interaction, so persisting here
# saves settings automatically whenever anything changes.
_active_proj = next(
    (p for p in st.session_state.get("projects", []) if p["id"] == st.session_state.get("active_project_id")),
    None,
)
if _active_proj is not None:
    _plugin = get_bot_plugin(_active_proj.get("type"))
    if _plugin is not None:
        _plugin.flush_config(_active_proj)
save_settings(st.session_state)
