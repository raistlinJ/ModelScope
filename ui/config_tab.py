import copy
import html
import json
import re
import time
import uuid
import pandas as pd
import streamlit as st
from config.defaults import (
    LLAMA_CPP_DEFAULT_URL, OLLAMA_DEFAULT_URL,
    GGUF_MODELS_DIR, MIN_CONTEXT_SIZE, MAX_CONTEXT_SIZE, CONTEXT_STEP,
    MCP_SERVER_BASE_URL,
    EXTERNAL_LLAMA_CPP_URL, EXTERNAL_LLAMA_CPP_MODEL,
)
from config.metrics import METRIC_TYPES, CATEGORIES, format_criterion
from config.scenarios import SCENARIOS
from core.models import scan_gguf_models, fetch_ollama_models, fetch_llama_cpp_models, compile_gguf, get_ollama_status, get_ollama_running_models, pull_ollama_model, delete_ollama_model
from core.mcp_manager import start_mcp, stop_mcp, discover_tools, poll_mcp_process
from core import llama_server
from core.state import sync_scenario
from ui.components import badge, type_badge, CAT_COLOUR
from ui.workflow_config import render_workflow_config

import pathlib as _pathlib
import json as _json_mod


def _load_llama_cli_presets() -> dict:
    """Load all *.json presets from config/presets/ and return {name: preset_dict}."""
    preset_dir = _pathlib.Path(__file__).parent.parent / "config" / "presets"
    presets = {}
    if preset_dir.exists():
        for f in sorted(preset_dir.glob("*.json")):
            try:
                data = _json_mod.loads(f.read_text())
                presets[data.get("name", f.stem)] = data
            except Exception:
                pass
    return presets


# Tool focus → scenario mapping (CAF-specific entries commented out — re-enable with CAF bot type)
_TOOL_SCENARIOS = {
    # Local tools
    "file_creator":               "Scenario 1 – File Creation",
    "run_nmap_scan":              "Scenario 2 – Network Scan",
    # CAF general scenarios — hidden until CyberAgentFlow bot type is implemented
    # "mcp_kali_run_command":       "CAF – Reconnaissance",
    # "msf_run":                    "CAF – Exploitation",
    # "caf_guardrail_test":         "CAF – Guardrail Test",
    # CAF per-tool scenarios — hidden until CyberAgentFlow bot type is implemented
    # "shell":                      "CAF – Shell Command Execution",
    # "shell_extended":             "CAF – Extended Shell Execution",
    # "shell_dangerous":            "CAF – Dangerous Command Audit",
    # "shell_sequence":             "CAF – Command Sequence",
    # "interactive_session_write":  "CAF – Interactive Session",
    # "ospf_sniff":                 "CAF – OSPF Sniffing",
    # "RIPv2":                      "CAF – RIPv2 Analysis",
}
_TOOL_LABELS = {
    "file_creator":               "file_creator — File Creation",
    "run_nmap_scan":              "run_nmap_scan — Network Scanner",
    # CAF tools — hidden until CyberAgentFlow bot type is implemented
    # "mcp_kali_run_command":       "mcp_kali_run_command — CAF Reconnaissance",
    # "msf_run":                    "msf_run — CAF Exploitation (Metasploit)",
    # "caf_guardrail_test":         "caf_guardrail_test — CAF Guardrail Test",
    # "shell":                      "shell — CAF Shell Command Execution",
    # "shell_extended":             "shell_extended — CAF Extended Shell (long-running)",
    # "shell_dangerous":            "shell_dangerous — CAF Dangerous Command Audit",
    # "shell_sequence":             "shell_sequence — CAF Command Sequence Chain",
    # "interactive_session_write":  "interactive_session_write — CAF Interactive Session",
    # "ospf_sniff":                 "ospf_sniff — CAF OSPF Protocol Analysis",
    # "RIPv2":                      "RIPv2 — CAF RIPv2 Protocol Analysis",
}


def _push_undo(snapshot: dict) -> None:
    stack = st.session_state.setdefault("_undo_stack", [])
    stack.append(snapshot)
    if len(stack) > 20:
        st.session_state["_undo_stack"] = stack[-20:]


def _export_project_json(project: dict) -> str:
    SENSITIVE = {"ssh_password", "openai_api_key"}
    proj_copy = copy.deepcopy(project)
    for k in SENSITIVE:
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


def _duplicate_project(project_id: str) -> None:
    projects = st.session_state.get("projects", [])
    proj = next((p for p in projects if p["id"] == project_id), None)
    if not proj:
        return
    new_proj = copy.deepcopy(proj)
    new_proj["id"] = str(uuid.uuid4())[:8]
    new_proj["name"] = f"{proj['name']} (copy)"
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
    st.warning(f"Permanently delete **{proj['name']}**? You can undo this with the ↩ Undo button in the sidebar.")
    _, c1, c2 = st.columns([2, 1, 1.5])
    with c1:
        if st.button("Delete", type="primary", use_container_width=True):
            projects = st.session_state.get("projects", [])
            _push_undo({"desc": f"delete '{proj['name']}'", "type": "project",
                        "projects": copy.deepcopy(projects),
                        "active_project_id": st.session_state.get("active_project_id")})
            remaining = [p for p in projects if p["id"] != project_id]
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
        </style>
        """,
        unsafe_allow_html=True,
    )

    proj = _get_active_project()
    if proj is None:
        st.info("No project selected. Use the sidebar to add or select a project.")
        return

    bot_type = proj.get("type", "bash_bot")
    if bot_type == "bash_bot":
        _render_bash_bot_config(proj)
    elif bot_type == "llama_cli_bot":
        _render_llama_cli_bot_config(proj)
    elif bot_type == "ai_agent":
        _render_ai_agent_config(proj)
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


# ── AI-Agent config ────────────────────────────────────────────────────────────

def _render_ai_agent_config(proj: dict) -> None:
    """Full configuration panel for AI-Agent type projects."""
    st.markdown(f"### {proj['name']}")
    st.caption(
        "AI-Agent mode runs evaluations using a local llama.cpp / Ollama server "
        "and the ModelScope MCP tool bridge. Configure the model, scenario, and metrics below."
    )

    sub_model, sub_metrics, sub_judge = st.tabs(
        ["⚙  Model Setup", "📐  Metrics & Scenario", "🤖  AI Judge"]
    )
    with sub_model:
        _model_setup()
    with sub_metrics:
        _metrics_setup()
    with sub_judge:
        from ui.judge_config import render as _render_judge
        _render_judge()


# ── Model Setup ────────────────────────────────────────────────────────────────

def _model_setup() -> None:
    # Apply any pending backend detection BEFORE the selectbox is created.
    if "_pending_backend" in st.session_state:
        pending = st.session_state.pop("_pending_backend")
        st.session_state["backend_type"] = pending
        st.session_state["_last_backend"] = pending
        st.session_state["llm_models"]    = []
        st.session_state["selected_model"] = None

    # ── Backend & Model (with Model Source merged at the top) ──────────────────
    # The "remote pre-compiled" source mode bypasses local backend machinery
    # entirely — the server is already running at the external URL.
    _src_mode = st.session_state.get("model_source_mode", "pre_compiled_local")

    with st.expander("Backend & Model", expanded=True):
        # ── Model Source (merged here from the old separate expander) ──────────
        _model_source_section()
        st.divider()

        # Re-read mode after the selectbox above may have changed it
        _src_mode = st.session_state.get("model_source_mode", "pre_compiled_local")

        if _src_mode == "pre_compiled_remote":
            # Remote mode renders its own sub-section (URL fetch, model select,
            # context slider, status check).  Local server controls are absent.
            _remote_model_section_inline()
        else:
            # ── Local / compile mode — Backend & Connection ────────────────────
            st.subheader("Backend & Connection")
            c_be, c_url = st.columns([2, 5])
            with c_be:
                backend = st.selectbox(
                    "Backend",
                    options=["llama.cpp", "ollama"],
                    index=0 if st.session_state["backend_type"] == "llama.cpp" else 1,
                    key="backend_type",
                    help="Inference engine: llama.cpp loads local GGUF files; Ollama manages models centrally.",
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
                st.text_input("Server URL", key="llm_url", help="HTTP base URL of the inference server (e.g., http://127.0.0.1:8080)")

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
                _ollama_server_controls()

    # ── MCP Server ─────────────────────────────────────────────────────────────
    with st.expander("SecOps MCP Server", expanded=True):
        _mcp_server_section()

    # ── CAF Runtime Configuration — hidden until CAF bot type is implemented ───
    # with st.expander("CAF Runtime Configuration", expanded=False):
    #     _caf_config_section()


def _caf_config_section() -> None:
    """CAF network boundary controls. Hidden until CyberAgentFlow bot type is implemented."""
    # ── CAF config section body — commented out, re-enable with CAF bot type ────
    # scope   = st.session_state.get("caf_scope", "Narrow")
    # urgency = st.session_state.get("caf_urgency", "Speed")
    # st.caption(
    #     "Configure Cyber-Agent-Flow's network boundary settings. "
    #     "Scope and Urgency are set in the CyberAgentFlow tab under 'CAF 4-Pillar Configuration'."
    # )
    # st.info("Scope and Urgency settings are managed in the CyberAgentFlow tab.", icon="ℹ️")
    # st.divider()
    # st.markdown("**Allowed Subnets** (Scope = Narrow guardrail)")
    # st.caption("IP ranges the agent is authorized to interact with. Leave empty to skip scope validation.")
    # subnets: list = st.session_state.get("caf_allowed_subnets", [])
    # col_sub_in, col_sub_add = st.columns([5, 1])
    # with col_sub_in:
    #     new_sub = st.text_input(
    #         "Subnet", placeholder="e.g. 192.168.1.0/24",
    #         label_visibility="collapsed", key="_new_caf_subnet",
    #     )
    # with col_sub_add:
    #     if st.button("Add", key="btn_add_caf_subnet", use_container_width=True):
    #         s = new_sub.strip()
    #         if s and s not in subnets:
    #             st.session_state["caf_allowed_subnets"] = subnets + [s]
    #             st.rerun()
    # for i, sub in enumerate(subnets):
    #     sc, sd = st.columns([8, 1])
    #     sc.code(sub)
    #     if sd.button("✕", key=f"del_caf_sub_{i}"):
    #         subnets.pop(i)
    #         st.session_state["caf_allowed_subnets"] = subnets
    #         st.rerun()
    # st.divider()
    # st.markdown("**Target Credentials** (Memory Recall metric)")
    # st.caption("Known credential strings to track across the trajectory for Memory Recall F1 scoring.")
    # creds: list = st.session_state.get("caf_target_credentials", [])
    # col_cred_in, col_cred_add = st.columns([5, 1])
    # with col_cred_in:
    #     new_cred = st.text_input(
    #         "Credential", placeholder="e.g. admin:password123",
    #         label_visibility="collapsed", key="_new_caf_cred",
    #     )
    # with col_cred_add:
    #     if st.button("Add", key="btn_add_caf_cred", use_container_width=True):
    #         c = new_cred.strip()
    #         if c and c not in creds:
    #             st.session_state["caf_target_credentials"] = creds + [c]
    #             st.rerun()
    # for i, cred in enumerate(creds):
    #     cc, cd = st.columns([8, 1])
    #     cc.code(cred)
    #     if cd.button("✕", key=f"del_caf_cred_{i}"):
    #         creds.pop(i)
    #         st.session_state["caf_target_credentials"] = creds
    #         st.rerun()
    # st.divider()
    # st.caption(
    #     f"Active config: Scope=**{scope}** | Urgency=**{urgency}** | "
    #     f"Subnets: {len(subnets)} | Credentials: {len(creds)}"
    # )
    st.info("CAF configuration is hidden. Re-enable _caf_config_section() when CyberAgentFlow bot type is implemented.")



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
        info = llama_server.get_server_info(url)
        model_name = (info.get("model_path") or "").split("/")[-1] if info else "?"
        n_ctx      = info.get("n_ctx", "?") if info else "?"
        st.markdown(
            f'<div class="service-active-box">'
            f'<div class="service-label">Running &amp; ready</div>'
            f'<div class="service-cmd">model: {model_name}  |  n_ctx: {n_ctx}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        _srv_cmd = st.session_state.get("_llama_server_cmd", "")
        if _srv_cmd:
            st.code(_srv_cmd, language="bash")
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
        placeholder="/usr/local/bin/llama-server",
        help="Path to the llama-server executable (e.g., /usr/local/bin/llama-server).",
    )

    col_start, col_stop, col_restart = st.columns(3)
    with col_start:
        if st.button("Start", use_container_width=True, key="btn_ls_start",
                     type="primary", disabled=not model_chosen):
            _bin_path  = st.session_state.get("llama_server_bin", "").strip() or "/usr/local/bin/llama-server"
            _mdl_path  = st.session_state["selected_model_path"]
            _ctx       = st.session_state.get("context_size", 4096)
            _cmd_str   = f"{_bin_path} --model {_mdl_path} --ctx-size {_ctx} --host 127.0.0.1 --port 8080 --jinja --parallel 1"
            st.session_state["_llama_server_cmd"] = _cmd_str
            ok, msg = llama_server.start(
                _mdl_path,
                context_size=_ctx,
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


def _ollama_server_controls() -> None:
    """Status indicator, running-model list, pull, and delete controls for Ollama."""
    url = st.session_state.get("llm_url", "")

    # ── Deferred messages (from previous rerun) ───────────────────────────────
    if msg_pair := st.session_state.pop("_ollama_msg", None):
        level, msg = msg_pair
        if level == "success":
            st.success(msg)
        elif level == "info":
            st.info(msg)
        else:
            st.error(msg)

    # ── Status row ────────────────────────────────────────────────────────────
    col_btn, col_pill = st.columns([2, 5])
    with col_btn:
        if st.button("Check Status", key="btn_ollama_check_status", use_container_width=True):
            status = get_ollama_status(url)
            st.session_state["_ollama_status"] = status
            if status["running"]:
                running_models, _ = get_ollama_running_models(url)
                st.session_state["_ollama_running_models"] = running_models
            else:
                st.session_state["_ollama_running_models"] = []
            st.rerun()

    with col_pill:
        ollama_status = st.session_state.get("_ollama_status")
        if ollama_status is None:
            st.caption("Press **Check Status** to probe the Ollama service.")
        elif ollama_status.get("running"):
            version_str = ollama_status.get("version", "")
            label = f"Running v{version_str}" if version_str else "Running"
            st.markdown(
                f'<p style="margin:4px 0 8px">'
                f'<span class="status-pill status-pill-up">{label}</span>'
                f'</p>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<p style="margin:4px 0 8px">'
                '<span class="status-pill status-pill-down">Offline</span>'
                '</p>',
                unsafe_allow_html=True,
            )
            if ollama_status.get("error"):
                st.caption(ollama_status["error"])

    # Running models list
    running_models: list = st.session_state.get("_ollama_running_models", [])
    if running_models:
        st.caption("**Currently loaded in memory:**")
        for m in running_models:
            expires = m.get("expires_at", "")
            size_str = f"  {m['size_gb']} GB" if m.get("size_gb") else ""
            exp_str = f"  expires: {expires[:19]}" if expires else ""
            st.caption(f"  `{m['name']}`{size_str}{exp_str}")

    st.divider()

    # ── Pull Model ────────────────────────────────────────────────────────────
    st.markdown("**Pull Model**")
    st.text_input(
        "Model name",
        key="ollama_pull_model_name",
        placeholder="e.g. llama3, mistral:7b",
    )
    if st.button("Pull", key="btn_ollama_pull", use_container_width=False):
        model_to_pull = st.session_state.get("ollama_pull_model_name", "").strip()
        if not model_to_pull:
            st.error("Enter a model name before pulling.")
        else:
            log_placeholder = st.empty()
            log_lines: list[str] = []

            def _append_log(msg: str) -> None:
                log_lines.append(msg)
                log_placeholder.code("\n".join(log_lines[-30:]), language=None)

            ok, result_msg = pull_ollama_model(url, model_to_pull, on_log=_append_log)
            if ok:
                # Refresh model list in the shape the selector expects
                found, _err = fetch_ollama_models(url)
                st.session_state["llm_models"] = [
                    {"name": m["name"], "path": m["name"], "size_gb": m["size_gb"]}
                    for m in found
                ]
                st.session_state["_ollama_msg"] = ("success", result_msg)
            else:
                st.session_state["_ollama_msg"] = ("error", result_msg)
            st.rerun()

    st.divider()

    # ── Delete Model ──────────────────────────────────────────────────────────
    models_list: list = st.session_state.get("llm_models", [])
    if models_list:
        st.markdown("**Delete Model**")
        model_names = [m["name"] for m in models_list]
        selected_for_delete = st.selectbox(
            "Model to delete",
            options=model_names,
            key="ollama_delete_model_select",
        )

        if st.session_state.get("_confirm_ollama_delete"):
            st.warning(f"Permanently delete **{selected_for_delete}** from Ollama? This cannot be undone.")
            cd1, cd2 = st.columns(2)
            with cd1:
                if st.button("Yes, delete", key="btn_ollama_confirm_delete", use_container_width=True):
                    ok, msg = delete_ollama_model(url, selected_for_delete)
                    st.session_state["_confirm_ollama_delete"] = False
                    if ok:
                        # Refresh model list
                        found, _err = fetch_ollama_models(url)
                        st.session_state["llm_models"] = [
                            {"name": m["name"], "path": m["name"], "size_gb": m["size_gb"]}
                            for m in found
                        ]
                        # Clear selected model if it was deleted
                        if st.session_state.get("selected_model") == selected_for_delete:
                            st.session_state["selected_model"] = None
                        st.session_state["_ollama_msg"] = ("success", msg)
                    else:
                        st.session_state["_ollama_msg"] = ("error", msg)
                    st.rerun()
            with cd2:
                if st.button("Cancel", key="btn_ollama_cancel_delete", use_container_width=True):
                    st.session_state["_confirm_ollama_delete"] = False
                    st.rerun()
        else:
            if st.button("Delete", key="btn_ollama_delete", use_container_width=False):
                st.session_state["_confirm_ollama_delete"] = True
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

    st.caption("Connection type: **Local** — SSH tunnel support coming soon.")
    conn_type = "Local"

    if conn_type == "Local":
        # ── Local MCP ─────────────────────────────────────────────────────────
        col_path, col_url = st.columns([1, 1])
        with col_path:
            st.text_input("MCP Server Script", key="mcp_url", help="Path to the MCP server index.js (Node.js entry point)")
        with col_url:
            st.text_input("MCP Server URL", key="mcp_server_url", help="HTTP endpoint where the MCP server listens (e.g., http://127.0.0.1:3000)")

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

        running = poll_mcp_process()
        pill_state = "up" if running else "wait"
        pill_label = "Running" if running else "Stopped"
        st.markdown(
            f'<p style="margin:4px 0 8px">'
            f'<span class="status-pill status-pill-{pill_state}">{pill_label}</span>'
            f'</p>',
            unsafe_allow_html=True,
        )

    st.divider()

    # ── Fetch MCP Tools ───────────────────────────────────────────────────────
    col_ft, _ = st.columns([2, 5])
    with col_ft:
        if st.button("Fetch MCP Tools", key="btn_fetch_mcp_tools", use_container_width=True):
            _url = st.session_state.get("mcp_server_url", MCP_SERVER_BASE_URL)
            from core.mcp_manager import load_tools_from_json
            import os as _os
            live_tools = discover_tools(st.session_state["mcp_url"], base_url=_url)
            _mcp_dir = _os.path.dirname(st.session_state.get("mcp_url", ""))
            catalog = load_tools_from_json(_mcp_dir)
            seen = {t["name"] for t in live_tools}
            all_t = live_tools + [t for t in catalog if t["name"] not in seen]
            if all_t:
                prev = st.session_state.get("mcp_tools", {})
                old_keys = set(prev.keys()) - {t["name"] for t in all_t}
                for old in old_keys:
                    st.session_state.pop(f"tool_chk_{old}", None)
                st.session_state["mcp_tools"] = {
                    t["name"]: prev.get(t["name"], True)
                    for t in all_t
                }
                st.success(f"Found {len(live_tools)} live + {len(all_t) - len(live_tools)} catalog tools")
            else:
                st.warning("No tools found — is the MCP server running?")

    tools_dict = st.session_state.get("mcp_tools", {})
    if tools_dict:
        from core.mcp_manager import load_tools_from_json
        from collections import defaultdict
        import os as _os2
        _mcp_dir2 = _os2.path.dirname(st.session_state.get("mcp_url", ""))
        _all_tool_info = {
            t["name"]: {"desc": t.get("description", ""), "server": t.get("server", "custom")}
            for t in load_tools_from_json(_mcp_dir2)
        }
        _SERVER_LABELS = {
            "custom":             "Custom Tools",
            "fetch":              "Fetch",
            "filesystem":         "Filesystem",
            "git":                "Git",
            "memory":             "Memory",
            "time":               "Time",
            "sequentialthinking": "Sequential Thinking",
        }
        _SERVER_ORDER = ["custom", "fetch", "filesystem", "git", "memory", "time", "sequentialthinking"]
        _by_server = defaultdict(list)
        for _tn in tools_dict:
            _srv = _all_tool_info.get(_tn, {}).get("server", "custom")
            _by_server[_srv].append(_tn)
        _updated = dict(tools_dict)
        for _srv in _SERVER_ORDER:
            _names = _by_server.get(_srv, [])
            if not _names:
                continue
            _label = _SERVER_LABELS.get(_srv, _srv)
            with st.expander(f"**{_label}** ({len(_names)} tools)", expanded=(_srv == "custom")):
                _ncols = min(3, len(_names))
                _cols = st.columns(_ncols)
                for _idx, _name in enumerate(sorted(_names)):
                    with _cols[_idx % _ncols]:
                        _updated[_name] = st.checkbox(
                            _name,
                            value=tools_dict.get(_name, True),
                            key=f"tool_chk_{_name}",
                            help=_all_tool_info.get(_name, {}).get("desc") or None,
                        )
        st.session_state["mcp_tools"] = _updated
    else:
        st.info("Click **Fetch MCP Tools** to discover tools from the MCP server.")


def _model_source_section() -> None:
    """
    Top-level 'Model Source' selector.

    Three modes:
      pre_compiled_local  — scan a local directory for .gguf files (existing flow)
      pre_compiled_remote — connect to an external llama.cpp server as-is
      compile             — build a GGUF from a HuggingFace model directory
    """
    st.caption(
        "Choose how ModelScope obtains the model: scan a local GGUF file, "
        "connect to a pre-compiled remote server, or compile a new GGUF from source."
    )

    _MODE_LABELS = {
        "pre_compiled_local":  "Pre-compiled (local GGUF file)",
        "pre_compiled_remote": "Pre-compiled (remote server endpoint)",
        "compile":             "Compile GGUF from source",
    }
    mode_options = list(_MODE_LABELS.keys())
    cur_mode     = st.session_state.get("model_source_mode", "pre_compiled_local")
    cur_idx      = mode_options.index(cur_mode) if cur_mode in mode_options else 0

    selected = st.selectbox(
        "Model Source Mode",
        options=mode_options,
        format_func=lambda k: _MODE_LABELS[k],
        index=cur_idx,
        key="model_source_mode",
        help="Controls which model loading path is active for evaluations.",
    )

    if selected == "compile":
        st.divider()
        _compile_gguf_section()


def _compile_gguf_section() -> None:
    """UI panel for the GGUF compile pipeline (HF model → GGUF → quantize)."""
    st.markdown("**Compile GGUF from HuggingFace model**")
    st.caption(
        "Converts a local HuggingFace model directory to GGUF format using "
        "`convert_hf_to_gguf.py`, then quantizes with `llama-quantize`."
    )

    st.text_input(
        "Source Model Directory",
        key="compile_source_path",
        placeholder="/path/to/hf/model",
        help="Local directory containing the HuggingFace model files (config.json, tokenizer, etc.).",
    )
    c1, c2 = st.columns(2)
    with c1:
        st.text_input(
            "Output Directory",
            key="compile_output_dir",
            help="Where the compiled .gguf file will be saved.",
        )
    with c2:
        st.selectbox(
            "Quantization",
            options=["Q4_K_M", "Q5_K_M", "Q8_0", "F16", "none"],
            key="compile_quantization",
            help="GGUF quantization type. 'none' skips quantization (saves F16).",
        )

    with st.expander("Advanced paths", expanded=False):
        st.text_input(
            "convert_hf_to_gguf.py path",
            key="compile_convert_script",
            help="Full path to convert_hf_to_gguf.py from the llama.cpp repository.",
        )
        st.text_input(
            "llama-quantize binary path",
            key="compile_quantize_bin",
            help="Full path to the llama-quantize binary.",
        )

    if st.button("Compile GGUF", key="btn_compile_gguf", use_container_width=False):
        src  = st.session_state.get("compile_source_path", "").strip()
        if not src:
            st.error("Source model directory is required.")
            return

        log_placeholder = st.empty()
        log_lines: list[str] = []

        def _append_log(msg: str) -> None:
            log_lines.append(msg)
            log_placeholder.code("\n".join(log_lines[-20:]), language=None)

        quant = st.session_state.get("compile_quantization", "Q4_K_M")
        quant_arg = "" if quant == "none" else quant

        ok, result = compile_gguf(
            source_path    = src,
            output_dir     = st.session_state.get("compile_output_dir", "").strip(),
            quantization   = quant_arg,
            convert_script = st.session_state.get("compile_convert_script", "").strip() or None,
            quantize_bin   = st.session_state.get("compile_quantize_bin", "").strip() or None,
            on_log         = _append_log,
        )
        if ok:
            st.success(f"Compiled successfully: `{result}`")
            # Automatically switch to local pre-compiled mode and scan the output dir
            st.session_state["model_source_mode"] = "pre_compiled_local"
            found = scan_gguf_models(st.session_state.get("compile_output_dir", "").strip())
            st.session_state["llm_models"] = found
            if found:
                # Pre-select the freshly compiled model
                matching = [m for m in found if result.endswith(m["name"])]
                if matching:
                    st.session_state["selected_model"]      = matching[0]["name"]
                    st.session_state["selected_model_path"] = matching[0]["path"]
            st.rerun()
        else:
            st.error(f"Compile failed: {result}")


def _remote_model_section_inline() -> None:
    """
    Inline UI for the 'Pre-compiled (remote server endpoint)' mode.
    Rendered inside the Backend & Model expander — no extra wrapper.
    Local server start/stop controls are intentionally absent here.
    """
    st.subheader("Remote llama.cpp Server")
    st.caption(
        "Connect to an external llama.cpp server. "
        "Server management (start/stop) is not available for remote endpoints."
    )

    col_url, col_fetch = st.columns([5, 1])
    with col_url:
        if not (st.session_state.get("external_llm_url") or "").strip():
            st.session_state["external_llm_url"] = EXTERNAL_LLAMA_CPP_URL
        st.text_input("Server URL", key="external_llm_url")
    with col_fetch:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Fetch", use_container_width=True, key="btn_fetch_remote_models"):
            found, err = fetch_llama_cpp_models(st.session_state["external_llm_url"])
            if found:
                st.session_state["external_llm_models"] = found
                st.success(f"{len(found)} model(s) available")
            elif err:
                st.error(err)
            else:
                st.warning("No models returned — is the server running?")

    remote_models = st.session_state.get("external_llm_models", [])

    # If no models fetched yet, seed from the known external model constant
    if not remote_models:
        remote_models = [
            {
                "name":         EXTERNAL_LLAMA_CPP_MODEL,
                "path":         EXTERNAL_LLAMA_CPP_MODEL,
                "size_gb":      22.1,
                "context_size": 262144,
                "source":       "remote",
            }
        ]
        st.info(
            f"Known model pre-loaded: `{EXTERNAL_LLAMA_CPP_MODEL}`. "
            "Click **Fetch** to refresh from the server."
        )

    labels    = []
    for m in remote_models:
        size_str = f"{m.get('size_gb', '?')} GB" if m.get("size_gb") else "?"
        ctx_str  = f"  ctx: {m['context_size']:,}" if m.get("context_size") else ""
        labels.append(f"{m['name']}  ({size_str}{ctx_str})")

    raw_names = [m["name"] for m in remote_models]
    cur       = st.session_state.get("external_selected_model", EXTERNAL_LLAMA_CPP_MODEL)
    idx       = raw_names.index(cur) if cur in raw_names else 0

    sel_label = st.selectbox(
        "Select Remote Model",
        options=labels,
        index=idx,
        key="_remote_model_label_sel",
        help="Choose which pre-compiled model to use on the remote llama.cpp server.",
    )
    sel_name = raw_names[labels.index(sel_label)]

    # Write the selection into the standard eval keys so the evaluator
    # doesn't need to know about the source mode.
    st.session_state["external_selected_model"] = sel_name
    st.session_state["selected_model"]          = sel_name
    st.session_state["selected_model_path"]     = sel_name  # remote: name IS the path
    st.session_state["backend_type"]            = "llama.cpp"
    st.session_state["llm_url"]                 = st.session_state["external_llm_url"]

    sel_meta = next((m for m in remote_models if m["name"] == sel_name), {})
    parts = [f"Source: remote  |  URL: `{st.session_state['external_llm_url']}`"]
    if sel_meta.get("size_gb"):
        parts.append(f"Size: `{sel_meta['size_gb']} GB`")
    if sel_meta.get("context_size"):
        ctx_val = int(sel_meta["context_size"])
        parts.append(f"Max ctx: `{ctx_val:,}`")
        # Push the remote server's n_ctx into the context slider when it
        # would otherwise be capped below the server's capability.
        if ctx_val > st.session_state.get("context_size", 0):
            st.session_state["context_size"] = min(ctx_val, MAX_CONTEXT_SIZE)
    st.caption("  |  ".join(parts))

    # Remote server status (read-only — no start/stop)
    ext_url = st.session_state.get("external_llm_url", "")
    if ext_url:
        col_chk, _ = st.columns([2, 5])
        with col_chk:
            if st.button("Check Status", key="btn_check_remote_status", use_container_width=True):
                info = llama_server.get_server_info(ext_url)
                if info:
                    st.success(
                        f"Online  |  model: `{(info.get('model_path') or '').split('/')[-1]}`"
                        f"  |  n_ctx: `{info.get('n_ctx', '?')}`"
                    )
                else:
                    st.error("Could not reach server. Check the URL and server status.")

    st.subheader("Context Window")
    st.slider(
        "Tokens",
        min_value=MIN_CONTEXT_SIZE,
        max_value=MAX_CONTEXT_SIZE,
        step=CONTEXT_STEP,
        key="context_size",
        help="Tokens sent per request. Remote server's actual n_ctx is shown via Check Status.",
    )


def _remote_model_section() -> None:
    """
    Kept for backward compatibility. Renders inline content directly.
    """
    _remote_model_section_inline()


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
        if st.button("Fetch Ollama Models", key="btn_fetch_ollama_models", use_container_width=True):
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
        sel_label = st.selectbox("Select Ollama Model", options=labels, index=idx, help="Choose an Ollama model from those available on the server.")
        sel_name  = raw_names[labels.index(sel_label)]
        st.session_state["selected_model"]      = sel_name
        st.session_state["selected_model_path"] = sel_name  # Ollama: name IS the path
    else:
        st.info("Click **Fetch Ollama Models** to list available models.")


# ── Metrics Setup ──────────────────────────────────────────────────────────────

def _on_tool_focus_change() -> None:
    """Callback: sync scenario, prompts, validation, metrics, and CAF config when tool changes."""
    tool     = st.session_state.get("tool_focus", "file_creator")
    scenario = _TOOL_SCENARIOS.get(tool, "Scenario 1 – File Creation")
    st.session_state["active_scenario"]      = scenario
    st.session_state["_prompts_user_edited"] = False
    sync_scenario(scenario)


def _metrics_setup() -> None:
    # ── Workflow-specific configuration ────────────────────────────────────────
    _active_sc_key = st.session_state.get("active_scenario", "")
    _active_sc     = SCENARIOS.get(_active_sc_key, {})
    _stype         = _active_sc.get("scenario_type", "")
    if _stype and _stype not in ("tool_use", ""):
        render_workflow_config()
        st.divider()

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
        new_id     = c1.text_input("ID",   value=suggested_id, key="_nm_id", help="Unique metric identifier in M-NNN format (e.g., M-001)")
        new_name   = c2.text_input("Name", placeholder="My Check", key="_nm_name", help="Human-readable name for this metric")
        type_label = c3.selectbox("Type",  options=type_options, key="_nm_type", help="Category and type of metric (validation, performance, tool usage, etc.)")

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
            rc[3].markdown(type_badge(m.get("type", "")), unsafe_allow_html=True)
            rc[4].markdown(
                f'<span class="criterion">{html.escape(format_criterion(m))}</span>',
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


# ── Bash-Bot configuration ─────────────────────────────────────────────────────

def _flush_bash_config(project: dict) -> None:
    """Write flat bash_* working keys back into the project's config bundle."""
    def _clean_steps(steps_list):
        cleaned = []
        for step in steps_list:
            if not isinstance(step, dict):
                continue
            new_step = copy.deepcopy(step)
            new_step["commands"] = [c for c in new_step.get("commands", []) if isinstance(c, dict) and c.get("command", "").strip()]
            if new_step["commands"] or new_step.get("delay_seconds", 0) > 0:
                cleaned.append(new_step)
        return cleaned

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
            st.session_state.get("bash_ssh_password", "")
            if st.session_state.get("bash_execution_target", "local") in ("ssh", "pct")
            else st.session_state.get("bash_sudo_password", "")
        ) if st.session_state.get("bash_sudo") else "",
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
                        "command":        str(c.get("command", "")),
                        "enabled":        bool(c.get("enabled", True)),
                        "long_running":   bool(c.get("long_running", False)),
                        "timeout_seconds": int(c.get("timeout_seconds", 60)),
                    }
                    if "expected_output_type" in c:
                        entry["expected_output_type"] = str(c["expected_output_type"])
                    if "expected_output" in c:
                        entry["expected_output"] = str(c["expected_output"])
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
                _first_cmd = commands[0].get("command", "").strip() if commands else ""
                _preview = (_first_cmd[:35] + "…") if len(_first_cmd) > 35 else _first_cmd
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
                    st.caption("No commands. Click **+ Add Command** below.")

                _addcmd_flag_key = f"_sc_{pfx}_{step_id}_addcmd_flag"
                _is_last_cmd_idx = len(commands) - 1

                for ci, cmd in enumerate(commands):
                    cmd_id = cmd["_id"]

                    # Single flat row: command | timeout | long-running | enabled | delete
                    cc1, ccl_to, ccv_to, cc3, cc4, cc5 = st.columns([5.0, 0.8, 1.0, 1.0, 1.0, 0.7])
                    with cc1:
                        cmd_key = f"_sc_{pfx}_{step_id}_{cmd_id}_cmd"
                        if cmd_key not in st.session_state:
                            st.session_state[cmd_key] = cmd.get("command", "")

                        # On the last command row, pressing Enter (on_change) sets a
                        # flag to add a new command — same action as clicking "+ Add Command".
                        # on_change fires after the widget commits its value, so
                        # st.session_state[cmd_key] already holds the new text.
                        if ci == _is_last_cmd_idx:
                            def _on_last_cmd_enter(
                                _ck=cmd_key, _fk=_addcmd_flag_key
                            ):
                                if st.session_state.get(_ck, "").strip():
                                    st.session_state[_fk] = True

                            st.text_input(
                                f"Command {ci + 1}",
                                key=cmd_key,
                                placeholder=placeholder,
                                label_visibility="collapsed",
                                on_change=_on_last_cmd_enter,
                            )
                        else:
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

                # Add Command: either button click or Enter in the last command field.
                if st.session_state.pop(_addcmd_flag_key, False):
                    mutation = ("add_cmd", si)
                elif st.button(f"+ Add Command", key=f"_sc_{pfx}_{step_id}_addcmd"):
                    mutation = ("add_cmd", si)

    # Add Step button
    if st.button("+ Add Step", key=f"_sc_{pfx}_addstep", type="primary"):
        mutation = ("add_step",)

    # ── Handle expand/collapse toggle (UI-only, no undo entry) ──────────────
    if toggle:
        st.session_state[toggle[0]] = toggle[1]
        st.rerun()

    # ── Apply mutation and rerun ─────────────────────────────────────────────
    if mutation:
        _push_undo({"desc": f"edit {pfx} commands", "type": "cmd",
                    "state_key": state_key, "data": copy.deepcopy(steps)})
        m = mutation
        if m[0] == "add_step":
            steps.append({
                "_id":           _next_step_id(),
                "delay_seconds": 0.0,
                "commands": [{
                    "_id":             _next_step_id(),
                    "command":         "",
                    "enabled":         True,
                    "long_running":    False,
                    "timeout_seconds": 60,
                }],
            })
        elif m[0] == "del_step":
            steps.pop(m[1])
        elif m[0] == "move_step":
            si1, si2 = m[1], m[2]
            steps[si1], steps[si2] = steps[si2], steps[si1]
        elif m[0] == "add_cmd":
            steps[m[1]]["commands"].append({
                "_id":             _next_step_id(),
                "command":         "",
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




def _render_validation_steps(state_key: str, pfx: str, placeholder: str) -> None:
    raw   = st.session_state.get(state_key, [])
    steps = _ensure_step_ids(_coerce_steps(raw))
    st.session_state[state_key] = steps

    def _val_add_step():
        st.session_state[state_key].append({
            "_id": _next_step_id(),
            "delay_seconds": 0.0,
            "commands": [{
                "_id": _next_step_id(),
                "command": "",
                "enabled": True,
                "timeout_seconds": 60,
                "expected_output_type": "Ignore",
                "expected_output": ""
            }],
        })

    def _val_del_step(si):
        st.session_state[state_key].pop(si)

    def _val_move_step(si1, si2):
        s = st.session_state[state_key]
        s[si1], s[si2] = s[si2], s[si1]

    def _val_add_cmd(si):
        st.session_state[state_key][si]["commands"].append({
            "_id": _next_step_id(),
            "command": "",
            "enabled": True,
            "timeout_seconds": 60,
            "expected_output_type": "Ignore",
            "expected_output": ""
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
                _first_cmd = commands[0].get("command", "").strip() if commands else ""
                _preview = (_first_cmd[:35] + "…") if len(_first_cmd) > 35 else _first_cmd
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
                    st.caption("No commands. Click **+ Add Command/Output** below.")

                _is_last_cmd_idx = len(commands) - 1

                for ci, cmd in enumerate(commands):
                    cmd_id = cmd["_id"]

                    if ci == 0:
                        hc_cmd, hc_to, hc_chk, hc_exp, hc_en, hc_del = st.columns([3.0, 0.8, 1.5, 2.0, 0.8, 0.6])
                        hc_cmd.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Command</div>", unsafe_allow_html=True)
                        hc_to.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Timeout</div>", unsafe_allow_html=True)
                        hc_chk.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Check Type</div>", unsafe_allow_html=True)
                        hc_exp.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Expected Value</div>", unsafe_allow_html=True)
                        hc_en.markdown("<div style='font-size: 12px; font-weight: bold; margin-bottom: -15px;'>Enabled</div>", unsafe_allow_html=True)
                        st.markdown("<div style='height: 5px;'></div>", unsafe_allow_html=True)

                    cc_cmd, cc_to, cc_chk, cc_exp, cc_en, cc_del = st.columns([3.0, 0.8, 1.5, 2.0, 0.8, 0.6])
                    
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
                        
                    with cc_chk:
                        chk_key = f"_sc_{pfx}_{step_id}_{cmd_id}_chk"
                        if chk_key not in st.session_state:
                            _init_type = cmd.get("expected_output_type", "Ignore")
                            if _init_type not in ["Regex", "Exact String"]:
                                _init_type = "Ignore"
                            st.session_state[chk_key] = _init_type
                        cmd["expected_output_type"] = st.selectbox("Check Type", options=["Ignore", "Regex", "Exact String", "No output"], key=chk_key, label_visibility="collapsed", disabled=is_disabled)
                        
                    with cc_exp:
                        exp_key = f"_sc_{pfx}_{step_id}_{cmd_id}_exp"
                        if exp_key not in st.session_state:
                            st.session_state[exp_key] = cmd.get("expected_output", "")
                            
                        # Disable expected value if row is disabled OR check type is Ignore
                        chk_type = st.session_state.get(chk_key, cmd.get("expected_output_type", "Ignore"))
                        exp_disabled = is_disabled or (chk_type in ("Ignore", "No output"))
                        
                        cmd["expected_output"] = st.text_input("Expected Value", key=exp_key, placeholder="Value to check", label_visibility="collapsed", disabled=exp_disabled)
                        
                    with cc_en:
                        en_key = f"_sc_{pfx}_{step_id}_{cmd_id}_en"
                        cmd["enabled"] = st.checkbox("Enabled", value=cmd.get("enabled", True), key=en_key)
                        
                    with cc_del:
                        st.button("✕", key=f"_sc_{pfx}_{step_id}_{cmd_id}_del", use_container_width=True, on_click=_val_del_cmd, args=(si, ci))

                st.button(f"+ Add Command/Output", key=f"_sc_{pfx}_{step_id}_addcmd", on_click=_val_add_cmd, args=(si,))

    st.button("+ Add Step", key=f"_sc_{pfx}_addstep", type="primary", on_click=_val_add_step)


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
            if target == "local":
                st.text_input(
                    "Sudo password",
                    key="bash_sudo_password",
                    type="password",
                    help="Piped to `sudo -S`. Leave blank if passwordless sudo (NOPASSWD) is configured.",
                )
            else:
                st.caption("SSH password will be reused for sudo.")
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
                st.number_input("Port", min_value=1, max_value=65535, key="bash_ssh_port")
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
            col_test, col_retry = st.columns([2, 1])
            # Check current state
            _ssh_result = st.session_state.get("bash_ssh_test_result")
            is_connected = _ssh_result and _ssh_result["status"] == "success"
            is_failed = _ssh_result and _ssh_result["status"] == "error"

            # If connected, just show the success state with a tiny reset button if they want to disconnect
            if is_connected:
                st.success(_ssh_result["message"])
                if st.button("Disconnect / Test Another Host", key="btn_ssh_disconnect", type="secondary", use_container_width=True):
                    st.session_state.pop("bash_ssh_test_result", None)
                    st.rerun()
            
            # If failed or not tested yet, show the action button
            else:
                btn_label = "Retry Connection" if is_failed else "Test Connection"
                btn_type = "primary" if is_failed else "secondary"
                
                if st.button(btn_label, key="btn_bash_test_ssh", use_container_width=True, type=btn_type):
                    st.session_state.pop("bash_ssh_test_result", None)
                    with st.spinner("Please wait..."):
                        _test_bash_ssh_connection()
                    st.rerun()
                
                # Show error message under the retry button if it exists
                if is_failed:
                    st.error(_ssh_result["message"])
                elif _ssh_result and _ssh_result["status"] == "warning":
                    st.warning(_ssh_result["message"])

    with st.expander("Commands", expanded=True):
        tab_startup, tab_completion = st.tabs(
            ["▶  Startup", "⏹  Completion"]
        )
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


def _test_local_connection(result_key: str) -> None:
    from core.environment import LocalEnvironment
    try:
        env = LocalEnvironment()
        res = env.execute("echo ok")
        if res["exit_code"] == 0 and "ok" in res["stdout"]:
            st.session_state[result_key] = {"status": "success", "message": "Local execution working successfully."}
        else:
            st.session_state[result_key] = {"status": "warning", "message": f"Unexpected response: {res!r}"}
    except Exception as exc:
        st.session_state[result_key] = {"status": "error", "message": f"Local execution failed: {exc}"}


def _test_pct_connection(vmid_key: str, result_key: str) -> None:
    from core.environment import LocalEnvironment, PCTEnvironment
    vmid = (st.session_state.get(vmid_key) or "").strip()
    if not vmid:
        st.session_state[result_key] = {"status": "error", "message": "VMID is required."}
        return
    try:
        env = PCTEnvironment(vmid, LocalEnvironment())
        res = env.execute("echo ok")
        if res["exit_code"] == 0 and "ok" in res["stdout"]:
            st.session_state[result_key] = {"status": "success", "message": f"Connected to PCT container {vmid}"}
        else:
            st.session_state[result_key] = {"status": "warning", "message": f"Connected but unexpected response: {res!r}"}
    except Exception as exc:
        st.session_state[result_key] = {"status": "error", "message": f"PCT connection failed: {exc}"}


def _render_test_button(target_type: str, state_prefix: str, vmid_key: str = "") -> None:
    result_key = f"{state_prefix}_{target_type}_test_result"
    _res = st.session_state.get(result_key)
    is_connected = _res and _res["status"] == "success"
    is_failed = _res and _res["status"] == "error"
    
    col_test, _ = st.columns([2, 1])
    with col_test:
        if is_connected:
            st.success(_res["message"])
            if st.button("Reset / Test Again", key=f"btn_{state_prefix}_test_{target_type}_reset", type="secondary", use_container_width=True):
                st.session_state.pop(result_key, None)
                st.rerun()
        else:
            btn_label = "Retry Connection" if is_failed else f"Test {target_type.upper() if target_type == 'pct' else 'Local'} Execution"
            btn_type = "primary" if is_failed else "secondary"
            if st.button(btn_label, key=f"btn_{state_prefix}_test_{target_type}", use_container_width=True, type=btn_type):
                st.session_state.pop(result_key, None)
                if target_type == "local":
                    _test_local_connection(result_key)
                elif target_type == "pct":
                    _test_pct_connection(vmid_key, result_key)
                st.rerun()
    if not is_connected and _res:
        if is_failed: st.error(_res["message"])
        elif _res["status"] == "warning": st.warning(_res["message"])


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
            _store("success", f"Connected to {user}@{host}:{port} ✓")
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
def _edit_validation_set_dialog(project: dict, sets: list, nonce: int, edit_idx: int = None) -> None:
    is_edit = edit_idx is not None
    si = edit_idx if is_edit else len(sets)
    target_set = sets[edit_idx] if is_edit else {}
    
    _name_key = f"bash_val_add_name_{si}_{nonce}"
    _desc_key = f"bash_val_add_desc_{si}_{nonce}"
    
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
    
    _steps_state_key = f"_bash_val_dialog_steps_{si}_{nonce}"
    if _steps_state_key not in st.session_state:
        if is_edit:
            st.session_state[_steps_state_key] = copy.deepcopy(target_set.get("steps", []))
        else:
            st.session_state[_steps_state_key] = [{
                "_id": _next_step_id(),
                "delay_seconds": 0.0,
                "commands": [{
                    "_id": _next_step_id(),
                    "command": "",
                    "timeout_seconds": 60,
                    "expected_output_type": "Ignore",
                    "expected_output": "",
                    "enabled": True,
                }]
            }]

    _render_validation_steps(_steps_state_key, pfx=f"val_{si}_{nonce}", placeholder="e.g. ls -l")
    
    col1, col2 = st.columns([4, 1])
    with col2:
        btn_label = "Save Set"
        if st.button(btn_label, type="primary", use_container_width=True):
            _push_undo({"desc": f"{'edit' if is_edit else 'add'} validation set", "type": "cmd",
                        "state_key": "bash_validation_sets", "data": copy.deepcopy(sets)})
            
            clean_steps = []
            for step in st.session_state[_steps_state_key]:
                step_copy = copy.deepcopy(step)
                step_copy["commands"] = [c for c in step_copy.get("commands", []) if c.get("command", "").strip()]
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
                
            st.session_state["bash_validation_sets"] = sets
            st.session_state["bash_val_editor_nonce"] = nonce + 1
            _flush_bash_config(project)
            st.rerun()


def _render_bash_validation(project: dict) -> None:
    """Validation sub-tab for Bash-Bot: Pass/Fail sets using inline data_editor."""

    sets = list(st.session_state.get("bash_validation_sets", []))
    nonce = st.session_state.get("bash_val_editor_nonce", 0)

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
                        en_key = f"bash_val_en_{si}_{nonce}"
                        new_enabled = st.checkbox("Enabled", value=active_set.get("enabled", True), key=en_key)
                        
                        if active_set.get("enabled", True) != new_enabled:
                            active_set["enabled"] = new_enabled
                            st.session_state["bash_validation_sets"] = sets
                            _flush_bash_config(project)

                    with hc_edit:
                        if st.button("View / Edit", key=f"btn_bash_val_edit_{si}", use_container_width=True):
                            _edit_validation_set_dialog(project, sets, nonce, edit_idx=si)
                    with hc_del:
                        if st.button("✕", key=f"btn_bash_val_del_{si}", use_container_width=True, help="Remove validation set"):
                            mutation = ("delete", si)

            if mutation:
                op, target_idx = mutation
                if op == "delete":
                    _push_undo({"desc": f"delete set '{sets[target_idx].get('name', '')}'",
                                "type": "cmd", "state_key": "bash_validation_sets",
                                "data": copy.deepcopy(sets)})
                    sets.pop(target_idx)
                    st.session_state["bash_validation_sets"] = sets
                    st.session_state["bash_val_editor_nonce"] = nonce + 1
                    _flush_bash_config(project)
                    st.rerun()

    # ── Add Validation Set ────────────────────────────────────────────────────
    if st.button("＋ Add Validation Set", key="btn_add_set_bash", type="primary"):
        _edit_validation_set_dialog(project, sets, nonce)




def _render_bash_bot_config(project: dict) -> None:
    """Top-level config renderer for Bash-Bot projects (2 sub-tabs: Runtime, Metrics)."""
    st.divider()

    sub_runtime, sub_validation = st.tabs(["🖥  Runtime", "📐  Validation"])
    with sub_runtime:
        _render_bash_runtime(project)
    with sub_validation:
        _render_bash_validation(project)


# ── Shared Metrics Matrix widget ───────────────────────────────────────────────

def _render_metrics_matrix(state_key: str, key_prefix: str) -> None:
    """Reusable metrics matrix add/edit/delete UI (shared by bash and llama_cli bots)."""
    matrix: list = st.session_state.get(state_key, [])

    visible_cats = [c for c in CATEGORIES if not c.startswith("CAF-")]

    with st.expander("+ Add metric"):
        type_options: list[str] = []
        for cat in visible_cats:
            for mkey, info in METRIC_TYPES.items():
                if info["category"] == cat:
                    type_options.append(f"{cat}: {info['label']}")

        existing_ids = {m.get("id", "") for m in matrix}
        suggested_id = next(
            f"M-{i:03d}" for i in range(1, 999)
            if f"M-{i:03d}" not in existing_ids
        )

        c1, c2, c3 = st.columns([2, 3, 4])
        new_id     = c1.text_input("ID",   value=suggested_id, key=f"_{key_prefix}_nm_id")
        new_name   = c2.text_input("Name", placeholder="My Check", key=f"_{key_prefix}_nm_name")
        type_label = c3.selectbox("Type",  options=type_options, key=f"_{key_prefix}_nm_type")

        selected_type_key = ""
        for mkey, info in METRIC_TYPES.items():
            if f"{info['category']}: {info['label']}" == type_label:
                selected_type_key = mkey
                break

        param_values: dict = {}
        if selected_type_key:
            type_info = METRIC_TYPES[selected_type_key]
            if type_info["params"]:
                st.caption(f"*{type_info['description']}*")
                pcols = st.columns(min(3, len(type_info["params"])))
                for i, param in enumerate(type_info["params"]):
                    with pcols[i % 3]:
                        pkey = f"_{key_prefix}_nm_p_{param['name']}"
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

        if st.button("Add Metric", key=f"btn_{key_prefix}_add_metric"):
            _errors = []
            _id   = new_id.strip()
            _name = new_name.strip()
            if not _name:
                _errors.append("Name is required.")
            if not _id:
                _errors.append("ID is required.")
            elif not re.match(r"^M-\d{3}$", _id):
                _errors.append("ID must match format M-NNN (e.g. M-001).")
            elif _id in existing_ids:
                _errors.append(f"ID **{_id}** is already used.")
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
                st.session_state[state_key] = matrix
                st.rerun()

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
                key=f"{key_prefix}_me_{i}_{m['id']}", label_visibility="collapsed",
            )
            matrix[i]["enabled"] = enabled
            rc[1].code(m["id"])
            rc[2].write(m["name"])
            rc[3].markdown(type_badge(m.get("type", "")), unsafe_allow_html=True)
            rc[4].markdown(
                f'<span class="criterion">{html.escape(format_criterion(m))}</span>',
                unsafe_allow_html=True,
            )
            if rc[5].button("✕", key=f"{key_prefix}_md_{i}"):
                to_delete = i
        if to_delete is not None:
            del matrix[to_delete]
            st.session_state[state_key] = matrix
            st.rerun()
        st.session_state[state_key] = matrix
    else:
        st.info("No metrics configured. Add one above.")


# ── Llama-CLI Bot configuration ────────────────────────────────────────────────

def _flush_llama_cli_config(project: dict) -> None:
    """Write flat llama_cli_* working keys back into the project's config bundle."""
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
        "backend":             st.session_state.get("llama_cli_backend", "llama.cpp"),
        "binary_path":         st.session_state.get("llama_cli_binary_path", ""),
        "model_dir":           st.session_state.get("llama_cli_model_dir", ""),
        "model_name":          st.session_state.get("llama_cli_model_name", ""),
        "openai_base_url":     st.session_state.get("llama_cli_openai_base_url", ""),
        "openai_api_key":      st.session_state.get("llama_cli_openai_api_key", ""),
        "openai_verify_ssl":   st.session_state.get("llama_cli_openai_verify_ssl", True),
        "tokens":              st.session_state.get("llama_cli_tokens", 2048),
        "mcp_config_path":     st.session_state.get("llama_cli_mcp_config_path", ""),
        "mcp_servers":         st.session_state.get("llama_cli_mcp_servers", []),
        "steps":               _steps,
        "prompts":             _prompts,
        "commands":            _commands,
        "timeout":             st.session_state.get("llama_cli_timeout", 120),
        "validation_commands": st.session_state.get("llama_cli_validation_commands", []),
        "fail_patterns":       st.session_state.get("llama_cli_fail_patterns", []),
        "metrics_matrix":      st.session_state.get("llama_cli_metrics_matrix", []),
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
            _store("success", f"Connected to {user}@{host}:{port} ✓")
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
            res   = env.execute(
                f'find "{model_dir}" -name "*.gguf" -not -name "ggml-vocab-*"',
                timeout=15,
            )
            paths  = [l.strip() for l in res["stdout"].splitlines() if l.strip()]
            models = [{"name": p.split("/")[-1], "path": p} for p in paths]
        finally:
            env.close()
    st.session_state["llama_cli_discovered_models"] = models
    if not models:
        st.warning("No .gguf models found in that directory.")
    else:
        st.success(f"Found {len(models)} model(s).")
    st.rerun()


def _fetch_mcp_servers(project: dict) -> None:
    """Read mcp_config.json from local or remote machine and populate server list."""
    cfg_path = st.session_state.get("llama_cli_mcp_config_path", "").strip()
    target   = st.session_state.get("llama_cli_execution_target", "local")
    if not cfg_path:
        st.warning("Set MCP Config Path first.")
        return
    import json
    raw: dict = {}
    if target == "local":
        import pathlib
        try:
            raw = json.loads(pathlib.Path(cfg_path).read_text())
        except Exception as exc:
            st.error(f"Could not read {cfg_path}: {exc}")
            return
    else:
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
            res = env.execute(f'cat "{cfg_path}"', timeout=10)
        finally:
            env.close()
        if res["exit_code"] != 0:
            st.error(f"Could not read remote file: {res['stderr']}")
            return
        try:
            raw = json.loads(res["stdout"])
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON in {cfg_path}: {exc}")
            return
    if not isinstance(raw, dict):
        st.error(
            f"Expected a JSON object in that file but got `{type(raw).__name__}`. "
            'MCP config must be `{"mcpServers": {"name": {"command": "...", "args": [...]}}}}`.'
        )
        return
    servers = []
    for name, spec in raw.get("mcpServers", {}).items():
        servers.append({
            "name":    name,
            "command": spec.get("command", ""),
            "args":    spec.get("args", []),
            "enabled": True,
        })
    st.session_state["llama_cli_mcp_servers"] = servers
    st.success(f"Fetched {len(servers)} MCP server(s).")
    st.rerun()


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
                st.number_input("Port", min_value=1, max_value=65535, key="llama_cli_ssh_port")
            c_user, c_pass = st.columns([1, 1])
            with c_user:
                st.text_input("Username", key="llama_cli_ssh_user")
            with c_pass:
                st.text_input("Password", key="llama_cli_ssh_password",
                              type="password",
                              help="Leave empty to use key-based auth.")
            st.text_input("Key Path", key="llama_cli_ssh_key_path",
                          placeholder="~/.ssh/id_rsa")
            col_llama_test, col_llama_retry = st.columns([2, 1])
            with col_llama_test:
                if st.button("Test Connection", key="btn_llama_test_ssh",
                             use_container_width=True, type="secondary"):
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
                    with col_llama_retry:
                        if st.button("Retry", key="btn_llama_retry_ssh",
                                     use_container_width=True):
                            st.session_state.pop("llama_cli_ssh_test_result", None)
                            _test_llama_cli_ssh_connection()
                            st.rerun()

    with st.expander("Model Setup", expanded=True):
        backend = st.selectbox(
            "Provider",
            options=["llama.cpp", "OpenAI-compatible HTTP"],
            key="llama_cli_backend",
            help="llama.cpp uses llama-cli binary with --prompt; OpenAI-compatible HTTP connects to any /v1/chat/completions endpoint.",
        )

        if backend == "llama.cpp":
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
            if discovered:
                model_names = [m["name"] for m in discovered]
                current     = st.session_state.get("llama_cli_model_name", "")
                default_idx = model_names.index(current) if current in model_names else 0
                chosen = st.selectbox(
                    "Model", options=model_names, index=default_idx,
                    key="_llama_model_sel_widget",
                )
                st.session_state["llama_cli_model_name"] = chosen
            elif st.session_state.get("llama_cli_model_name"):
                st.info(f"Current model: `{st.session_state['llama_cli_model_name']}` "
                        "(click Scan to refresh list)")
            else:
                st.caption("Set Model Directory and click **Scan** to discover models.")
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
                            st.error(_err or "No models returned — is the server running?")
                    else:
                        st.warning("Enter an Instance URL first.")

            _ssl = st.checkbox(
                "Require SSL Certificate Verification",
                key="_llama_openai_ssl_widget",
                value=st.session_state.get("llama_cli_openai_verify_ssl", True),
                help="Uncheck for self-signed certs or plain HTTP servers.",
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
                        st.success(f"Online  |  model: `{_mname}`  |  n_ctx: `{_info.get('n_ctx', '?')}`")
                    else:
                        st.error("Could not reach server.")
                else:
                    st.warning("Enter an Instance URL first.")

        st.number_input(
            "Context Window (tokens)",
            min_value=128, max_value=131072, step=256,
            key="llama_cli_tokens",
            help="Maximum context length passed to llama-cli via -c.",
        )

        st.divider()
        _col_svc, _col_status = st.columns([1, 2])
        _backend = st.session_state.get("llama_cli_backend", "llama.cpp")
        with _col_svc:
            if st.button("Start Service", key="btn_llama_start_service",
                         use_container_width=True, type="primary",
                         help="Start llama-server with the selected model (llama.cpp), or test connectivity (OpenAI-compatible)."):
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
                                f"Online  |  model: `{_mn}`  |  n_ctx: `{_info.get('n_ctx', '?')}`",
                                "",
                            )
                        else:
                            st.session_state["_llama_svc_result"] = (
                                "error",
                                f"Could not reach `{_base}` — check URL and network.",
                                "",
                            )
                else:
                    # Resolve model path from discovered models
                    _disc = st.session_state.get("llama_cli_discovered_models", [])
                    _mname = st.session_state.get("llama_cli_model_name", "")
                    _mpath = next(
                        (m["path"] for m in _disc if m["name"] == _mname),
                        _mname,  # fallback: use the name as the path
                    )
                    _bin  = (st.session_state.get("llama_cli_binary_path") or "").strip() or "/usr/local/bin/llama-server"
                    _ctx  = int(st.session_state.get("llama_cli_tokens", 2048))
                    _cmd  = f"{_bin} --model {_mpath} --ctx-size {_ctx} --host 127.0.0.1 --port 8080 --jinja --parallel 1"
                    if not _mpath:
                        st.session_state["_llama_svc_result"] = (
                            "error",
                            "No model selected — set Model Directory and Scan first.",
                            "",
                        )
                    else:
                        _ok, _msg = llama_server.start(
                            _mpath,
                            context_size=_ctx,
                            binary=_bin,
                        )
                        st.session_state["_llama_svc_result"] = ("ok" if _ok else "error", _msg, _cmd)
                        st.session_state["_llama_svc_cmd"]    = _cmd
                st.rerun()

        # ── Service status display (always shown) ─────────────────────────────
        with _col_status:
            _svc_result = st.session_state.get("_llama_svc_result")
            if _svc_result:
                _level, _msg, _cmd = _svc_result
                if _level == "ok":
                    st.success(_msg)
                    if _cmd:
                        st.code(_cmd, language="bash")
                else:
                    st.error(_msg)
            elif _backend.lower().startswith("llama") or _backend == "llama.cpp":
                # Show running state if server is already up
                _srv_url = "http://127.0.0.1:8080"
                if llama_server.poll_ready(_srv_url):
                    _info = llama_server.get_server_info(_srv_url)
                    _mn   = (_info.get("model_path") or "").split("/")[-1] if _info else "?"
                    _ctx  = _info.get("n_ctx", "?") if _info else "?"
                    st.markdown(
                        f'<div class="service-active-box">'
                        f'<div class="service-label">Running</div>'
                        f'<div class="service-cmd">model: {_mn}  |  n_ctx: {_ctx}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                    _cached_cmd = st.session_state.get("_llama_svc_cmd", "")
                    if _cached_cmd:
                        st.code(_cached_cmd, language="bash")

    with st.expander("MCP Servers", expanded=False):
        st.caption("Discover MCP servers available on the target machine.")
        col_path, col_fetch = st.columns([4, 1])
        with col_path:
            st.text_input(
                "MCP Config Path",
                key="llama_cli_mcp_config_path",
                placeholder="/home/user/.mcp/config.json",
                help="Path to a mcp_config.json file on the target machine.",
                label_visibility="collapsed",
            )
        with col_fetch:
            if st.button("Fetch", key="btn_llama_fetch_mcp", use_container_width=True):
                _fetch_mcp_servers(project)

        servers: list = st.session_state.get("llama_cli_mcp_servers", [])
        if servers:
            st.caption(f"{len(servers)} server(s) discovered. Enable the ones to use:")
            for i, srv in enumerate(servers):
                enabled = st.checkbox(
                    srv["name"],
                    value=srv.get("enabled", True),
                    key=f"llama_mcp_en_{i}",
                )
                servers[i]["enabled"] = enabled
            st.session_state["llama_cli_mcp_servers"] = servers
        else:
            st.caption("No servers fetched yet. Set the config path and click **Fetch**.")

    st.number_input(
        "Timeout (seconds)",
        min_value=0.1, max_value=3600.0, step=1.0,
        key="llama_cli_timeout",
        help="Maximum time (seconds) each command or prompt invocation may run.",
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
                if step["type"] == "command":
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
                            disabled=step["long_running"],
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


def _render_llama_cli_metrics_setup(project: dict) -> None:
    """Inputs sub-tab for Llama-CLI-Bot: steps, validation, metrics."""

    # ── Preset Scenarios ─────────────────────────────────────────────────────
    _presets = _load_llama_cli_presets()
    if _presets:
        st.subheader("Preset Scenarios")
        _preset_opts = ["Custom"] + list(_presets.keys())
        _sel_col, _btn_col = st.columns([4, 1])
        with _sel_col:
            _chosen = st.selectbox(
                "Scenario", options=_preset_opts,
                key="_llama_preset_sel", label_visibility="collapsed",
            )
        with _btn_col:
            if st.button("Load", key="btn_llama_load_preset", type="primary",
                         disabled=(_chosen == "Custom"), use_container_width=True):
                _p = _presets[_chosen]
                # Build unified steps from preset prompts + commands
                _new_steps: list = []
                for _pr in _p.get("prompts", []):
                    _new_steps.append({
                        "_id": _next_step_id(), "type": "prompt",
                        "content": _pr, "enabled": True,
                        "long_running": False, "timeout_seconds": 60,
                    })
                for _cm in _p.get("commands", []):
                    _new_steps.append({
                        "_id": _next_step_id(), "type": "command",
                        "content": _cm, "enabled": True,
                        "long_running": False, "timeout_seconds": 60,
                    })
                st.session_state["llama_cli_steps"]               = _new_steps
                # Keep legacy lists in sync for settings persistence
                st.session_state["llama_cli_prompts"]             = list(_p.get("prompts", []))
                st.session_state["llama_cli_commands"]            = list(_p.get("commands", []))
                st.session_state["llama_cli_validation_commands"] = list(_p.get("validation_commands", []))
                st.session_state["llama_cli_fail_patterns"]       = copy.deepcopy(_p.get("fail_patterns", []))
                st.session_state["llama_cli_metrics_matrix"]      = copy.deepcopy(_p.get("metrics_matrix", []))
                st.rerun()
        st.divider()

    # ── Migration: convert legacy flat lists to unified steps ─────────────────
    _existing_steps = st.session_state.get("llama_cli_steps", [])
    if not _existing_steps:
        _old_prompts  = st.session_state.get("llama_cli_prompts", [])
        _old_commands = st.session_state.get("llama_cli_commands", [])
        if _old_prompts or _old_commands:
            _migrated: list = []
            for _pr in _old_prompts:
                _migrated.append({
                    "_id": _next_step_id(), "type": "prompt",
                    "content": _pr, "enabled": True,
                    "long_running": False, "timeout_seconds": 60,
                })
            for _cm in _old_commands:
                _migrated.append({
                    "_id": _next_step_id(), "type": "command",
                    "content": _cm, "enabled": True,
                    "long_running": False, "timeout_seconds": 60,
                })
            st.session_state["llama_cli_steps"] = _migrated

    # ── Evaluation Steps ──────────────────────────────────────────────────────
    st.subheader("Evaluation Steps")
    st.caption(
        "Steps are executed in order. "
        "**Prompt** steps send natural-language instructions to the LLM; "
        "**Command** steps run shell commands directly."
    )
    _render_step_editor("llama_cli_steps", "llama")

    st.divider()

    # ── Validation Steps ──────────────────────────────────────────────────────
    st.subheader("Validation Steps")
    with st.expander("Validation Commands", expanded=True):
        st.caption("Commands run after steps complete — must exit 0 to count as PASS.")
        _addable_list(
            state_key="llama_cli_validation_commands",
            placeholder="grep -q 'expected' /tmp/output.txt",
            input_key="_llama_new_val",
            add_key="btn_llama_add_val",
            del_key_prefix="llama_del_val",
        )

    with st.expander("Fail Patterns", expanded=True):
        st.caption("Patterns evaluated after execution — string match, shell command exit, or prompt check.")
        # Normalize legacy string patterns to typed dicts
        _raw_patterns = st.session_state.get("llama_cli_fail_patterns", [])
        patterns: list = [
            p if isinstance(p, dict) else {"type": "string", "value": p}
            for p in _raw_patterns
        ]
        st.session_state["llama_cli_fail_patterns"] = patterns

        _TYPE_LABELS = {"string": "String match", "command": "Command", "prompt": "Prompt"}
        _fp_type_col, _fp_val_col, _fp_add_col = st.columns([2, 5, 1])
        with _fp_type_col:
            _new_fp_type = st.selectbox(
                "Type", options=list(_TYPE_LABELS.keys()),
                format_func=lambda k: _TYPE_LABELS[k],
                key="_llama_fp_type", label_visibility="collapsed",
            )
        with _fp_val_col:
            _fp_placeholders = {
                "string":  'e.g. "Error: file not found"',
                "command": "e.g. test -f /tmp/output.txt",
                "prompt":  "e.g. Did the command produce an error? Answer YES or NO.",
            }
            _new_fp_val = st.text_input(
                "Value", placeholder=_fp_placeholders[_new_fp_type],
                key="_llama_fp_val", label_visibility="collapsed",
            )
        with _fp_add_col:
            if st.button("Add", key="btn_llama_add_fp", use_container_width=True):
                _v = _new_fp_val.strip()
                if _v:
                    patterns.append({"type": _new_fp_type, "value": _v})
                    st.session_state["llama_cli_fail_patterns"] = patterns
                    st.rerun()

        if patterns:
            _TYPE_COLOURS = {
                "string":  ("#79c0ff", "rgba(121,192,255,0.14)", "rgba(121,192,255,0.35)"),
                "command": ("#f0883e", "rgba(240,136,62,0.14)",  "rgba(240,136,62,0.35)"),
                "prompt":  ("#bc8cff", "rgba(188,140,255,0.14)", "rgba(188,140,255,0.35)"),
            }
            _TYPE_ICONS = {"string": "STR", "command": "CMD", "prompt": "PROMPT"}
            _to_remove = None
            for _i, _fp in enumerate(patterns):
                _ftype = _fp.get("type", "string")
                _clr, _bg, _border = _TYPE_COLOURS.get(_ftype, ("#8b949e", "rgba(139,148,158,0.14)", "rgba(139,148,158,0.35)"))
                _badge_html = (
                    f'<span style="background:{_bg};color:{_clr};padding:3px 9px;'
                    f'border-radius:999px;font-size:0.67rem;font-weight:700;'
                    f'border:1px solid {_border};font-family:monospace;letter-spacing:0.3px;'
                    f'display:inline-block">{_TYPE_ICONS.get(_ftype, "?")}</span>'
                )
                _pc1, _pc2, _pc3 = st.columns([1, 7, 1])
                _pc1.markdown(_badge_html, unsafe_allow_html=True)
                _pc2.code(_fp.get("value", ""))
                if _pc3.button("✕", key=f"llama_del_fp_{_i}"):
                    _to_remove = _i
            if _to_remove is not None:
                patterns.pop(_to_remove)
                st.session_state["llama_cli_fail_patterns"] = patterns
                st.rerun()

    st.divider()

    st.subheader("Metrics Matrix")
    st.caption("Metrics evaluated against run telemetry after execution.")
    _render_metrics_matrix("llama_cli_metrics_matrix", "llama")

    _flush_llama_cli_config(project)


def _render_llama_cli_bot_config(project: dict) -> None:
    """Top-level renderer for Llama-CLI bot configuration."""
    st.divider()

    sub_runtime, sub_inputs = st.tabs(["🖥  Runtime", "📐  Metrics Setup"])
    with sub_runtime:
        _render_llama_cli_runtime(project)
    with sub_inputs:
        _render_llama_cli_metrics_setup(project)
