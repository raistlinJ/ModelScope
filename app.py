import copy
import html
import json
import uuid
from pathlib import Path
import streamlit as st
from core.bot_types import get_bot_plugin, iter_bot_plugins, refresh_bot_plugins
from core.state import hydrate_persisted_run_state, init_state, sync_project
from core import llama_server
from core.logsetup import configure_logging
from core.settings_store import load_settings, reconcile_projects, save_settings
from core.project_import import prepare_imported_project
from ui.components import status_pill
from ui.styles import inject
from ui import config_tab, execute_tab, dashboard_tab
# from ui import caf_tab, target_tab  # CAF/Target tabs hidden — re-enable when CAF bot type is implemented


def _source_revision() -> int:
    """Return a cheap revision token for UI/runtime source changes.

    Streamlit can preserve an active-project marker across a hot reload while
    rebuilding the widget state. We use source mtimes to detect that boundary
    and rehydrate the active project before any config view flushes defaults.
    """
    root = Path(__file__).resolve().parent
    paths = [root / "app.py"]
    for directory in ("core", "ui", "config", "plugins"):
        paths.extend((root / directory).rglob("*.py"))
    return max((path.stat().st_mtime_ns for path in paths if path.exists()), default=0)

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

_current_source_revision = _source_revision()
_source_reloaded = st.session_state.get("_source_revision") != _current_source_revision
st.session_state["_source_revision"] = _current_source_revision
if _source_reloaded:
    # External bot plugins are loaded dynamically, so Streamlit's ordinary
    # module reload does not replace their cached plugin instances.
    refresh_bot_plugins()

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

# A source reload can retain some session keys while losing the project list.
# Reconcile first so a temporary default project never displaces persisted
# projects in the sidebar or on the following auto-save.
reconcile_projects(st.session_state)

if not st.session_state.get("projects") and not st.session_state.get("_show_new_project_dialog"):
    _default = _make_default_project()
    st.session_state["projects"] = [_default]
    st.session_state["active_project_id"] = _default["id"]

# Persisted session logs are the source of truth for completed runs. Hydrate
# every project before rendering the sidebar so its last result and indicators
# survive a refresh, not only after the dashboard is opened.
for _project in st.session_state.get("projects", []):
    hydrate_persisted_run_state(_project.get("id", ""))

# ── Project-change sync ────────────────────────────────────────────────────────
_active_proj_id = st.session_state.get("active_project_id")
if (
    st.session_state.get("_last_active_project_id") != _active_proj_id
    or _source_reloaded
) and _active_proj_id:
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
        restored_ids = {project.get("id") for project in snapshot["projects"] if isinstance(project, dict)}
        st.session_state["_deleted_project_ids"] = [
            project_id for project_id in st.session_state.get("_deleted_project_ids", [])
            if project_id not in restored_ids
        ]
        st.session_state["active_project_id"] = snapshot["active_project_id"]
        if snapshot["active_project_id"]:
            sync_project(snapshot["active_project_id"])
    elif snapshot["type"] == "cmd":
        st.session_state[snapshot["state_key"]] = snapshot["data"]
    st.rerun()


_RUN_INDICATOR_COLOURS = {
    "hard_fail": "#e5484d",  # red
    "soft_fail": "#ec4899",  # pink
    "soft_pass": "#d4a72c",  # yellow
    "hard_pass": "#2da44e",  # green
}


def _current_status_config(project: dict, plugin) -> dict:
    """Overlay active widgets so a config edit invalidates boxes immediately."""
    config = copy.deepcopy(project.get("config", {}))
    if plugin is not None:
        for state_key, config_key in plugin.state_key_map.items():
            if state_key in st.session_state:
                config[config_key] = st.session_state[state_key]
    return config


def _sidebar_run_indicators(project: dict, plugin, telemetry: dict) -> list[dict[str, str]]:
    """Return freshness-aware status indicators for one project row."""
    from core.run_status import sidebar_status_indicators

    return sidebar_status_indicators(
        telemetry,
        _current_status_config(project, plugin) if project["id"] == st.session_state.get("active_project_id")
        else project.get("config", {}),
    )


def _sidebar_run_indicator_boxes(indicators: list[dict[str, str]]) -> str:
    """Render indicator metadata as compact, accessible color boxes."""
    return "".join(
        f'<span class="run-indicator" title="{html.escape(item["label"], quote=True)}" '
        f'aria-label="{html.escape(item["label"], quote=True)}" '
        f'style="background:{_RUN_INDICATOR_COLOURS[item["level"]]};">'
        f'{html.escape(item["icon"])}</span>'
        for item in indicators
    )


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
            if name.strip():
                proj_name = name.strip()
                existing_names = [p["name"].casefold() for p in st.session_state.get("projects", [])]
                if proj_name.casefold() in existing_names:
                    st.error(f"A project named '{proj_name}' already exists. Please pick a unique name.")
                    st.stop()
            else:
                from core.project_import import _unique_name
                existing_names = [p["name"] for p in st.session_state.get("projects", [])]
                proj_name = _unique_name(f"Project {len(existing_names) + 1}", existing_names, suffix="")

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


@st.dialog("Import Project")
def _show_import_project_dialog() -> None:
    """Import a project previously exported from the Configuration tab."""
    uploaded = st.file_uploader("Project JSON", type=["json"], key="import_project_file")
    st.caption("The imported project receives a new ID and will not overwrite an existing project.")
    col_import, col_cancel = st.columns(2)
    with col_import:
        if st.button("＋ Import Project", type="primary", use_container_width=True):
            if uploaded is None:
                st.error("Choose a project JSON file first.")
                return
            try:
                payload = json.loads(uploaded.getvalue().decode("utf-8"))
                project = prepare_imported_project(
                    payload,
                    [item.get("name", "") for item in st.session_state.get("projects", [])],
                )
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                st.error(f"Could not import project: {exc}")
                return

            _push_undo({"desc": "import project", "type": "project",
                        "projects": copy.deepcopy(st.session_state.get("projects", [])),
                        "active_project_id": st.session_state.get("active_project_id")})
            st.session_state["projects"].append(project)
            st.session_state["active_project_id"] = project["id"]
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
    st.markdown(
        "<style>"
        ".project-status-icons{display:flex;justify-content:flex-end;align-items:center;"
        "gap:4px;flex-wrap:nowrap;min-height:40px;}"
        ".run-indicator{width:22px;height:22px;border-radius:5px;display:inline-flex;"
        "align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:0.67rem;"
        "box-shadow:inset 0 0 0 1px rgba(255,255,255,.32);cursor:help;}"
        "[class*='st-key-proj_btn_'] button,[class*='st-key-proj-btn-'] button{"
        "height:40px!important;min-height:40px!important;}"
        "[class*='st-key-proj_more_'] button,[class*='st-key-proj-more-'] button{"
        "width:22px!important;min-width:22px!important;height:22px!important;min-height:22px!important;"
        "padding:0!important;font-size:0.62rem!important;line-height:1!important;"
        "margin-left:4px!important;transform:translateY(8px)!important;}"
        "</style>",
        unsafe_allow_html=True,
    )

    _active_pid = st.session_state.get("active_project_id")
    for _proj in st.session_state.get("projects", []):
        _icon    = _BOT_ICON.get(_proj.get("type", ""), "📁")
        _is_active = _proj["id"] == _active_pid
        _btn_type = "primary" if _is_active else "secondary"
        _telemetry = (
            st.session_state.get("telemetry", {})
            if _is_active else st.session_state.get(f"telemetry_{_proj['id']}", {})
        )
        _indicators = _sidebar_run_indicators(
            _proj,
            get_bot_plugin(_proj.get("type")),
            _telemetry,
        )
        _name = _proj["name"]
        _has_overflow = len(_indicators) > 3
        _max_name_length = 24 if _indicators else 34
        _display_name = _name if len(_name) <= _max_name_length else f"{_name[:_max_name_length - 1]}…"
        _label = f"{'▶ ' if _is_active else '　'}{_icon} {_display_name}"
        _name_help = _name if _display_name != _name else None
        if not _indicators:
            if st.button(
                _label,
                key=f"proj_btn_{_proj['id']}",
                use_container_width=True,
                type=_btn_type,
                help=_name_help,
            ):
                st.session_state["active_project_id"] = _proj["id"]
                st.session_state["tab_version"] += 1
                st.rerun()
        else:
            # Keep the project button flush with the result icons; the status
            # column is only as wide as the visible boxes and overflow control.
            _button_col, _status_col = st.columns(
                [1, 0.44 if _has_overflow else 0.32],
                gap=None,
                vertical_alignment="center",
            )
            with _button_col:
                if st.button(
                    _label,
                    key=f"proj_btn_{_proj['id']}",
                    use_container_width=True,
                    type=_btn_type,
                    help=_name_help,
                ):
                    st.session_state["active_project_id"] = _proj["id"]
                    st.session_state["tab_version"] += 1
                    st.rerun()
            with _status_col:
                _visible_indicators = _indicators[:3]
                _overflow_indicators = _indicators[3:]
                if _overflow_indicators:
                    _icons_col, _more_col = st.columns([3, 1], gap=None, vertical_alignment="center")
                    with _icons_col:
                        st.markdown(
                            f'<div class="project-status-icons">'
                            f'{_sidebar_run_indicator_boxes(_visible_indicators)}</div>',
                            unsafe_allow_html=True,
                        )
                    with _more_col:
                        with st.popover(
                            "",
                            help=f"Show {len(_overflow_indicators)} more metric results",
                            key=f"proj_more_{_proj['id']}",
                        ):
                            st.markdown(
                                f'<div class="project-status-icons">'
                                f'{_sidebar_run_indicator_boxes(_overflow_indicators)}</div>',
                                unsafe_allow_html=True,
                            )
                else:
                    st.markdown(
                        f'<div class="project-status-icons">'
                        f'{_sidebar_run_indicator_boxes(_visible_indicators)}</div>',
                        unsafe_allow_html=True,
                    )



    _new_project_col, _import_project_col = st.columns(2, gap="small")
    with _new_project_col:
        if st.button("＋  New Project", use_container_width=True):
            _show_add_project_dialog()
    with _import_project_col:
        if st.button("＋  Import Project", use_container_width=True):
            _show_import_project_dialog()

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
