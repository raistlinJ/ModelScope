import html
import json
import streamlit as st
from config.metrics import (
    METRIC_TYPES, CATEGORIES,
    evaluate_metric, format_criterion, metric_observed_value,
)
from ui.components import badge, badge_pass, badge_fail, badge_na, type_badge, CAT_COLOUR


def _get_active_project() -> "dict | None":
    pid = st.session_state.get("active_project_id")
    for p in st.session_state.get("projects", []):
        if p["id"] == pid:
            return p
    return None


def _hydrate_project_history_if_empty(project: dict) -> None:
    """Populate ``run_history_<pid>`` from on-disk session logs if it's empty.

    Runs in this Streamlit process keep appending to the in-memory history,
    so hydration only fires when the list is empty — i.e. the user just opened
    the app or just switched to this project.  Guarded by a per-pid flag so
    even an empty on-disk store isn't re-scanned on every rerun.
    """
    from config.defaults import MAX_RUN_HISTORY
    from core.session_log import SessionRepository

    pid        = project["id"]
    history_key = f"run_history_{pid}"
    hydrated_key = f"_history_hydrated_{pid}"

    if st.session_state.get(hydrated_key):
        return
    existing = st.session_state.get(history_key, [])
    if existing:
        # User already has in-memory runs (current session); don't overwrite
        # those with disk data — they may be newer than what's persisted.
        st.session_state[hydrated_key] = True
        return

    repo = SessionRepository()
    try:
        loaded = repo.history_for_project(pid, limit=MAX_RUN_HISTORY)
    except Exception:
        # Don't let a broken sessions dir block the dashboard.
        st.session_state[hydrated_key] = True
        return

    if loaded:
        st.session_state[history_key] = loaded
    st.session_state[hydrated_key] = True


# ── Shared metrics rendering helper ───────────────────────────────────────────

def _render_metrics_evaluation(
    matrix: list,
    tel: dict,
    *,
    categories: list = None,
) -> None:
    """Render the full metrics evaluation table — badges, per-category rows,
    Observed column, and collapsible detail expanders.

    Args:
        matrix:     List of enabled metric dicts (pre-filtered to m["enabled"]).
        tel:        Telemetry dict for the run being displayed.
        categories: Category display order; defaults to all CATEGORIES.
                   Bash uses a filtered list excluding CAF-prefixed categories.
    """
    if categories is None:
        categories = CATEGORIES

    if not matrix:
        st.info("No metrics enabled — configure them in **Configuration → Metrics Setup**.")
        return

    results = [(m, evaluate_metric(m, tel)) for m in matrix]
    passed  = sum(1 for _, r in results if r is True)
    failed  = sum(1 for _, r in results if r is False)
    na      = sum(1 for _, r in results if r is None)

    s1, s2, s3, _ = st.columns([1, 1, 1, 4])
    s1.markdown(badge_pass(f"PASS {passed}"), unsafe_allow_html=True)
    s2.markdown(badge_fail(f"FAIL {failed}"), unsafe_allow_html=True)
    s3.markdown(badge_na(f"N/A  {na}"), unsafe_allow_html=True)
    st.write("")

    # Group by category
    by_cat: dict[str, list] = {c: [] for c in categories}
    for m, r in results:
        cat = METRIC_TYPES.get(m.get("type", ""), {}).get("category", "Validation")
        if cat in by_cat:
            by_cat[cat].append((m, r))

    # Determine which metric statuses are PASS/FAIL/NA so we can render each
    # section visibly (groups that are entirely one colour otherwise look
    # identical to a passing row).
    first_cat = True
    for cat in categories:
        items = by_cat.get(cat, [])
        if not items:
            continue
        if not first_cat:
            st.divider()
        first_cat = False
        colour = CAT_COLOUR.get(cat, "#64748b")
        _cat_passed = sum(1 for _, r in items if r is True)
        _cat_failed = sum(1 for _, r in items if r is False)
        _cat_total  = len(items)
        _cat_summary = (
            f"  ·  <span style='color:#3fb950'>{_cat_passed} ✓</span>"
            if _cat_passed else ""
        ) + (
            f"  ·  <span style='color:#f85149'>{_cat_failed} ✗</span>"
            if _cat_failed else ""
        )
        st.markdown(
            f'<div style="margin:12px 0 6px;font-weight:700;font-size:0.75rem;'
            f'letter-spacing:0.8px;text-transform:uppercase;color:{colour};">'
            f'{cat}  ({_cat_total}){_cat_summary}'
            f'</div>',
            unsafe_allow_html=True,
        )
        # Header row: ID, Name, Type, Criterion, Observed, Result
        hcols = st.columns([2, 3, 3, 3, 5, 2])
        for lbl, col in zip(["ID", "Name", "Type", "Criterion", "Observed", "Result"], hcols):
            col.markdown(f"*{lbl}*")

        for m, result in items:
            _obs = metric_observed_value(m, tel)
            with st.container():
                rc = st.columns([2, 3, 3, 3, 5, 2])
                rc[0].code(m["id"])
                rc[1].write(m["name"])
                rc[2].markdown(type_badge(m.get("type", "")), unsafe_allow_html=True)
                rc[3].markdown(
                    f'<span class="criterion">{html.escape(format_criterion(m))}</span>',
                    unsafe_allow_html=True,
                )
                # Observed value — coloured to match the result so it's
                # scannable at a glance even without the badge column.
                if result is True:
                    _obs_colour = "#3fb950"
                elif result is False:
                    _obs_colour = "#f85149"
                else:
                    _obs_colour = "#94a3b8"
                rc[4].markdown(
                    f'<span style="color:{_obs_colour};font-size:0.85rem;'
                    f'font-family:ui-monospace,monospace;">'
                    f'{html.escape(_obs)}</span>',
                    unsafe_allow_html=True,
                )
                if result is True:
                    rc[5].markdown(badge_pass("PASS"), unsafe_allow_html=True)
                elif result is False:
                    rc[5].markdown(badge_fail("FAIL"), unsafe_allow_html=True)
                else:
                    rc[5].markdown(badge_na(), unsafe_allow_html=True)

                # Expandable detail row so users can see the full params
                # and the criterion verbatim without it dominating the table.
                with st.expander(f"Details for {m.get('id', '?')} — {m.get('name', '?')}",
                                 expanded=False):
                    d1, d2 = st.columns(2)
                    with d1:
                        st.markdown("**Criterion**")
                        st.code(format_criterion(m) or "(no criterion)")
                    with d2:
                        st.markdown("**Observed value**")
                        st.code(_obs)
                    params = m.get("params", {}) or {}
                    if params:
                        st.markdown("**Configured parameters**")
                        st.json(params)


# ── Sessions viewer ────────────────────────────────────────────────────────────

def _summarize_loops(tool_calls: list) -> list[str]:
    """Group tool calls by (tool_name, args_json) and return human-readable
    descriptions for any combination called more than twice."""
    from collections import Counter
    counts: Counter = Counter()
    for tc in tool_calls:
        name     = tc.get("tool", "?")
        args_key = json.dumps(tc.get("args", {}), sort_keys=True, default=str)
        counts[(name, args_key)] += 1
    summaries = []
    for (name, args_key), n in counts.items():
        if n > 2:
            try:
                args_display = json.dumps(json.loads(args_key))
            except Exception:
                args_display = args_key
            # Truncate very long arg strings for readability
            if len(args_display) > 120:
                args_display = args_display[:117] + "…"
            summaries.append(
                f"`{name}` called {n}× with identical args `{args_display}`"
                " — the agent may have been stuck in a loop."
            )
    return summaries


def _render_bash_dashboard(project: dict) -> None:
    """Bash-bot specific dashboard — per-project history, no LLM metrics."""
    _hydrate_project_history_if_empty(project)
    pid = project["id"]
    history_key = f"run_history_{pid}"
    history: list = st.session_state.get(history_key, [])
    # Filter to only bash_bot runs (default for legacy entries without run_bot_type)
    history = [h for h in history if h.get("run_bot_type", "bash_bot") == "bash_bot"]

    if not history:
        st.info("No runs yet for this project — go to **Execute** and run it.")
        return

    # Run selector
    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            lbl = f"Run {len(history)-i}  —  {ts}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run", options=labels, index=0,
            help="Browse previous runs for this project",
            # Keyed by run count too, not just pid: a stable key would retain
            # the previously-selected run's label after a new run completes,
            # so the dashboard wouldn't auto-jump to the fresh result.
            key=f"bash_dash_sel_{pid}_{len(history)}",
        )
        sel_idx = labels.index(sel_label)
        tel = list(reversed(history))[sel_idx]
    else:
        tel: dict = history[-1]


    # Per-run token so text_area keys change when the selected run changes,
    # preventing Streamlit from retaining stale widget state across selections.
    _run_tok = (tel.get("run_timestamp", "") or "latest").replace(" ", "_").replace(":", "-")

    ts = tel.get("run_timestamp", "")
    if ts:
        st.caption(f"🕒 {ts}  |  💻 {project['name']} (bash_bot)")

    if tel.get("run_aborted"):
        st.warning("⚠️  This run was aborted — metrics may be incomplete.")

    # Top metrics — bash-relevant only (no tokens/LLM rounds)
    tool_calls: list = tel.get("tool_calls", [])
    val_passed = tel.get("validation_passed")
    val_label  = "PASS ✓" if val_passed is True else ("FAIL ✗" if val_passed is False else "N/A")

    c1, c2, c3 = st.columns(3)
    c1.metric("Latency (s)",          f"{tel.get('total_latency', 0):.2f}",
              help="Total wall-clock time from run start to completion")
    c2.metric("Commands Executed",    len(tool_calls),
              help="Number of bash commands that ran (startup + completion)")
    c3.metric("Validation",           val_label,
              help="Aggregate result of all validation commands")

    st.divider()

    # Export
    export_col, _ = st.columns([2, 5])
    with export_col:
        _ts_safe = tel.get("run_timestamp", "run").replace(" ", "_").replace(":", "-")
        st.download_button(
            "⬇  Export Results (JSON)",
            data=json.dumps(tel, indent=2, default=str),
            file_name=f"bash_results_{_ts_safe}.json",
            mime="application/json",
        )

    # Commands executed
    if tool_calls:
        st.subheader(f"Commands Executed  ({len(tool_calls)})")
        for i, tc in enumerate(tool_calls):
            exit_code = tc.get("exit_code", "?")
            status    = "✓" if exit_code == 0 else "✗"
            cmd_str   = tc.get("args", {}).get("command", tc.get("tool", "?"))
            label     = f"#{i+1}  {cmd_str[:60]}  {status}"
            with st.expander(label):
                result = tc.get("result", {})
                if isinstance(result, dict):
                    if result.get("stdout"):
                        st.code(result["stdout"][:1000], language="bash")
                    if result.get("stderr"):
                        st.warning(result["stderr"][:400])
                else:
                    st.code(str(result))
                st.caption(f"Exit code: {exit_code}")

    st.divider()

    # Validation commands — each is shown as an individual metric check
    st.subheader("Validation Checks")
    val_sets_results = tel.get("validation_sets_results")
    val_results: list = tel.get("validation_results", [])
    
    if val_sets_results is not None:
        if not val_sets_results:
            st.info("No validation sets configured.")
        else:
            for s_idx, vr_set in enumerate(val_sets_results):
                set_name = vr_set.get("name", "Unnamed Set")
                set_desc = vr_set.get("description", "")
                set_passed = vr_set.get("passed", False)
                
                badge = "PASS ✓" if set_passed else "FAIL ✗"
                label = f"{badge} Validation Set: {set_name} — {set_desc}"
                
                with st.expander(label, expanded=not set_passed):
                    # Show steps in the set
                    steps = vr_set.get("steps", [])
                    if not steps:
                        st.caption("No commands executed in this set.")
                    for c_idx, cmd_res in enumerate(steps):
                        cmd_text = cmd_res.get("command", "")
                        cmd_passed = cmd_res.get("passed", False)
                        exit_cd = cmd_res.get("exit_code", "?")
                        checks = cmd_res.get("checks") or [{
                            "expected_output_type": cmd_res.get("expected_output_type", "Ignore"),
                            "expected_output": cmd_res.get("expected_output", ""),
                        }]
                        reason = cmd_res.get("reason", "")
                        
                        cmd_badge = "✓" if cmd_passed else "✗"
                        st.markdown(f"**Command {c_idx + 1}:** `{cmd_text}` ({cmd_badge})")
                        
                        # Match spec details
                        check_parts = []
                        for check in checks:
                            out_type = check.get("expected_output_type", check.get("type", "Ignore"))
                            expected = check.get("expected_output", check.get("value", ""))
                            if out_type == "Ignore":
                                continue
                            if out_type == "No output":
                                check_parts.append("No output")
                            else:
                                check_parts.append(f"{out_type} ({expected!r})")
                        expected_summary = " OR ".join(check_parts) if check_parts else "Ignore"
                        st.caption(f"Expected Output: {expected_summary} | Exit code: {exit_cd}")
                        if reason:
                            st.warning(f"Failure reason: {reason}")
                            
                        # Stdout / Stderr details
                        if cmd_res.get("stdout"):
                            st.text_area("Stdout", value=cmd_res["stdout"], height=120,
                                         key=f"bash_vr_stdout_{pid}_{_run_tok}_{s_idx}_{c_idx}")
                        if cmd_res.get("stderr"):
                            st.text_area("Stderr", value=cmd_res["stderr"], height=80,
                                         key=f"bash_vr_stderr_{pid}_{_run_tok}_{s_idx}_{c_idx}")
                        
                        if c_idx < len(steps) - 1:
                            st.markdown("---")
    elif val_results:
        for i, vr in enumerate(val_results):
            passed   = vr.get("passed")
            cmd      = vr.get("cmd", "?")
            exit_cd  = vr.get("exit_code", "?")
            badge    = "PASS ✓" if passed else "FAIL ✗"
            label    = f"{badge}  `{cmd[:80]}`"
            with st.expander(label, expanded=not passed):
                if passed:
                    st.success(f"PASS ✓  exit code: {exit_cd}")
                else:
                    st.error(f"FAIL ✗  exit code: {exit_cd}")
                if vr.get("stdout"):
                    st.text_area("Stdout", value=vr["stdout"], height=120,
                                 key=f"bash_vr_stdout_{pid}_{_run_tok}_{i}")
                if vr.get("stderr"):
                    st.text_area("Stderr", value=vr["stderr"], height=80,
                                 key=f"bash_vr_stderr_{pid}_{_run_tok}_{i}")
    elif tel.get("validation_exit_code") is not None:
        # Legacy telemetry (pre-validation_results) — fall back to aggregate display
        if tel.get("validation_passed"):
            st.success(f"PASS ✓  (exit code: {tel['validation_exit_code']})")
        else:
            st.error(f"FAIL ✗  (exit code: {tel['validation_exit_code']})")
        if tel.get("validation_stdout"):
            st.text_area("Stdout", value=tel["validation_stdout"], height=160,
                         key=f"bash_val_stdout_{pid}_{_run_tok}")
        if tel.get("validation_stderr"):
            st.text_area("Stderr", value=tel["validation_stderr"], height=100,
                         key=f"bash_val_stderr_{pid}_{_run_tok}")
    elif tel.get("prompt_call_failed"):
        st.error("FAIL ✗ — an LLM Judge prompt failed or could not connect (see Tool Calls above).")
    else:
        st.info("No validation commands were run.")

    # Metrics matrix — use run-snapshot or current bash_metrics_matrix
    _run_matrix = tel.get("metrics_matrix", [])
    matrix = [m for m in (_run_matrix or st.session_state.get("bash_metrics_matrix", []))
              if m.get("enabled")]

    if matrix:
        st.divider()
        st.subheader("Metrics Evaluation")
        # Exclude CAF-specific categories for bash view
        bash_categories = [c for c in CATEGORIES if not c.startswith("CAF-")]
        _render_metrics_evaluation(matrix, tel, categories=bash_categories)


def _render_llama_cli_dashboard(
    project: dict,
    bot_type: str = "llama_cli_bot",
    metrics_key: str = "llama_cli_metrics_matrix",
) -> None:
    """Llama-backed bot dashboard — per-project history with prompt responses and validation."""
    _hydrate_project_history_if_empty(project)
    pid         = project["id"]
    history_key = f"run_history_{pid}"
    history: list = st.session_state.get(history_key, [])
    # Filter to this llama-backed bot type (excludes legacy/other bot-type entries)
    history = [h for h in history if h.get("run_bot_type") == bot_type]

    if not history:
        st.info("No runs yet for this project — go to **Execute** and run it.")
        return

    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            lbl = f"Run {len(history) - i}  —  {ts}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run", options=labels, index=0,
            # Keyed by run count too, not just pid: a stable key would retain
            # the previously-selected run's label after a new run completes,
            # so the dashboard wouldn't auto-jump to the fresh result.
            key=f"{bot_type}_dash_sel_{pid}_{len(history)}",
        )
        tel = list(reversed(history))[labels.index(sel_label)]
    else:
        tel: dict = history[-1]

    # Per-run token so text_area keys change when the selected run changes,
    # preventing Streamlit from retaining stale widget state across selections.
    _run_tok = (tel.get("run_timestamp", "") or "latest").replace(" ", "_").replace(":", "-")

    ts = tel.get("run_timestamp", "")
    if ts:
        model   = tel.get("run_model", "")
        backend = tel.get("run_backend", "")
        st.caption(f"🕒 {ts}  |  🦙 {project['name']}  |  {backend}  |  {model}")

    if tel.get("run_aborted"):
        if tel.get("interrupted_by_user"):
            st.warning("🛑 **Run cancelled by user** — metrics below reflect partial execution.")
        else:
            st.warning("⚠️ **This run was interrupted** — the agent did not complete all turns.")

    # Top metrics
    prompt_responses: list = tel.get("prompt_responses", [])
    tool_calls: list       = tel.get("tool_calls", [])
    val_passed = tel.get("validation_passed")
    val_label  = "PASS ✓" if val_passed is True else ("FAIL ✗" if val_passed is False else "N/A")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Latency (s)",      f"{tel.get('total_latency', 0):.2f}")
    c2.metric("Prompts Run",      len(prompt_responses))
    c3.metric("Commands Run",     len([tc for tc in tool_calls if tc.get("tool") == "bash"]))
    c4.metric("Validation",       val_label)

    st.divider()

    # Export
    export_col, _ = st.columns([2, 5])
    with export_col:
        _ts_safe = tel.get("run_timestamp", "run").replace(" ", "_").replace(":", "-")
        st.download_button(
            "⬇  Export Results (JSON)",
            data=json.dumps(tel, indent=2, default=str),
            file_name=f"llama_results_{_ts_safe}.json",
            mime="application/json",
        )

    # Prompt responses
    if prompt_responses:
        st.subheader(f"Prompt Responses  ({len(prompt_responses)})")
        for i, pr in enumerate(prompt_responses):
            with st.expander(f"Prompt {i + 1}: {pr.get('prompt', '')[:60]}…"):
                st.markdown("**Prompt**")
                st.code(pr.get("prompt", ""), language="text")
                st.markdown("**Response**")
                st.code(pr.get("response", ""), language="text")

    # Shell commands
    shell_calls = [tc for tc in tool_calls if tc.get("tool") == "bash"]
    if shell_calls:
        st.subheader(f"Commands Executed  ({len(shell_calls)})")
        for i, tc in enumerate(shell_calls):
            exit_code = tc.get("exit_code", "?")
            status    = "✓" if exit_code == 0 else "✗"
            cmd_str   = tc.get("args", {}).get("command", "?")
            with st.expander(f"#{i + 1}  {cmd_str[:60]}  {status}"):
                result = tc.get("result", {})
                if isinstance(result, dict):
                    if result.get("stdout"):
                        st.code(result["stdout"][:1000], language="bash")
                    if result.get("stderr"):
                        st.warning(result["stderr"][:400])
                st.caption(f"Exit code: {exit_code}")

    st.divider()

    # Validation output
    st.subheader("Validation Output")
    val_exit = tel.get("validation_exit_code")
    if val_exit is None:
        if tel.get("prompt_call_failed"):
            st.error("FAIL ✗ — an LLM Judge prompt failed or could not connect (see Tool Calls above).")
        else:
            st.info("No validation command was run.")
    else:
        if tel.get("validation_passed"):
            st.success(f"PASS ✓  (exit code: {val_exit})")
        else:
            st.error(f"FAIL ✗  (exit code: {val_exit})")
        if tel.get("validation_stdout"):
            st.text_area("Stdout", value=tel["validation_stdout"], height=160,
                         key=f"llama_val_stdout_{pid}_{_run_tok}")
        if tel.get("validation_stderr"):
            st.text_area("Stderr", value=tel["validation_stderr"], height=100,
                         key=f"llama_val_stderr_{pid}_{_run_tok}")

    # Loop / inefficiency detection
    _inefficiencies = tel.get("inefficiencies", [])
    _loop_summaries = _summarize_loops(tel.get("tool_calls", []))
    _all_issues = _inefficiencies + [s for s in _loop_summaries if s not in _inefficiencies]
    if _all_issues:
        with st.expander(
            f"Loop / inefficiency detected ({len(_all_issues)} issue(s))",
            expanded=True,
        ):
            for issue in _all_issues:
                st.markdown(f"- {issue}")

    # Metrics matrix
    _run_matrix = tel.get("metrics_matrix", [])
    matrix = [m for m in (_run_matrix or st.session_state.get(metrics_key, []))
              if m.get("enabled")]
    if matrix:
        st.divider()
        st.subheader("Metrics Evaluation")
        _render_metrics_evaluation(matrix, tel)


def render() -> None:
    st.header("Analytical Dashboard")

    # Dispatch to bot-type-specific view
    _proj = _get_active_project()
    if _proj and _proj.get("type") == "bash_bot":
        _render_bash_dashboard(_proj)
        return
    if _proj and _proj.get("type") == "llama_cli_bot":
        _render_llama_cli_dashboard(_proj)
        return
    if _proj and _proj.get("type") == "llama_server_bot":
        _render_llama_cli_dashboard(
            _proj,
            bot_type="llama_server_bot",
            metrics_key="llama_server_metrics_matrix",
        )
        return

    if _proj is None:
        st.info("No project selected. Use the sidebar to add or select a project.")
        return

    st.info(
        f"**{_proj['name']}** ({_proj.get('type', '?')}) — dashboard coming soon."
    )
