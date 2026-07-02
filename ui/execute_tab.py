import os
import time
import threading
import streamlit as st
from config.defaults import LLAMA_CPP_DEFAULT_URL, OLLAMA_DEFAULT_URL
from core.evaluator import run_evaluation
from core.logsetup import logged_on_log
from core.session_log import SessionLog
from core import llama_server
from core.mcp_manager import poll_mcp_process
from ui.components import status_pill
from ui.terminal import render_terminal
from ui.config_tab import _flush_bash_config, _flush_llama_cli_config


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
}


def _tag(line: str) -> str:
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

    _flush_bash_config(project)
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
        entry = {"text": msg, "tag": _tag(msg)}
        if source == "shell":
            shared["logs_shell"].append(entry)
        else:
            shared["logs_llama"].append(entry)
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
        "sudo_password":       cfg.get("sudo_password", ""),
        "cancel_requested_ref": cancel_ref,
        "execution_mode": "bash",
        "active_project_id":   project.get("id"),
    }
    # Inherit LLM Judge configurations
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
                                        cmd_text = cmd_obj.get("command", "")
                                        if not cmd_text:
                                            continue
                                        cmd_key  = f"bash_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                                        cmd_sel  = st.session_state.get(cmd_key, cmd_obj.get("enabled", True))
                                        exp_type = cmd_obj.get("expected_output_type", "Ignore")
                                        exp_out  = cmd_obj.get("expected_output", "")
                                        hint = ""
                                        if exp_type != "Ignore" and exp_out:
                                            short = exp_out[:40] + ("…" if len(exp_out) > 40 else "")
                                            hint = f"  # expect {exp_type.lower()}: {short}"

                                        col_cc, col_cl = st.columns([1, 10])
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
            st.session_state["run_logs_shell"]   = []
            st.session_state["run_logs_llama"]   = []
            st.session_state["run_completed"]    = False
            st.session_state["telemetry"]        = {}
            st.session_state["_run_in_progress"] = False
            st.session_state["cancel_requested"] = False
            st.session_state["_exec_phase"]      = ""
            st.rerun()

    col_sh, col_ll = st.columns(2)
    col_sh.markdown("**Shell Execution Log**")
    col_ll.markdown("**LLM Interaction Log**")
    shell_placeholder = col_sh.empty()
    llama_placeholder = col_ll.empty()
    st.session_state["_bash_log_placeholder_shell"] = shell_placeholder
    st.session_state["_bash_log_placeholder_llama"] = llama_placeholder

    if run_btn and not run_in_progress:
        st.session_state["run_logs_shell"]   = []
        st.session_state["run_logs_llama"]   = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True
        st.session_state["_exec_phase"]      = ""
        shell_placeholder.empty()
        llama_placeholder.empty()
        # Launch in background thread so the UI stays responsive
        shared_state = {
            "cancel_requested": False,
            "phase": "",
            "logs_shell": [],
            "logs_llama": [],
            "completed": False,
            "telemetry": {},
        }
        st.session_state["_run_shared"] = shared_state
        thread = threading.Thread(target=_run_bash_bot, args=(project, shared_state), daemon=True)
        thread.start()
        st.session_state["_run_thread"] = thread
        st.rerun()

    # Polling: if a run is in progress, refresh the UI periodically
    if run_in_progress:
        shared = st.session_state.get("_run_shared", {})
        
        # Sync shared state to session state for UI to render
        st.session_state["run_logs_shell"] = shared.get("logs_shell", [])
        st.session_state["run_logs_llama"] = shared.get("logs_llama", [])
        if shared.get("phase"):
            st.session_state["_exec_phase"] = shared["phase"]
            
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_shell", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_llama", []))
        thread = st.session_state.get("_run_thread")
        
        if thread and thread.is_alive():
            # If user clicked Stop in UI, push it to shared dict
            if st.session_state.get("cancel_requested"):
                shared["cancel_requested"] = True
            time.sleep(0.5)
            st.rerun()
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
    else:
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_shell", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_llama", []))

    # Show result summary if run just completed
    if st.session_state.get("run_completed") and st.session_state.get("telemetry"):
        tel = st.session_state["telemetry"]
        if tel.get("run_aborted"):
            st.warning("⚠️ Run was cancelled or aborted.")
        elif tel.get("validation_passed") is True:
            st.success("✓ Execution complete — all validation commands passed.")
        elif tel.get("validation_passed") is False:
            st.error("✗ Execution complete — one or more validation commands failed.")
        else:
            st.info("📊 Execution complete.")



# ── Llama-CLI Bot execute ──────────────────────────────────────────────────────

def _run_llama_cli_bot(project: dict, shared: dict) -> None:
    """Build environment and run llama_cli evaluation.

    ``shared`` is a plain dict visible to both this thread and the main
    Streamlit thread.  We never touch ``st.session_state`` here.
    """
    import shlex
    from core.environment import LocalEnvironment, SSHEnvironment
    from core.evaluator import run_llama_cli_evaluation
    from core.session_log import SessionLog

    _flush_llama_cli_config(project)
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
        entry = {"text": msg, "tag": _tag(msg)}
        if source == "shell":
            shared["logs_shell"].append(entry)
        else:
            shared["logs_llama"].append(entry)
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

    llama_config = {
        "backend":             cfg.get("backend", "llama.cpp"),
        "binary_path":         cfg.get("binary_path", "llama-cli"),
        "model_dir":           cfg.get("model_dir", ""),
        "model_name":          cfg.get("model_name", ""),
        "tokens":              cfg.get("tokens", 2048),
        "server_port":         cfg.get("server_port", 18080),
        "mcp_server_url":      "http://127.0.0.1:9191",
        "openai_base_url":     cfg.get("openai_base_url", ""),
        "openai_api_key":      cfg.get("openai_api_key", ""),
        "openai_verify_ssl":   cfg.get("openai_verify_ssl", True),
        "mcp_servers":         [s for s in cfg.get("mcp_servers", []) if s.get("enabled")],
        "prompts":             cfg.get("prompts", []),
        "commands":            cfg.get("commands", []),
        "startup_commands":    cfg.get("startup_commands", []),
        "completion_commands": cfg.get("completion_commands", []),
        "timeout":             cfg.get("timeout", 60),
        "validation_commands": cfg.get("validation_commands", []),
        "validation_sets":     _get_llama_selected_validation_sets(cfg),
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
        "seed":                cfg.get("seed", 42),
        "cancel_requested_ref": cancel_ref,
        "active_project_id":   project.get("id"),
    }
    # Inherit LLM Judge configurations
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


def _get_llama_selected_validation_sets(cfg: dict) -> list:
    """Return a filtered deep-copy of cfg['validation_sets'] based on llama execute-tab checkboxes."""
    import copy
    filtered = []
    for idx, vset in enumerate(cfg.get("validation_sets", [])):
        if not st.session_state.get(f"llama_exec_vset_{idx}_selected", True):
            continue
        vset_copy = copy.deepcopy(vset)
        vset_copy["steps"] = _clean_steps(vset_copy.get("steps", []))
        for sidx, step in enumerate(vset_copy.get("steps", [])):
            for cidx, cmd_obj in enumerate(step.get("commands", [])):
                key = f"llama_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                cmd_obj["enabled"] = st.session_state.get(key, cmd_obj.get("enabled", True))
        filtered.append(vset_copy)
    return filtered


def _render_llama_cli_execute(project: dict) -> None:
    """Execute view for Llama-CLI-Bot: collapsible config sub-blocks + Execute button + log."""
    cfg = project.get("config", {})

    st.markdown(f"### {project['name']}")

    # ── Two-column configuration panel ───────────────────────────────────────
    _cfg_open = st.session_state.get("llama_exec_config_expanded", True)
    with st.container(border=True):
        col_hdr_outer, col_tog_outer = st.columns([9, 1])
        with col_hdr_outer:
            st.markdown("**⚙️ Run Configuration**")
        with col_tog_outer:
            if st.button("▼" if _cfg_open else "▶",
                         key="btn_llama_exec_outer_toggle", use_container_width=True,
                         help="Expand/collapse all"):
                st.session_state["llama_exec_config_expanded"] = not _cfg_open
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
                    if backend == "llama-cli":
                        st.caption(f"Binary: `{cfg.get('binary_path', 'llama-cli')}`")
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
                            set_sel_key = f"llama_exec_vset_{idx}_selected"
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
                                        cmd_text = cmd_obj.get("command", "")
                                        if not cmd_text:
                                            continue
                                        cmd_key  = f"llama_exec_vset_{idx}_step_{sidx}_cmd_{cidx}_selected"
                                        cmd_sel  = st.session_state.get(cmd_key, cmd_obj.get("enabled", True))
                                        exp_type = cmd_obj.get("expected_output_type", "Ignore")
                                        exp_out  = cmd_obj.get("expected_output", "")
                                        hint = ""
                                        if exp_type != "Ignore" and exp_out:
                                            short = exp_out[:40] + ("…" if len(exp_out) > 40 else "")
                                            hint = f"  # expect {exp_type.lower()}: {short}"

                                        col_cc, col_cl = st.columns([1, 10])
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
                                            display = cmd_text + hint if hint else cmd_text
                                            if cmd_sel and set_selected:
                                                st.code(display, language="bash")
                                            else:
                                                st.markdown(f"~~`{display}`~~ *(skipped)*")

                with st.expander(_phase_label("Completion", "completion"), expanded=False):
                    _render_step_list_readonly(_clean_steps(cfg.get("completion_commands", [])), "completion")

    # ── Scenario system prompt (editable, persisted) ──────────────────────────
    st.session_state.setdefault("llama_cli_system_prompt", cfg.get("system_prompt", ""))
    sys_prompt = st.text_area(
        "System Prompt",
        key="llama_cli_system_prompt",
        height=100,
        placeholder="Optional system prompt sent to the model before evaluation prompts.",
        help="Custom system prompt. Leave empty for no system prompt.",
    )
    # Persist back to config
    project["config"]["system_prompt"] = sys_prompt
    _flush_llama_cli_config(project)

    # Run / Cancel / Clear buttons
    run_in_progress = st.session_state.get("_run_in_progress", False)
    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Execute",
            key="btn_llama_exec_run",
            type="primary",
            use_container_width=True,
            disabled=run_in_progress,
        )
    with col_cancel:
        if st.button("⏹  Stop", key="btn_llama_exec_cancel",
                     use_container_width=True, disabled=not run_in_progress):
            st.session_state["cancel_requested"] = True
            st.rerun()
    with col_clear:
        if st.button("Clear Log", key="btn_llama_exec_clear", use_container_width=True):
            st.session_state["run_logs_shell"]   = []
            st.session_state["run_logs_llama"]   = []
            st.session_state["run_completed"]    = False
            st.session_state["telemetry"]        = {}
            st.session_state["_run_in_progress"] = False
            st.session_state["cancel_requested"] = False
            st.session_state["_exec_phase"]      = ""
            st.rerun()

    col_sh, col_ll = st.columns(2)
    col_sh.markdown("**Shell Execution Log**")
    col_ll.markdown("**LLM Interaction Log**")
    shell_placeholder = col_sh.empty()
    llama_placeholder = col_ll.empty()
    st.session_state["_llama_log_placeholder_shell"] = shell_placeholder
    st.session_state["_llama_log_placeholder_llama"] = llama_placeholder

    if run_btn and not run_in_progress:
        st.session_state["run_logs_shell"]   = []
        st.session_state["run_logs_llama"]   = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True
        st.session_state["_exec_phase"]      = ""
        shell_placeholder.empty()
        llama_placeholder.empty()
        # Launch in background thread so the UI stays responsive
        shared_state = {
            "cancel_requested": False,
            "phase": "",
            "logs_shell": [],
            "logs_llama": [],
            "completed": False,
            "telemetry": {},
        }
        st.session_state["_run_shared"] = shared_state
        thread = threading.Thread(target=_run_llama_cli_bot, args=(project, shared_state), daemon=True)
        thread.start()
        st.session_state["_run_thread"] = thread
        st.rerun()

    # Polling: if a run is in progress, refresh the UI periodically
    if run_in_progress:
        shared = st.session_state.get("_run_shared", {})
        
        # Sync shared state to session state for UI to render
        st.session_state["run_logs_shell"] = shared.get("logs_shell", [])
        st.session_state["run_logs_llama"] = shared.get("logs_llama", [])
        if shared.get("phase"):
            st.session_state["_exec_phase"] = shared["phase"]
            
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_shell", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_llama", []))
        thread = st.session_state.get("_run_thread")
        
        if thread and thread.is_alive():
            # If user clicked Stop in UI, push it to shared dict
            if st.session_state.get("cancel_requested"):
                shared["cancel_requested"] = True
            time.sleep(0.5)
            st.rerun()
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
    else:
        _render_terminal(shell_placeholder, st.session_state.get("run_logs_shell", []))
        _render_terminal(llama_placeholder, st.session_state.get("run_logs_llama", []))

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
            st.error("✗ Execution complete — one or more validation commands failed.")
        else:
            responses = tel.get("prompt_responses", [])
            if responses:
                st.info(f"📊 Execution complete — {len(responses)} prompt(s) processed.")
            else:
                st.info("📊 Execution complete.")


def render() -> None:
    st.header("Execute Evaluation")

    # Dispatch to bot-type-specific execute view
    _proj = _get_active_project()
    if _proj and _proj.get("type") == "bash_bot":
        _render_bash_execute(_proj)
        return
    if _proj and _proj.get("type") == "llama_cli_bot":
        _render_llama_cli_execute(_proj)
        return

    # ai_agent type: show guidance when no model is configured yet
    if _proj and _proj.get("type") == "ai_agent" and not st.session_state.get("selected_model"):
        st.info(
            "**AI-Agent** mode requires a model to be configured. "
            "Go to **Configuration → Model Setup** to scan and select a model, "
            "then start the LLM server."
        )

    # Refresh llama-server status so the pill reflects current reality
    _backend_now = st.session_state.get("backend_type", "llama.cpp")
    if _backend_now == "llama.cpp":
        _url_now = (st.session_state.get("llm_url") or LLAMA_CPP_DEFAULT_URL).strip()
        llama_server.poll_ready(_url_now)

    # ── Pre-flight status bar (fix #24) ───────────────────────────────────────
    backend      = st.session_state.get("backend_type", "llama.cpp")
    model_sel    = st.session_state.get("selected_model")
    llm_running  = st.session_state.get("llama_server_running", False)
    mcp_running  = poll_mcp_process()
    tools_loaded = bool(st.session_state.get("mcp_tools"))

    llm_state  = "up"   if llm_running  else ("wait" if backend == "ollama" else "down")
    # "wait" = not started yet (amber); "down" = started but broken (red)
    mcp_state  = "up"   if mcp_running  else "wait"
    tool_state = (
        "up"   if tools_loaded else
        "down" if mcp_running  else  # MCP running but empty = genuine problem
        "wait"                        # MCP not started = expected state
    )
    mod_state  = "up"   if model_sel    else "wait"

    pills = status_pill(f"Model: {model_sel or 'not chosen'}", mod_state)
    
    # Only show llama-server status if model has been chosen
    if model_sel:
        pills += status_pill(f"{'Ollama' if backend == 'ollama' else 'llama-server'}: {'ready' if llm_running else 'not ready'}", llm_state)
    
    pills += (
        status_pill(f"MCP: {'running' if mcp_running else 'stopped'}", mcp_state)
        + status_pill(f"Tools: {len(st.session_state.get('mcp_tools', {}))}", tool_state)
    )
    st.markdown(pills, unsafe_allow_html=True)

    # Pre-flight warnings
    _backend   = st.session_state.get("backend_type", "llama.cpp")
    _url       = (st.session_state.get("llm_url") or "").strip()
    _default_u = LLAMA_CPP_DEFAULT_URL if _backend == "llama.cpp" else OLLAMA_DEFAULT_URL
    if not model_sel:
        st.warning(
            "⚠️  No model selected — go to **Configuration → Model Setup** and scan/select a model."
        )
    if not _url:
        st.warning(
            f"⚠️  Server URL is empty — defaulting to `{_default_u}`. "
            "Set it in **Configuration → Model Setup**."
        )
    # Warn if the server is loaded with a different model than the one selected.
    # Skip this check in remote/pre-compiled mode: the server reports a GGUF
    # filename while selected_model_path holds an Ollama model ID — different
    # naming formats for the same model, not a genuine mismatch.
    _src_mode = st.session_state.get("model_source_mode", "pre_compiled_local")
    if _backend == "llama.cpp" and _src_mode != "pre_compiled_remote" and llm_running and model_sel:
        _info = llama_server.get_server_info((_url or _default_u))
        if _info and _info.get("model_path"):
            _running_base  = os.path.basename(_info["model_path"])
            _selected_base = os.path.basename(st.session_state.get("selected_model_path") or "")
            if _running_base and _selected_base and _running_base != _selected_base:
                col_warn, col_fix = st.columns([5, 1])
                with col_warn:
                    st.error(
                        f"🚨  Model mismatch: server running **{_running_base}** "
                        f"but **{_selected_base}** is selected."
                    )
                with col_fix:
                    if st.button(
                        "↺ Restart",
                        key="btn_exec_restart_mismatch",
                        use_container_width=True,
                        type="primary",
                    ):
                        from core import llama_server as _ls
                        _ls.stop()
                        _mp = st.session_state.get("selected_model_path")
                        if _mp:
                            ok, msg = _ls.start(
                                _mp,
                                context_size=st.session_state.get("context_size", 4096),
                            )
                            st.session_state["_srv_msg"] = ("success" if ok else "error", msg)
                        st.rerun()

    # ── Tool focus info ────────────────────────────────────────────────────────
    _tool_f = st.session_state.get("tool_focus", "")
    st.caption(f"**Tool:** `{_tool_f}`" if _tool_f else "")
    # Track when user edits the prompt fields (fix #10)

    # ── Prompt editors ────────────────────────────────────────────────────────
    col_sys, col_cfg = st.columns(2)
    with col_sys:
        _sp_hdr, _sp_reset = st.columns([5, 1])
        with _sp_hdr:
            st.markdown(
                '<div class="sys-prompt-label">'
                '<span class="label-text">Scenario System Prompt</span>'
                '<span class="label-badge">Initial LLM Context</span>'
                '</div>',
                unsafe_allow_html=True,
            )
        with _sp_reset:
            if st.button("↺ Reset", key="btn_reset_sys_prompt",
                         help="Reset to system prompt default", use_container_width=True):
                st.session_state["_prompts_user_edited"] = False
                st.rerun()
        prev_sys = st.session_state.get("sys_prompt", "")
        new_sys  = st.text_area(
            "Scenario System Prompt", height=150, key="sys_prompt",
            help="Initial context injected before the user task — defines the agent's behaviour and available tools.",
            label_visibility="collapsed",
        )
        if new_sys != prev_sys:
            st.session_state["_prompts_user_edited"] = True

    with col_cfg:
        st.markdown(
            '<div class="sys-prompt-label">'
            '<span class="label-text">Run Configuration</span>'
            '<span class="label-badge">Active Settings</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        def _esc_pipe(v: str) -> str:
            return v.replace("|", r"\|")

        _backend_disp  = _esc_pipe(st.session_state.get("backend_type", "llama.cpp"))
        _model_disp    = _esc_pipe(st.session_state.get("selected_model") or "not selected")
        _ctx_disp      = st.session_state.get("context_size", 4096)
        _target_disp   = _esc_pipe(st.session_state.get("target_env_type", "local"))
        _val_cmd_disp  = _esc_pipe(st.session_state.get("validation_command", "") or "none")[:60]
        st.markdown(
            f"| Setting | Value |\n"
            f"|---|---|\n"
            f"| Backend | `{_backend_disp}` |\n"
            f"| Model | `{_model_disp}` |\n"
            f"| Context | `{_ctx_disp}` tokens |\n"
            f"| Target | `{_target_disp}` |\n"
            f"| Validation | `{_val_cmd_disp}` |",
        )
        st.markdown(
            '<div class="sys-prompt-label" style="margin-top:8px;">'
            '<span class="label-text">User Prompt</span>'
            '<span class="label-badge">Task</span>'
            '</div>',
            unsafe_allow_html=True,
        )
        prev_usr = st.session_state.get("user_prompt", "")
        new_usr  = st.text_area(
            "User Prompt", height=80, key="user_prompt",
            help="The task given to the agent.",
            label_visibility="collapsed",
        )
        if new_usr != prev_usr:
            st.session_state["_prompts_user_edited"] = True

    # ── Run / Cancel / Clear row ──────────────────────────────────────────────
    run_in_progress = st.session_state.get("_run_in_progress", False)
    model_chosen = bool(st.session_state.get("selected_model"))
    ssh_ready = (
        st.session_state.get("target_env_type", "local") != "remote (SSH)"
        or bool(st.session_state.get("target_ssh_host", "").strip())
    )

    if not ssh_ready:
        st.warning(
            "⚠️ SSH host is required — configure it in the **🎯 Target** tab."
        )

    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Run Evaluation",
            key="btn_exec_run",
            type="primary",
            use_container_width=True,
            disabled=run_in_progress or not model_chosen or not ssh_ready,
        )
    with col_cancel:
        # Stop button
        if st.button("⏹  Stop", key="btn_exec_cancel", use_container_width=True, disabled=not run_in_progress):
            st.session_state["cancel_requested"] = True
    with col_clear:
        if st.button("Clear Log", key="btn_exec_clear_log", use_container_width=True):
            st.session_state["run_logs"]          = []
            st.session_state["run_completed"]     = False
            st.session_state["telemetry"]         = {}
            st.session_state["_run_in_progress"]  = False
            st.session_state["cancel_requested"]  = False
            st.rerun()

    log_placeholder = st.empty()
    _render_terminal(log_placeholder, st.session_state.get("run_logs", []))

    if run_btn and not run_in_progress:
        st.session_state["run_logs"]         = []
        st.session_state["run_completed"]    = False
        st.session_state["telemetry"]        = {}
        st.session_state["cancel_requested"] = False
        st.session_state["_run_in_progress"] = True

        logs: list[dict]       = []
        cancel_ref: list[bool] = [False]
        _last_render: list[float] = [0.0]

        # Create a session log for this run.  The directory is created lazily
        # on first write so empty/cancelled runs leave no stray directories.
        session_log = SessionLog()

        def on_log(msg: str) -> None:
            # Propagate cancel flag into the evaluator via the shared reference
            if st.session_state.get("cancel_requested"):
                cancel_ref[0] = True
            entry = {"text": msg, "tag": _tag(msg)}
            logs.append(entry)
            st.session_state["run_logs"] = list(logs)
            # Persist the message to the session log file.
            session_log.log(msg)
            # Throttle terminal redraws to ~2/sec for streaming tokens.
            # Always render immediately for non-streaming events so tool calls,
            # errors, and validation results appear without delay.
            now = time.monotonic()
            is_stream_token = entry["tag"] in ("llm", "thinking")
            if not is_stream_token or (now - _last_render[0]) >= 0.5:
                _render_terminal(log_placeholder, logs)
                _last_render[0] = now

        # Mirror every event to the terminal logger (issue #3) while keeping the
        # in-browser terminal behaviour identical.
        on_log = logged_on_log(inner=on_log)

        _backend   = st.session_state.get("backend_type", "llama.cpp")
        _def_url   = LLAMA_CPP_DEFAULT_URL if _backend == "llama.cpp" else OLLAMA_DEFAULT_URL
        _url_val   = (st.session_state.get("llm_url") or _def_url).strip()

        _active_scenario = st.session_state.get("active_scenario", "")
        config = {
            "backend_type":        _backend,
            "llm_url":             _url_val,
            "selected_model":      st.session_state.get("selected_model"),
            "context_size":        st.session_state.get("context_size", 4096),
            "active_project_id":   st.session_state.get("active_project_id"),
            "sys_prompt":          st.session_state.get("sys_prompt", ""),
            "user_prompt":         st.session_state.get("user_prompt", ""),
            "mcp_url":             st.session_state.get("mcp_url", ""),
            "mcp_server_url":      st.session_state.get("mcp_server_url", ""),
            "mcp_tools":           st.session_state.get("mcp_tools", {}),
            "mcp_running":         st.session_state.get("mcp_running", False),
            "validation_command":  st.session_state.get("validation_command", ""),
            "fail_patterns":       st.session_state.get("fail_patterns", []),
            "active_scenario":     _active_scenario,
            "tool_focus":          st.session_state.get("tool_focus", ""),
            "metrics_matrix":      st.session_state.get("metrics_matrix", []),
            "cancel_requested_ref": cancel_ref,
            # CAF 4-Pillar runtime config
            "caf_scope":              st.session_state.get("caf_scope", "Narrow"),
            "caf_urgency":            st.session_state.get("caf_urgency", "Speed"),
            "caf_allowed_subnets":    st.session_state.get("caf_allowed_subnets", []),
            "caf_target_credentials": st.session_state.get("caf_target_credentials", []),
            # Optional AI Judge
            "judge_enabled":     st.session_state.get("judge_enabled", False),
            "judge_provider":    st.session_state.get("judge_provider", "anthropic"),
            "judge_model":       st.session_state.get("judge_model", ""),
            "judge_api_key":     st.session_state.get("judge_api_key", ""),
            "judge_temperature": st.session_state.get("judge_temperature", 0.0),
            "judge_mode":        st.session_state.get("judge_mode", "Score all responses"),
        }

        telemetry: dict | None = None

        # Spinner shows during the run (fix #15)
        with st.spinner("Evaluation running…"):
            from core.environment import create_environment
            env_type = st.session_state.get("target_env_type", "local")
            is_ssh = env_type == "remote (SSH)"
            is_pct = env_type == "pct (Proxmox LXC)"
            env = None
            try:
                env = create_environment(
                    ssh=is_ssh,
                    host=st.session_state.get("target_ssh_host", ""),
                    port=st.session_state.get("target_ssh_port") or 22,
                    username=st.session_state.get("target_ssh_user", "root"),
                    password=st.session_state.get("target_ssh_password"),
                    key_path=st.session_state.get("target_ssh_key_path"),
                    remote_cwd=st.session_state.get("target_ssh_caf_dir") or "",
                    pct_vmid=st.session_state.get("target_pct_vmid") if is_pct else None,
                )
                if is_pct:
                    on_log(f"[INIT] Target: PCT (VMID: {st.session_state.get('target_pct_vmid', '?')}) via SSH/Local")
                elif is_ssh:
                    on_log(
                        f"[INIT] Target: SSH "
                        f"({st.session_state.get('target_ssh_user', 'root')}@"
                        f"{st.session_state.get('target_ssh_host', '?')})"
                    )
                else:
                    on_log("[INIT] Target: Local")

                # Make the dispatch mode explicit rather than relying on the
                # evaluator to sniff the environment type.
                config["execution_mode"] = "pct" if is_pct else ("caf_ssh" if is_ssh else "local")
                telemetry = run_evaluation(env, config, on_log)
            except Exception as exc:
                on_log(f"[ERROR] Evaluation failed: {exc}")
                from core.evaluator import _init_telemetry
                telemetry = _init_telemetry(config)
                telemetry["run_aborted"] = True
                telemetry["error"] = str(exc)
            finally:
                if env and hasattr(env, "close"):
                    env.close()

        if telemetry is None:
            from core.evaluator import _init_telemetry
            telemetry = _init_telemetry(config)
            telemetry["run_aborted"] = True
            telemetry["error"] = "Evaluation ended without telemetry"

        st.session_state["telemetry"]        = telemetry
        st.session_state["run_completed"]    = True
        st.session_state["_run_in_progress"] = False
        st.session_state["cancel_requested"] = False

        # Persist session artefacts.
        session_log.save_telemetry(telemetry)
        session_log.save_config(config)
        session_log.close()

        # Append to per-project run history
        from config.defaults import MAX_RUN_HISTORY
        pid = st.session_state.get("active_project_id", "default")
        history_key = f"run_history_{pid}"
        history: list = st.session_state.get(history_key, [])
        history.append(telemetry)
        st.session_state[history_key] = history[-MAX_RUN_HISTORY:]

        if telemetry.get("run_aborted"):
            st.warning("⚠️ Run was cancelled.")
        elif telemetry.get("validation_passed"):
            st.success(
                "✓ Evaluation complete — validation passed. "
                "Open **📊 Analytical Dashboard** to view metrics."
            )
        else:
            st.info(
                "📊 Evaluation complete — open **Analytical Dashboard** to review results."
            )
        if not telemetry.get("run_aborted"):
            st.info(f"Session log saved to: `{session_log.session_dir}`")
