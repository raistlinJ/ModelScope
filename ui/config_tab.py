import copy
import html
import json
import math
import re
import uuid
import pandas as pd
import streamlit as st
from core.bot_types import get_bot_plugin
from core import llama_server
from config.defaults import MCP_CONFIG_PATH
from core.mcp_catalog import load_mcp_tool_config, merge_mcp_tool_selections




def _push_undo(snapshot: dict) -> None:
    stack = st.session_state.setdefault("_undo_stack", [])
    stack.append(snapshot)
    if len(stack) > 20:
        st.session_state["_undo_stack"] = stack[-20:]


def _export_project_json(project: dict) -> str:
    """Return a complete, portable snapshot of the active project.

    Widgets are a working copy of the project's config, so flush the owning
    plugin first. This prevents Export from serializing a stale config when a
    user has just changed a runtime field such as model name.
    """
    plugin = get_bot_plugin(project.get("type", ""))
    if plugin is not None:
        plugin.flush_config(project)

    # Same secret set the settings store strips before writing to disk.
    from core.settings_store import NESTED_SENSITIVE
    bot_type = project.get("type", "bash_bot")
    plugin = get_bot_plugin(bot_type)
    if plugin is not None:
        plugin.flush_config(project)
    proj_copy = copy.deepcopy(project)
    for k in NESTED_SENSITIVE:
        proj_copy.get("config", {}).pop(k, None)
    return json.dumps(proj_copy, indent=2)


def _strip_step_ids(config: dict) -> None:
    """Remove _id fields from all steps/commands in a project config so that
    the duplicate receives fresh IDs and never shares them with the original."""
    for list_key in ("startup_commands", "completion_commands"):
        for step in config.get(list_key, []):
            step.pop("_id", None)
            for cmd in step.get("commands", []):
                cmd.pop("_id", None)


def _bot_prefix_from_state_key(state_key: str) -> str:
    if state_key.startswith("llama_server"):
        return "llama_server"
    if state_key.startswith("llama_cli"):
        return "llama_cli"
    if state_key.startswith("caf_cli"):
        return "caf_cli"
    return "bash"


def _validation_bot_supports_prompts(bot_type: str) -> bool:
    return bot_type in ("llama_cli", "llama_server", "caf_cli")


def _validation_bot_prompt_title(bot_type: str) -> str:
    if bot_type == "llama_server":
        return "Configured LLAMA-SERVER LLM"
    if bot_type == "llama_cli":
        return "Configured LLAMA-CLI LLM"
    if bot_type == "caf_cli":
        return "Configured CYBERAGENTFLOW CLI"
    return "Configured LLM"


def _duplicate_project(project_id: str) -> None:
    projects = st.session_state.get("projects", [])
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj:
        return
        
    # Flush latest UI state into proj before duplicating so we don't miss just-edited values
    bot_type = proj.get("type", "bash_bot")
    plugin = get_bot_plugin(bot_type)
    if plugin is not None:
        plugin.flush_config(proj)

    new_proj = copy.deepcopy(proj)
    new_proj["id"] = str(uuid.uuid4())[:8]
    
    from core.project_import import _unique_name
    existing_names = [p["name"] for p in projects]
    new_proj["name"] = _unique_name(proj["name"], existing_names, suffix="copy")
    
    _strip_step_ids(new_proj.get("config", {}))
    _push_undo({"desc": "duplicate project", "type": "project",
                "projects": copy.deepcopy(projects),
                "active_project_id": st.session_state.get("active_project_id")})
    projects.append(new_proj)
    st.session_state["projects"] = projects
    st.session_state["active_project_id"] = new_proj["id"]
    st.rerun()


@st.dialog("Delete Project")
def _show_delete_project_dialog(project_id: str) -> None:
    proj = next((p for p in st.session_state.get("projects", []) if p["id"] == project_id), None)
    if not proj:
        st.rerun()
        return
    st.warning(f"Permanently delete **{proj['name']}**?")
    _, c1, c2 = st.columns([2, 1, 1.5])
    with c1:
        if st.button("Delete", type="primary", use_container_width=True):
            bot_type = proj.get("type", "bash_bot")
            plugin = get_bot_plugin(bot_type)
            if plugin is not None:
                plugin.flush_config(proj)

            projects = st.session_state.get("projects", [])
            _push_undo({"desc": f"delete '{proj['name']}'", "type": "project",
                        "projects": copy.deepcopy(projects),
                        "active_project_id": st.session_state.get("active_project_id")})
            remaining = [p for p in projects if p["id"] != project_id]
            deleted = set(st.session_state.get("_deleted_project_ids", []))
            deleted.add(project_id)
            st.session_state["_deleted_project_ids"] = list(deleted)
            st.session_state["projects"] = remaining
            if remaining:
                st.session_state["active_project_id"] = remaining[0]["id"]
            else:
                st.session_state["active_project_id"] = None
                st.session_state["_show_new_project_dialog"] = True
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


@st.dialog("Rename Project")
def _show_rename_project_dialog(project_id: str) -> None:
    proj = next((p for p in st.session_state.get("projects", []) if p["id"] == project_id), None)
    if not proj:
        st.rerun()
        return
    new_name = st.text_input("New name", value=proj["name"], key="_rename_proj_input")
    _, c1, c2 = st.columns([2, 1, 1.5])
    with c1:
        if st.button("Rename", type="primary", use_container_width=True):
            name_stripped = new_name.strip()
            if not name_stripped:
                st.error("Name cannot be empty.")
                return
            
            projects = st.session_state.get("projects", [])
            existing_names = [p["name"].casefold() for p in projects if p["id"] != project_id]
            if name_stripped.casefold() in existing_names:
                st.error(f"A project named '{name_stripped}' already exists. Please pick a unique name.")
                return

            bot_type = proj.get("type", "bash_bot")
            plugin = get_bot_plugin(bot_type)
            if plugin is not None:
                plugin.flush_config(proj)

            _push_undo({"desc": f"rename '{proj['name']}'", "type": "project",
                        "projects": copy.deepcopy(projects),
                        "active_project_id": st.session_state.get("active_project_id")})
            for p in projects:
                if p["id"] == project_id:
                    p["name"] = name_stripped
                    break
            st.session_state["projects"] = projects
            st.rerun()
    with c2:
        if st.button("Cancel", use_container_width=True):
            st.rerun()


def _get_active_project() -> dict | None:
    """Return the active project dict from session state, or None."""
    pid = st.session_state.get("active_project_id")
    for p in st.session_state.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def render() -> None:
    proj = _get_active_project()
    if proj is not None:
        c_head, c_ren, c_dup, c_exp, c_del = st.columns([3, 0.8, 1, 0.8, 0.6])
        pid = proj["id"]
        with c_head:
            st.header("Configuration", anchor=False)
        with c_ren:
            if st.button("✏ Rename", key=f"btn_ren_{pid}", use_container_width=True):
                _show_rename_project_dialog(pid)
        with c_dup:
            if st.button("⧉ Duplicate", key=f"btn_dup_{pid}", use_container_width=True):
                _duplicate_project(pid)
        with c_exp:
            st.download_button(
                "⬇ Export", data=_export_project_json(proj),
                file_name=f"{proj['name'].replace(' ', '_')}.json",
                mime="application/json", key=f"btn_export_{pid}",
                use_container_width=True,
            )
        with c_del:
            if st.button("🗑", key=f"btn_del_{pid}", use_container_width=True,
                         help="Delete this project", type="secondary"):
                _show_delete_project_dialog(pid)
    else:
        st.header("Configuration", anchor=False)
    # Danger styling for Delete buttons — scoped to project action headers.
    # Uses the .st-key-{key} container class emitted by Streamlit ≥1.38.
    st.markdown(
        """
        <style>
        /* Streamlit ≥1.38 uses st-key-{key} where underscores are preserved.
           Include both variants as a safety net across minor versions. */
        [class*="st-key-btn_del_"] button,
        [class*="st-key-btn-del-"] button {
            border-color: #c0392b !important;
            color:         #c0392b !important;
        }
        [class*="st-key-btn_del_"] button:hover,
        [class*="st-key-btn-del-"] button:hover {
            background-color: #c0392b !important;
            color:            #ffffff !important;
        }
        /* Secondary tabs (nested) — visually lighter than primary tabs */
        [data-testid="stTabs"] [data-testid="stTabs"] button[role="tab"] {
            font-size: 0.68rem !important;
            padding: 5px 12px !important;
            text-transform: none !important;
            letter-spacing: 0 !important;
        }
        [data-testid="stTabs"] [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
            font-weight: 600 !important;
        }
        /* Accepted Output Checks popover panel — a lighter, clearly bordered
           panel so it reads as a distinct floating layer instead of blending
           into the (near-identical dark) Validation Set modal behind it. */
        div[data-testid="stPopoverBody"] {
            background-color: #1c2333 !important;
            border: 1px solid rgba(45, 212, 191, 0.55) !important;
            box-shadow: 0 6px 16px rgba(0, 0, 0, 0.6) !important;
            padding: 11px !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    proj = _get_active_project()
    if proj is None:
        st.info("No project selected. Use the sidebar to add or select a project.")
        return

    bot_type = proj.get("type", "bash_bot")
    plugin = get_bot_plugin(bot_type)
    if plugin is not None:
        plugin.render_config(proj)
    else:
        st.info(
            f"**{proj['name']}** ({bot_type}) — configuration coming soon."
        )


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


# ── Bash-Bot configuration ─────────────────────────────────────────────────────

def _clean_steps(steps_list):
    cleaned = []
    for step in steps_list:
        if not isinstance(step, dict):
            continue
        new_step = copy.deepcopy(step)
        new_step["commands"] = [
            c for c in new_step.get("commands", []) 
            if isinstance(c, dict) and (c.get("command", "").strip() or c.get("type") == "prompt")
        ]
        if new_step["commands"] or new_step.get("delay_seconds", 0) > 0:
            cleaned.append(new_step)
    return cleaned

def _flush_bash_config(project: dict) -> None:
    """Write flat bash_* working keys back into the project's config bundle."""
    get_bot_plugin(project.get("type", "bash_bot")).flush_mapped_config(project)

    project["config"].update({
        "execution_target":  st.session_state.get("bash_execution_target", "local"),
        "pct_vmid":          st.session_state.get("bash_pct_vmid", ""),
        "ssh_host":          st.session_state.get("bash_ssh_host", ""),
        "ssh_port":          st.session_state.get("bash_ssh_port", 22),
        "ssh_user":          st.session_state.get("bash_ssh_user", "root"),
        "ssh_password":      st.session_state.get("bash_ssh_password", ""),
        "ssh_key_path":      st.session_state.get("bash_ssh_key_path", ""),
        "startup_commands":  _clean_steps(st.session_state.get("bash_startup_commands", [])),
        "bash_timeout":      st.session_state.get("bash_timeout", 60),
        "completion_commands": _clean_steps(st.session_state.get("bash_completion_commands", [])),
        "validation_commands": st.session_state.get("bash_validation_commands", []),
        "fail_patterns":     st.session_state.get("bash_fail_patterns", []),
        "metrics_matrix":    st.session_state.get("bash_metrics_matrix", []),
        "validation_sets":   st.session_state.get("bash_validation_sets", []),
        "sudo":              st.session_state.get("bash_sudo", False),
        "sudo_password":     (
            st.session_state.get("bash_sudo_password", "") or
            (st.session_state.get("bash_ssh_password", "") if st.session_state.get("bash_execution_target", "local") in ("ssh", "pct") else "")
        ) if st.session_state.get("bash_sudo") else "",
        "llm_helper_backend": st.session_state.get("bash_llm_helper_backend", "OpenAI-Compatible"),
        "llm_helper_openai_url": st.session_state.get("bash_llm_helper_openai_url", ""),
        "llm_helper_openai_apikey": st.session_state.get("bash_llm_helper_openai_apikey", ""),
        "llm_helper_openai_verify_ssl": st.session_state.get("bash_llm_helper_openai_verify_ssl", True),
        "llm_helper_ollama_url": st.session_state.get("bash_llm_helper_ollama_url", "http://localhost:11434"),
        "llm_helper_model": st.session_state.get("bash_llm_helper_model", ""),
        "llm_helper_enabled": st.session_state.get("bash_llm_helper_enabled", False),
        "llm_helper_openai_models": st.session_state.get("bash_llm_helper_openai_models", []),
        "llm_helper_ollama_models": st.session_state.get("bash_llm_helper_ollama_models", []),
        "llm_helper_mcp_enabled": st.session_state.get("bash_llm_helper_mcp_enabled", False),
        "llm_helper_mcp_config_path": st.session_state.get("bash_llm_helper_mcp_config_path", MCP_CONFIG_PATH),
        "llm_helper_mcp_tools": st.session_state.get("bash_llm_helper_mcp_tools", []),
        "llm_helper_mcp_strict": st.session_state.get("bash_llm_helper_mcp_strict", False),
    })
    from core.settings_store import save_settings
    save_settings(st.session_state)


def _addable_list(
    state_key: str,
    placeholder: str,
    input_key: str,
    add_key: str,
    del_key_prefix: str,
) -> None:
    """Reusable addable/removable list widget (same pattern as fail_patterns)."""
    items: list = st.session_state.get(state_key, [])
    col_inp, col_add = st.columns([5, 1])
    with col_inp:
        new_val = st.text_input(
            "Command", placeholder=placeholder,
            label_visibility="collapsed", key=input_key,
        )
    with col_add:
        if st.button("Add", use_container_width=True, key=add_key):
            v = new_val.strip()
            if v:
                st.session_state[state_key] = items + [v]
                st.rerun()
    if items:
        to_remove = None
        for i, item in enumerate(items):
            ic, id_ = st.columns([8, 1])
            ic.code(item)
            if id_.button("✕", key=f"{del_key_prefix}_{i}"):
                to_remove = i
        if to_remove is not None:
            items.pop(to_remove)
            st.session_state[state_key] = items
            st.rerun()


def _next_step_id() -> int:
    """Return a monotonically increasing ID stored in session state (used as stable widget key)."""
    st.session_state["_step_id_counter"] = st.session_state.get("_step_id_counter", 0) + 1
    return st.session_state["_step_id_counter"]


def _coerce_steps(raw: list) -> list:
    """
    Normalise a startup/completion command list to the step format.
    Handles legacy List[str] entries and dicts with missing/malformed fields.
    """
    if not raw:
        return []
    result = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            result.append({
                "delay_seconds": 0.0,
                "commands": [{
                    "command":        text,
                    "enabled":        True,
                    "long_running":   False,
                    "timeout_seconds": 60,
                }],
            })
        elif isinstance(item, dict):
            cmds = []
            for c in item.get("commands", []):
                if isinstance(c, str):
                    cmds.append({
                        "command":        c,
                        "enabled":        True,
                        "long_running":   False,
                        "timeout_seconds": 60,
                    })
                elif isinstance(c, dict):
                    entry = {
                        "type":           str(c.get("type", "command")),
                        "enabled":        bool(c.get("enabled", True)),
                    }
                    if entry["type"] == "prompt":
                        entry["system_prompt"] = str(c.get("system_prompt", ""))
                        entry["user_prompt"] = str(c.get("user_prompt", ""))
                        entry["preserve_context"] = bool(c.get("preserve_context", True))
                    else:
                        entry["command"] = str(c.get("command", ""))
                        entry["long_running"] = bool(c.get("long_running", False))
                        entry["timeout_seconds"] = int(c.get("timeout_seconds", 60))
                        
                    if "expected_output_type" in c:
                        entry["expected_output_type"] = str(c["expected_output_type"])
                    if "expected_output" in c:
                        entry["expected_output"] = str(c["expected_output"])
                    if "checks" in c and isinstance(c["checks"], list):
                        entry["checks"] = copy.deepcopy(c["checks"])
                    if "_id" in c:
                        entry["_id"] = c["_id"]
                    cmds.append(entry)
            step = {
                "delay_seconds": float(item.get("delay_seconds", 0.0)),
                "commands":      cmds,
            }
            if "_id" in item:
                step["_id"] = item["_id"]
            result.append(step)
    return result


def _ensure_step_ids(steps: list) -> list:
    """Assign a stable _id to any step or command that does not yet have one.

    Also deduplicates IDs within each list level so that data corruption
    (e.g. from an app-restart counter reset) never produces duplicate widget keys.
    """
    # Advance the counter past every existing ID so that newly-assigned IDs
    # never collide with IDs that were persisted in a previous session.
    max_existing = st.session_state.get("_step_id_counter", 0)
    for _s in steps:
        if isinstance(_s, dict):
            _sid = _s.get("_id", 0)
            if isinstance(_sid, int):
                max_existing = max(max_existing, _sid)
            for _c in _s.get("commands", []):
                if isinstance(_c, dict):
                    _cid = _c.get("_id", 0)
                    if isinstance(_cid, int):
                        max_existing = max(max_existing, _cid)
    st.session_state["_step_id_counter"] = max_existing

    seen_step_ids: set = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "_id" not in step or step["_id"] in seen_step_ids:
            step["_id"] = _next_step_id()
        seen_step_ids.add(step["_id"])
        seen_cmd_ids: set = set()
        for cmd in step.get("commands", []):
            if isinstance(cmd, dict):
                if "_id" not in cmd or cmd["_id"] in seen_cmd_ids:
                    cmd["_id"] = _next_step_id()
                seen_cmd_ids.add(cmd["_id"])
    return steps


def _step_commands_preview(commands: list, max_len: int = 35) -> str:
    """Return a collapsed step preview for command and prompt entries."""
    for cmd in commands:
        if isinstance(cmd, str):
            text = cmd.strip()
        elif isinstance(cmd, dict) and cmd.get("type", "command") == "prompt":
            text = (cmd.get("user_prompt") or cmd.get("system_prompt") or "").strip()
            text = f"Prompt: {text}" if text else "Prompt"
        elif isinstance(cmd, dict):
            text = cmd.get("command", "").strip()
        else:
            text = ""

        if text:
            return (text[:max_len] + "…") if len(text) > max_len else text
    return ""


_VALIDATION_CHECK_TYPES = ("Ignore", "Regex", "Exact String", "No output")
_VALIDATION_CHECK_TYPE_ICONS = {
    "Ignore": "⊘",
    "Regex": ".*",
    "Exact String": "≡",
    "No output": "∅",
}
_VALIDATION_CHECK_TYPE_DESCRIPTIONS = {
    "Ignore": "Ignore command output.",
    "Regex": "Pass when stdout or stderr matches the regex.",
    "Exact String": "Pass when stdout exactly matches the expected value.",
    "No output": "Pass only when stdout and stderr are empty.",
}


def _validation_check_type_label(check_type: str) -> str:
    return _VALIDATION_CHECK_TYPE_ICONS.get(check_type, "?")


def _validation_check_type_help() -> str:
    return "\n".join(
        f"{_validation_check_type_label(check_type)} {check_type}: {_VALIDATION_CHECK_TYPE_DESCRIPTIONS[check_type]}"
        for check_type in _VALIDATION_CHECK_TYPES
    )


def _validation_check_type_tooltip(check_type: str) -> str:
    description = _VALIDATION_CHECK_TYPE_DESCRIPTIONS.get(check_type, "Unknown check type.")
    return f"{check_type}: {description}"


def _validation_check_type_icon_html(check_type: str) -> str:
    icon = html.escape(_validation_check_type_label(check_type))
    tooltip = html.escape(_validation_check_type_tooltip(check_type), quote=True)
    return (
        f'<span title="{tooltip}" '
        'style="display:inline-flex;align-items:center;justify-content:center;'
        'min-width:1.65rem;height:1.45rem;border:1px solid rgba(49, 51, 63, 0.22);'
        'border-radius:6px;font-weight:700;font-size:0.9rem;cursor:help;">'
        f"{icon}</span>"
    )


def _validation_check_type_legend_html() -> str:
    icons = "".join(_validation_check_type_icon_html(check_type) for check_type in _VALIDATION_CHECK_TYPES)
    return (
        '<div style="display:flex;gap:0.35rem;margin:0.15rem 0 0.65rem 0;align-items:center;">'
        f"{icons}</div>"
    )


def _clear_llm_helper_model_selection(pfx: str, backend: str) -> None:
    st.session_state[f"{pfx}_llm_helper_model"] = ""
    if backend == "Ollama":
        st.session_state[f"{pfx}_llm_helper_ollama_models"] = []
        st.session_state[f"{pfx}_llm_helper_ollama_model_manual_widget"] = ""
        st.session_state.pop(f"{pfx}_llm_helper_ollama_model_sel", None)
    else:
        st.session_state[f"{pfx}_llm_helper_openai_models"] = []
        st.session_state[f"{pfx}_llm_helper_openai_model_manual_widget"] = ""
        st.session_state.pop(f"{pfx}_llm_helper_openai_model_sel", None)


def _clear_llama_openai_model_selection() -> None:
    st.session_state["llama_cli_openai_models"] = []
    st.session_state["llama_cli_model_name"] = ""
    st.session_state["_llama_openai_model_manual"] = ""
    st.session_state.pop("_llama_openai_model_sel", None)


def _normalize_validation_checks(cmd: dict) -> list[dict]:
    checks = []
    raw_checks = cmd.get("checks", [])
    if isinstance(raw_checks, list):
        if "checks" in cmd and not raw_checks:
            return []
        for check in raw_checks:
            if not isinstance(check, dict):
                continue
            check_type = str(check.get("expected_output_type", check.get("type", "Ignore")))
            if check_type not in _VALIDATION_CHECK_TYPES:
                check_type = "Ignore"
            normalized = {
                "expected_output_type": check_type,
                "expected_output": str(check.get("expected_output", check.get("value", ""))),
            }
            if "_id" in check:
                normalized["_id"] = check["_id"]
            checks.append(normalized)

    if checks:
        return checks

    legacy_type = str(cmd.get("expected_output_type", "Ignore"))
    if legacy_type not in _VALIDATION_CHECK_TYPES:
        legacy_type = "Ignore"
    return [{
        "expected_output_type": legacy_type,
        "expected_output": str(cmd.get("expected_output", "")),
    }]


def _actionable_validation_checks(cmd: dict) -> list[dict]:
    return [
        check for check in _normalize_validation_checks(cmd)
        if check.get("expected_output_type", "Ignore") != "Ignore"
    ]


def _validation_checks_button_label(cmd: dict) -> str:
    count = len(_actionable_validation_checks(cmd))
    if count == 0:
        return "Output ignored"
    return f"{count} check" if count == 1 else f"{count} checks"


def _sync_validation_checks(cmd: dict, checks: list[dict]) -> None:
    cmd["checks"] = copy.deepcopy(checks)
    first = checks[0] if checks else {"expected_output_type": "Ignore", "expected_output": ""}
    cmd["expected_output_type"] = first.get("expected_output_type", "Ignore")
    cmd["expected_output"] = first.get("expected_output", "")


def _validation_checks_summary(cmd: dict, max_len: int = 70) -> str:
    actionable = _actionable_validation_checks(cmd)
    if not actionable:
        return "Ignore output"
    parts = []
    for check in actionable:
        check_type = check.get("expected_output_type", "Ignore")
        expected = check.get("expected_output", "")
        if check_type == "No output":
            parts.append("No output")
        else:
            parts.append(f"{check_type}: {expected}")
    summary = " OR ".join(parts)
    return (summary[:max_len] + "...") if len(summary) > max_len else summary


def _render_validation_checks_control(cmd: dict, key_prefix: str, disabled: bool = False) -> None:
    checks = _normalize_validation_checks(cmd)
    _sync_validation_checks(cmd, checks)
    seen_check_ids: set = set()
    for check in cmd["checks"]:
        # A duplicated persisted check ID creates duplicate Streamlit widget
        # keys inside this popover. Treat it like any missing editor ID.
        if "_id" not in check or check["_id"] in seen_check_ids:
            check["_id"] = _next_step_id()
        seen_check_ids.add(check["_id"])

    with st.popover(_validation_checks_button_label(cmd), use_container_width=True, disabled=disabled):
        st.markdown("**Accepted Output Checks**")
        st.caption("The command passes when any one check matches.")
        st.markdown(_validation_check_type_legend_html(), unsafe_allow_html=True)

        def _add_check() -> None:
            cmd["checks"].append({
                "_id": _next_step_id(),
                "expected_output_type": "Regex",
                "expected_output": "",
            })
            _sync_validation_checks(cmd, cmd["checks"])

        def _remove_check(check_id: int) -> None:
            cmd["checks"] = [c for c in cmd["checks"] if c.get("_id") != check_id]
            _sync_validation_checks(cmd, cmd["checks"])

        if st.button(
            "＋ Add Accepted Output",
            key=f"{key_prefix}_add",
            use_container_width=True,
            on_click=_add_check,
        ):
            pass

        for idx, check in enumerate(cmd["checks"]):
            if "_id" not in check:
                check["_id"] = _next_step_id()
            check_id = check["_id"]
            with st.container(border=True):
                col_type, col_value, col_remove = st.columns([0.9, 4.0, 1.0])
                with col_type:
                    type_key = f"{key_prefix}_{check_id}_type"
                    current_type = check.get("expected_output_type", "Ignore")
                    if current_type not in _VALIDATION_CHECK_TYPES:
                        current_type = "Ignore"
                    st.session_state.setdefault(type_key, current_type)
                    display_type = st.session_state.get(type_key, current_type)
                    if display_type not in _VALIDATION_CHECK_TYPES:
                        display_type = "Ignore"
                    # Match col_value's "Expected Value" label row height so the
                    # dropdown below lines up with the text input/remove button.
                    st.markdown(
                        '<div style="height:1.5rem;display:flex;align-items:flex-end;'
                        f'margin-bottom:0.25rem;">{_validation_check_type_icon_html(display_type)}</div>',
                        unsafe_allow_html=True,
                    )
                    new_type = st.selectbox(
                        "Check Type",
                        options=list(_VALIDATION_CHECK_TYPES),
                        key=type_key,
                        format_func=_validation_check_type_label,
                        help=_validation_check_type_help(),
                        label_visibility="collapsed",
                    )
                    check["expected_output_type"] = new_type
                with col_value:
                    value_key = f"{key_prefix}_{check_id}_value"
                    st.session_state.setdefault(value_key, check.get("expected_output", ""))
                    check["expected_output"] = st.text_input(
                        "Expected Value",
                        key=value_key,
                        disabled=new_type in ("Ignore", "No output"),
                        placeholder="Value or regex to accept",
                    )
                    if new_type in ("Ignore", "No output"):
                        check["expected_output"] = ""
                with col_remove:
                    st.write("")
                    st.write("")
                    st.button(
                        "×",
                        key=f"{key_prefix}_{check_id}_remove",
                        use_container_width=True,
                        help="Remove accepted output check",
                        on_click=_remove_check,
                        args=(check_id,),
                    )

    _sync_validation_checks(cmd, cmd["checks"])


def _render_command_steps(state_key: str, pfx: str, placeholder: str) -> None:
    """
    Step-based command editor for startup/completion lists.

    Each step has a delay (seconds) and one or more command entries.
    Commands within a step execute sequentially after the step's delay.
    Steps and commands can be reordered and removed.

    Uses stable _id fields on steps/commands so that widget keys survive
    reorders without stale session_state collisions.
    """
    raw   = st.session_state.get(state_key, [])
    steps = _ensure_step_ids(_coerce_steps(raw))

    mutation = None  # tuple describing pending structural mutation
    toggle   = None  # (session_state_key, new_bool) for expand/collapse

    if not steps:
        st.caption("No steps configured. Click **+ Add Step** to begin.")

    for si, step in enumerate(steps):
        step_id  = step["_id"]
        commands = step.get("commands", [])

        with st.container(border=True):
            # ── Step header: toggle | delay | ↑ ↓ ✕ ───────────────────────
            hc1, hcl_delay, hcv_delay, hc2, hc3, hc4 = st.columns([5.0, 0.8, 1.0, 1.0, 1.0, 0.7])
            with hc1:
                _open = st.session_state.get(f"_sc_{pfx}_{step_id}_open", True)
                _preview = _step_commands_preview(commands)
                _label = f"{'▼' if _open else '▶'} Step {si + 1}{(' — ' + _preview) if _preview else ' — (empty)'}"
                if st.button(_label, key=f"_sc_{pfx}_{step_id}_toggle", use_container_width=True,
                             help="Collapse/expand this step"):
                    toggle = (f"_sc_{pfx}_{step_id}_open", not _open)
            with hcl_delay:
                st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Delay (s)</div>", unsafe_allow_html=True)
            with hcv_delay:
                delay_key = f"_sc_{pfx}_{step_id}_delay"
                if delay_key not in st.session_state:
                    st.session_state[delay_key] = float(step.get("delay_seconds", 0.0))
                new_delay = st.number_input(
                    "Delay (s)",
                    min_value=0.0, max_value=3600.0, step=0.5,
                    key=delay_key,
                    label_visibility="collapsed",
                    help="Delay before this step's commands run (seconds).",
                )
                step["delay_seconds"] = st.session_state.get(delay_key, 0.0)
            with hc2:
                if st.button("↑", key=f"_sc_{pfx}_{step_id}_up",
                             disabled=(si == 0), use_container_width=True,
                             help="Move step up"):
                    mutation = ("move_step", si, si - 1)
            with hc3:
                if st.button("↓", key=f"_sc_{pfx}_{step_id}_dn",
                             disabled=(si == len(steps) - 1), use_container_width=True,
                             help="Move step down"):
                    mutation = ("move_step", si, si + 1)
            with hc4:
                if st.button("✕", key=f"_sc_{pfx}_{step_id}_del",
                             use_container_width=True, help="Remove step"):
                    mutation = ("del_step", si)

            # ── Collapsible body ────────────────────────────────────────────
            if st.session_state.get(f"_sc_{pfx}_{step_id}_open", True):
                # ── Commands ────────────────────────────────────────────────
                if not commands:
                    st.caption("No commands. Click the buttons below to begin.")
                    _bot_pfx = _bot_prefix_from_state_key(state_key)
                    _llm_enabled = st.session_state.get(f"{_bot_pfx}_llm_helper_enabled", False)
                    if _llm_enabled:
                        ca, cb, _ = st.columns([2, 2, 6])
                        with ca:
                            if st.button("+ Add Command", key=f"_sc_{pfx}_{step_id}_addcmd_empty", use_container_width=True):
                                mutation = ("add_cmd", si)
                        with cb:
                            if st.button("+ Add LLM Judge", key=f"_sc_{pfx}_{step_id}_addprompt_empty", use_container_width=True):
                                mutation = ("add_prompt", si)
                    else:
                        ca, _ = st.columns([2, 7])
                        with ca:
                            if st.button("+ Add Command", key=f"_sc_{pfx}_{step_id}_addcmd_empty", use_container_width=True):
                                mutation = ("add_cmd", si)
                else:
                    for ci, cmd in enumerate(commands):
                        cmd_id = cmd["_id"]

                        _cmd_type = cmd.get("type", "command")
                        if _cmd_type == "prompt":
                            with st.container(border=True):
                                cc0, cc1, ccl_to, ccv_to, cc_lr, cc2, cc3, cc4 = st.columns([0.3, 2.7, 0.8, 1.0, 1.1, 1.6, 1.0, 0.7])
                                with cc0:
                                    st.markdown("<div style='margin-top:8px; font-size:18px;' title='Prompt'>💬</div>", unsafe_allow_html=True)
                                with cc1:
                                    st.markdown("**LLM Judge**")
                                with ccl_to:
                                    st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Timeout (s)</div>", unsafe_allow_html=True)
                                with ccv_to:
                                    lr_key = f"_sc_{pfx}_{step_id}_{cmd_id}_lr"
                                    is_lr = st.session_state.get(lr_key, cmd.get("long_running", False))
                                    to_key = f"_sc_{pfx}_{step_id}_{cmd_id}_to"
                                    if to_key not in st.session_state:
                                        st.session_state[to_key] = float(cmd.get("timeout_seconds", 60.0))
                                    cmd["timeout_seconds"] = st.number_input(
                                        "Timeout (s)", min_value=0.1, max_value=3600.0, step=1.0,
                                        key=to_key, label_visibility="collapsed", disabled=is_lr
                                    )
                                with cc_lr:
                                    cmd["long_running"] = st.checkbox("Long-running", value=cmd.get("long_running", False), key=lr_key, help="Disables per-command timeout")
                                with cc2:
                                    pc_key = f"_sc_{pfx}_{step_id}_{cmd_id}_pc"
                                    cmd["preserve_context"] = st.checkbox("Preserve Context", value=cmd.get("preserve_context", True), key=pc_key)
                                with cc3:
                                    en_key = f"_sc_{pfx}_{step_id}_{cmd_id}_en"
                                    cmd["enabled"] = st.checkbox("Enabled", value=cmd.get("enabled", True), key=en_key)
                                with cc4:
                                    if st.button("✕", key=f"_sc_{pfx}_{step_id}_{cmd_id}_del", use_container_width=True):
                                        mutation = ("del_cmd", si, ci)
                                
                                sys_key = f"_sc_{pfx}_{step_id}_{cmd_id}_sys"
                                if sys_key not in st.session_state:
                                    st.session_state[sys_key] = cmd.get("system_prompt", "")
                                with st.expander("System Prompt", expanded=False):
                                    cmd["system_prompt"] = st.text_area("System Prompt", key=sys_key, placeholder="System instructions...", label_visibility="collapsed")
                                
                                usr_key = f"_sc_{pfx}_{step_id}_{cmd_id}_usr"
                                if usr_key not in st.session_state:
                                    st.session_state[usr_key] = cmd.get("user_prompt", "")
                                with st.expander("User Prompt", expanded=False):
                                    cmd["user_prompt"] = st.text_area("User Prompt", key=usr_key, placeholder="User prompt...", label_visibility="collapsed")
                        else:
                            # Single flat row: indicator | command | timeout | long-running | enabled | delete
                            cc0, cc1, ccl_to, ccv_to, cc3, cc4, cc5 = st.columns([0.3, 4.7, 0.8, 1.0, 1.0, 1.0, 0.7])
                            with cc0:
                                st.markdown("<div style='margin-top:8px; font-size:18px;' title='Command'>💻</div>", unsafe_allow_html=True)
                            with cc1:
                                cmd_key = f"_sc_{pfx}_{step_id}_{cmd_id}_cmd"
                                if cmd_key not in st.session_state:
                                    st.session_state[cmd_key] = cmd.get("command", "")

                                st.text_input(
                                    f"Command {ci + 1}",
                                    key=cmd_key,
                                    placeholder=placeholder,
                                    label_visibility="collapsed",
                                )
                                cmd["command"] = st.session_state.get(cmd_key, "")
                            with ccl_to:
                                st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Timeout (s)</div>", unsafe_allow_html=True)
                            with ccv_to:
                                lr_key = f"_sc_{pfx}_{step_id}_{cmd_id}_lr"
                                is_lr  = st.session_state.get(lr_key, cmd.get("long_running", False))
                                
                                to_key = f"_sc_{pfx}_{step_id}_{cmd_id}_to"
                                if to_key not in st.session_state:
                                    st.session_state[to_key] = float(cmd.get("timeout_seconds", 60.0))
                                cmd["timeout_seconds"] = st.number_input(
                                    "Timeout (s)",
                                    min_value=0.1, max_value=3600.0, step=1.0,
                                    key=to_key,
                                    disabled=is_lr,
                                    label_visibility="collapsed",
                                    help="Per-command timeout in seconds.",
                                )
                            with cc3:
                                cmd["long_running"] = st.checkbox(
                                    "Long-running",
                                    value=cmd.get("long_running", False),
                                    key=lr_key,
                                    help="Disables the per-command timeout; allows up to 1 hour.",
                                )
                            with cc4:
                                en_key         = f"_sc_{pfx}_{step_id}_{cmd_id}_en"
                                cmd["enabled"] = st.checkbox(
                                    "Enabled",
                                    value=cmd.get("enabled", True),
                                    key=en_key,
                                )
                            with cc5:
                                if st.button("✕", key=f"_sc_{pfx}_{step_id}_{cmd_id}_del",
                                             use_container_width=True):
                                    mutation = ("del_cmd", si, ci)

                    # ── Add Command / Add LLM Judge buttons (after all commands in step) ──
                    # Only show when the step has commands — these let the user add more
                    # entries to an already-populated step.
                    _bot_pfx = _bot_prefix_from_state_key(state_key)
                    _llm_enabled = st.session_state.get(f"{_bot_pfx}_llm_helper_enabled", False)
                    if _llm_enabled:
                        ca, cb, _ = st.columns([1.5, 1.5, 7.0])
                        with ca:
                            if st.button(f"+ Add Command", key=f"_sc_{pfx}_{step_id}_addcmd", use_container_width=True):
                                mutation = ("add_cmd", si)
                        with cb:
                            if st.button(f"+ Add LLM Judge", key=f"_sc_{pfx}_{step_id}_addprompt", use_container_width=True):
                                mutation = ("add_prompt", si)
                    else:
                        if st.button(f"+ Add Command", key=f"_sc_{pfx}_{step_id}_addcmd"):
                            mutation = ("add_cmd", si)

    # A direct prompt action makes LLM Judge-backed Startup/Completion work
    # discoverable even before the user has created the first step.
    _bot_pfx = _bot_prefix_from_state_key(state_key)
    _llm_enabled = st.session_state.get(f"{_bot_pfx}_llm_helper_enabled", False)
    if not steps and _llm_enabled:
        add_step_col, add_prompt_col, _ = st.columns([2, 2, 6])
        with add_step_col:
            if st.button("+ Add Step", key=f"_sc_{pfx}_addstep", type="primary", use_container_width=True):
                mutation = ("add_step",)
        with add_prompt_col:
            if st.button("+ Add LLM Judge", key=f"_sc_{pfx}_addprompt_step", use_container_width=True):
                mutation = ("add_prompt_step",)
    elif st.button("+ Add Step", key=f"_sc_{pfx}_addstep", type="primary"):
        mutation = ("add_step",)

    # ── Handle expand/collapse toggle (UI-only, no undo entry) ──────────────
    # Accordion behaviour: expanding a step collapses every other step.
    if toggle:
        _tgl_key, _tgl_open = toggle
        st.session_state[_tgl_key] = _tgl_open
        if _tgl_open:
            for _s in steps:
                _k = f"_sc_{pfx}_{_s['_id']}_open"
                if _k != _tgl_key:
                    st.session_state[_k] = False
        st.rerun()

    # ── Apply mutation and rerun ─────────────────────────────────────────────
    if mutation:
        _push_undo({"desc": f"edit {pfx} commands", "type": "cmd",
                    "state_key": state_key, "data": copy.deepcopy(steps)})
        m = mutation
        if m[0] in ("add_step", "add_prompt_step"):
            _bot_pfx = _bot_prefix_from_state_key(state_key)
            _llm_enabled = st.session_state.get(f"{_bot_pfx}_llm_helper_enabled", False)

            new_step = {
                "_id":           _next_step_id(),
                "delay_seconds": 0.0,
                "commands":      [],
            }

            if m[0] == "add_prompt_step":
                new_step["commands"].append({
                    "_id":             _next_step_id(),
                    "type":            "prompt",
                    "system_prompt":   "",
                    "user_prompt":     "",
                    "preserve_context": True,
                    "enabled":         True,
                    "long_running":    False,
                    "timeout_seconds": 60,
                })
            # LLM Judge disabled → pre-populate with a blank command row
            # so the user can type immediately without clicking "+ Add Command".
            elif not _llm_enabled:
                new_step["commands"].append({
                    "_id":             _next_step_id(),
                    "type":            "command",
                    "command":         "",
                    "enabled":         True,
                    "long_running":    False,
                    "timeout_seconds": 60,
                })

            steps.append(new_step)
            # Collapse every previously-rendered step; the new step stays open.
            new_id = new_step["_id"]
            for _s in steps:
                if _s.get("_id") == new_id:
                    continue
                st.session_state[f"_sc_{pfx}_{_s['_id']}_open"] = False
            st.session_state[f"_sc_{pfx}_{new_id}_open"] = True
        elif m[0] == "del_step":
            steps.pop(m[1])
        elif m[0] == "move_step":
            si1, si2 = m[1], m[2]
            steps[si1], steps[si2] = steps[si2], steps[si1]
        elif m[0] == "add_cmd":
            # Only reachable via the "+ Add Command" button — a single click is a
            # single rerun, so no double-add guard is needed.
            steps[m[1]]["commands"].append({
                "_id":             _next_step_id(),
                "type":            "command",
                "command":         "",
                "enabled":         True,
                "long_running":    False,
                "timeout_seconds": 60,
            })
        elif m[0] == "add_prompt":
            steps[m[1]]["commands"].append({
                "_id":             _next_step_id(),
                "type":            "prompt",
                "system_prompt":   "",
                "user_prompt":     "",
                "preserve_context": True,
                "enabled":         True,
                "long_running":    False,
                "timeout_seconds": 60,
            })
        elif m[0] == "del_cmd":
            steps[m[1]]["commands"].pop(m[2])
        elif m[0] == "move_cmd":
            # per-command reorder removed — flat row layout no longer shows ↑/↓ buttons
            _, ci1, ci2 = m[1], m[2], m[3]
            cmds         = steps[m[1]]["commands"]
            cmds[ci1], cmds[ci2] = cmds[ci2], cmds[ci1]

        st.session_state[state_key] = steps
        st.rerun()
    else:
        st.session_state[state_key] = steps




def _render_validation_steps(state_key: str, pfx: str, placeholder: str, bot_type: str) -> None:
    raw   = st.session_state.get(state_key, [])
    steps = _ensure_step_ids(_coerce_steps(raw))
    st.session_state[state_key] = steps

    def _val_add_step():
        _steps = st.session_state[state_key]
        new_step = {
            "_id": _next_step_id(),
            "delay_seconds": 0.0,
            "commands": [],
        }
        _steps.append(new_step)
        # Collapse every previously-rendered step; the new step stays open.
        new_id = new_step["_id"]
        for _s in _steps:
            if _s.get("_id") == new_id:
                continue
            st.session_state[f"_sc_{pfx}_{_s['_id']}_open"] = False
        st.session_state[f"_sc_{pfx}_{new_id}_open"] = True

    def _val_del_step(si):
        st.session_state[state_key].pop(si)

    def _val_move_step(si1, si2):
        s = st.session_state[state_key]
        s[si1], s[si2] = s[si2], s[si1]

    def _val_add_cmd(si):
        cmds = st.session_state[state_key][si]["commands"]
        if cmds and cmds[-1].get("type", "command") == "command" and not cmds[-1].get("command", "").strip():
            return
        cmds.append({
            "_id": _next_step_id(),
            "type": "command",
            "command": "",
            "enabled": True,
            "timeout_seconds": 60,
            "expected_output_type": "Ignore",
            "expected_output": "",
            "checks": [],
        })

    def _val_add_prompt(si):
        cmds = st.session_state[state_key][si]["commands"]
        if cmds and cmds[-1].get("type") == "prompt" and not cmds[-1].get("system_prompt", "").strip() and not cmds[-1].get("user_prompt", "").strip():
            return
        cmds.append({
            "_id": _next_step_id(),
            "type": "prompt",
            "system_prompt": "",
            "user_prompt": "",
            "preserve_context": True,
            "enabled": True,
            "long_running": False,
            "timeout_seconds": 60,
            "expected_output_type": "Ignore",
            "expected_output": "",
            "checks": [],
        })

    def _val_del_cmd(si, ci):
        st.session_state[state_key][si]["commands"].pop(ci)

    def _val_toggle(key, val):
        st.session_state[key] = val

    if not steps:
        st.caption("No steps configured. Click **+ Add Step** to begin.")

    for si, step in enumerate(steps):
        step_id  = step["_id"]
        commands = step.get("commands", [])

        with st.container(border=True):
            hc1, hcl_delay, hcv_delay, hc2, hc3, hc4 = st.columns([5.0, 0.8, 1.0, 1.0, 1.0, 0.7])
            with hc1:
                _open_key = f"_sc_{pfx}_{step_id}_open"
                _open = st.session_state.get(_open_key, True)
                _preview = _step_commands_preview(commands)
                _label = f"{'▼' if _open else '▶'} Step {si + 1}{(' — ' + _preview) if _preview else ' — (empty)'}"
                st.button(_label, key=f"_sc_{pfx}_{step_id}_toggle", use_container_width=True, help="Collapse/expand this step", on_click=_val_toggle, args=(_open_key, not _open))
            with hcl_delay:
                st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Delay (s)</div>", unsafe_allow_html=True)
            with hcv_delay:
                delay_key = f"_sc_{pfx}_{step_id}_delay"
                if delay_key not in st.session_state:
                    st.session_state[delay_key] = float(step.get("delay_seconds", 0.0))
                new_delay = st.number_input(
                    "Delay (s)", min_value=0.0, max_value=3600.0, step=0.5,
                    key=delay_key, label_visibility="collapsed",
                )
                step["delay_seconds"] = st.session_state.get(delay_key, 0.0)
            with hc2:
                st.button("↑", key=f"_sc_{pfx}_{step_id}_up", disabled=(si == 0), use_container_width=True, on_click=_val_move_step, args=(si, si - 1))
            with hc3:
                st.button("↓", key=f"_sc_{pfx}_{step_id}_dn", disabled=(si == len(steps) - 1), use_container_width=True, on_click=_val_move_step, args=(si, si + 1))
            with hc4:
                st.button("✕", key=f"_sc_{pfx}_{step_id}_del", use_container_width=True, on_click=_val_del_step, args=(si,))

            if st.session_state.get(f"_sc_{pfx}_{step_id}_open", True):
                if not commands:
                    st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
                    if _validation_bot_supports_prompts(bot_type):
                        _ca, _cb, _ = st.columns([2, 2, 5])
                        with _ca:
                            st.button(
                                "+COMMAND/CHECK",
                                key=f"_sc_{pfx}_{step_id}_choice_cmd",
                                use_container_width=True,
                                on_click=_val_add_cmd, args=(si,),
                            )
                        with _cb:
                            st.button(
                                "+PROMPT/CHECK",
                                key=f"_sc_{pfx}_{step_id}_choice_prompt",
                                use_container_width=True,
                                on_click=_val_add_prompt, args=(si,),
                            )
                    else:
                        _ca, _ = st.columns([2, 7])
                        with _ca:
                            st.button(
                                "+COMMAND/CHECK",
                                key=f"_sc_{pfx}_{step_id}_choice_cmd",
                                use_container_width=True,
                                on_click=_val_add_cmd, args=(si,),
                            )
                else:
                    _is_last_cmd_idx = len(commands) - 1

                    first_cmd_seen = False

                    for ci, cmd in enumerate(commands):
                        cmd_id = cmd["_id"]
                        _cmd_type = cmd.get("type", "command")

                        if _cmd_type == "prompt":
                            with st.container(border=True):
                                cc0, cc1, ccl_to, ccv_to, cc_lr, cc2, cc3, cc4 = st.columns([0.3, 2.7, 0.8, 1.0, 1.1, 1.6, 1.0, 0.7])
                                with cc0:
                                    st.markdown("<div style='margin-top:8px; font-size:18px;' title='Prompt'>💬</div>", unsafe_allow_html=True)
                                with cc1:
                                    _title = _validation_bot_prompt_title(bot_type)
                                    st.markdown(f"**{_title}**")
                                with ccl_to:
                                    st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Timeout (s)</div>", unsafe_allow_html=True)
                                with ccv_to:
                                    lr_key = f"_sc_{pfx}_{step_id}_{cmd_id}_lr"
                                    is_lr = st.session_state.get(lr_key, cmd.get("long_running", False))
                                    to_key = f"_sc_{pfx}_{step_id}_{cmd_id}_to"
                                    if to_key not in st.session_state:
                                        st.session_state[to_key] = float(cmd.get("timeout_seconds", 60.0))
                                    cmd["timeout_seconds"] = st.number_input(
                                        "Timeout (s)", min_value=0.1, max_value=3600.0, step=1.0,
                                        key=to_key, label_visibility="collapsed", disabled=is_lr
                                    )
                                with cc_lr:
                                    cmd["long_running"] = st.checkbox("Long-running", value=cmd.get("long_running", False), key=lr_key, help="Disables per-command timeout")
                                with cc2:
                                    pc_key = f"_sc_{pfx}_{step_id}_{cmd_id}_pc"
                                    cmd["preserve_context"] = st.checkbox("Preserve Context", value=cmd.get("preserve_context", True), key=pc_key)
                                with cc3:
                                    en_key = f"_sc_{pfx}_{step_id}_{cmd_id}_en"
                                    cmd["enabled"] = st.checkbox("Enabled", value=cmd.get("enabled", True), key=en_key)
                                with cc4:
                                    st.button("✕", key=f"_sc_{pfx}_{step_id}_{cmd_id}_del", use_container_width=True, on_click=_val_del_cmd, args=(si, ci))
                                
                                if bot_type != "caf_cli":
                                    sys_key = f"_sc_{pfx}_{step_id}_{cmd_id}_sys"
                                    if sys_key not in st.session_state:
                                        st.session_state[sys_key] = cmd.get("system_prompt", "")
                                    with st.expander("System Prompt", expanded=False):
                                        cmd["system_prompt"] = st.text_area("System Prompt", key=sys_key, placeholder="System instructions...", label_visibility="collapsed")
                                else:
                                    cmd.pop("system_prompt", None)
                                
                                usr_key = f"_sc_{pfx}_{step_id}_{cmd_id}_usr"
                                if usr_key not in st.session_state:
                                    st.session_state[usr_key] = cmd.get("user_prompt", "")
                                with st.expander("User Prompt", expanded=False):
                                    cmd["user_prompt"] = st.text_area("User Prompt", key=usr_key, placeholder="User prompt...", label_visibility="collapsed")

                                st.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -5px;'>Accepted Checks</div>", unsafe_allow_html=True)
                                _render_validation_checks_control(
                                    cmd,
                                    key_prefix=f"_sc_{pfx}_{step_id}_{cmd_id}_checks",
                                    disabled=not cmd.get("enabled", True),
                                )
                        else:
                            if not first_cmd_seen:
                                hc_cmd, hc_to, hc_checks, hc_en, hc_del = st.columns([3.0, 0.8, 3.5, 0.8, 0.6])
                                hc_cmd.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Command</div>", unsafe_allow_html=True)
                                hc_to.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Timeout</div>", unsafe_allow_html=True)
                                hc_checks.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Accepted Checks</div>", unsafe_allow_html=True)
                                hc_en.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Enabled</div>", unsafe_allow_html=True)
                                st.markdown("<div style='height: 5px;'></div>", unsafe_allow_html=True)
                                first_cmd_seen = True

                            cc_cmd, cc_to, cc_checks, cc_en, cc_del = st.columns([3.0, 0.8, 3.5, 0.8, 0.6])
                            
                            with cc_cmd:
                                cmd_key = f"_sc_{pfx}_{step_id}_{cmd_id}_cmd"
                                if cmd_key not in st.session_state:
                                    st.session_state[cmd_key] = cmd.get("command", "")

                                is_disabled = not cmd.get("enabled", True)

                                if ci == _is_last_cmd_idx:
                                    def _on_last_cmd_enter(_ck=cmd_key, _si=si):
                                        if st.session_state.get(_ck, "").strip():
                                            _val_add_cmd(_si)
                                    st.text_input(f"Command {ci + 1}", key=cmd_key, placeholder=placeholder, label_visibility="collapsed", on_change=_on_last_cmd_enter, disabled=is_disabled)
                                else:
                                    st.text_input(f"Command {ci + 1}", key=cmd_key, placeholder=placeholder, label_visibility="collapsed", disabled=is_disabled)
                                cmd["command"] = st.session_state.get(cmd_key, "")
                                
                            with cc_to:
                                to_key = f"_sc_{pfx}_{step_id}_{cmd_id}_to"
                                if to_key not in st.session_state:
                                    st.session_state[to_key] = float(cmd.get("timeout_seconds", 60.0))
                                cmd["timeout_seconds"] = st.number_input("Timeout (s)", min_value=0.1, max_value=3600.0, step=1.0, key=to_key, label_visibility="collapsed", disabled=is_disabled)

                            with cc_checks:
                                _render_validation_checks_control(
                                    cmd,
                                    key_prefix=f"_sc_{pfx}_{step_id}_{cmd_id}_checks",
                                    disabled=is_disabled,
                                )
                                
                            with cc_en:
                                en_key = f"_sc_{pfx}_{step_id}_{cmd_id}_en"
                                cmd["enabled"] = st.checkbox("Enabled", value=cmd.get("enabled", True), key=en_key)
                                
                            with cc_del:
                                st.button("✕", key=f"_sc_{pfx}_{step_id}_{cmd_id}_del", use_container_width=True, on_click=_val_del_cmd, args=(si, ci))

                    # "+PROMPT/CHECK" (LLM Judge validation step) is only meaningful for
                    # Llama-backed projects have a main LLM under test; Bash-Bot
                    # validation is deterministic command/output checks only.
                    if _validation_bot_supports_prompts(bot_type):
                        ca, cb, _ = st.columns([1.5, 2.0, 6.5])
                        with ca:
                            st.button("+COMMAND/CHECK", key=f"_sc_{pfx}_{step_id}_addcmd", on_click=_val_add_cmd, args=(si,), use_container_width=True)
                        with cb:
                            st.button("+PROMPT/CHECK", key=f"_sc_{pfx}_{step_id}_addprompt", on_click=_val_add_prompt, args=(si,), use_container_width=True)
                    else:
                        ca, _ = st.columns([2.0, 7.5])
                        with ca:
                            st.button("+COMMAND/CHECK", key=f"_sc_{pfx}_{step_id}_addcmd", on_click=_val_add_cmd, args=(si,), use_container_width=True)

    st.button("+ Add Step", key=f"_sc_{pfx}_addstep", type="primary", on_click=_val_add_step)


def _render_llm_prompt_helper_tab(pfx: str) -> None:
    """Render the connection settings for the LLM Judge.

    Every widget below seeds its key via ``setdefault`` from the working-copy
    value, then binds with ``key=`` alone (no ``value=``/``index=``). Passing
    both ``key=`` and ``value=`` only seeds the widget on that key's very
    first-ever render in the whole session — every render after that ignores
    ``value=`` and just keeps showing whatever the key already holds, which is
    exactly what let one project's LLM Judge settings bleed into another's.
    """
    st.caption(
        "Configure an LLM connection here to assist with generating commands or prompts "
        "for your tasks."
    )
    st.session_state.setdefault(f"{pfx}_llm_helper_enabled_widget", st.session_state.get(f"{pfx}_llm_helper_enabled", False))
    _is_enabled = st.toggle(
        "Enable LLM Judge",
        key=f"{pfx}_llm_helper_enabled_widget",
        help="When enabled, 'prompt' type steps in Startup/Completion will be executed by this helper backend.",
    )
    st.session_state[f"{pfx}_llm_helper_enabled"] = _is_enabled

    if _is_enabled:
        st.session_state.setdefault(f"{pfx}_llm_helper_backend_sel", st.session_state.get(f"{pfx}_llm_helper_backend", "OpenAI-Compatible"))
        backend = st.selectbox(
            "LLM Backend",
            options=["OpenAI-Compatible", "Ollama"],
            key=f"{pfx}_llm_helper_backend_sel",
        )
        st.session_state[f"{pfx}_llm_helper_backend"] = backend

        if backend == "Ollama":
            st.session_state.setdefault(f"{pfx}_llm_helper_ollama_url_widget", st.session_state.get(f"{pfx}_llm_helper_ollama_url", "http://localhost:11434"))
            _url = st.text_input(
                "Ollama Server URL",
                key=f"{pfx}_llm_helper_ollama_url_widget",
            )
            st.session_state[f"{pfx}_llm_helper_ollama_url"] = _url

            if st.button("Fetch Models", key=f"btn_{pfx}_fetch_ollama_models", use_container_width=True):
                if _url.strip():
                    from core.models import fetch_ollama_models
                    with st.spinner("Fetching..."):
                        target = st.session_state.get(f"{pfx}_execution_target", "local")
                        env = None
                        if target in ("ssh", "pct"):
                            from core.environment import create_environment
                            env = create_environment(
                                ssh=(target == "ssh"),
                                host=st.session_state.get(f"{pfx}_ssh_host", ""),
                                port=int(st.session_state.get(f"{pfx}_ssh_port", 22)),
                                username=st.session_state.get(f"{pfx}_ssh_user", "root"),
                                password=st.session_state.get(f"{pfx}_ssh_password", ""),
                                key_path=st.session_state.get(f"{pfx}_ssh_key_path", ""),
                                pct_vmid=st.session_state.get(f"{pfx}_pct_vmid", ""),
                            )
                        _found, _err = fetch_ollama_models(_url.strip(), env=env)
                    if _found:
                        st.session_state[f"{pfx}_llm_helper_ollama_models"] = _found
                        st.toast(f"✅ Found {len(_found)} models.")
                    else:
                        _clear_llm_helper_model_selection(pfx, "Ollama")
                        st.toast(f"❌ {_err or 'No models returned.'}")
                else:
                    _clear_llm_helper_model_selection(pfx, "Ollama")
                    st.toast("⚠️ Enter a valid URL.")

            _models = st.session_state.get(f"{pfx}_llm_helper_ollama_models", [])
            if _models:
                _model_names = [m["name"] for m in _models]
                _cur = st.session_state.get(f"{pfx}_llm_helper_model", "")
                st.session_state.setdefault(f"{pfx}_llm_helper_ollama_model_sel", _cur if _cur in _model_names else _model_names[0])
                _chosen = st.selectbox("Model", options=_model_names, key=f"{pfx}_llm_helper_ollama_model_sel")
                st.session_state[f"{pfx}_llm_helper_model"] = _chosen
            else:
                st.session_state.setdefault(f"{pfx}_llm_helper_ollama_model_manual_widget", st.session_state.get(f"{pfx}_llm_helper_model", ""))
                _man = st.text_input("Model", key=f"{pfx}_llm_helper_ollama_model_manual_widget")
                st.session_state[f"{pfx}_llm_helper_model"] = _man

        else:
            # OpenAI-Compatible
            col_url, col_api, col_fetch = st.columns([4, 3, 1])
            with col_url:
                st.session_state.setdefault(f"{pfx}_llm_helper_openai_url_widget", st.session_state.get(f"{pfx}_llm_helper_openai_url", ""))
                _url = st.text_input(
                    "Instance URL",
                    key=f"{pfx}_llm_helper_openai_url_widget",
                    placeholder="http://localhost:8080",
                    help="Base URL of any OpenAI-compatible server. Do not include /v1.",
                )
                st.session_state[f"{pfx}_llm_helper_openai_url"] = _url

            with col_api:
                st.session_state.setdefault(f"{pfx}_llm_helper_openai_apikey_widget", st.session_state.get(f"{pfx}_llm_helper_openai_apikey", ""))
                _apikey = st.text_input(
                    "API Key (optional)",
                    key=f"{pfx}_llm_helper_openai_apikey_widget",
                    type="password",
                )
                st.session_state[f"{pfx}_llm_helper_openai_apikey"] = _apikey

            with col_fetch:
                st.write("")
                st.write("")
                if st.button("Fetch", key=f"btn_{pfx}_fetch_openai_models", use_container_width=True):
                    if _url.strip():
                        from core.models import fetch_llama_cpp_models
                        with st.spinner("Fetching..."):
                            target = st.session_state.get(f"{pfx}_execution_target", "local")
                            env = None
                            if target in ("ssh", "pct"):
                                from core.environment import create_environment
                                env = create_environment(
                                    ssh=(target == "ssh"),
                                    host=st.session_state.get(f"{pfx}_ssh_host", ""),
                                    port=int(st.session_state.get(f"{pfx}_ssh_port", 22)),
                                    username=st.session_state.get(f"{pfx}_ssh_user", "root"),
                                    password=st.session_state.get(f"{pfx}_ssh_password", ""),
                                    key_path=st.session_state.get(f"{pfx}_ssh_key_path", ""),
                                    pct_vmid=st.session_state.get(f"{pfx}_pct_vmid", ""),
                                )
                            _found, _err = fetch_llama_cpp_models(_url.strip(), verify_ssl=st.session_state.get(f"{pfx}_llm_helper_openai_verify_ssl", True), env=env)
                        if _found:
                            st.session_state[f"{pfx}_llm_helper_openai_models"] = _found
                            st.toast(f"✅ Found {len(_found)} models.")
                        else:
                            _clear_llm_helper_model_selection(pfx, "OpenAI-Compatible")
                            st.toast(f"❌ {_err or 'No models returned.'}")
                    else:
                        _clear_llm_helper_model_selection(pfx, "OpenAI-Compatible")
                        st.toast("⚠️ Enter a valid URL.")

            st.session_state.setdefault(f"{pfx}_llm_helper_openai_verify_ssl_widget", st.session_state.get(f"{pfx}_llm_helper_openai_verify_ssl", True))
            _ssl = st.checkbox(
                "Require SSL Certificate Verification",
                key=f"{pfx}_llm_helper_openai_verify_ssl_widget",
                help="Used for https:// URLs; plain HTTP endpoints ignore certificate verification. Uncheck for self-signed certificates.",
            )
            st.session_state[f"{pfx}_llm_helper_openai_verify_ssl"] = _ssl
            _models = st.session_state.get(f"{pfx}_llm_helper_openai_models", [])
            if _models:
                _model_names = [m["name"] for m in _models]
                _cur = st.session_state.get(f"{pfx}_llm_helper_model", "")
                st.session_state.setdefault(f"{pfx}_llm_helper_openai_model_sel", _cur if _cur in _model_names else _model_names[0])
                _chosen = st.selectbox("Model", options=_model_names, key=f"{pfx}_llm_helper_openai_model_sel")
                st.session_state[f"{pfx}_llm_helper_model"] = _chosen
            else:
                st.session_state.setdefault(f"{pfx}_llm_helper_openai_model_manual_widget", st.session_state.get(f"{pfx}_llm_helper_model", ""))
                _man = st.text_input("Model", key=f"{pfx}_llm_helper_openai_model_manual_widget")
                st.session_state[f"{pfx}_llm_helper_model"] = _man

            if st.button("Check Status", key=f"btn_{pfx}_check_openai_status", use_container_width=True):
                if _url.strip():
                    from core.llama_server import get_server_info
                    target = st.session_state.get(f"{pfx}_execution_target", "local")
                    env = None
                    if target in ("ssh", "pct"):
                        from core.environment import create_environment
                        env = create_environment(
                            ssh=(target == "ssh"),
                            host=st.session_state.get(f"{pfx}_ssh_host", ""),
                            port=int(st.session_state.get(f"{pfx}_ssh_port", 22)),
                            username=st.session_state.get(f"{pfx}_ssh_user", "root"),
                            password=st.session_state.get(f"{pfx}_ssh_password", ""),
                            key_path=st.session_state.get(f"{pfx}_ssh_key_path", ""),
                            pct_vmid=st.session_state.get(f"{pfx}_pct_vmid", ""),
                        )
                    _info = get_server_info(_url.strip(), verify_ssl=_ssl, env=env)
                    if _info:
                        _mname = (_info.get("model_path") or "").split("/")[-1] or "?"
                        st.success(f"Online  |  model: `{_mname}`  |  Context Window Length: `{_info.get('n_ctx') or '?'}`")
                    else:
                        st.error("Could not reach server.")
                else:
                    st.warning("Enter an Instance URL first.")

        st.divider()
        st.caption("Judge-only MCP tools are separate from the student bot's tool policy.")
        st.session_state.setdefault(f"{pfx}_llm_helper_mcp_enabled", False)
        judge_mcp_enabled = st.toggle("Allow MCP tools for LLM Judge", key=f"{pfx}_llm_helper_mcp_enabled")
        if not st.session_state.get(f"{pfx}_llm_helper_mcp_config_path"):
            st.session_state[f"{pfx}_llm_helper_mcp_config_path"] = MCP_CONFIG_PATH
        st.text_input("Judge MCP Tool Config", key=f"{pfx}_llm_helper_mcp_config_path", disabled=not judge_mcp_enabled)
        try:
            judge_declared = load_mcp_tool_config(st.session_state[f"{pfx}_llm_helper_mcp_config_path"])
            judge_tools = merge_mcp_tool_selections(
                judge_declared, st.session_state.get(f"{pfx}_llm_helper_mcp_tools", []),
            )
        except ValueError as exc:
            judge_tools = []
            st.error(str(exc))
        for tool in judge_tools:
            tool["enabled"] = st.checkbox(
                tool["name"], value=tool.get("enabled", False),
                key=f"{pfx}_judge_mcp_{tool['tool_name']}", disabled=not judge_mcp_enabled,
            )
        st.session_state[f"{pfx}_llm_helper_mcp_tools"] = judge_tools
        st.checkbox(
            "Require structured tool calls", key=f"{pfx}_llm_helper_mcp_strict",
            disabled=not judge_mcp_enabled,
            help="Fail a judge prompt if it needs tools but the model does not return structured tool calls.",
        )

def _render_bash_runtime(project: dict) -> None:
    """Runtime sub-tab for Bash-Bot: execution target, commands (steps), timeout."""

    with st.expander("Execution Target", expanded=True):
        target = st.radio(
            "Mode",
            options=["local", "ssh", "pct"],
            format_func=lambda v: {"local": "Local", "ssh": "SSH (Remote)", "pct": "PCT (Proxmox LXC)"}.get(v, v),
            key="bash_execution_target",
            help="Run commands locally, via SSH, or inside a Proxmox LXC container via pct.",
            horizontal=True,
        )
        st.checkbox(
            "Run commands with sudo",
            key="bash_sudo",
            help="Run every startup, validation, and completion command as root via `sudo bash -c`.",
        )
        if st.session_state.get("bash_sudo"):
            st.text_input(
                "Sudo password",
                key="bash_sudo_password",
                type="password",
                help="Piped to `sudo -S`. Leave blank to reuse the SSH password or if passwordless sudo (NOPASSWD) is configured.",
            )
        if target == "local":
            st.divider()
            _render_test_button("local", "bash")
        elif target == "pct":
            st.divider()
            st.text_input("LXC Container ID (VMID)", key="bash_pct_vmid", placeholder="100")
            _render_test_button("pct", "bash", "bash_pct_vmid")
        elif target == "ssh":
            st.divider()
            st.markdown("**SSH Credentials**")
            c_host, c_port = st.columns([4, 1])
            with c_host:
                st.text_input("Host", key="bash_ssh_host", placeholder="192.168.1.100")
            with c_port:
                st.number_input(
                    "Port", min_value=1, max_value=65535,
                    value=st.session_state.get("bash_ssh_port", 22),
                    key="bash_ssh_port",
                )
            c_user, c_pass = st.columns([1, 1])
            with c_user:
                st.text_input("Username", key="bash_ssh_user")
            with c_pass:
                st.text_input("Password", key="bash_ssh_password",
                              type="password",
                              help="Leave empty to use key-based auth.")
            st.text_input("Key Path", key="bash_ssh_key_path",
                          placeholder="~/.ssh/id_rsa",
                          help="Path to private key file. Leave empty if using password auth.")
            _, col_test, _ = st.columns([1, 2, 1])
            with col_test:
                if st.button("Test Connection", key="btn_bash_test_ssh", type="secondary", use_container_width=True):
                    st.session_state.pop("bash_ssh_test_result", None)
                    with st.spinner("Please wait..."):
                        _test_bash_ssh_connection()

            _ssh_result = st.session_state.get("bash_ssh_test_result")
            if _ssh_result:
                _ls, _lm = _ssh_result["status"], _ssh_result["message"]
                if _ls == "success":
                    st.success(_lm)
                elif _ls == "warning":
                    st.warning(_lm)
                else:
                    st.error(_lm)

    with st.expander("Commands", expanded=True):
        tab_llm, tab_startup, tab_completion = st.tabs(
            ["🤖 LLM Judge", "▶  Startup", "⏹  Completion"]
        )
        with tab_llm:
            _render_llm_prompt_helper_tab("bash")
        with tab_startup:
            st.caption(
                "Commands run when execution starts, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="bash_startup_commands",
                pfx="startup",
                placeholder="e.g. /bin/bash setup.sh",
            )
        with tab_completion:
            st.caption(
                "Cleanup commands run after startup and validation finish, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="bash_completion_commands",
                pfx="completion",
                placeholder="e.g. rm -rf /tmp/test_workdir",
            )



    _flush_bash_config(project)


def _state_prefix_from_test_result_key(result_key: str) -> str:
    """Recover the state_prefix passed to _render_test_button from its derived
    result_key (f"{state_prefix}_{target_type}_test_result").  A naive
    split("_")[0] breaks for multi-word prefixes like "llama_cli"/"llama_server",
    so strip the known "_local"/"_pct" + "_test_result" suffix instead."""
    prefix = result_key.removesuffix("_test_result")
    for target_suffix in ("_local", "_pct"):
        if prefix.endswith(target_suffix):
            return prefix[: -len(target_suffix)]
    return prefix.split("_")[0]


def _test_local_connection(result_key: str) -> None:
    from core.environment import LocalEnvironment
    import shlex
    try:
        env = LocalEnvironment()
        res = env.execute("echo ok")
        if res["exit_code"] != 0 or "ok" not in res["stdout"]:
            st.session_state[result_key] = {"status": "warning", "message": f"Unexpected response: {res!r}"}
            return

        prefix = _state_prefix_from_test_result_key(result_key)
        use_sudo = st.session_state.get(f"{prefix}_sudo")
        if use_sudo:
            sudo_pw = (st.session_state.get(f"{prefix}_sudo_password") or "").strip()
            sudo_check_cmd = f"sudo -k; echo {shlex.quote(sudo_pw)} | sudo -S -v" if sudo_pw else "sudo -k; sudo -n -v"
            check_res = env.execute(sudo_check_cmd, timeout=10)
            if check_res.get("exit_code", -1) != 0:
                err_msg = check_res.get("stderr", "") or check_res.get("stdout", "Unknown error")
                err_clean = err_msg.replace("sudo: ", "").strip().capitalize()
                st.session_state[result_key] = {"status": "error", "message": f"Sudo auth failed: {err_clean}"}
                return

        st.session_state[result_key] = {"status": "success", "message": "Local execution working successfully."}
    except Exception as exc:
        st.session_state[result_key] = {"status": "error", "message": f"Local execution failed: {exc}"}


def _test_pct_connection(vmid_key: str, result_key: str) -> None:
    from core.environment import LocalEnvironment, PCTEnvironment
    import shlex
    vmid = (st.session_state.get(vmid_key) or "").strip()
    if not vmid:
        st.session_state[result_key] = {"status": "error", "message": "VMID is required."}
        return
    try:
        env = PCTEnvironment(vmid, LocalEnvironment())
        res = env.execute("echo ok")
        if res["exit_code"] != 0 or "ok" not in res["stdout"]:
            st.session_state[result_key] = {"status": "warning", "message": f"Unexpected response: {res!r}"}
            return

        prefix = _state_prefix_from_test_result_key(result_key)
        use_sudo = st.session_state.get(f"{prefix}_sudo")
        if use_sudo:
            sudo_pw = (st.session_state.get(f"{prefix}_sudo_password") or "").strip()
            sudo_check_cmd = f"sudo -k; echo {shlex.quote(sudo_pw)} | sudo -S -v" if sudo_pw else "sudo -k; sudo -n -v"
            check_res = env.execute(sudo_check_cmd, timeout=10)
            if check_res.get("exit_code", -1) != 0:
                err_msg = check_res.get("stderr", "") or check_res.get("stdout", "Unknown error")
                err_clean = err_msg.replace("sudo: ", "").strip().capitalize()
                st.session_state[result_key] = {"status": "error", "message": f"Sudo auth failed: {err_clean}"}
                return

        st.session_state[result_key] = {"status": "success", "message": f"Connection test succeeded for PCT container {vmid}"}
    except Exception as exc:
        st.session_state[result_key] = {"status": "error", "message": f"PCT connection failed: {exc}"}


def _render_test_button(target_type: str, state_prefix: str, vmid_key: str = "") -> None:
    result_key = f"{state_prefix}_{target_type}_test_result"

    btn_label = f"Test {target_type.upper() if target_type == 'pct' else 'Local'} Execution"

    _, col_test, _ = st.columns([1, 2, 1])
    with col_test:
        if st.button(btn_label, key=f"btn_{state_prefix}_test_{target_type}", type="secondary", use_container_width=True):
            st.session_state.pop(result_key, None)
            with st.spinner("Please wait..."):
                if target_type == "local":
                    _test_local_connection(result_key)
                elif target_type == "pct":
                    _test_pct_connection(vmid_key, result_key)

        _res = st.session_state.get(result_key)
        if _res:
            _ls, _lm = _res["status"], _res["message"]
            if _ls == "success": st.success(_lm)
            elif _ls == "warning": st.warning(_lm)
            else: st.error(_lm)


def _test_bash_ssh_connection() -> None:
    """Quick SSH connectivity check using paramiko. Stores result in session state."""
    import socket
    import paramiko

    host     = st.session_state.get("bash_ssh_host", "").strip()
    port     = int(st.session_state.get("bash_ssh_port", 22))
    user     = st.session_state.get("bash_ssh_user", "root").strip()
    password = st.session_state.get("bash_ssh_password", "").strip() or None
    key_path = st.session_state.get("bash_ssh_key_path", "").strip() or None

    def _store(status: str, msg: str) -> None:
        st.session_state["bash_ssh_test_result"] = {"status": status, "message": msg}

    if not host:
        _store("error", "Host is required.")
        return

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"hostname": host, "port": port, "username": user, "timeout": 10}
        if key_path:
            connect_kwargs["key_filename"] = key_path
        if password:
            connect_kwargs["password"] = password
        client.connect(**connect_kwargs)
        _, stdout, _ = client.exec_command("echo ok")
        result = stdout.read().decode().strip()
        client.close()
        if result == "ok":
            _store("success", f"Connection test succeeded for {user}@{host}:{port} ✓")
        else:
            _store("warning", f"Connected but unexpected echo response: {result!r}")
    except socket.gaierror:
        _store("error", f"Could not find server '{host}' — check hostname or DNS")
    except (ConnectionRefusedError, paramiko.ssh_exception.NoValidConnectionsError):
        _store("error", f"Connection refused at {host}:{port} — SSH service may be down")
    except paramiko.AuthenticationException:
        _store("error", f"Authentication failed for {user}@{host} — check credentials")
    except socket.timeout:
        _store("error", f"Connection timed out reaching {host}:{port} — check firewall or VPN")
    except Exception as exc:
        _store("error", f"Connection failed: {exc}")


@st.dialog("Validation Set", width="large")
def _edit_validation_set_dialog(project: dict, sets: list, nonce: int, prefix: str, flush_fn, edit_idx: int = None) -> None:
    is_edit = edit_idx is not None
    si = edit_idx if is_edit else len(sets)
    target_set = sets[edit_idx] if is_edit else {}
    
    _name_key = f"{prefix}_val_add_name_{si}_{nonce}"
    _desc_key = f"{prefix}_val_add_desc_{si}_{nonce}"
    
    if _name_key not in st.session_state:
        st.session_state[_name_key] = target_set.get("name", f"Set {si + 1}")
    if _desc_key not in st.session_state:
        st.session_state[_desc_key] = target_set.get("description", "")
        
    nc1, nc2 = st.columns([1, 2])
    with nc1:
        new_name = st.text_input("Name", key=_name_key)
    with nc2:
        new_desc = st.text_input("Description", key=_desc_key)

    st.markdown("**Steps & Commands**")
    
    _steps_state_key = f"_{prefix}_val_dialog_steps_{si}_{nonce}"
    if _steps_state_key not in st.session_state:
        if is_edit:
            st.session_state[_steps_state_key] = copy.deepcopy(target_set.get("steps", []))
        else:
            st.session_state[_steps_state_key] = [{
                "_id": _next_step_id(),
                "delay_seconds": 0.0,
                "commands": [],
            }]

    _render_validation_steps(_steps_state_key, pfx=f"val_{si}_{nonce}", placeholder="e.g. ls -l", bot_type=prefix)
    
    col1, col2 = st.columns([4, 1])
    with col2:
        btn_label = "Save Set"
        if st.button(btn_label, type="primary", use_container_width=True):
            _push_undo({"desc": f"{'edit' if is_edit else 'add'} validation set", "type": "cmd",
                        "state_key": f"{prefix}_validation_sets", "data": copy.deepcopy(sets)})
            
            clean_steps = []
            for step in st.session_state[_steps_state_key]:
                step_copy = copy.deepcopy(step)
                step_copy["commands"] = [c for c in step_copy.get("commands", []) if c.get("command", "").strip() or c.get("type") == "prompt"]
                if step_copy["commands"] or step_copy.get("delay_seconds", 0) > 0:
                    clean_steps.append(step_copy)
                    
            new_set = {
                **target_set,
                "name": new_name.strip(),
                "description": new_desc.strip(),
                "enabled": target_set.get("enabled", True),
                "steps": clean_steps
            }
            new_set.pop("commands", None)
            new_set.pop("checks", None)
            
            if is_edit:
                sets[edit_idx] = new_set
            else:
                sets.append(new_set)
                
            st.session_state[f"{prefix}_validation_sets"] = sets
            st.session_state[f"{prefix}_val_editor_nonce"] = nonce + 1
            flush_fn(project)
            st.rerun()


def _render_validation_sets_ui(project: dict, prefix: str, flush_fn) -> None:
    """Validation sub-tab: Pass/Fail sets using inline data_editor."""

    state_key = f"{prefix}_validation_sets"
    sets = list(st.session_state.get(state_key, []))
    nonce = st.session_state.get(f"{prefix}_val_editor_nonce", 0)

    with st.container(border=True):
        st.subheader("✓ Pass/Fail Validation")
        st.caption("Deterministic checks that run commands and match output patterns.")

        if not sets:
            st.info("No validation sets configured. Click **＋ Add Validation Set** below.")
        else:
            mutation = None
            for si, active_set in enumerate(sets):
                _name = active_set.get("name", f"Set {si + 1}")
                _desc = active_set.get("description", "")
                
                with st.container(border=True):
                    hc_name, hc_desc, hc_enabled, hc_edit, hc_del = st.columns([2.5, 3.5, 1, 1.2, 0.8])
                    with hc_name:
                        st.markdown(f"**{_name}**")
                    with hc_desc:
                        st.caption(_desc if _desc else "_No description_")
                    with hc_enabled:
                        en_key = f"{prefix}_val_en_{si}_{nonce}"
                        new_enabled = st.checkbox("Enabled", value=active_set.get("enabled", True), key=en_key)
                        
                        if active_set.get("enabled", True) != new_enabled:
                            active_set["enabled"] = new_enabled
                            st.session_state[state_key] = sets
                            flush_fn(project)

                    with hc_edit:
                        if st.button("View / Edit", key=f"btn_{prefix}_val_edit_{si}", use_container_width=True):
                            _edit_validation_set_dialog(project, sets, nonce, prefix, flush_fn, edit_idx=si)
                    with hc_del:
                        if st.button("✕", key=f"btn_{prefix}_val_del_{si}", use_container_width=True, help="Remove validation set"):
                            mutation = ("delete", si)

            if mutation:
                op, target_idx = mutation
                if op == "delete":
                    _push_undo({"desc": f"delete set '{sets[target_idx].get('name', '')}'",
                                "type": "cmd", "state_key": state_key,
                                "data": copy.deepcopy(sets)})
                    sets.pop(target_idx)
                    st.session_state[state_key] = sets
                    st.session_state[f"{prefix}_val_editor_nonce"] = nonce + 1
                    flush_fn(project)
                    st.rerun()

    # ── Add Validation Set ────────────────────────────────────────────────────
    if st.button("＋ Add Validation Set", key=f"btn_add_set_{prefix}", type="primary"):
        _edit_validation_set_dialog(project, sets, nonce, prefix, flush_fn)


def _render_metric_thresholds_config(project: dict, prefix: str, flush_fn) -> None:
    """Configure optional severity thresholds for every Llama dashboard metric."""
    from core.metric_thresholds import (
        THRESHOLD_LEVELS, configured_thresholds, metrics_for_bot,
        threshold_direction, threshold_levels,
    )

    state_key = f"{prefix}_metric_thresholds"
    raw_thresholds = st.session_state.get(state_key, {})
    configured = configured_thresholds(raw_thresholds)
    supported_metrics = {metric for metric, _ in metrics_for_bot(prefix)}
    # A prior release exposed optional llama-cli stderr-performance controls.
    # Those values are not reliably produced and are no longer configurable;
    # discard them when this config is next saved.
    updated = {
        metric: values for metric, values in configured.items()
        if metric in supported_metrics
    }

    st.subheader("Dashboard Metric Thresholds")
    st.caption(
        "Optional run labels only — they do not alter validation or stop a run. "
        "Leave a field blank to ignore that threshold. Use the direction button for metrics "
        "where higher values are better; the first matching band wins."
    )

    threshold_layout = [2.2, 1.25, 1, 0.22, 1, 0.22, 1, 0.22, 1, 0.72]
    headers = st.columns(threshold_layout)
    headers[0].markdown("**Metric**")
    headers[1].markdown("**Direction**")
    for col_idx, (_, label, _) in zip((2, 4, 6, 8), THRESHOLD_LEVELS):
        headers[col_idx].markdown(f"**{label}**")
    headers[-1].markdown("**Reset**")

    for metric, metric_spec in metrics_for_bot(prefix):
        metric_label = metric_spec["label"]
        metric_unit = metric_spec["unit"]
        values = dict(updated.get(metric, {}))
        direction_key = f"_{prefix}_metric_threshold_direction_{metric}"
        if direction_key not in st.session_state:
            st.session_state[direction_key] = threshold_direction(
                raw_thresholds.get(metric) if isinstance(raw_thresholds, dict) else None
            )
        direction = st.session_state[direction_key]
        levels = threshold_levels(direction)

        row = st.columns(threshold_layout)
        row[0].markdown(f"**{metric_label}**  \n`{metric_unit}`")
        direction_label = "↓ Lower is better" if direction == "lower" else "↑ Higher is better"
        if row[1].button(direction_label, key=f"_{prefix}_metric_threshold_switch_{metric}",
                         help="Switch whether lower or higher values are better"):
            direction = "higher" if direction == "lower" else "lower"
            st.session_state[direction_key] = direction
            if values:
                updated[metric] = {
                    **values,
                    **({"direction": "higher"} if direction == "higher" else {}),
                }
            st.session_state[state_key] = updated
            flush_fn(project)
            st.rerun()

        for idx, (level, _, operator) in enumerate(levels):
            if idx:
                row[3 + (idx - 1) * 2].markdown(
                    f"<div style='padding-top: 0.5rem; text-align: center; font-size: 1.2rem;'>"
                    f"{'&lt;' if direction == 'higher' else '&gt;'}</div>",
                    unsafe_allow_html=True,
                )
            col = row[2 + idx * 2]
            widget_key = f"_{prefix}_metric_threshold_{metric}_{level}"
            if widget_key not in st.session_state:
                existing = values.get(level)
                st.session_state[widget_key] = "" if existing is None else f"{existing:g}"
            raw_value = col.text_input(
                f"{metric_label} {level} {operator}",
                key=widget_key,
                label_visibility="collapsed",
                placeholder=f"{operator} value",
            ).strip()
            if not raw_value:
                values.pop(level, None)
                continue
            try:
                value = float(raw_value)
                if not math.isfinite(value):
                    raise ValueError
                values[level] = value
            except ValueError:
                col.error("Number required")

        if row[-1].button("Clear", key=f"_{prefix}_metric_threshold_clear_{metric}"):
            updated.pop(metric, None)
            for level, _, _ in THRESHOLD_LEVELS:
                st.session_state.pop(f"_{prefix}_metric_threshold_{metric}_{level}", None)
            st.session_state.pop(direction_key, None)
            st.session_state[state_key] = updated
            flush_fn(project)
            st.rerun()

        if values:
            updated[metric] = {
                **values,
                **({"direction": "higher"} if direction == "higher" else {}),
            }
        else:
            updated.pop(metric, None)

    st.session_state[state_key] = updated
    flush_fn(project)




def _render_bash_bot_config(project: dict) -> None:
    """Top-level config renderer for Bash-Bot projects."""
    st.divider()

    sub_runtime, sub_val = st.tabs(["🖥  Runtime", "✅  Validation"])
    with sub_runtime:
        _render_bash_runtime(project)
    with sub_val:
        _render_validation_sets_ui(project, "bash", _flush_bash_config)


# ── Llama-CLI Bot configuration ────────────────────────────────────────────────

def _flush_llama_cli_config(project: dict) -> None:
    """Write flat llama_cli_* working keys back into the project's config bundle."""
    get_bot_plugin(project.get("type", "llama_cli_bot")).flush_mapped_config(project)
    # Derive legacy prompts/commands lists from unified steps for evaluator compat
    _steps = st.session_state.get("llama_cli_steps", [])
    _prompts  = [s["content"] for s in _steps if s.get("type") == "prompt"  and s.get("enabled", True)]
    _commands = [s["content"] for s in _steps if s.get("type") == "command" and s.get("enabled", True)]
    # Fall back to the flat lists if steps not yet migrated
    if not _steps:
        _prompts  = st.session_state.get("llama_cli_prompts", [])
        _commands = st.session_state.get("llama_cli_commands", [])

    project["config"].update({
        "execution_target":    st.session_state.get("llama_cli_execution_target", "local"),
        "pct_vmid":            st.session_state.get("llama_cli_pct_vmid", ""),
        "ssh_host":            st.session_state.get("llama_cli_ssh_host", ""),
        "ssh_port":            st.session_state.get("llama_cli_ssh_port", 22),
        "ssh_user":            st.session_state.get("llama_cli_ssh_user", "root"),
        "ssh_password":        st.session_state.get("llama_cli_ssh_password", ""),
        "ssh_key_path":        st.session_state.get("llama_cli_ssh_key_path", ""),
        "sudo":                st.session_state.get("llama_cli_sudo", False),
        "sudo_password":       (
            st.session_state.get("llama_cli_sudo_password", "") or
            (st.session_state.get("llama_cli_ssh_password", "") if st.session_state.get("llama_cli_execution_target", "local") in ("ssh", "pct") else "")
        ) if st.session_state.get("llama_cli_sudo") else "",
        "backend":             st.session_state.get("llama_cli_backend", "llama.cpp"),
        "binary_path":         st.session_state.get("llama_cli_binary_path", ""),
        "model_dir":           st.session_state.get("llama_cli_model_dir", ""),
        "model_name":          st.session_state.get("llama_cli_model_name", ""),
        "server_port":         st.session_state.get("llama_cli_server_port", 8080),
        "openai_base_url":     st.session_state.get("llama_cli_openai_base_url", ""),
        "openai_api_key":      st.session_state.get("llama_cli_openai_api_key", ""),
        "openai_verify_ssl":   st.session_state.get("llama_cli_openai_verify_ssl", True),
        "tokens":              st.session_state.get("llama_cli_tokens", 32768),
        "temperature":         st.session_state.get("llama_cli_temperature", 0.8),
        "en_temp":             st.session_state.get("llama_cli_en_temp", False),
        "gpu_layers":          st.session_state.get("llama_cli_gpu_layers", 99),
        "en_gpu_layers":       st.session_state.get("llama_cli_en_gpu_layers", False),
        "threads":             st.session_state.get("llama_cli_threads", 4),
        "en_threads":          st.session_state.get("llama_cli_en_threads", False),
        "top_k":               st.session_state.get("llama_cli_top_k", 40),
        "en_top_k":            st.session_state.get("llama_cli_en_top_k", False),
        "top_p":               st.session_state.get("llama_cli_top_p", 0.9),
        "en_top_p":            st.session_state.get("llama_cli_en_top_p", False),
        "min_p":               st.session_state.get("llama_cli_min_p", 0.1),
        "en_min_p":            st.session_state.get("llama_cli_en_min_p", False),
        "repeat_penalty":      st.session_state.get("llama_cli_repeat_penalty", 1.1),
        "en_repeat_penalty":   st.session_state.get("llama_cli_en_repeat_penalty", False),
        "predict":             st.session_state.get("llama_cli_predict", 512),
        "en_predict":          st.session_state.get("llama_cli_en_predict", False),
        "freq_penalty":        st.session_state.get("llama_cli_freq_penalty", 0.0),
        "en_freq_penalty":     st.session_state.get("llama_cli_en_freq_penalty", False),
        "rope_freq_base":      st.session_state.get("llama_cli_rope_freq_base", 10000.0),
        "en_rope_freq_base":   st.session_state.get("llama_cli_en_rope_freq_base", False),
        "rope_freq_scale":     st.session_state.get("llama_cli_rope_freq_scale", 1.0),
        "en_rope_freq_scale":  st.session_state.get("llama_cli_en_rope_freq_scale", False),
        "seed":                st.session_state.get("llama_cli_seed", -1),
        "en_seed":             st.session_state.get("llama_cli_en_seed", False),
        "flash_attn":          st.session_state.get("llama_cli_flash_attn", False),
        "custom_flags":        st.session_state.get("llama_cli_custom_flags", ""),
        "mcp_enabled":         st.session_state.get("llama_cli_mcp_enabled", False),
        "mcp_config_path":     st.session_state.get("llama_cli_mcp_config_path", ""),
        "mcp_servers":         st.session_state.get("llama_cli_mcp_servers", []),
        "startup_commands":    _clean_steps(st.session_state.get("llama_cli_startup_commands", [])),
        "completion_commands": _clean_steps(st.session_state.get("llama_cli_completion_commands", [])),
        "steps":               _steps,
        "prompts":             _prompts,
        "commands":            _commands,
        "timeout":             st.session_state.get("llama_cli_timeout", 120),
        "validation_commands": st.session_state.get("llama_cli_validation_commands", []),
        "fail_patterns":       st.session_state.get("llama_cli_fail_patterns", []),
        "validation_sets":     st.session_state.get("llama_cli_validation_sets", []),
        "metrics_matrix":      st.session_state.get("llama_cli_metrics_matrix", []),
        "metric_thresholds":   st.session_state.get("llama_cli_metric_thresholds", {}),
        "system_prompt":       st.session_state.get("llama_cli_system_prompt", ""),
        "llm_helper_backend": st.session_state.get("llama_cli_llm_helper_backend", "OpenAI-Compatible"),
        "llm_helper_openai_url": st.session_state.get("llama_cli_llm_helper_openai_url", ""),
        "llm_helper_openai_apikey": st.session_state.get("llama_cli_llm_helper_openai_apikey", ""),
        "llm_helper_openai_verify_ssl": st.session_state.get("llama_cli_llm_helper_openai_verify_ssl", True),
        "llm_helper_ollama_url": st.session_state.get("llama_cli_llm_helper_ollama_url", "http://localhost:11434"),
        "llm_helper_model": st.session_state.get("llama_cli_llm_helper_model", ""),
        "llm_helper_enabled": st.session_state.get("llama_cli_llm_helper_enabled", False),
        "llm_helper_openai_models": st.session_state.get("llama_cli_llm_helper_openai_models", []),
        "llm_helper_ollama_models": st.session_state.get("llama_cli_llm_helper_ollama_models", []),
        "llm_helper_mcp_enabled": st.session_state.get("llama_cli_llm_helper_mcp_enabled", False),
        "llm_helper_mcp_config_path": st.session_state.get("llama_cli_llm_helper_mcp_config_path", MCP_CONFIG_PATH),
        "llm_helper_mcp_tools": st.session_state.get("llama_cli_llm_helper_mcp_tools", []),
        "llm_helper_mcp_strict": st.session_state.get("llama_cli_llm_helper_mcp_strict", False),
    })
    from core.settings_store import save_settings
    save_settings(st.session_state)


def _test_llama_cli_ssh_connection() -> None:
    """Quick SSH connectivity check for llama_cli_ssh_* credentials. Stores result in session state."""
    import socket
    import paramiko

    host     = st.session_state.get("llama_cli_ssh_host", "").strip()
    port     = int(st.session_state.get("llama_cli_ssh_port", 22))
    user     = st.session_state.get("llama_cli_ssh_user", "root").strip()
    password = st.session_state.get("llama_cli_ssh_password", "").strip() or None
    key_path = st.session_state.get("llama_cli_ssh_key_path", "").strip() or None

    def _store(status: str, msg: str) -> None:
        st.session_state["llama_cli_ssh_test_result"] = {"status": status, "message": msg}

    if not host:
        _store("error", "Host is required.")
        return

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"hostname": host, "port": port, "username": user, "timeout": 10}
        if key_path:
            connect_kwargs["key_filename"] = key_path
        if password:
            connect_kwargs["password"] = password
        client.connect(**connect_kwargs)
        _, stdout, _ = client.exec_command("echo ok")
        result = stdout.read().decode().strip()
        client.close()
        if result == "ok":
            _store("success", f"Connection test succeeded for {user}@{host}:{port} ✓")
        else:
            _store("warning", f"Connected but unexpected echo response: {result!r}")
    except socket.gaierror:
        _store("error", f"Could not find server '{host}' — check hostname or DNS")
    except (ConnectionRefusedError, paramiko.ssh_exception.NoValidConnectionsError):
        _store("error", f"Connection refused at {host}:{port} — SSH service may be down")
    except paramiko.AuthenticationException:
        _store("error", f"Authentication failed for {user}@{host} — check credentials")
    except socket.timeout:
        _store("error", f"Connection timed out reaching {host}:{port} — check firewall or VPN")
    except Exception as exc:
        _store("error", f"Connection failed: {exc}")


def _test_llama_server_ssh_connection() -> None:
    """Quick SSH connectivity check for llama_server_ssh_* credentials. Stores result in session state."""
    import socket
    import paramiko

    host     = st.session_state.get("llama_server_ssh_host", "").strip()
    port     = int(st.session_state.get("llama_server_ssh_port", 22))
    user     = st.session_state.get("llama_server_ssh_user", "root").strip()
    password = st.session_state.get("llama_server_ssh_password", "").strip() or None
    key_path = st.session_state.get("llama_server_ssh_key_path", "").strip() or None

    def _store(status: str, msg: str) -> None:
        st.session_state["llama_server_ssh_test_result"] = {"status": status, "message": msg}

    if not host:
        _store("error", "Host is required.")
        return

    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        connect_kwargs: dict = {"hostname": host, "port": port, "username": user, "timeout": 10}
        if key_path:
            connect_kwargs["key_filename"] = key_path
        if password:
            connect_kwargs["password"] = password
        client.connect(**connect_kwargs)
        _, stdout, _ = client.exec_command("echo ok")
        result = stdout.read().decode().strip()
        client.close()
        if result == "ok":
            _store("success", f"Connection test succeeded for {user}@{host}:{port} ✓")
        else:
            _store("warning", f"Connected but unexpected echo response: {result!r}")
    except socket.gaierror:
        _store("error", f"Could not find server '{host}' — check hostname or DNS")
    except (ConnectionRefusedError, paramiko.ssh_exception.NoValidConnectionsError):
        _store("error", f"Connection refused at {host}:{port} — SSH service may be down")
    except paramiko.AuthenticationException:
        _store("error", f"Authentication failed for {user}@{host} — check credentials")
    except socket.timeout:
        _store("error", f"Connection timed out reaching {host}:{port} — check firewall or VPN")
    except Exception as exc:
        _store("error", f"Connection failed: {exc}")


def _scan_models(project: dict) -> None:
    """Scan local or remote machine for .gguf models and populate discovered list."""
    model_dir = st.session_state.get("llama_cli_model_dir", "").strip()
    target    = st.session_state.get("llama_cli_execution_target", "local")
    if not model_dir:
        st.warning("Set Model Directory first.")
        return
    if target == "local":
        from core.models import scan_gguf_models
        models = scan_gguf_models(model_dir)
    else:
        import shlex
        _flush_llama_cli_config(project)
        cfg = project["config"]
        from core.environment import SSHEnvironment
        env = SSHEnvironment(
            host=cfg.get("ssh_host", ""), port=int(cfg.get("ssh_port", 22)),
            username=cfg.get("ssh_user", "root"),
            password=cfg.get("ssh_password") or None,
            key_path=cfg.get("ssh_key_path") or None,
            remote_cwd=".",
        )
        try:
            if model_dir.startswith("~/"):
                model_dir_sh = '"$HOME/' + model_dir[2:] + '"'
            else:
                model_dir_sh = f'"{model_dir}"'
            res   = env.execute(
                f'find {model_dir_sh} -name "*.gguf" -not -name "ggml-vocab-*"',
                timeout=15,
            )
            paths  = [l.strip() for l in res["stdout"].splitlines() if l.strip()]
            
            # resolve the base dir on the remote so we can make relative paths
            base_dir = model_dir
            if base_dir.startswith("~/"):
                try:
                    home_res = env.execute("echo $HOME")
                    home = home_res["stdout"].strip()
                    base_dir = home + "/" + base_dir[2:]
                except Exception:
                    pass
            
            models = []
            for p in paths:
                rel = p
                if p.startswith(base_dir):
                    rel = p[len(base_dir):].lstrip("/")
                if not rel:
                    rel = p.split("/")[-1]
                models.append({"name": rel, "path": p})
        finally:
            env.close()
    st.session_state["llama_cli_discovered_models"] = models
    if not models:
        st.session_state["llama_cli_model_name"] = ""
        st.warning("No .gguf models found in that directory.")
    else:
        # If the previously selected model isn't in the new scan results, auto-select the first one
        current = st.session_state.get("llama_cli_model_name", "")
        new_names = [m["name"] for m in models]
        if current not in new_names:
            st.session_state["llama_cli_model_name"] = new_names[0]
        st.success(f"Found {len(models)} model(s).")


def _fetch_mcp_servers(project: dict) -> None:
    """Validate a local MCP manifest and create one entry per declared tool."""
    cfg_path = st.session_state.get("llama_cli_mcp_config_path", "").strip() or MCP_CONFIG_PATH
    try:
        declared = load_mcp_tool_config(cfg_path)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state["llama_cli_mcp_servers"] = merge_mcp_tool_selections(
        declared, st.session_state.get("llama_cli_mcp_servers", []),
    )
    st.success(f"Loaded and validated {len(declared)} MCP tool(s).")


def _test_llama_cli_run(project: dict) -> None:
    import shlex
    import os
    
    target = st.session_state.get("llama_cli_execution_target", "local")
    env = None
    
    if target == "local":
        from core.environment import LocalEnvironment
        env = LocalEnvironment()
    elif target == "pct":
        from core.environment import PCTEnvironment, LocalEnvironment
        vmid = st.session_state.get("llama_cli_pct_vmid", "").strip()
        if not vmid:
            st.session_state["_llama_svc_result"] = ("error", "VMID is required for PCT target.", "")
            return
        env = PCTEnvironment(vmid, LocalEnvironment())
    elif target == "ssh":
        from core.environment import SSHEnvironment
        _flush_llama_cli_config(project)
        cfg = project["config"]
        host = cfg.get("ssh_host", "").strip()
        if not host:
            st.session_state["_llama_svc_result"] = ("error", "SSH Host is required.", "")
            return
        env = SSHEnvironment(
            host=host, port=int(cfg.get("ssh_port", 22)),
            username=cfg.get("ssh_user", "root"),
            password=cfg.get("ssh_password") or None,
            key_path=cfg.get("ssh_key_path") or None,
            remote_cwd=".",
        )

    try:
        use_sudo = st.session_state.get("llama_cli_sudo", False)
        sudo_pw  = (st.session_state.get("llama_cli_sudo_password") or "").strip()
        
        _disc = st.session_state.get("llama_cli_discovered_models", [])
        _mname = st.session_state.get("llama_cli_model_name", "")
        _mdir  = st.session_state.get("llama_cli_model_dir", "").strip()
        _mpath = next(
            (m["path"] for m in _disc if m["name"] == _mname),
            os.path.join(_mdir, _mname) if _mdir and _mname else _mname,
        )
        if not _mpath:
            st.session_state["_llama_svc_result"] = ("error", "No model selected.", "")
            return

        _bin  = (st.session_state.get("llama_cli_binary_path") or "").strip()
        if not _bin:
            st.session_state["_llama_svc_result"] = ("error", "No binary path configured.", "")
            return

        if os.path.isdir(_bin) and target == "local":
            _bin = os.path.join(_bin, "llama-cli")
        
        _mpath_quoted = f'\"$HOME/\"{shlex.quote(_mpath[2:])}' if _mpath.startswith("~/") else shlex.quote(_mpath)
        cmd = f"{_bin} -m {_mpath_quoted} --prompt \"Hello, world!\" -n 1 --simple-io --no-display-prompt --single-turn --log-disable"
        
        if use_sudo:
            if sudo_pw:
                cmd = f"echo {shlex.quote(sudo_pw)} | sudo -S bash -c {shlex.quote(cmd)}"
            else:
                cmd = f"sudo {cmd}"

        res = env.execute(cmd, timeout=30)
        
        if res["exit_code"] != 0:
            err = res.get("stderr", "").strip() or res.get("stdout", "").strip()
            import re
            err = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', err)
            st.session_state["_llama_svc_result"] = ("error", f"Test failed (exit {res['exit_code']}): {err[:400]}", cmd)
        else:
            out = res.get("stdout", "").strip()
            import re
            out = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', out)
            st.session_state["_llama_svc_result"] = ("ok", "Test successful! (Model loaded and executed correctly)", cmd)

    except Exception as exc:
        st.session_state["_llama_svc_result"] = ("error", f"Execution error: {exc}", "")
    finally:
        if env and hasattr(env, "close"):
            try:
                env.close()
            except Exception:
                pass


def _render_llama_cli_runtime(project: dict) -> None:
    """Runtime sub-tab for Llama-CLI-Bot: target, model setup, MCP servers, timeout."""

    with st.expander("Execution Target", expanded=True):
        target = st.radio(
            "Mode",
            options=["local", "ssh", "pct"],
            format_func=lambda v: {"local": "Local", "ssh": "SSH (Remote)", "pct": "PCT (Proxmox LXC)"}.get(v, v),
            key="llama_cli_execution_target",
            help="Run locally, via SSH, or inside a Proxmox LXC container via pct.",
            horizontal=True,
        )
        st.checkbox(
            "Run commands with sudo",
            key="llama_cli_sudo",
            help="Prefix shell commands and the llama-cli invocation with `sudo`.",
        )
        if st.session_state.get("llama_cli_sudo"):
            st.text_input(
                "Sudo password",
                key="llama_cli_sudo_password",
                type="password",
                help="Piped to `sudo -S`. Leave blank to reuse the SSH password or if passwordless sudo (NOPASSWD) is configured.",
            )
        if target == "local":
            st.divider()
            _render_test_button("local", "llama_cli")
        elif target == "pct":
            st.divider()
            st.text_input("LXC Container ID (VMID)", key="llama_cli_pct_vmid", placeholder="100")
            _render_test_button("pct", "llama_cli", "llama_cli_pct_vmid")
        elif target == "ssh":
            st.divider()
            st.markdown("**SSH Credentials**")
            c_host, c_port = st.columns([4, 1])
            with c_host:
                st.text_input("Host", key="llama_cli_ssh_host", placeholder="192.168.1.100")
            with c_port:
                st.number_input(
                    "Port", min_value=1, max_value=65535,
                    value=st.session_state.get("llama_cli_ssh_port", 22),
                    key="llama_cli_ssh_port",
                )
            c_user, c_pass = st.columns([1, 1])
            with c_user:
                st.text_input("Username", key="llama_cli_ssh_user")
            with c_pass:
                st.text_input("Password", key="llama_cli_ssh_password",
                              type="password",
                              help="Leave empty to use key-based auth.")
            st.text_input("Key Path", key="llama_cli_ssh_key_path",
                          placeholder="~/.ssh/id_rsa")
            _, col_test, _ = st.columns([1, 2, 1])
            with col_test:
                if st.button("Test Connection", key="btn_llama_test_ssh", type="secondary", use_container_width=True):
                    st.session_state.pop("llama_cli_ssh_test_result", None)
                    with st.spinner("Please wait..."):
                        _test_llama_cli_ssh_connection()

            _llama_ssh_result = st.session_state.get("llama_cli_ssh_test_result")
            if _llama_ssh_result:
                _ls, _lm = _llama_ssh_result["status"], _llama_ssh_result["message"]
                if _ls == "success":
                    st.success(_lm)
                elif _ls == "warning":
                    st.warning(_lm)
                else:
                    st.error(_lm)

    with st.expander("Model Setup", expanded=True):
        st.session_state["llama_cli_backend"] = "llama-cli"
        backend = "llama-cli"

        if backend == "llama-cli":
            st.text_input(
                "Binary Path",
                key="llama_cli_binary_path",
                placeholder="/usr/local/bin/llama-cli",
                help="Full path to the llama-cli executable (batch-inference binary). E.g. /usr/local/bin/llama-cli. Note: llama-server is the HTTP server — this field needs llama-cli.",
            )
            col_dir, col_scan = st.columns([4, 1])
            with col_dir:
                st.text_input(
                    "Model Directory",
                    key="llama_cli_model_dir",
                    placeholder="/home/user/models",
                    help="Directory to scan for .gguf model files.",
                )
            with col_scan:
                st.write("")
                st.write("")
                if st.button("Scan", key="btn_llama_scan_models", use_container_width=True):
                    _scan_models(project)
            discovered: list = st.session_state.get("llama_cli_discovered_models", [])
            model_names = []
            for m in discovered:
                if m["name"] not in model_names:
                    model_names.append(m["name"])

            current = st.session_state.get("llama_cli_model_name", "")
            default_idx = model_names.index(current) if current in model_names else 0

            if model_names:
                st.selectbox(
                    "Model", options=model_names, index=default_idx,
                    key="llama_cli_model_name",
                )
            else:
                st.text_input(
                    "Model",
                    key="llama_cli_model_name",
                    help="Enter model name manually, or use Scan to find models."
                )

            st.session_state.setdefault("llama_cli_tokens", 32768)
            st.number_input(
                "Context Window (tokens)",
                min_value=128, max_value=131072, step=256,
                key="llama_cli_tokens",
                help="Maximum context length passed to llama-cli via -c.",
            )

            st.text_input(
                "Custom Flags",
                key="llama_cli_custom_flags",
                placeholder="-ngl 99 --temp 0.7",
                help="Additional flags to pass to llama-cli.",
            )
        else:
            col_url, col_fetch = st.columns([5, 1])
            with col_url:
                _url = st.text_input(
                    "Instance URL",
                    key="_llama_openai_url_widget",
                    value=st.session_state.get("llama_cli_openai_base_url", ""),
                    placeholder="http://localhost:8080",
                    help="Base URL of any OpenAI-compatible server. Examples: http://localhost:8080 (llama-server), https://api.openai.com (OpenAI), http://localhost:1234 (LM Studio). Do not include /v1 — it is appended automatically.",
                )
                st.session_state["llama_cli_openai_base_url"] = _url
            with col_fetch:
                st.write("")
                st.write("")
                if st.button("Fetch", key="btn_llama_fetch_openai_models", use_container_width=True):
                    _base = (st.session_state.get("llama_cli_openai_base_url") or "").strip()
                    if _base:
                        from core.models import fetch_llama_cpp_models
                        _found, _err = fetch_llama_cpp_models(_base)
                        if _found:
                            st.session_state["llama_cli_openai_models"] = _found
                            st.success(f"{len(_found)} model(s) found")
                        else:
                            _clear_llama_openai_model_selection()
                            st.error(_err or "No models returned — is the server running?")
                    else:
                        _clear_llama_openai_model_selection()
                        st.warning("Enter an Instance URL first.")

            _ssl = st.checkbox(
                "Require SSL Certificate Verification",
                key="_llama_openai_ssl_widget",
                value=st.session_state.get("llama_cli_openai_verify_ssl", True),
                help="Only applies to https:// URLs — ignored for plain HTTP. Uncheck for self-signed certs.",
                disabled=not _url.strip().lower().startswith("https://"),
            )
            st.session_state["llama_cli_openai_verify_ssl"] = _ssl

            _apikey = st.text_input(
                "API Key (optional)",
                key="_llama_openai_apikey_widget",
                type="password",
                help="API key for the endpoint. Leave empty for unauthenticated servers.",
            )
            st.session_state["llama_cli_openai_api_key"] = _apikey

            _openai_models = st.session_state.get("llama_cli_openai_models", [])
            if _openai_models:
                _model_names = [m["name"] for m in _openai_models]
                _cur = st.session_state.get("llama_cli_model_name", "")
                _idx = _model_names.index(_cur) if _cur in _model_names else 0
                _chosen = st.selectbox("Model", options=_model_names, index=_idx, key="_llama_openai_model_sel")
                st.session_state["llama_cli_model_name"] = _chosen
            else:
                # No models fetched yet — allow manual entry so users can always
                # specify a model name (e.g. "llama3.2", "phi3") without needing
                # a live Fetch response.
                _manual_model = st.text_input(
                    "Model Name (optional)",
                    key="_llama_openai_model_manual",
                    value=st.session_state.get("llama_cli_model_name", ""),
                    placeholder="e.g. llama3.2, phi3, gpt-4o — leave blank to use server default",
                    help="Specify the model to request from the server. Leave blank to use whatever model the server has loaded.",
                )
                st.session_state["llama_cli_model_name"] = _manual_model

            if st.button("Check Status", key="btn_llama_check_openai_status", use_container_width=True):
                _base = (st.session_state.get("llama_cli_openai_base_url") or "").strip()
                if _base:
                    _info = llama_server.get_server_info(_base)
                    if _info:
                        _mname = (_info.get("model_path") or "").split("/")[-1] or "?"
                        st.success(f"Online  |  model: `{_mname}`  |  Context Window Length: `{_info.get('n_ctx') or '?'}`")
                    else:
                        st.error("Could not reach server.")
                else:
                    st.warning("Enter an Instance URL first.")



        with st.expander("Advanced Options", expanded=False):
            def _adv_opt(
                col,
                label,
                key_suffix,
                min_v,
                max_v,
                step,
                help_text,
                is_float=False,
                value_key_suffix=None,
                default_value=None,
            ):
                with col:
                    st.session_state.setdefault(f"llama_cli_en_{key_suffix}", False)
                    value_key = f"llama_cli_{value_key_suffix or key_suffix}"
                    if default_value is not None:
                        st.session_state.setdefault(value_key, default_value)
                    c1, c2 = st.columns([0.2, 0.8], gap="small")
                    with c1:
                        st.write("")
                        st.write("")
                        _en = st.checkbox(f"en_{key_suffix}", key=f"llama_cli_en_{key_suffix}", label_visibility="collapsed", help=f"Enable {label}")
                    with c2:
                        st.number_input(
                            label,
                            min_value=float(min_v) if is_float else int(min_v),
                            max_value=float(max_v) if is_float else int(max_v),
                            step=float(step) if is_float else int(step),
                            key=value_key,
                            disabled=not _en,
                            help=help_text,
                            format="%.2f" if is_float else None
                        )

            adv_cols = st.columns(4)
            _adv_opt(
                adv_cols[0],
                "Temperature",
                "temp",
                0.0,
                2.0,
                0.1,
                "Higher values = more random (--temp).",
                True,
                value_key_suffix="temperature",
                default_value=0.8,
            )
            _adv_opt(adv_cols[1], "GPU Layers", "gpu_layers", 0, 999, 1, "Layers to offload to GPU (-ngl).", default_value=99)
            _adv_opt(adv_cols[2], "Threads", "threads", 1, 256, 1, "CPU threads to use (-t).", default_value=4)
            _adv_opt(adv_cols[3], "Top K", "top_k", 0, 1000, 1, "Limit next token selection (--top-k).", default_value=40)
            
            _adv_opt(adv_cols[0], "Top P", "top_p", 0.0, 1.0, 0.05, "Cumulative probability (--top-p).", True, default_value=0.9)
            _adv_opt(adv_cols[1], "Min P", "min_p", 0.0, 1.0, 0.05, "Minimum probability (--min-p).", True, default_value=0.1)
            _adv_opt(adv_cols[2], "Repeat Pen.", "repeat_penalty", 0.0, 2.0, 0.1, "Penalize repetition (--repeat-penalty).", True, default_value=1.1)
            _adv_opt(adv_cols[3], "Freq Pen.", "freq_penalty", 0.0, 2.0, 0.1, "Frequency penalty (--freq-penalty).", True, default_value=0.0)
            
            _adv_opt(adv_cols[0], "Predict", "predict", -1, 131072, 128, "Tokens to predict (-n).", default_value=512)
            _adv_opt(adv_cols[1], "Seed", "seed", -1, 2147483647, 1, "RNG seed (-1 for random) (--seed).", default_value=-1)
            _adv_opt(adv_cols[2], "RoPE Base", "rope_freq_base", 1000.0, 10000000.0, 1000.0, "RoPE base frequency (--rope-freq-base).", True, default_value=10000.0)
            _adv_opt(adv_cols[3], "RoPE Scale", "rope_freq_scale", 0.0, 100.0, 0.1, "RoPE frequency scale (--rope-freq-scale).", True, default_value=1.0)
            
            with adv_cols[0]:
                st.session_state.setdefault("llama_cli_flash_attn", False)
                st.write("")
                st.write("")
                st.checkbox("Flash Attn", key="llama_cli_flash_attn", help="Use Flash Attention (-fa).")

        st.divider()
        _backend = st.session_state.get("llama_cli_backend", "llama.cpp")
        
        _, _col_svc, _ = st.columns([1, 2, 1])
        with _col_svc:
            if st.button("Test Run", key="btn_llama_test_run",
                         use_container_width=True, type="primary",
                         help="Run a test prompt (llama-cli), or test connectivity (OpenAI)."):
                st.session_state.pop("_llama_svc_result", None)
                with st.spinner("Testing model execution... (Loading the model into memory may take a few minutes)"):
                    if _backend.lower().startswith("openai"):
                        _base = (st.session_state.get("llama_cli_openai_base_url") or "").strip()
                        if not _base:
                            st.session_state["_llama_svc_result"] = ("error", "No Instance URL configured.", "")
                        else:
                            _info = llama_server.get_server_info(_base)
                            if _info:
                                _mn = (_info.get("model_path") or "").split("/")[-1] or "?"
                                st.session_state["_llama_svc_result"] = (
                                    "ok",
                                    f"Online  |  model: `{_mn}`  |  Context Window Length: `{_info.get('n_ctx') or '?'}`",
                                    "",
                                )
                            else:
                                st.session_state["_llama_svc_result"] = (
                                    "error",
                                    f"Could not reach `{_base}` — check URL and network.",
                                    "",
                                )
                    else:
                        _test_llama_cli_run(project)

        # ── Test Run status display (always shown) ─────────────────────────────
        _svc_result = st.session_state.get("_llama_svc_result")
        if _svc_result:
            _level, _msg, _cmd = _svc_result
            if _level == "ok":
                st.success(_msg)
                if _cmd:
                    st.code(_cmd, language="bash")
            else:
                st.error(_msg)
                if _cmd:
                    st.code(_cmd, language="bash")

    _render_mcp_tool_panel(project, "llama_cli")

    with st.expander("Commands", expanded=True):
        tab_llm, tab_startup, tab_completion = st.tabs(
            ["🤖 LLM Judge", "▶  Startup", "⏹  Completion"]
        )
        with tab_llm:
            _render_llm_prompt_helper_tab("llama_cli")
        with tab_startup:
            st.caption(
                "Commands run when execution starts, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="llama_cli_startup_commands",
                pfx="llama_startup",
                placeholder="e.g. /bin/bash setup.sh",
            )
        with tab_completion:
            st.caption(
                "Cleanup commands run after startup and validation finish, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="llama_cli_completion_commands",
                pfx="llama_completion",
                placeholder="e.g. rm -rf /tmp/test_workdir",
            )

    _flush_llama_cli_config(project)


def _coerce_step_types(raw: list) -> list:
    """Normalise a steps list; handles legacy dicts and assigns _id fields."""
    if not raw:
        return []
    out = []
    for item in raw:
        if isinstance(item, dict) and "type" in item:
            entry = {
                "type":            item.get("type", "prompt"),
                "content":         str(item.get("content", "")),
                "enabled":         bool(item.get("enabled", True)),
                "long_running":    bool(item.get("long_running", False)),
                "timeout_seconds": int(item.get("timeout_seconds", 60)),
            }
            if "_id" in item:
                entry["_id"] = item["_id"]
            out.append(entry)
    return out


def _ensure_unified_step_ids(steps: list) -> list:
    """Assign stable _id to any unified step that lacks one."""
    seen: set = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "_id" not in step or step["_id"] in seen:
            step["_id"] = _next_step_id()
        seen.add(step["_id"])
    return steps


def _render_step_editor(state_key: str, pfx: str) -> None:
    """
    Unified step editor: each step has a Prompt | Command radio type.
    Mirrors the structural pattern of _render_command_steps.
    """
    raw   = st.session_state.get(state_key, [])
    steps = _ensure_unified_step_ids(_coerce_step_types(raw))

    mutation = None
    toggle   = None

    if not steps:
        st.caption("No steps. Click **+ Add Step** to begin.")

    for si, step in enumerate(steps):
        step_id = step["_id"]
        stype   = step.get("type", "prompt")

        border_color = "#2dd4bf" if stype == "prompt" else "#f0883e"
        with st.container(border=True):
            # Custom left-border via inline style
            st.markdown(
                f'<div style="border-left:3px solid {border_color};'
                f'margin:-8px -12px 6px;padding:2px 12px 0;border-radius:2px 0 0 2px;'
                f'opacity:0.9"></div>',
                unsafe_allow_html=True,
            )

            # ── Step header row ──────────────────────────────────────────
            hc1, hc2, hc3, hc4 = st.columns([4, 0.7, 0.7, 0.7])
            with hc1:
                _open    = st.session_state.get(f"_us_{pfx}_{step_id}_open", True)
                _preview = (step.get("content", "")[:42] + "…") if len(step.get("content", "")) > 42 else step.get("content", "")
                _icon    = "▼" if _open else "▶"
                _badge   = "PROMPT" if stype == "prompt" else "CMD"
                _label   = f"{_icon} Step {si + 1}  [{_badge}]{(' — ' + _preview) if _preview else ' — (empty)'}"
                if st.button(_label, key=f"_us_{pfx}_{step_id}_toggle",
                             use_container_width=True, help="Collapse/expand"):
                    toggle = (f"_us_{pfx}_{step_id}_open", not _open)
            with hc2:
                if st.button("↑", key=f"_us_{pfx}_{step_id}_up",
                             disabled=(si == 0), use_container_width=True):
                    mutation = ("move", si, si - 1)
            with hc3:
                if st.button("↓", key=f"_us_{pfx}_{step_id}_dn",
                             disabled=(si == len(steps) - 1), use_container_width=True):
                    mutation = ("move", si, si + 1)
            with hc4:
                if st.button("✕", key=f"_us_{pfx}_{step_id}_del",
                             use_container_width=True):
                    mutation = ("del", si)

            # ── Collapsible body ─────────────────────────────────────────
            if st.session_state.get(f"_us_{pfx}_{step_id}_open", True):
                # Type radio
                type_key  = f"_us_{pfx}_{step_id}_type"
                new_type  = st.radio(
                    "Step Type",
                    options=["Prompt", "Command"],
                    index=0 if stype == "prompt" else 1,
                    key=type_key,
                    horizontal=True,
                    label_visibility="collapsed",
                )
                step["type"] = "prompt" if new_type == "Prompt" else "command"

                # Content input
                content_key    = f"_us_{pfx}_{step_id}_content"
                placeholder    = "Describe what you want the model to do…" if step["type"] == "prompt" else "echo hello"
                step["content"] = st.text_area(
                    "Content",
                    value=step.get("content", ""),
                    placeholder=placeholder,
                    key=content_key,
                    height=80,
                    label_visibility="collapsed",
                )

                # Meta row
                mc1, mc2, mcl_to, mcv_to = st.columns([1.2, 1.5, 0.8, 1.7])
                with mc1:
                    step["enabled"] = st.checkbox(
                        "Enabled",
                        value=step.get("enabled", True),
                        key=f"_us_{pfx}_{step_id}_en",
                    )
                with mc2:
                    step["long_running"] = st.checkbox(
                        "Long-running",
                        value=step.get("long_running", False),
                        key=f"_us_{pfx}_{step_id}_lr",
                        help="Disables per-step timeout.",
                    )
                with mcl_to:
                    st.markdown("<div style='margin-top: 6px; text-align: right; font-size: 14px;'>Timeout (s)</div>", unsafe_allow_html=True)
                with mcv_to:
                    step["timeout_seconds"] = st.number_input(
                        "Timeout (s)",
                        min_value=0.1, max_value=3600.0, step=1.0,
                        value=float(step.get("timeout_seconds", 60.0)),
                        key=f"_us_{pfx}_{step_id}_to",
                        label_visibility="collapsed",
                        disabled=step.get("long_running", False),
                    )

    if st.button("+ Add Step", key=f"_us_{pfx}_addstep", type="primary"):
        mutation = ("add",)

    if toggle:
        st.session_state[toggle[0]] = toggle[1]
        st.rerun()

    if mutation:
        _push_undo({"desc": f"edit steps ({pfx})", "type": "cmd",
                    "state_key": state_key, "data": copy.deepcopy(steps)})
        m = mutation
        if m[0] == "add":
            steps.append({
                "_id":             _next_step_id(),
                "type":            "prompt",
                "content":         "",
                "enabled":         True,
                "long_running":    False,
                "timeout_seconds": 60,
            })
        elif m[0] == "del":
            steps.pop(m[1])
        elif m[0] == "move":
            i1, i2 = m[1], m[2]
            steps[i1], steps[i2] = steps[i2], steps[i1]
        st.session_state[state_key] = steps
        st.rerun()
    else:
        st.session_state[state_key] = steps



def _render_llama_cli_validation(project: dict) -> None:
    """Validation sub-tab for Llama-CLI-Bot pass/fail sets."""
    _render_validation_sets_ui(project, "llama_cli", _flush_llama_cli_config)

    _flush_llama_cli_config(project)


def _render_llama_cli_bot_config(project: dict) -> None:
    """Top-level renderer for Llama-CLI bot configuration."""
    st.divider()

    sub_runtime, sub_val, sub_metrics = st.tabs(
        ["🖥  Runtime", "✅  Validation", "📊  Metrics Config"]
    )
    with sub_runtime:
        _render_llama_cli_runtime(project)
    with sub_val:
        _render_llama_cli_validation(project)
    with sub_metrics:
        _render_metric_thresholds_config(project, "llama_cli", _flush_llama_cli_config)


# ── Llama-Server Bot configuration ────────────────────────────────────────────

def _llama_server_client_base_url(host: str, port: int) -> str:
    host = (host or "127.0.0.1").strip()
    client_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
    return f"http://{client_host}:{int(port)}"


def _flush_llama_server_config(project: dict) -> None:
    """Write flat llama_server_* working keys back into the project's config bundle."""
    get_bot_plugin(project.get("type", "llama_server_bot")).flush_mapped_config(project)
    _steps = st.session_state.get("llama_server_steps", [])
    _prompts = [s["content"] for s in _steps if s.get("type") == "prompt" and s.get("enabled", True)]
    _commands = [s["content"] for s in _steps if s.get("type") == "command" and s.get("enabled", True)]
    if not _steps:
        _prompts = st.session_state.get("llama_server_prompts", [])
        _commands = st.session_state.get("llama_server_commands", [])

    host = (st.session_state.get("llama_server_server_host") or "127.0.0.1").strip()
    port = int(st.session_state.get("llama_server_server_port") or 8080)
    base_url = _llama_server_client_base_url(host, port)
    st.session_state["llama_server_openai_base_url"] = base_url

    project["config"].update({
        "execution_target":    st.session_state.get("llama_server_execution_target", "local"),
        "pct_vmid":            st.session_state.get("llama_server_pct_vmid", ""),
        "ssh_host":            st.session_state.get("llama_server_ssh_host", ""),
        "ssh_port":            st.session_state.get("llama_server_ssh_port", 22),
        "ssh_user":            st.session_state.get("llama_server_ssh_user", "root"),
        "ssh_password":        st.session_state.get("llama_server_ssh_password", ""),
        "ssh_key_path":        st.session_state.get("llama_server_ssh_key_path", ""),
        "sudo":                st.session_state.get("llama_server_sudo", False),
        "sudo_password":       (
            st.session_state.get("llama_server_sudo_password", "") or
            (st.session_state.get("llama_server_ssh_password", "") if st.session_state.get("llama_server_execution_target", "local") in ("ssh", "pct") else "")
        ) if st.session_state.get("llama_server_sudo") else "",
        "backend":             "llama-server (managed)",
        "binary_path":         st.session_state.get("llama_server_binary_path", ""),
        "model_dir":           st.session_state.get("llama_server_model_dir", ""),
        "model_name":          st.session_state.get("llama_server_model_name", ""),
        "tokens":              st.session_state.get("llama_server_tokens", 32768),
        "server_ready_timeout": st.session_state.get("llama_server_ready_timeout", 300),
        "en_temp":             st.session_state.get("llama_server_en_temp", False),
        "temperature":         st.session_state.get("llama_server_temperature", 0.8),
        "en_gpu_layers":       st.session_state.get("llama_server_en_gpu_layers", False),
        "gpu_layers":          st.session_state.get("llama_server_gpu_layers", 99),
        "en_threads":          st.session_state.get("llama_server_en_threads", False),
        "threads":             st.session_state.get("llama_server_threads", 4),
        "flash_attn":          st.session_state.get("llama_server_flash_attn", False),
        "en_top_k":            st.session_state.get("llama_server_en_top_k", False),
        "top_k":               st.session_state.get("llama_server_top_k", 40),
        "en_top_p":            st.session_state.get("llama_server_en_top_p", False),
        "top_p":               st.session_state.get("llama_server_top_p", 0.9),
        "en_min_p":            st.session_state.get("llama_server_en_min_p", False),
        "min_p":               st.session_state.get("llama_server_min_p", 0.1),
        "en_repeat_penalty":   st.session_state.get("llama_server_en_repeat_penalty", False),
        "repeat_penalty":      st.session_state.get("llama_server_repeat_penalty", 1.1),
        "en_freq_penalty":     st.session_state.get("llama_server_en_freq_penalty", False),
        "freq_penalty":        st.session_state.get("llama_server_freq_penalty", 0.0),
        "en_predict":          st.session_state.get("llama_server_en_predict", False),
        "predict":             st.session_state.get("llama_server_predict", 512),
        "en_rope_freq_base":   st.session_state.get("llama_server_en_rope_freq_base", False),
        "rope_freq_base":      st.session_state.get("llama_server_rope_freq_base", 10000.0),
        "en_rope_freq_scale":  st.session_state.get("llama_server_en_rope_freq_scale", False),
        "rope_freq_scale":     st.session_state.get("llama_server_rope_freq_scale", 1.0),
        "en_seed":             st.session_state.get("llama_server_en_seed", False),
        "seed":                st.session_state.get("llama_server_seed", -1),
        "custom_flags":        st.session_state.get("llama_server_custom_flags", ""),
        "server_host":         host,
        "server_port":         port,
        "openai_base_url":     base_url,
        "openai_api_key":      st.session_state.get("llama_server_openai_api_key", ""),
        "openai_verify_ssl":   st.session_state.get("llama_server_openai_verify_ssl", True),
        "mcp_enabled":         st.session_state.get("llama_server_mcp_enabled", False),
        "mcp_config_path":     st.session_state.get("llama_server_mcp_config_path", ""),
        "mcp_servers":         st.session_state.get("llama_server_mcp_servers", []),
        "startup_commands":    _clean_steps(st.session_state.get("llama_server_startup_commands", [])),
        "completion_commands": _clean_steps(st.session_state.get("llama_server_completion_commands", [])),
        "steps":               _steps,
        "prompts":             _prompts,
        "commands":            _commands,
        "timeout":             st.session_state.get("llama_server_timeout", 120),
        "validation_commands": st.session_state.get("llama_server_validation_commands", []),
        "fail_patterns":       st.session_state.get("llama_server_fail_patterns", []),
        "validation_sets":     st.session_state.get("llama_server_validation_sets", []),
        "metrics_matrix":      st.session_state.get("llama_server_metrics_matrix", []),
        "metric_thresholds":   st.session_state.get("llama_server_metric_thresholds", {}),
        "system_prompt":       st.session_state.get("llama_server_system_prompt", ""),
        "llm_helper_backend": st.session_state.get("llama_server_llm_helper_backend", "OpenAI-Compatible"),
        "llm_helper_openai_url": st.session_state.get("llama_server_llm_helper_openai_url", ""),
        "llm_helper_openai_apikey": st.session_state.get("llama_server_llm_helper_openai_apikey", ""),
        "llm_helper_openai_verify_ssl": st.session_state.get("llama_server_llm_helper_openai_verify_ssl", True),
        "llm_helper_ollama_url": st.session_state.get("llama_server_llm_helper_ollama_url", "http://localhost:11434"),
        "llm_helper_model": st.session_state.get("llama_server_llm_helper_model", ""),
        "llm_helper_enabled": st.session_state.get("llama_server_llm_helper_enabled", False),
        "llm_helper_openai_models": st.session_state.get("llama_server_llm_helper_openai_models", []),
        "llm_helper_ollama_models": st.session_state.get("llama_server_llm_helper_ollama_models", []),
        "llm_helper_mcp_enabled": st.session_state.get("llama_server_llm_helper_mcp_enabled", False),
        "llm_helper_mcp_config_path": st.session_state.get("llama_server_llm_helper_mcp_config_path", MCP_CONFIG_PATH),
        "llm_helper_mcp_tools": st.session_state.get("llama_server_llm_helper_mcp_tools", []),
        "llm_helper_mcp_strict": st.session_state.get("llama_server_llm_helper_mcp_strict", False),
    })
    from core.settings_store import save_settings
    save_settings(st.session_state)


def _scan_llama_server_models(project: dict) -> None:
    """Scan local or remote machine for .gguf models for managed llama-server.

    The Model Directory is scanned wherever Execution Target points (local/ssh).
    For SSH, the managed llama-server process also launches on that same remote
    host (see core.remote_server); for PCT it does not — see core.evaluator's
    managed-server path.
    """
    model_dir = st.session_state.get("llama_server_model_dir", "").strip()
    target    = st.session_state.get("llama_server_execution_target", "local")
    if not model_dir:
        st.warning("Set Model Directory first.")
        return
    if target == "local":
        from core.models import scan_gguf_models
        models = scan_gguf_models(model_dir)
    else:
        _flush_llama_server_config(project)
        cfg = project["config"]
        from core.environment import SSHEnvironment
        env = SSHEnvironment(
            host=cfg.get("ssh_host", ""), port=int(cfg.get("ssh_port", 22)),
            username=cfg.get("ssh_user", "root"),
            password=cfg.get("ssh_password") or None,
            key_path=cfg.get("ssh_key_path") or None,
            remote_cwd=".",
        )
        try:
            if model_dir.startswith("~/"):
                model_dir_sh = '"$HOME/' + model_dir[2:] + '"'
            else:
                model_dir_sh = f'"{model_dir}"'
            res   = env.execute(
                f'find {model_dir_sh} -name "*.gguf" -not -name "ggml-vocab-*"',
                timeout=15,
            )
            paths  = [l.strip() for l in res["stdout"].splitlines() if l.strip()]

            # resolve the base dir on the remote so we can make relative paths
            base_dir = model_dir
            if base_dir.startswith("~/"):
                try:
                    home_res = env.execute("echo $HOME")
                    home = home_res["stdout"].strip()
                    base_dir = home + "/" + base_dir[2:]
                except Exception:
                    pass

            models = []
            for p in paths:
                rel = p
                if p.startswith(base_dir):
                    rel = p[len(base_dir):].lstrip("/")
                if not rel:
                    rel = p.split("/")[-1]
                models.append({"name": rel, "path": p})
        finally:
            env.close()

    st.session_state["llama_server_discovered_models"] = models
    if not models:
        st.session_state["llama_server_model_name"] = ""
        st.warning("No .gguf models found in that directory.")
    else:
        current = st.session_state.get("llama_server_model_name", "")
        new_names = [m["name"] for m in models]
        if current not in new_names:
            st.session_state["llama_server_model_name"] = new_names[0]
        st.success(f"Found {len(models)} model(s).")
    _flush_llama_server_config(project)


def _fetch_llama_server_mcp_servers(project: dict) -> None:
    """Validate a local MCP manifest and create one entry per declared tool."""
    cfg_path = st.session_state.get("llama_server_mcp_config_path", "").strip() or MCP_CONFIG_PATH
    try:
        declared = load_mcp_tool_config(cfg_path)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state["llama_server_mcp_servers"] = merge_mcp_tool_selections(
        declared, st.session_state.get("llama_server_mcp_servers", []),
    )
    _flush_llama_server_config(project)
    st.success(f"Loaded and validated {len(declared)} MCP tool(s).")


def _render_mcp_tool_panel(project: dict, prefix: str) -> None:
    """Render the manifest-backed MCP tool checkboxes for a llama bot."""
    config_key = f"{prefix}_mcp_config_path"
    servers_key = f"{prefix}_mcp_servers"
    enabled_key = f"{prefix}_mcp_enabled"
    is_server = prefix == "llama_server"
    fetch = _fetch_llama_server_mcp_servers if is_server else _fetch_mcp_servers
    button_key = "btn_llama_server_fetch_mcp" if is_server else "btn_llama_fetch_mcp"

    if not st.session_state.get(config_key):
        st.session_state[config_key] = MCP_CONFIG_PATH
    panel_enabled = st.session_state.get(enabled_key, False)
    with st.expander("MCP Servers", expanded=panel_enabled):
        enabled = st.toggle("Enable MCP Servers", key=enabled_key, help="Turn on MCP support for this runtime.")
        st.caption("The bundled MCP manifest is used by default. Supply a local override manifest to change the selectable tools.")
        if st.session_state.get(f"{prefix}_execution_target") == "ssh":
            st.caption("During execution, ModelScope deploys its built-in MCP broker to the SSH target and reaches it through a private tunnel.")
        col_path, col_validate = st.columns([4, 1])
        with col_path:
            st.text_input(
                "MCP Tool Config", key=config_key,
                help="Local path to a validated ModelScope MCP manifest.",
                label_visibility="collapsed", disabled=not enabled,
            )
        with col_validate:
            if st.button("Validate", key=button_key, use_container_width=True, disabled=not enabled):
                fetch(project)

        try:
            declared_tools = load_mcp_tool_config(st.session_state[config_key])
            servers = merge_mcp_tool_selections(declared_tools, st.session_state.get(servers_key, []))
        except ValueError as exc:
            servers = []
            st.error(str(exc))
        if servers:
            st.caption("Enable the tools available to this bot:")
            for tool in servers:
                tool["enabled"] = st.checkbox(
                    tool["name"], value=tool.get("enabled", True),
                    key=f"{prefix}_mcp_en_{tool['tool_name']}", disabled=not enabled,
                )
            st.session_state[servers_key] = servers
        else:
            st.caption("No valid MCP tools are available from this manifest.")


def _test_llama_server_run(project: dict) -> None:
    """Prove the current managed-server configuration actually launches.

    Mirrors _test_llama_cli_run's "run it for real, report the result" pattern,
    adapted for a long-lived server process instead of a one-shot CLI call:
    start llama-server with the current binary/model/flags, confirm it
    responds, then stop it again.

    When Execution Target is SSH, the server is launched ON that remote host
    (see core.remote_server) and reached through an SSH-tunnelled local port —
    matching what a real Execute run does. For Local/PCT it launches on the
    ModelScope host itself (PCT's container network namespace can't be
    port-forwarded to the same way SSH's host can); the "already listening"
    pre-check (skip launching if an Execute run already has the server up)
    only applies to that local case, since checking a remote port would need
    its own SSH round-trip regardless of whether a launch is about to happen.
    """
    import os
    import subprocess
    from core import llama_server as _llama_server_mod
    from core.evaluator import _start_managed_llama_server, _managed_llama_server_advanced_flags

    _flush_llama_server_config(project)
    cfg = project["config"]

    host = (cfg.get("server_host") or "127.0.0.1").strip()
    port = int(cfg.get("server_port") or 8080)
    verify_ssl = cfg.get("openai_verify_ssl", True)
    exec_target = cfg.get("execution_target", "local")
    use_remote = exec_target == "ssh"

    # Validate the configured launch inputs before accepting an unrelated
    # process already bound to the default address as a successful test.
    model_name = cfg.get("model_name", "")
    if not model_name:
        st.session_state["_llama_server_svc_result"] = ("error", "No model selected.", "")
        return

    if not use_remote:
        client_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
        url = f"http://{client_host}:{port}"
        if _llama_server_mod.port_open(url, timeout=1.5):
            info = _llama_server_mod.get_server_info(url, verify_ssl=verify_ssl)
            if info:
                model = (info.get("model_path") or "").split("/")[-1] or "?"
                st.session_state["_llama_server_svc_result"] = (
                    "ok",
                    f"A server is already running here (e.g. an active Execute run)  |  "
                    f"model: `{model}`  |  Context Window Length: `{info.get('n_ctx') or '?'}`",
                    "",
                )
            else:
                st.session_state["_llama_server_svc_result"] = (
                    "error",
                    "A server is already listening at this address but didn't return model info.",
                    "",
                )
            return

    model_dir  = cfg.get("model_dir", "")
    model_path = os.path.join(model_dir, model_name) if model_dir else model_name
    if not use_remote:
        model_path = os.path.abspath(os.path.expanduser(model_path))

    binary = (cfg.get("binary_path") or "").strip()
    if not binary:
        st.session_state["_llama_server_svc_result"] = ("error", "No binary path configured.", "")
        return
    if not use_remote and os.path.isdir(binary):
        binary = os.path.join(binary, "llama-server")

    context_size   = int(cfg.get("tokens") or 32768)
    custom_flags   = cfg.get("custom_flags", "")
    advanced_flags = _managed_llama_server_advanced_flags(cfg)
    ready_timeout  = float(cfg.get("server_ready_timeout") or 300)

    logs: list[str] = []
    ssh_env = None
    try:
        if use_remote:
            from core.environment import SSHEnvironment
            from core.remote_server import start_remote_managed_llama_server
            ssh_env = SSHEnvironment(
                host=cfg.get("ssh_host", ""), port=int(cfg.get("ssh_port", 22)),
                username=cfg.get("ssh_user", "root"),
                password=cfg.get("ssh_password") or None,
                key_path=cfg.get("ssh_key_path") or None,
                remote_cwd=".",
            )
            proc = start_remote_managed_llama_server(
                ssh_env, binary, model_path, context_size, port, host,
                logs.append,
                custom_flags=custom_flags,
                advanced_flags=advanced_flags,
                ready_timeout=ready_timeout,
            )
            url = f"http://127.0.0.1:{proc.local_port}"
        else:
            proc = _start_managed_llama_server(
                binary, model_path, context_size, port, host,
                logs.append,
                custom_flags=custom_flags,
                advanced_flags=advanced_flags,
                ready_timeout=ready_timeout,
            )
            client_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
            url = f"http://{client_host}:{port}"
    except Exception as exc:
        st.session_state["_llama_server_svc_result"] = ("error", str(exc), "\n".join(logs))
        if ssh_env is not None:
            ssh_env.close()
        return

    try:
        info = _llama_server_mod.get_server_info(url, verify_ssl=verify_ssl)
        if info:
            model = (info.get("model_path") or "").split("/")[-1] or "?"
            st.session_state["_llama_server_svc_result"] = (
                "ok",
                f"Test successful! Server started and responded correctly  |  "
                f"model: `{model}`  |  Context Window Length: `{info.get('n_ctx') or '?'}`",
                "\n".join(logs),
            )
        else:
            st.session_state["_llama_server_svc_result"] = (
                "error", "Server started but didn't return model info.", "\n".join(logs),
            )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        if ssh_env is not None:
            ssh_env.close()


def _render_llama_server_runtime(project: dict) -> None:
    """Runtime sub-tab for Llama-Server-Bot: target, model setup, server bind, commands, MCP."""

    with st.expander("Execution Target", expanded=True):
        target = st.radio(
            "Mode",
            options=["local", "ssh", "pct"],
            format_func=lambda v: {"local": "Local", "ssh": "SSH (Remote)", "pct": "PCT (Proxmox LXC)"}.get(v, v),
            key="llama_server_execution_target",
            help=(
                "Where startup/completion/validation commands run and where the model "
                "directory is scanned. For SSH, the managed llama-server process also "
                "launches on that remote host (via an SSH tunnel). For PCT it does not — "
                "the managed server always starts on the ModelScope host since a PCT "
                "container's network namespace can't be tunnelled to the same way."
            ),
            horizontal=True,
        )
        st.checkbox(
            "Run commands with sudo",
            key="llama_server_sudo",
            help="Prefix shell commands with `sudo`.",
        )
        if st.session_state.get("llama_server_sudo"):
            st.text_input(
                "Sudo password",
                key="llama_server_sudo_password",
                type="password",
                help="Piped to `sudo -S`. Leave blank to reuse the SSH password or if passwordless sudo (NOPASSWD) is configured.",
            )
        if target == "local":
            st.divider()
            _render_test_button("local", "llama_server")
        elif target == "pct":
            st.divider()
            st.text_input("LXC Container ID (VMID)", key="llama_server_pct_vmid", placeholder="100")
            _render_test_button("pct", "llama_server", "llama_server_pct_vmid")
        elif target == "ssh":
            st.divider()
            st.markdown("**SSH Credentials**")
            c_host, c_port = st.columns([4, 1])
            with c_host:
                st.text_input("Host", key="llama_server_ssh_host", placeholder="192.168.1.100")
            with c_port:
                st.number_input(
                    "Port", min_value=1, max_value=65535,
                    value=st.session_state.get("llama_server_ssh_port", 22),
                    key="llama_server_ssh_port",
                )
            c_user, c_pass = st.columns([1, 1])
            with c_user:
                st.text_input("Username", key="llama_server_ssh_user")
            with c_pass:
                st.text_input("Password", key="llama_server_ssh_password",
                              type="password",
                              help="Leave empty to use key-based auth.")
            st.text_input("Key Path", key="llama_server_ssh_key_path",
                          placeholder="~/.ssh/id_rsa")
            _, col_test, _ = st.columns([1, 2, 1])
            with col_test:
                if st.button("Test Connection", key="btn_llama_server_test_ssh", type="secondary", use_container_width=True):
                    st.session_state.pop("llama_server_ssh_test_result", None)
                    with st.spinner("Please wait..."):
                        _test_llama_server_ssh_connection()

            _llama_server_ssh_result = st.session_state.get("llama_server_ssh_test_result")
            if _llama_server_ssh_result:
                _ls, _lm = _llama_server_ssh_result["status"], _llama_server_ssh_result["message"]
                if _ls == "success":
                    st.success(_lm)
                elif _ls == "warning":
                    st.warning(_lm)
                else:
                    st.error(_lm)

    with st.expander("Server Setup", expanded=True):
        st.session_state["llama_server_backend"] = "llama-server (managed)"
        _exec_target_for_warning = st.session_state.get("llama_server_execution_target", "local")
        if _exec_target_for_warning == "ssh":
            st.info(
                "Execution Target is **SSH** — the managed llama-server launches on that remote "
                "host (via an SSH tunnel), so the Binary Path and Model Directory/Model below must "
                "point to files that exist **on the remote host**, not on this machine.",
            )
        elif _exec_target_for_warning == "pct":
            st.warning(
                "Execution Target is **PCT**, but the managed llama-server doesn't support "
                "launching inside a Proxmox container — it starts on **this machine** (the one "
                "running ModelScope) instead. PCT only affects startup/completion/validation "
                "commands and model-directory scanning. The Binary Path and Model Directory/Model "
                "below must point to files that exist **locally on this machine**.",
            )
        st.text_input(
            "llama-server Binary Path",
            key="llama_server_binary_path",
            placeholder="/usr/local/bin/llama-server",
            help="Full path to the llama-server executable. Leave blank to use `llama-server` from PATH.",
        )

        col_dir, col_scan = st.columns([4, 1])
        with col_dir:
            st.text_input(
                "Model Directory",
                key="llama_server_model_dir",
                placeholder="/home/user/models",
                help="Local directory to scan for .gguf model files.",
            )
        with col_scan:
            st.write("")
            st.write("")
            if st.button("Scan", key="btn_llama_server_scan_models", use_container_width=True):
                _scan_llama_server_models(project)

        discovered: list = st.session_state.get("llama_server_discovered_models", [])
        model_names = []
        for model in discovered:
            if model["name"] not in model_names:
                model_names.append(model["name"])

        current = st.session_state.get("llama_server_model_name", "")
        default_idx = model_names.index(current) if current in model_names else 0

        if model_names:
            st.selectbox(
                "Model",
                options=model_names,
                index=default_idx,
                key="llama_server_model_name",
            )
        else:
            st.text_input(
                "Model",
                key="llama_server_model_name",
                help="Enter a local model filename/path manually, or use Scan to find models.",
            )

        col_host, col_port = st.columns([3, 1])
        with col_host:
            st.text_input(
                "Listen Host",
                key="llama_server_server_host",
                placeholder="127.0.0.1",
                help="Interface llama-server binds to. Use 127.0.0.1 for local-only or 0.0.0.0 to listen on all interfaces.",
            )
        with col_port:
            st.number_input(
                "Listen Port",
                min_value=1,
                max_value=65535,
                step=1,
                key="llama_server_server_port",
            )

        host = st.session_state.get("llama_server_server_host", "127.0.0.1")
        port = int(st.session_state.get("llama_server_server_port") or 8080)
        base_url = _llama_server_client_base_url(host, port)
        st.session_state["llama_server_openai_base_url"] = base_url
        st.caption(f"ModelScope will call `{base_url}` after starting the server.")

        st.session_state.setdefault("llama_server_tokens", 32768)
        st.number_input(
            "Context Window (tokens)",
            min_value=128,
            max_value=131072,
            step=256,
            key="llama_server_tokens",
            help="Maximum context length passed to llama-server via -c.",
        )

        st.session_state.setdefault("llama_server_ready_timeout", 300)
        st.number_input(
            "Server Startup Timeout (seconds)",
            min_value=10,
            max_value=3600,
            step=10,
            key="llama_server_ready_timeout",
            help="How long to wait for the model to load before giving up. "
                 "Increase this for large models or slow (CPU) inference. "
                 "Applies both here and during Execute.",
        )

        st.text_input(
            "Custom Flags",
            key="llama_server_custom_flags",
            placeholder="--jinja --parallel 1 -ngl 99",
            help="Additional flags to pass to llama-server.",
        )

        with st.expander("Advanced Options", expanded=False):
            def _adv_opt(
                col,
                label,
                key_suffix,
                min_v,
                max_v,
                step,
                help_text,
                is_float=False,
                value_key_suffix=None,
                default_value=None,
            ):
                with col:
                    st.session_state.setdefault(f"llama_server_en_{key_suffix}", False)
                    value_key = f"llama_server_{value_key_suffix or key_suffix}"
                    if default_value is not None:
                        st.session_state.setdefault(value_key, default_value)
                    c1, c2 = st.columns([0.2, 0.8], gap="small")
                    with c1:
                        st.write("")
                        st.write("")
                        _en = st.checkbox(f"en_{key_suffix}", key=f"llama_server_en_{key_suffix}", label_visibility="collapsed", help=f"Enable {label}")
                    with c2:
                        st.number_input(
                            label,
                            min_value=float(min_v) if is_float else int(min_v),
                            max_value=float(max_v) if is_float else int(max_v),
                            step=float(step) if is_float else int(step),
                            key=value_key,
                            disabled=not _en,
                            help=help_text,
                            format="%.2f" if is_float else None
                        )

            adv_cols = st.columns(4)
            _adv_opt(
                adv_cols[0],
                "Temperature",
                "temp",
                0.0,
                2.0,
                0.1,
                "Higher values = more random (--temp).",
                True,
                value_key_suffix="temperature",
                default_value=0.8,
            )
            _adv_opt(adv_cols[1], "GPU Layers", "gpu_layers", 0, 999, 1, "Layers to offload to GPU (-ngl).", default_value=99)
            _adv_opt(adv_cols[2], "Threads", "threads", 1, 256, 1, "CPU threads to use (-t).", default_value=4)
            _adv_opt(adv_cols[3], "Top K", "top_k", 0, 1000, 1, "Limit next token selection (--top-k).", default_value=40)

            _adv_opt(adv_cols[0], "Top P", "top_p", 0.0, 1.0, 0.05, "Cumulative probability (--top-p).", True, default_value=0.9)
            _adv_opt(adv_cols[1], "Min P", "min_p", 0.0, 1.0, 0.05, "Minimum probability (--min-p).", True, default_value=0.1)
            _adv_opt(adv_cols[2], "Repeat Pen.", "repeat_penalty", 0.0, 2.0, 0.1, "Penalize repetition (--repeat-penalty).", True, default_value=1.1)
            _adv_opt(adv_cols[3], "Freq Pen.", "freq_penalty", 0.0, 2.0, 0.1, "Frequency penalty (--freq-penalty).", True, default_value=0.0)

            _adv_opt(adv_cols[0], "Predict", "predict", -1, 131072, 128, "Default tokens to predict when a request doesn't specify max_tokens (-n).", default_value=512)
            _adv_opt(adv_cols[1], "Seed", "seed", -1, 2147483647, 1, "RNG seed (-1 for random) (--seed).", default_value=-1)
            _adv_opt(adv_cols[2], "RoPE Base", "rope_freq_base", 1000.0, 10000000.0, 1000.0, "RoPE base frequency (--rope-freq-base).", True, default_value=10000.0)
            _adv_opt(adv_cols[3], "RoPE Scale", "rope_freq_scale", 0.0, 100.0, 0.1, "RoPE frequency scale (--rope-freq-scale).", True, default_value=1.0)

            with adv_cols[0]:
                st.session_state.setdefault("llama_server_flash_attn", False)
                st.write("")
                st.write("")
                st.checkbox("Flash Attn", key="llama_server_flash_attn", help="Use Flash Attention (-fa).")

        _, col_status, _ = st.columns([1, 2, 1])
        with col_status:
            if st.button(
                "Check Status",
                key="btn_llama_server_check_status",
                use_container_width=True,
                type="primary",
                help="Launches the managed llama-server with the current settings to verify "
                     "they work, then stops it again (unless a server is already running here).",
            ):
                st.session_state.pop("_llama_server_svc_result", None)
                with st.spinner("Starting llama-server... (loading the model into memory may take a few minutes)"):
                    _test_llama_server_run(project)

        _svc_result = st.session_state.get("_llama_server_svc_result")
        if _svc_result:
            _level, _msg, _cmd = _svc_result
            if _level == "ok":
                st.success(_msg)
                if _cmd:
                    st.code(_cmd, language="bash")
            else:
                st.error(_msg)
                if _cmd:
                    st.code(_cmd, language="bash")

        st.divider()
        _render_mcp_tool_panel(project, "llama_server")

    with st.expander("Commands", expanded=True):
        tab_llm, tab_startup, tab_completion = st.tabs(
            ["🤖 LLM Judge", "▶  Startup", "⏹  Completion"]
        )
        with tab_llm:
            _render_llm_prompt_helper_tab("llama_server")
        with tab_startup:
            st.caption(
                "Commands run when execution starts, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="llama_server_startup_commands",
                pfx="llama_server_startup",
                placeholder="e.g. /bin/bash setup.sh",
            )
        with tab_completion:
            st.caption(
                "Cleanup commands run after startup and validation finish, organised as steps. "
                "Each step runs its commands sequentially after the configured delay."
            )
            _render_command_steps(
                state_key="llama_server_completion_commands",
                pfx="llama_server_completion",
                placeholder="e.g. rm -rf /tmp/test_workdir",
            )

    _flush_llama_server_config(project)


def _render_llama_server_validation(project: dict) -> None:
    """Validation sub-tab for Llama-Server-Bot pass/fail sets."""
    _render_validation_sets_ui(project, "llama_server", _flush_llama_server_config)
    _flush_llama_server_config(project)


def _render_llama_server_bot_config(project: dict) -> None:
    """Top-level renderer for Llama-Server bot configuration."""
    st.divider()

    sub_runtime, sub_val, sub_metrics = st.tabs(
        ["🖥  Runtime", "✅  Validation", "📊  Metrics Config"]
    )
    with sub_runtime:
        _render_llama_server_runtime(project)
    with sub_val:
        _render_llama_server_validation(project)
    with sub_metrics:
        _render_metric_thresholds_config(project, "llama_server", _flush_llama_server_config)
