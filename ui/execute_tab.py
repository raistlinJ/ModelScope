import threading
import streamlit as st
from core.bot_types import get_bot_plugin
from ui.terminal import render_terminal
from ui.config_tab import _flush_bash_config, _flush_llama_cli_config, _flush_llama_server_config


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
    "[RUN]":          "cmd",
    "[PROMPT HELPER]": "prompt",
    "[JUDGE]":        "prompt",
}


def _tag(line: str) -> str:
    if "PASS ✓" in line:
        return "result"
    if "FAIL ✗" in line:
        return "warn"
    for prefix, tag in _LOG_TAG_MAP.items():
        if line.startswith(prefix):
            return tag
    return ""


def _render_terminal(placeholder, logs: list[dict]) -> None:
    render_terminal(placeholder, logs, _tag, empty_msg="Awaiting run…")


def _get_active_project() -> dict | None:
    pid = st.session_state.get("active_project_id")
    for p in st.session_state.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def _clean_steps(steps_list: list) -> list:
    import copy
    cleaned = []
    for step in steps_list:
        if not isinstance(step, dict):
            if isinstance(step, str) and step.strip():
                cleaned.append(step)
            continue
        new_step = copy.deepcopy(step)
        new_step["commands"] = [
            c for c in new_step.get("commands", [])
            if (isinstance(c, dict) and (c.get("command", "").strip() or c.get("prompt", "").strip() or c.get("type") == "prompt")) or
               (isinstance(c, str) and c.strip())
        ]
        if new_step["commands"] or new_step.get("delay_seconds", 0) > 0:
            cleaned.append(new_step)
    return cleaned


def _get_selected_validation_sets(cfg: dict) -> list:
    """Return a filtered deep-copy of cfg['validation_sets'] based on execute-tab checkboxes.

    Sets whose bash_exec_vset_{idx}_selected flag is False are excluded entirely.
    Per-command enabled fields are overridden by bash_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected.
    """
    import copy
    filtered = []
    for idx, vset in enumerate(cfg.get("validation_sets", [])):
        if not st.session_state.get(f"bash_exec_vset_{idx}_selected", True):
            continue
        vset_copy = copy.deepcopy(vset)
        vset_copy["steps"] = _clean_steps(vset_copy.get("steps", []))
        for sidx, step in enumerate(vset_copy.get("steps", [])):
            for cidx, cmd_obj in enumerate(step.get("commands", [])):
                key = f"bash_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                cmd_obj["enabled"] = st.session_state.get(key, cmd_obj.get("enabled", True))
        filtered.append(vset_copy)
    return filtered


def _validation_checks_summary(cmd_obj: dict) -> str:
    raw_checks = cmd_obj.get("checks", [])
    checks = raw_checks if isinstance(raw_checks, list) and raw_checks else [{
        "expected_output_type": cmd_obj.get("expected_output_type", "Ignore"),
        "expected_output": cmd_obj.get("expected_output", ""),
    }]
    parts = []
    for check in checks:
        check_type = check.get("expected_output_type", check.get("type", "Ignore"))
        expected = check.get("expected_output", check.get("value", ""))
        if check_type == "Ignore":
            continue
        if check_type == "No output":
            parts.append("no output")
        elif expected:
            short = expected[:40] + ("…" if len(expected) > 40 else "")
            parts.append(f"{check_type.lower()}: {short}")
    return "  # accept " + " OR ".join(parts) if parts else ""


def _phase_label(title: str, phase_key: str) -> str:
    """Return an expander label with a phase indicator emoji.
    
    phase_key: one of 'startup', 'validation', 'completion'
    """
    current = st.session_state.get("_exec_phase", "")
    running = st.session_state.get("_run_in_progress", False)
    if not running:
        if current == "done":
            return f"✅ **{title}**"
        return f"**{title}**"
    if current == phase_key:
        return f"🔄 **{title}** *(running…)*"
    # Determine ordering to show check vs pending
    order = ["startup", "validation", "completion"]
    if phase_key in order and current in order:
        if order.index(phase_key) < order.index(current):
            return f"✅ **{title}**"
    return f"⏳ **{title}**"


def _render_step_list_readonly(steps: list, label: str) -> None:
    """Read-only listing of a step/command list (startup or completion)."""
    total_cmds = sum(
        len(s.get("commands", [])) if isinstance(s, dict) else 1
        for s in steps
    )
    st.caption(f"{len(steps)} step(s), {total_cmds} command(s)")
    if not steps:
        st.caption(f"None configured — add {label} commands in the Config tab.")
        return
    for si, step in enumerate(steps):
        if isinstance(step, str):
            st.code(step, language="bash")
        else:
            delay = step.get("delay_seconds", 0)
            delay_str = f" (+{delay}s delay)" if delay > 0 else ""
            st.markdown(f"*Step {si + 1}{delay_str}*")
            for cmd_obj in step.get("commands", []):
                if isinstance(cmd_obj, dict):
                    _type   = cmd_obj.get("type", "command")
                    enabled = cmd_obj.get("enabled", True)
                    
                    if _type == "prompt":
                        with st.container(border=True):
                            pc_icon = "🔗" if cmd_obj.get("preserve_context", True) else "🚫"
                            en_str = "" if enabled else " &nbsp; *(disabled)*"
                            st.markdown(f"💬 **LLM Judge** &nbsp; | &nbsp; {pc_icon} **Context** {en_str}")
                            
                            sys_text = cmd_obj.get("system_prompt", "")
                            if sys_text:
                                with st.expander("System Prompt", expanded=False):
                                    st.code(sys_text, language="text")
                                    
                            usr_text = cmd_obj.get("user_prompt", "")
                            if usr_text:
                                with st.expander("User Prompt", expanded=False):
                                    st.code(usr_text, language="text")
                    else:
                        text = cmd_obj.get("command", "")
                        if text and enabled:
                            st.code(text, language="bash")
                        elif text:
                            st.markdown(f"~~`{text}`~~ *(disabled)*")
                elif isinstance(cmd_obj, str) and cmd_obj:
                    st.code(cmd_obj, language="bash")


def _save_bash_validation_validation_set(project: dict, validation_set_name: str) -> None:
    """Save current bash_validation_sets as a named validation_set in project config."""
    import copy
    sets = copy.deepcopy(st.session_state.get("bash_validation_sets", []))
    project["config"].setdefault("validation_validation_sets", {})[validation_set_name] = sets
    _flush_bash_config(project)
    st.success(f"Validation set '{validation_set_name}' saved.")


def _load_bash_validation_validation_set(project: dict, validation_set_name: str) -> None:
    """Load a user-saved validation validation_set, reset per-set selection state, and rerun."""
    import copy
    validation_sets = project["config"].get("validation_validation_sets", {})
    if validation_set_name not in validation_sets:
        st.error(f"Validation set '{validation_set_name}' not found.")
        return
    st.session_state["bash_validation_sets"] = copy.deepcopy(validation_sets[validation_set_name])
    _flush_bash_config(project)
    for k in list(st.session_state.keys()):
        if k.startswith("bash_exec_vset_"):
            del st.session_state[k]
    st.rerun()


def _run_bash_bot(project: dict, shared: dict) -> None:
    """Build the environment and run the bash evaluation.

    ``shared`` is a plain dict visible to both this thread and the main
    Streamlit thread.  We never touch ``st.session_state`` here — the
    polling loop in the main thread mirrors ``shared`` → session_state.
    """
    from core.environment import LocalEnvironment, SSHEnvironment
    from core.evaluator import run_bash_evaluation
    from core.session_log import SessionLog

    cfg = project["config"]

    cancel_ref: list[bool] = [False]

    session_log = SessionLog()

    def on_log(msg: str, source: str = "shell") -> None:
        if shared.get("cancel_requested"):
            cancel_ref[0] = True
        # Track execution phase from log prefixes
        if msg.startswith("[RUN]") or msg.startswith("[DELAY] Step"):
            shared["phase"] = "startup"
        elif msg.startswith("[VALIDATE"):
            shared["phase"] = "validation"
        elif msg.startswith("[CLEANUP]"):
            shared["phase"] = "completion"
        elif msg.startswith("[COMPLETE]"):
            shared["phase"] = "done"
        # Determine log destination by phase
        phase = shared.get("phase", "startup")
        entry = {"text": msg, "tag": _tag(msg)}
        if phase == "validation":
            shared.setdefault("logs_validation", []).append(entry)
        else:
            shared.setdefault("logs_setup", []).append(entry)
        session_log.log(msg)

    from core.environment import create_environment
    tgt = cfg.get("execution_target", "local")
    is_ssh = tgt == "ssh"

    env = create_environment(
        ssh=is_ssh,
        host=cfg.get("ssh_host", ""),
        port=int(cfg.get("ssh_port") or 22),
        username=cfg.get("ssh_user", "root"),
        password=cfg.get("ssh_password") or None,
        key_path=cfg.get("ssh_key_path") or None,
        remote_cwd=".",  # bash-bot: stay in the login shell's default directory
        pct_vmid=cfg.get("pct_vmid", "") if tgt == "pct" else None,
    )

    bash_config = {
        "startup_commands":    cfg.get("startup_commands", []),
        "bash_timeout":        cfg.get("bash_timeout", 60),
        "completion_commands": cfg.get("completion_commands", []),
        "validation_commands": cfg.get("validation_commands", []),
        "validation_sets":     _get_selected_validation_sets(cfg),
        "fail_patterns":       cfg.get("fail_patterns", []),
        "metrics_matrix":      cfg.get("metrics_matrix", []),
        "bash_sudo":           cfg.get("sudo", False),
        # The evaluator reads "sudo"; keep "bash_sudo" for telemetry/back-compat
        "sudo":                cfg.get("sudo", False),
        "sudo_password":       cfg.get("sudo_password", ""),
        "cancel_requested_ref": cancel_ref,
        "execution_mode": "bash",
        "active_project_id":   project.get("id"),
    }
    # LLM Judge connection — also drives final-response scoring (_run_ai_judge)
    bash_config.update({k: v for k, v in cfg.items() if k.startswith("llm_helper_")})

    telemetry: dict | None = None
    try:
        telemetry = run_bash_evaluation(env, bash_config, on_log)
    except Exception as exc:
        on_log(f"[ERROR] Evaluation failed: {exc}")
        telemetry = {"run_aborted": True, "error": str(exc)}
    finally:
        if hasattr(env, "close"):
            env.close()

    session_log.save_telemetry(telemetry or {})
    session_log.save_config(bash_config)
    session_log.close()

    shared["telemetry"]  = telemetry or {}
    shared["completed"]  = True
    shared["project_id"] = project.get("id")


def _render_bash_execute(project: dict) -> None:
    """Execute view for Bash-Bot: collapsible config sub-blocks + Execute button + log."""
    cfg = project.get("config", {})

    st.markdown(f"### {project['name']}")

    # ── Two-column configuration panel ───────────────────────────────────────
    _cfg_open = st.session_state.get("bash_exec_config_expanded", True)
    with st.container(border=True):
        col_hdr_outer, col_tog_outer = st.columns([9, 1])
        with col_hdr_outer:
            st.markdown("**⚙️ Run Configuration**")
        with col_tog_outer:
            if st.button("▼" if _cfg_open else "▶",
                         key="btn_bash_exec_outer_toggle", use_container_width=True,
                         help="Expand/collapse all"):
                st.session_state["bash_exec_config_expanded"] = not _cfg_open
                st.rerun()

        if _cfg_open:
            with st.container(border=True):
                target = cfg.get("execution_target", "local")
                ssh_info = f" ({cfg.get('ssh_user','root')}@{cfg.get('ssh_host','?')}:{cfg.get('ssh_port',22)})" if target == "ssh" else ""
                target_str = f"Target: {target.upper()}{ssh_info}"
                timeout_str = f"Timeout: {cfg.get('bash_timeout', 60)}s"
                st.markdown(f"**Execution Configuration** &nbsp;&nbsp;<span style='color: #888; font-size: 0.9em'>|&nbsp;&nbsp; {target_str} &nbsp;&nbsp;|&nbsp;&nbsp; {timeout_str}</span>", unsafe_allow_html=True)

                with st.expander(_phase_label("Startup", "startup"), expanded=False):
                    _render_step_list_readonly(_clean_steps(cfg.get("startup_commands", [])), "startup")

                with st.expander(_phase_label("Validation", "validation"), expanded=False):
                    # Validation set list
                    val_sets = cfg.get("validation_sets", [])
                    if not val_sets:
                        st.caption("No validation sets configured — add them in the Config tab (Validation).")
                    else:
                        for idx, vset in enumerate(val_sets):
                            set_sel_key = f"bash_exec_vset_{idx}_selected"
                            set_selected = st.session_state.get(set_sel_key, True)

                            desc = vset.get("description", "")
                            label_md = f"**{idx + 1}. {vset['name']}**"
                            if desc:
                                label_md += f" — {desc}"

                            with st.expander(label_md, expanded=set_selected):
                                st.session_state.setdefault(set_sel_key, True)
                                new_sel = st.checkbox(
                                    "Enable this Validation Set",
                                    key=set_sel_key,
                                )
                                set_selected = new_sel

                                for sidx, step in enumerate(vset.get("steps", [])):
                                    delay = step.get("delay_seconds", 0)
                                    dstr  = f" ({delay}s delay)" if delay > 0 else ""
                                    st.caption(f"Step {sidx + 1}{dstr}:")
                                    for cidx, cmd_obj in enumerate(step.get("commands", [])):
                                        _type = cmd_obj.get("type", "command")
                                        if _type == "prompt":
                                            sys_p = cmd_obj.get("system_prompt", "")
                                            usr_p = cmd_obj.get("user_prompt", "")
                                            if not sys_p and not usr_p:
                                                continue
                                            cmd_text = f"Configured LLM: {sys_p[:20]}... | {usr_p[:20]}..."
                                        else:
                                            cmd_text = cmd_obj.get("command", "")
                                            if not cmd_text:
                                                continue
                                        cmd_key  = f"bash_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                                        cmd_sel  = st.session_state.get(cmd_key, cmd_obj.get("enabled", True))
                                        hint = _validation_checks_summary(cmd_obj)

                                        col_cc, col_cl = st.columns([0.3, 10.7])
                                        with col_cc:
                                            st.session_state.setdefault(cmd_key, cmd_obj.get("enabled", True))
                                            new_cmd_sel = st.checkbox(
                                                f"Enable command {cidx+1} in step {sidx+1}",
                                                key=cmd_key,
                                                label_visibility="collapsed",
                                                disabled=not set_selected,
                                            )
                                            cmd_sel = new_cmd_sel
                                        with col_cl:
                                            if _type == "prompt":
                                                with st.container(border=True):
                                                    st.markdown(f"💬 **Configured LLM**")
                                                    if sys_p:
                                                        with st.expander("System Prompt", expanded=False):
                                                            st.code(sys_p, language="text")
                                                    if usr_p:
                                                        with st.expander("User Prompt", expanded=False):
                                                            st.code(usr_p, language="text")
                                            else:
                                                display = cmd_text + hint if hint else cmd_text
                                                if cmd_sel and set_selected:
                                                    st.code(display, language="bash")
                                                else:
                                                    st.markdown(f"~~`{display}`~~ *(skipped)*")

                with st.expander(_phase_label("Completion", "completion"), expanded=False):
                    _render_step_list_readonly(_clean_steps(cfg.get("completion_commands", [])), "completion")

    # Run / Cancel / Clear buttons
    run_in_progress = st.session_state.get("_run_in_progress", False)
    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Execute",
            key="btn_bash_exec_run",
            type="primary",
            use_container_width=True,
            disabled=run_in_progress,
        )
    with col_cancel:
        if st.button("⏹  Stop", key="btn_bash_exec_cancel",
                     use_container_width=True, disabled=not run_in_progress):
            st.session_state["cancel_requested"] = True
            st.rerun()
    with col_clear:
        if st.button("Clear Log", key="btn_bash_exec_clear",
                     use_container_width=True):
            st.session_state["run_logs_setup"]   = []
            st.session_state["run_logs_validation"]   = []
            st.session_state["run_completed"]    = False
            st.session_state["telemetry"]        = {}
            st.session_state["_run_in_progress"] = False
            st.session_state["cancel_requested"] = False
            st.session_state["_exec_phase"]      = ""
            st.rerun()

    col_sh, col_ll = st.columns(2)
    col_sh.markdown("**Setup/Cleanup Log**")
    col_ll.markdown("**Validation Log**")
    shell_placeholder = col_sh.empty()
    llama_placeholder = col_ll.empty()
    st.session_state["_bash_log_placeholder_shell"] = shell_placeholder
    st.session_state["_bash_log_placeholder_llama"] = llama_placeholder

    if run_btn and not run_in_progress:
        st.session_state["run_logs_setup"]   = []
        st.session_state["run_logs_validation"]   = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True
        st.session_state["_exec_phase"]      = ""
        shell_placeholder.empty()
        llama_placeholder.empty()
        
        # Flush config in the main thread before launching background execution
        _flush_bash_config(project)
        
        # Launch in background thread so the UI stays responsive
        shared_state = {
            "cancel_requested": False,
            "phase": "",
            "logs_setup": [],
            "logs_validation": [],
            "completed": False,
            "telemetry": {},
        }
        st.session_state["_run_shared"] = shared_state
        thread = threading.Thread(target=_run_bash_bot, args=(project, shared_state), daemon=True)
        thread.start()
        st.session_state["_run_thread"] = thread
        st.rerun()

    # Polling: if a run is in progress, refresh the UI periodically without flickering
    @st.fragment(run_every="0.5s")
    def _poll_bash_execution():
        shared = st.session_state.get("_run_shared", {})
        
        # Sync shared state to session state for UI to render
        st.session_state["run_logs_setup"] = shared.get("logs_setup", [])
        st.session_state["run_logs_validation"] = shared.get("logs_validation", [])
        if shared.get("phase"):
            st.session_state["_exec_phase"] = shared["phase"]
            
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_setup", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_validation", []))
        thread = st.session_state.get("_run_thread")
        
        if thread and thread.is_alive():
            # If user clicked Stop in UI, push it to shared dict
            if st.session_state.get("cancel_requested"):
                shared["cancel_requested"] = True
        else:
            # Thread finished — ensure state is clean
            st.session_state["_run_in_progress"] = False
            st.session_state.pop("_run_thread", None)
            
            # Save telemetry to history if completed
            if shared.get("completed"):
                st.session_state["telemetry"] = shared.get("telemetry", {})
                st.session_state["run_completed"] = True
                
                from config.defaults import MAX_RUN_HISTORY
                history_key = f"run_history_{project['id']}"
                history: list = st.session_state.get(history_key, [])
                history.append(shared.get("telemetry", {}))
                st.session_state[history_key] = history[-MAX_RUN_HISTORY:]
                
            if st.session_state.get("_exec_phase") != "done":
                st.session_state["_exec_phase"] = "done"
            st.rerun()

    if run_in_progress:
        _poll_bash_execution()
    else:
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_setup", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_validation", []))

    # Show result summary if run just completed
    if st.session_state.get("run_completed") and st.session_state.get("telemetry"):
        tel = st.session_state["telemetry"]
        if tel.get("run_aborted"):
            st.warning("⚠️ Run was cancelled or aborted.")
        elif tel.get("validation_passed") is True:
            st.success("✓ Execution complete — all validation commands passed.")
        elif tel.get("validation_passed") is False:
            if tel.get("prompt_call_failed"):
                st.error("✗ Execution complete — an LLM Judge prompt failed or could not connect.")
            else:
                st.error("✗ Execution complete — one or more validation commands failed.")
        else:
            st.info("📊 Execution complete.")



# ── Llama-CLI Bot execute ──────────────────────────────────────────────────────

def _run_llama_cli_bot(project: dict, shared: dict, bot_type: str = "llama_cli_bot") -> None:
    """Build environment and run a llama-backed evaluation.

    ``shared`` is a plain dict visible to both this thread and the main
    Streamlit thread.  We never touch ``st.session_state`` here.
    """
    import shlex
    from core.environment import LocalEnvironment, SSHEnvironment
    from core.evaluator import run_llama_cli_evaluation
    from core.session_log import SessionLog

    cfg = project["config"]

    cancel_ref: list[bool] = [False]

    session_log = SessionLog()

    def on_log(msg: str, source: str = "llama") -> None:
        if shared.get("cancel_requested"):
            cancel_ref[0] = True
        # Track execution phase from log prefixes
        if msg.startswith("[RUN]") or msg.startswith("[DELAY] Step"):
            shared["phase"] = "startup"
        elif msg.startswith("[VALIDATE"):
            shared["phase"] = "validation"
        elif msg.startswith("[CLEANUP]"):
            shared["phase"] = "completion"
        elif msg.startswith("[COMPLETE]"):
            shared["phase"] = "done"
        # Determine log destination by phase
        phase = shared.get("phase", "startup")
        entry = {"text": msg, "tag": _tag(msg)}
        if phase == "validation":
            shared.setdefault("logs_validation", []).append(entry)
        else:
            shared.setdefault("logs_setup", []).append(entry)
        session_log.log(msg)

    from core.environment import create_environment
    tgt = cfg.get("execution_target", "local")
    is_ssh = tgt == "ssh"

    env = create_environment(
        ssh=is_ssh,
        host=cfg.get("ssh_host", ""),
        port=int(cfg.get("ssh_port") or 22),
        username=cfg.get("ssh_user", "root"),
        password=cfg.get("ssh_password") or None,
        key_path=cfg.get("ssh_key_path") or None,
        remote_cwd=".",
        pct_vmid=cfg.get("pct_vmid", "") if tgt == "pct" else None,
    )

    is_llama_server = bot_type == "llama_server_bot"
    default_backend = "llama-server (managed)" if is_llama_server else "llama.cpp"

    llama_config = {
        "type":                bot_type,
        "backend":             cfg.get("backend", default_backend),
        "backend_type":        cfg.get("backend", default_backend),
        "binary_path":         cfg.get("binary_path", ""),
        "model_dir":           cfg.get("model_dir", ""),
        "model_name":          cfg.get("model_name", ""),
        "selected_model":      cfg.get("model_name", ""),
        "tokens":              cfg.get("tokens", 2048),
        "context_size":        cfg.get("tokens", 2048),
        "server_host":         cfg.get("server_host", "127.0.0.1"),
        "server_port":         cfg.get("server_port", 8080),
        "server_ready_timeout": cfg.get("server_ready_timeout", 300),
        "mcp_server_url":      "http://127.0.0.1:9191",
        "openai_base_url":     cfg.get("openai_base_url", ""),
        "llm_url":             cfg.get("openai_base_url", ""),
        "openai_api_key":      cfg.get("openai_api_key", ""),
        "openai_verify_ssl":   cfg.get("openai_verify_ssl", True),
        "mcp_servers":         [s for s in cfg.get("mcp_servers", []) if s.get("enabled")],
        "prompts":             cfg.get("prompts", []),
        "commands":            cfg.get("commands", []),
        "startup_commands":    cfg.get("startup_commands", []),
        "completion_commands": cfg.get("completion_commands", []),
        "timeout":             cfg.get("timeout", 60),
        "validation_commands": cfg.get("validation_commands", []),
        "validation_sets":     _get_llama_selected_validation_sets(
            cfg,
            exec_prefix="llama_server_exec" if is_llama_server else "llama_exec",
        ),
        "fail_patterns":       cfg.get("fail_patterns", []),
        "metrics_matrix":      cfg.get("metrics_matrix", []),
        "sudo":                cfg.get("sudo", False),
        "sudo_password":       cfg.get("sudo_password", ""),
        "system_prompt":       cfg.get("system_prompt", ""),
        "execution_target":    tgt,
        "custom_flags":        cfg.get("custom_flags", ""),
        "en_temp":             cfg.get("en_temp", False),
        "temperature":         cfg.get("temperature", 0.8),
        "en_gpu_layers":       cfg.get("en_gpu_layers", False),
        "gpu_layers":          cfg.get("gpu_layers", 99),
        "en_threads":          cfg.get("en_threads", False),
        "threads":             cfg.get("threads", 4),
        "flash_attn":          cfg.get("flash_attn", False),
        "en_top_k":            cfg.get("en_top_k", False),
        "top_k":               cfg.get("top_k", 40),
        "en_top_p":            cfg.get("en_top_p", False),
        "top_p":               cfg.get("top_p", 0.9),
        "en_min_p":            cfg.get("en_min_p", False),
        "min_p":               cfg.get("min_p", 0.1),
        "en_repeat_penalty":   cfg.get("en_repeat_penalty", False),
        "repeat_penalty":      cfg.get("repeat_penalty", 1.1),
        "en_freq_penalty":     cfg.get("en_freq_penalty", False),
        "freq_penalty":        cfg.get("freq_penalty", 0.0),
        "en_predict":          cfg.get("en_predict", False),
        "predict":             cfg.get("predict", 512),
        "en_seed":             cfg.get("en_seed", False),
        "seed":                cfg.get("seed", -1),
        "en_rope_freq_base":   cfg.get("en_rope_freq_base", False),
        "rope_freq_base":      cfg.get("rope_freq_base", 10000.0),
        "en_rope_freq_scale":  cfg.get("en_rope_freq_scale", False),
        "rope_freq_scale":     cfg.get("rope_freq_scale", 1.0),
        "cancel_requested_ref": cancel_ref,
        "active_project_id":   project.get("id"),
    }
    # LLM Judge connection — also drives final-response scoring (_run_ai_judge)
    llama_config.update({k: v for k, v in cfg.items() if k.startswith("llm_helper_")})

    telemetry: dict | None = None
    try:
        telemetry = run_llama_cli_evaluation(env, llama_config, on_log)
    except Exception as exc:
        on_log(f"[ERROR] Evaluation failed: {exc}")
        telemetry = {"run_aborted": True, "error": str(exc)}
    finally:
        if hasattr(env, "close"):
            env.close()

    # Mark whether an explicit user cancellation caused the abort (vs timeout).
    if isinstance(telemetry, dict) and telemetry.get("run_aborted"):
        telemetry["interrupted_by_user"] = bool(shared.get("cancel_requested"))

    session_log.save_telemetry(telemetry or {})
    session_log.save_config(llama_config)
    session_log.close()

    shared["telemetry"]  = telemetry or {}
    shared["completed"]  = True
    shared["project_id"] = project.get("id")


def _get_llama_selected_validation_sets(cfg: dict, exec_prefix: str = "llama_exec") -> list:
    """Return a filtered deep-copy of cfg['validation_sets'] based on execute-tab checkboxes."""
    import copy
    filtered = []
    for idx, vset in enumerate(cfg.get("validation_sets", [])):
        if not st.session_state.get(f"{exec_prefix}_vset_{idx}_selected", True):
            continue
        vset_copy = copy.deepcopy(vset)
        vset_copy["steps"] = _clean_steps(vset_copy.get("steps", []))
        for sidx, step in enumerate(vset_copy.get("steps", [])):
            for cidx, cmd_obj in enumerate(step.get("commands", [])):
                key = f"{exec_prefix}_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                cmd_obj["enabled"] = st.session_state.get(key, cmd_obj.get("enabled", True))
        filtered.append(vset_copy)
    return filtered


def _render_llama_cli_execute(
    project: dict,
    bot_type: str = "llama_cli_bot",
    llm_label: str = "LLAMA-CLI",
    state_prefix: str = "llama_cli",
    exec_prefix: str = "llama_exec",
    flush_fn=_flush_llama_cli_config,
) -> None:
    """Execute view for llama-backed bots: config summary + Execute button + log."""
    cfg = project.get("config", {})

    st.markdown(f"### {project['name']}")

    # ── Two-column configuration panel ───────────────────────────────────────
    config_expanded_key = f"{exec_prefix}_config_expanded"
    _cfg_open = st.session_state.get(config_expanded_key, True)
    with st.container(border=True):
        col_hdr_outer, col_tog_outer = st.columns([9, 1])
        with col_hdr_outer:
            st.markdown("**⚙️ Run Configuration**")
        with col_tog_outer:
            if st.button("▼" if _cfg_open else "▶",
                         key=f"btn_{exec_prefix}_outer_toggle", use_container_width=True,
                         help="Expand/collapse all"):
                st.session_state[config_expanded_key] = not _cfg_open
                st.rerun()

        if _cfg_open:
            with st.container(border=True):
                target = cfg.get("execution_target", "local")
                ssh_info = f" ({cfg.get('ssh_user','root')}@{cfg.get('ssh_host','?')}:{cfg.get('ssh_port',22)})" if target == "ssh" else ""
                target_str = f"Target: {target.upper()}{ssh_info}"
                model_name = cfg.get("model_name", "") or "not selected"
                timeout_str = f"Timeout: {cfg.get('timeout', 60)}s"
                st.markdown(f"**Execution Configuration** &nbsp;&nbsp;<span style='color: #888; font-size: 0.9em'>|&nbsp;&nbsp; {target_str} &nbsp;&nbsp;|&nbsp;&nbsp; {timeout_str}</span>", unsafe_allow_html=True)

                # Model info summary
                backend = cfg.get("backend", "llama-cli")
                with st.expander("**Model Info**", expanded=True):
                    st.caption(f"Backend: **{backend}**")
                    st.caption(f"Model: **{model_name}**")
                    if bot_type == "llama_server_bot":
                        st.caption(f"Binary: `{cfg.get('binary_path', '') or 'not configured'}`")
                        st.caption(f"Listen: `{cfg.get('server_host', '127.0.0.1')}:{cfg.get('server_port', 8080)}`")
                        st.caption(f"Client URL: `{cfg.get('openai_base_url', '') or 'not configured'}`")
                    elif backend == "llama-cli":
                        st.caption(f"Binary: `{cfg.get('binary_path', '') or 'not configured'}`")
                    else:
                        st.caption(f"URL: `{cfg.get('openai_base_url', '') or 'not configured'}`")
                    enabled_mcps = [s["name"] for s in cfg.get("mcp_servers", []) if s.get("enabled")]
                    if enabled_mcps:
                        st.caption(f"MCP: {', '.join(enabled_mcps)}")

                with st.expander(_phase_label("Startup", "startup"), expanded=False):
                    _render_step_list_readonly(_clean_steps(cfg.get("startup_commands", [])), "startup")

                with st.expander(_phase_label("Validation", "validation"), expanded=False):
                    val_sets = cfg.get("validation_sets", [])
                    if not val_sets:
                        st.caption("No validation sets configured — add them in the Config tab (Validation).")
                    else:
                        for idx, vset in enumerate(val_sets):
                            set_sel_key = f"{exec_prefix}_vset_{idx}_selected"
                            set_selected = st.session_state.get(set_sel_key, True)

                            desc = vset.get("description", "")
                            label_md = f"**{idx + 1}. {vset['name']}**"
                            if desc:
                                label_md += f" — {desc}"

                            with st.expander(label_md, expanded=set_selected):
                                st.session_state.setdefault(set_sel_key, True)
                                new_sel = st.checkbox(
                                    "Enable this Validation Set",
                                    key=set_sel_key,
                                )
                                set_selected = new_sel

                                for sidx, step in enumerate(vset.get("steps", [])):
                                    delay = step.get("delay_seconds", 0)
                                    dstr  = f" ({delay}s delay)" if delay > 0 else ""
                                    st.caption(f"Step {sidx + 1}{dstr}:")
                                    for cidx, cmd_obj in enumerate(step.get("commands", [])):
                                        _type = cmd_obj.get("type", "command")
                                        if _type == "prompt":
                                            sys_p = cmd_obj.get("system_prompt", "")
                                            usr_p = cmd_obj.get("user_prompt", "")
                                            if not sys_p and not usr_p:
                                                continue
                                            cmd_text = f"Configured {llm_label} LLM: {sys_p[:20]}... | {usr_p[:20]}..."
                                        else:
                                            cmd_text = cmd_obj.get("command", "")
                                            if not cmd_text:
                                                continue
                                        cmd_key  = f"{exec_prefix}_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                                        cmd_sel  = st.session_state.get(cmd_key, cmd_obj.get("enabled", True))
                                        hint = _validation_checks_summary(cmd_obj)

                                        col_cc, col_cl = st.columns([0.3, 10.7])
                                        with col_cc:
                                            st.session_state.setdefault(cmd_key, cmd_obj.get("enabled", True))
                                            new_cmd_sel = st.checkbox(
                                                f"Enable command {cidx+1} in step {sidx+1}",
                                                key=cmd_key,
                                                label_visibility="collapsed",
                                                disabled=not set_selected,
                                            )
                                            cmd_sel = new_cmd_sel
                                        with col_cl:
                                            if _type == "prompt":
                                                with st.container(border=True):
                                                    st.markdown(f"💬 **Configured {llm_label} LLM**")
                                                    if sys_p:
                                                        with st.expander("System Prompt", expanded=False):
                                                            st.code(sys_p, language="text")
                                                    if usr_p:
                                                        with st.expander("User Prompt", expanded=False):
                                                            st.code(usr_p, language="text")
                                            else:
                                                display = cmd_text + hint if hint else cmd_text
                                                if cmd_sel and set_selected:
                                                    st.code(display, language="bash")
                                                else:
                                                    st.markdown(f"~~`{display}`~~ *(skipped)*")

                with st.expander(_phase_label("Completion", "completion"), expanded=False):
                    _render_step_list_readonly(_clean_steps(cfg.get("completion_commands", [])), "completion")

    # ── Scenario system prompt (editable, persisted) ──────────────────────────
    system_prompt_key = f"{state_prefix}_system_prompt"
    st.session_state.setdefault(system_prompt_key, cfg.get("system_prompt", ""))
    sys_prompt = st.text_area(
        "System Prompt",
        key=system_prompt_key,
        height=100,
        placeholder="Optional system prompt sent to the model before evaluation prompts.",
        help="Custom system prompt. Leave empty for no system prompt.",
    )
    # Persist back to config
    project["config"]["system_prompt"] = sys_prompt
    flush_fn(project)

    # Run / Cancel / Clear buttons
    run_in_progress = st.session_state.get("_run_in_progress", False)
    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Execute",
            key=f"btn_{exec_prefix}_run",
            type="primary",
            use_container_width=True,
            disabled=run_in_progress,
        )
    with col_cancel:
        if st.button("⏹  Stop", key=f"btn_{exec_prefix}_cancel",
                     use_container_width=True, disabled=not run_in_progress):
            st.session_state["cancel_requested"] = True
            st.rerun()
    with col_clear:
        if st.button("Clear Log", key=f"btn_{exec_prefix}_clear", use_container_width=True):
            st.session_state["run_logs_setup"]   = []
            st.session_state["run_logs_validation"]   = []
            st.session_state["run_completed"]    = False
            st.session_state["telemetry"]        = {}
            st.session_state["_run_in_progress"] = False
            st.session_state["cancel_requested"] = False
            st.session_state["_exec_phase"]      = ""
            st.rerun()

    col_sh, col_ll = st.columns(2)
    col_sh.markdown("**Setup/Cleanup Log**")
    col_ll.markdown("**Validation Log**")
    shell_placeholder = col_sh.empty()
    llama_placeholder = col_ll.empty()
    st.session_state["_llama_log_placeholder_shell"] = shell_placeholder
    st.session_state["_llama_log_placeholder_llama"] = llama_placeholder

    if run_btn and not run_in_progress:
        st.session_state["run_logs_setup"]   = []
        st.session_state["run_logs_validation"]   = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True
        st.session_state["_exec_phase"]      = ""
        shell_placeholder.empty()
        llama_placeholder.empty()
        
        # Flush config in the main thread before launching background execution
        flush_fn(project)
        
        # Launch in background thread so the UI stays responsive
        shared_state = {
            "cancel_requested": False,
            "phase": "",
            "logs_setup": [],
            "logs_validation": [],
            "completed": False,
            "telemetry": {},
        }
        st.session_state["_run_shared"] = shared_state
        thread = threading.Thread(target=_run_llama_cli_bot, args=(project, shared_state, bot_type), daemon=True)
        thread.start()
        st.session_state["_run_thread"] = thread
        st.rerun()

    # Polling: if a run is in progress, refresh the UI periodically without flickering
    @st.fragment(run_every="0.5s")
    def _poll_llama_execution():
        shared = st.session_state.get("_run_shared", {})
        
        # Sync shared state to session state for UI to render
        st.session_state["run_logs_setup"] = shared.get("logs_setup", [])
        st.session_state["run_logs_validation"] = shared.get("logs_validation", [])
        if shared.get("phase"):
            st.session_state["_exec_phase"] = shared["phase"]
            
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_setup", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_validation", []))
        thread = st.session_state.get("_run_thread")
        
        if thread and thread.is_alive():
            # If user clicked Stop in UI, push it to shared dict
            if st.session_state.get("cancel_requested"):
                shared["cancel_requested"] = True
        else:
            # Thread finished — ensure state is clean
            st.session_state["_run_in_progress"] = False
            st.session_state.pop("_run_thread", None)
            
            # Save telemetry to history if completed
            if shared.get("completed"):
                st.session_state["telemetry"] = shared.get("telemetry", {})
                st.session_state["run_completed"] = True
                
                from config.defaults import MAX_RUN_HISTORY
                history_key = f"run_history_{project['id']}"
                history: list = st.session_state.get(history_key, [])
                history.append(shared.get("telemetry", {}))
                st.session_state[history_key] = history[-MAX_RUN_HISTORY:]
                
            if st.session_state.get("_exec_phase") != "done":
                st.session_state["_exec_phase"] = "done"
            st.rerun()

    if run_in_progress:
        _poll_llama_execution()
    else:
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_setup", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_validation", []))

    # Show result summary if run just completed
    if st.session_state.get("run_completed") and st.session_state.get("telemetry"):
        tel = st.session_state["telemetry"]
        if tel.get("run_aborted"):
            if tel.get("interrupted_by_user"):
                st.warning("⚠️ Execution was interrupted by the user — metrics may be incomplete.")
            else:
                st.warning("⚠️ Execution timed out — metrics may be incomplete.")
        elif tel.get("validation_passed") is True:
            st.success("✓ Execution complete — all validation commands passed.")
        elif tel.get("validation_passed") is False:
            if tel.get("prompt_call_failed"):
                st.error("✗ Execution complete — an LLM Judge prompt failed or could not connect.")
            else:
                st.error("✗ Execution complete — one or more validation commands failed.")
        else:
            responses = tel.get("prompt_responses", [])
            if responses:
                st.info(f"📊 Execution complete — {len(responses)} prompt(s) processed.")
            else:
                st.info("📊 Execution complete.")


def _render_llama_server_execute(project: dict) -> None:
    """Execute view for Llama-Server-Bot."""
    _render_llama_cli_execute(
        project,
        bot_type="llama_server_bot",
        llm_label="LLAMA-SERVER",
        state_prefix="llama_server",
        exec_prefix="llama_server_exec",
        flush_fn=_flush_llama_server_config,
    )


def render() -> None:
    st.header("Execute Evaluation")

    # Dispatch to bot-type-specific execute view
    _proj = _get_active_project()
    if _proj is None:
        st.info("No project selected. Use the sidebar to add or select a project.")
        return

    plugin = get_bot_plugin(_proj.get("type", "bash_bot"))
    if plugin is not None:
        plugin.render_execute(_proj)
        return

    st.info(f"**{_proj['name']}** ({_proj.get('type', '?')}) — execution coming soon.")
