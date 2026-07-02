"""
Dedicated CyberAgentFlow (CAF) evaluation tab.

Provides:
  - Multi-prompt list with scenario preset loading
  - CAF scope / urgency 4-Pillar controls
  - SSH execution target (pre-filled from config_tab settings)
  - Real-time streaming terminal output (background thread + queue)
  - Interactive input panel for responding to CAF decision prompts
    ([approval] dangerous commands, [timeout] actions, [decision] retries)
"""
from __future__ import annotations

import queue
import threading
import time
import streamlit as st

from core.logsetup import logged_on_log
from core.session_log import SessionLog
from ui.components import status_pill
from ui.terminal import render_terminal


_LOG_TAG_MAP = {
    "[INIT]":         "init",
    "[CAF]":          "init",
    "[CAF OUTPUT]":   "llm",
    "[CAF STDERR]":   "warn",
    "[VALIDATE]":     "val",
    "[COMPLETE]":     "done",
    "[WARN]":         "warn",
    "[ERROR]":        "warn",
    "[CANCEL]":       "cancel",
    "[ABORTED]":      "warn",
}

# CAF real-time stream tags (detected inside [STREAM] lines)
_STREAM_INNER_TAGS = {
    "[tool]":      "tool",
    "[result]":    "result",
    "[decision]":  "decision",
    "[approval]":  "decision",
    "[timeout]":   "decision",
}

# Scenario concept removed - CAF no longer uses scenario presets


def _tag(line: str) -> str:
    if line.startswith("[STREAM]"):
        inner = line[len("[STREAM]"):].lstrip()
        for marker, tag in _STREAM_INNER_TAGS.items():
            if inner.startswith(marker):
                return tag
        return "stream"
    # For legacy [CAF OUTPUT] blobs — detect tool/result inside
    if line.startswith("[CAF OUTPUT]"):
        inner = line[len("[CAF OUTPUT]"):].lstrip()
        if inner.startswith("[tool]"):
            return "tool"
        if inner.startswith("[result]"):
            return "result"
    for prefix, tag in _LOG_TAG_MAP.items():
        if line.startswith(prefix):
            return tag
    return ""


def _render_terminal(placeholder, logs: list[dict]) -> None:
    render_terminal(placeholder, logs, _tag, empty_msg="Awaiting CAF run…")


# ── Prompt list helpers ────────────────────────────────────────────────────────

def _init_caf_prompts() -> None:
    st.session_state.setdefault("caf_prompts", [
        "Perform a reconnaissance scan of the target network and report open ports and services."
    ])


def _prompt_list_editor() -> None:
    """Render the dynamic multi-prompt list editor."""
    prompts: list[str] = st.session_state.get("caf_prompts", [])

    st.caption(
        "Each prompt becomes a separate CAF run executed in sequence. "
        "Drag to reorder — or add / remove entries below."
    )

    # Scenario concept removed - no more CAF scenario presets
    # CAF prompts are now managed directly by the user

    to_remove: int | None = None
    for i, p in enumerate(prompts):
        col_txt, col_del = st.columns([9, 1])
        with col_txt:
            new_val = st.text_area(
                f"Prompt {i + 1}",
                value=p,
                height=80,
                key=f"caf_prompt_{i}",
                label_visibility="collapsed",
            )
            prompts[i] = new_val
        with col_del:
            st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if st.button("✕", key=f"del_caf_p_{i}", help="Remove this prompt"):
                to_remove = i

    if to_remove is not None:
        del prompts[to_remove]
        st.session_state["caf_prompts"] = prompts
        st.rerun()
    else:
        st.session_state["caf_prompts"] = prompts

    col_inp, col_add = st.columns([8, 1])
    with col_inp:
        new_p = st.text_input(
            "New prompt",
            placeholder="Describe the next CAF objective…",
            label_visibility="collapsed",
            key="_caf_new_prompt",
        )
    with col_add:
        if st.button("＋ Add", use_container_width=True, key="btn_add_caf_prompt"):
            p = new_p.strip()
            if p:
                st.session_state["caf_prompts"] = prompts + [p]
                st.rerun()


# ── SSH status display (read-only) ─────────────────────────────────────────────

def _ssh_status_display() -> None:
    """Read-only status showing the SSH target configured in the Target tab."""
    with st.expander("SSH Target (Kali machine)", expanded=True):
        host    = (st.session_state.get("target_ssh_host") or "").strip()
        user    = st.session_state.get("target_ssh_user", "root")
        caf_dir = st.session_state.get("target_ssh_caf_dir") or "~/cyber-agent-flow"
        if host:
            st.success(f"SSH Target: `{user}@{host}` | CAF dir: `{caf_dir}`")
            st.caption("Change SSH settings in the **Target** tab.")
        else:
            st.warning("SSH target not configured — set it in the **Target** tab.")
        st.info(
            "**CAF uses its own MCP server** on the remote Kali machine. "
            "The ModelScope MCP status (shown in other tabs) is for local LLM evaluations and "
            "does not affect CAF. CAF's tools (nmap, tshark, netdiscover, etc.) are managed by CAF itself."
        )


# ── CAF 4-Pillar controls ──────────────────────────────────────────────────────

def _pillar_controls() -> None:
    with st.expander("CAF 4-Pillar Configuration", expanded=True):
        st.caption(
            "Scope and Urgency guide the TDI (Tactical Decision Index) engine inside CAF."
        )
        col_sc, col_ur = st.columns(2)
        with col_sc:
            st.selectbox(
                "Scope",
                options=["Narrow", "Broad"],
                key="caf_scope",
                help="Scope determines the reconnaissance breadth: Narrow = focused exploitation; Broad = comprehensive network discovery",
            )
        with col_ur:
            st.selectbox(
                "Urgency",
                options=["Speed", "Stealth", "Balanced"],
                key="caf_urgency",
                help="Urgency guides TDI: Speed = breadth-first exploration; Stealth = depth-first exploitation; Balanced = adaptive",
            )

        def _sync_val_cmd():
            st.session_state["validation_command"] = st.session_state.get(
                "caf_tab_validation_command", ""
            )

        st.text_input(
            "Validation Command (optional)",
            value=st.session_state.get("validation_command", ""),
            key="caf_tab_validation_command",
            placeholder="e.g. nmap 10.0.0.1 -p 22",
            help="Shell command run on the remote after each CAF run to verify success.",
            on_change=_sync_val_cmd,
        )


# ── Status bar ─────────────────────────────────────────────────────────────────

def _status_bar() -> None:
    model_sel      = st.session_state.get("selected_model")
    ssh_host       = (st.session_state.get("target_ssh_host") or "").strip()
    prompts        = st.session_state.get("caf_prompts", [])
    caf_tool_count = st.session_state.get("_caf_last_tool_count")

    model_state  = "up"   if model_sel      else "wait"
    ssh_state    = "up"   if ssh_host       else "wait"
    prompt_state = "up"   if prompts        else "down"
    tools_state  = "up"   if caf_tool_count else "wait"
    tools_label  = (
        f"CAF tools: {caf_tool_count}" if caf_tool_count else "CAF tools: unknown"
    )

    pills = (
        status_pill(f"Model: {model_sel or 'not chosen'}", model_state)
        + status_pill(f"SSH: {ssh_host or 'not set'}", ssh_state)
        + status_pill(f"Prompts: {len(prompts)}", prompt_state)
        + status_pill(tools_label, tools_state)
    )
    st.markdown(pills, unsafe_allow_html=True)

    if not model_sel:
        st.warning("No model selected — choose one in **Configuration -> Model Setup**.")
    if not ssh_host:
        st.warning("SSH host not set — configure it in the **Target** tab.")
    if not prompts:
        st.error("Prompt list is empty — add at least one prompt.")


# ── Interactive input panel ────────────────────────────────────────────────────

def _render_input_panel() -> None:
    """Show stdin-injection UI while a CAF run is in progress.

    CAF pauses at [approval] (dangerous commands), [decision] (retry/cancel),
    and [timeout] (wait/background/kill) prompts. The quick buttons cover all
    valid responses; the free-text field handles numeric wait-seconds.
    """
    with st.container(border=True):
        st.markdown(
            "**Respond to CAF agent** — watch for "
            "`[approval]`, `[decision]`, or `[timeout]` lines in the terminal."
        )
        _q = st.session_state.get("_caf_input_queue")

        # Quick-action buttons covering every valid CAF decision response
        btn_cols = st.columns(6)
        _actions = ["approve", "cancel", "retry", "wait", "background", "kill"]
        for col, action in zip(btn_cols, _actions):
            with col:
                if st.button(
                    action,
                    key=f"btn_caf_qa_{action}",
                    use_container_width=True,
                ):
                    if _q is not None:
                        _q.put(action)

        # Free-text input (e.g. for wait-seconds)
        _key_idx = st.session_state.get("_caf_stdin_counter", 0)
        col_txt, col_send = st.columns([5, 1])
        with col_txt:
            user_text = st.text_input(
                "Custom response",
                placeholder="Type response (e.g. a number for wait-seconds) and press Send…",
                label_visibility="collapsed",
                key=f"_caf_stdin_text_{_key_idx}",
            )
        with col_send:
            if st.button("Send ↵", key="btn_caf_stdin_send", use_container_width=True):
                if _q is not None and user_text.strip():
                    _q.put(user_text.strip())
                # Bump counter to clear the text input on next rerun
                st.session_state["_caf_stdin_counter"] = _key_idx + 1
                st.rerun()


# ── Main render ────────────────────────────────────────────────────────────────

def render() -> None:
    _init_caf_prompts()

    st.header("CyberAgentFlow Evaluation")
    st.caption(
        "Run CyberAgentFlow (CAF) benchmarks on a remote Kali Linux machine over SSH. "
        "Each prompt drives a separate autonomous pentest run."
    )

    # ── Show completion result from previous run ───────────────────────────────
    run_result = st.session_state.pop("_caf_run_result", None)
    if run_result:
        completed = sum(1 for _, _, t in run_result["all_results"] if not t.get("run_aborted"))
        total     = run_result["total"]
        if run_result.get("cancelled"):
            st.warning(f"⚠️ CAF run cancelled ({completed}/{total} completed).")
        elif completed == total:
            st.success(
                f"✓ {total} CAF run(s) complete. "
                "Open **📊 Analytical Dashboard** to review results."
            )
        else:
            st.info(f"CAF evaluation finished ({completed}/{total} succeeded).")
        session_dir = st.session_state.get("_caf_session_dir")
        if session_dir:
            st.info(f"Session log saved to: `{session_dir}`")

    _status_bar()

    # Warn if a non-CAF scenario is active
    _active_sc = st.session_state.get("active_scenario", "")
    if _active_sc and not _active_sc.startswith("CAF"):
        st.warning(
            f"⚠️ Active scenario **{_active_sc}** is not a CAF scenario. "
            f"It will be automatically switched to **{_CAF_DEFAULT_SCENARIO}** "
            "when you run — or go to **⚙ Configuration → Projects** and select "
            "**CyberAgentFlow**."
        )

    st.divider()

    # ── Left / right column layout ─────────────────────────────────────────────
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("Objective Prompts")
        _prompt_list_editor()

    with col_right:
        _ssh_status_display()
        st.write("")
        _pillar_controls()

    st.divider()

    # ── Run controls ───────────────────────────────────────────────────────────
    run_in_progress = st.session_state.get("_caf_run_in_progress", False)
    prompts         = st.session_state.get("caf_prompts", [])
    model_sel       = st.session_state.get("selected_model")
    ssh_host        = (st.session_state.get("target_ssh_host") or "").strip()
    can_run         = bool(model_sel and ssh_host and prompts and not run_in_progress)

    col_run, col_cancel, col_clear = st.columns([3, 1, 1])
    with col_run:
        run_btn = st.button(
            "▶  Run CAF Evaluation",
            key="btn_caf_run",
            type="primary",
            use_container_width=True,
            disabled=not can_run,
        )
    with col_cancel:
        if st.button(
            "⏹  Cancel",
            key="btn_caf_cancel",
            use_container_width=True,
            disabled=not run_in_progress,
        ):
            st.session_state["_caf_cancel_requested"] = True
            cancel_ref = st.session_state.get("_caf_cancel_ref")
            if cancel_ref is not None:
                cancel_ref[0] = True
    with col_clear:
        if st.button("Clear Log", key="btn_caf_clear_log", use_container_width=True):
            st.session_state["caf_run_logs"]          = []
            st.session_state["_caf_run_in_progress"]  = False
            st.session_state["_caf_cancel_requested"] = False
            st.rerun()

    # ── Interactive input panel (visible only while a run is active) ───────────
    if run_in_progress:
        _render_input_panel()

    log_placeholder = st.empty()
    _render_terminal(log_placeholder, st.session_state.get("caf_run_logs", []))

    if run_btn and can_run:
        _start_caf_runs()
        st.rerun()  # enter the polling loop immediately on the same click

    # ── Polling loop — drain queue and trigger rerun while run is active ───────
    if run_in_progress:
        _drain_output_queue()
        time.sleep(0.3)
        st.rerun()


# ── Execution logic ────────────────────────────────────────────────────────────

_CAF_DEFAULT_SCENARIO = "CAF – Reconnaissance"


def _ensure_caf_scenario() -> str:
    """Auto-sync to the default CAF scenario if the active scenario is not CAF-specific."""
    from core.state import sync_scenario

    active = st.session_state.get("active_scenario", "")
    if not active.startswith("CAF"):
        sync_scenario(_CAF_DEFAULT_SCENARIO)
        st.session_state["active_scenario"] = _CAF_DEFAULT_SCENARIO
        return _CAF_DEFAULT_SCENARIO
    return active


def _caf_worker(
    host: str,
    port: int,
    username: str,
    password,
    key_path,
    caf_dir: str,
    prompts: list,
    base_config: dict,
    output_q: queue.Queue,
    input_q: queue.Queue,
    cancel_ref: list,
    session_log: SessionLog,
) -> None:
    """Background thread: execute CAF prompts sequentially, stream output to output_q."""
    from core.environment import create_environment
    from core.caf_runner import run_caf_ssh_evaluation

    def on_log(msg: str) -> None:
        output_q.put(("log", msg))
        session_log.log(msg)

    on_log_wrapped = logged_on_log(inner=on_log)

    all_results: list = []
    env = None

    try:
        env = create_environment(
            ssh=True, host=host, port=port, username=username,
            password=password, key_path=key_path, remote_cwd=caf_dir,
        )
        on_log_wrapped(f"[INIT] SSH target: {username}@{host}:{port}  caf_dir={caf_dir}")

        for idx, prompt in enumerate(prompts):
            if cancel_ref[0]:
                on_log_wrapped("[CANCEL] Evaluation cancelled by user.")
                break

            on_log_wrapped(f"\n[INIT] ── Prompt {idx + 1}/{len(prompts)} ──")
            config = {**base_config, "user_prompt": prompt}

            try:
                telemetry = run_caf_ssh_evaluation(
                    env, config, on_log_wrapped,
                    input_queue=input_q,
                )
            except Exception as exc:
                on_log_wrapped(f"[ERROR] Prompt {idx + 1} failed: {exc}")
                telemetry = {"run_aborted": True, "error": str(exc)}

            tool_count = len(telemetry.get("tool_calls", []))
            if tool_count:
                output_q.put(("tool_count", tool_count))

            all_results.append((idx, prompt, telemetry))
            session_log.save_telemetry(telemetry, index=idx)

    except Exception as exc:
        on_log_wrapped(f"[ERROR] SSH connection failed: {exc}")

    finally:
        if env and hasattr(env, "close"):
            env.close()
        session_log.save_config(base_config)
        session_log.close()
        output_q.put(("done", all_results))


def _start_caf_runs() -> None:
    """Snapshot UI state, initialise queues, and launch the CAF worker thread."""
    _ensure_caf_scenario()

    prompts  = list(st.session_state.get("caf_prompts", []))
    host     = st.session_state.get("target_ssh_host", "").strip()
    port     = int(st.session_state.get("target_ssh_port") or 22)
    username = st.session_state.get("target_ssh_user", "root")
    password = st.session_state.get("target_ssh_password") or None
    key_path = st.session_state.get("target_ssh_key_path") or None
    caf_dir  = st.session_state.get("target_ssh_caf_dir") or "~/cyber-agent-flow"
    model    = st.session_state.get("selected_model", "")

    output_q:  queue.Queue = queue.Queue()
    input_q:   queue.Queue = queue.Queue()
    cancel_ref: list[bool] = [False]
    session_log = SessionLog()

    base_config = {
        "backend_type":           st.session_state.get("backend_type", "llama.cpp"),
        "llm_url":                st.session_state.get("llm_url", ""),
        "selected_model":         model,
        "context_size":           st.session_state.get("context_size", 4096),
        "mcp_url":                st.session_state.get("mcp_url", ""),
        "mcp_server_url":         st.session_state.get("mcp_server_url", ""),
        "mcp_tools":              st.session_state.get("mcp_tools", {}),
        "mcp_running":            st.session_state.get("mcp_running", False),
        "validation_command":     st.session_state.get("validation_command", ""),
        "fail_patterns":          st.session_state.get("fail_patterns", []),
        "active_scenario":        st.session_state.get("active_scenario", ""),
        "tool_focus":             st.session_state.get("tool_focus", ""),
        "metrics_matrix":         st.session_state.get("metrics_matrix", []),
        "expected_stdout":        "",
        "pre_run_cleanup":        [],
        "cancel_requested_ref":   cancel_ref,
        "caf_scope":              st.session_state.get("caf_scope", "Narrow"),
        "caf_urgency":            st.session_state.get("caf_urgency", "Speed"),
        "caf_allowed_subnets":    st.session_state.get("caf_allowed_subnets", []),
        "caf_target_credentials": st.session_state.get("caf_target_credentials", []),
        "execution_mode":         "caf_ssh",
        "sys_prompt":             st.session_state.get("sys_prompt", ""),
    }

    # Store worker state so poll loop and cancel button can access them
    st.session_state["caf_run_logs"]           = []
    st.session_state["_caf_run_in_progress"]   = True
    st.session_state["_caf_cancel_requested"]  = False
    st.session_state["_caf_output_queue"]      = output_q
    st.session_state["_caf_input_queue"]       = input_q
    st.session_state["_caf_cancel_ref"]        = cancel_ref
    st.session_state["_caf_run_total"]         = len(prompts)
    st.session_state["_caf_session_dir"]       = str(session_log.session_dir)
    st.session_state["_caf_stdin_counter"]     = 0

    threading.Thread(
        target=_caf_worker,
        args=(
            host, port, username, password, key_path, caf_dir,
            prompts, base_config, output_q, input_q, cancel_ref, session_log,
        ),
        daemon=True,
    ).start()


def _drain_output_queue() -> None:
    """Drain the worker output queue into session_state (Streamlit main thread only)."""
    from config.defaults import MAX_RUN_HISTORY

    q: queue.Queue | None = st.session_state.get("_caf_output_queue")
    if q is None:
        return

    logs = list(st.session_state.get("caf_run_logs", []))

    for _ in range(200):  # cap items consumed per rerun to prevent blocking
        try:
            item_type, data = q.get_nowait()
        except Exception:
            break

        if item_type == "log":
            logs.append({"text": data, "tag": _tag(data)})

        elif item_type == "tool_count":
            st.session_state["_caf_last_tool_count"] = data

        elif item_type == "done":
            all_results = data  # list of (idx, prompt, telemetry)
            history = list(st.session_state.get("run_history", []))
            for idx, prompt, telemetry in all_results:
                telemetry["caf_prompt_index"] = idx
                telemetry["caf_prompt"]       = prompt
                history.append(telemetry)
            st.session_state["run_history"]          = history[-MAX_RUN_HISTORY:]
            st.session_state["_caf_run_in_progress"] = False
            # Store result for display on next rerun
            st.session_state["_caf_run_result"] = {
                "all_results": all_results,
                "total":       st.session_state.get("_caf_run_total", 1),
                "cancelled":   st.session_state.get("_caf_cancel_requested", False),
            }
            break

    st.session_state["caf_run_logs"] = logs
