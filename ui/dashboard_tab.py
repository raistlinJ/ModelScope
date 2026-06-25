import html
import json
import streamlit as st
from config.metrics import (
    METRIC_TYPES, CATEGORIES,
    evaluate_metric, format_criterion, metric_observed_value,
)
from ui.caf_dashboard import render_attack_tree, render_judge_panel
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


def _render_session_summary(t_path, t_idx: int = 0, total: int = 1) -> None:
    """Render the top metrics row for a single telemetry JSON file."""
    try:
        tel_data = json.loads(t_path.read_text(encoding="utf-8"))
    except Exception:
        st.warning(f"Could not parse {t_path.name}.")
        return
    label = "**Telemetry summary**" if total == 1 else f"**Prompt {t_idx + 1}**"
    st.markdown(label)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Model", tel_data.get("run_model") or tel_data.get("selected_model") or "?")
    c2.metric("Latency", f"{tel_data.get('total_latency', 0):.2f} s")
    c3.metric("Total Tokens", tel_data.get("total_tokens", "?"))
    _val = tel_data.get("validation_passed")
    _val_label = "PASS" if _val is True else ("FAIL" if _val is False else "N/A")
    c4.metric("Validation", _val_label)
    if total > 1 and t_idx < total - 1:
        st.divider()


def _render_sessions_viewer(sessions: list | None = None, title: str = "Recent Sessions") -> None:
    """List recent session directories and let users inspect them.

    Args:
        sessions: Optional pre-filtered list of session dirs.  When None, all
                  sessions from the repo's base dir are shown.
        title:    The expander title — caller can rebrand for context.
    """
    from core.session_log import SessionRepository

    repo = SessionRepository()
    sessions_base = repo.base_dir
    if sessions is None:
        session_dirs = repo.list_sessions(limit=20)
    else:
        session_dirs = sessions

    if not session_dirs:
        return

    with st.expander(title, expanded=False):
        st.caption(
            f"Showing the last {len(session_dirs)} session(s) from "
            f"`{sessions_base}`.  Each directory contains `run.log`, "
            "`telemetry.json`, and `config.json`."
        )

        selected = st.selectbox(
            "Session",
            options=[d.name for d in session_dirs],
            key=f"_sessions_viewer_sel_{title}",
            label_visibility="collapsed",
        )
        if not selected:
            return

        sel_dir = sessions_base / selected
        st.caption(f"Path: `{sel_dir}`")

        # ── Telemetry summary ──────────────────────────────────────────────
        # Prefer telemetry.json; fall back to indexed CAF files (telemetry_0.json, etc.)
        tel_files = repo.telemetry_files(sel_dir)
        for t_idx, t_path in enumerate(tel_files):
            _render_session_summary(t_path, t_idx, len(tel_files))

        # ── run.log viewer ────────────────────────────────────────────────
        log_path = sel_dir / "run.log"
        if log_path.exists():
            st.markdown("**run.log**")
            try:
                log_text = log_path.read_text(encoding="utf-8")
                st.code(log_text, language=None)
            except Exception:
                st.warning("Could not read run.log.")


def _render_sessions_for_project() -> None:
    """Show on-disk sessions that belong to the active project.

    A session is associated with a project when its ``config.json`` contains a
    matching ``active_project_id``.  Sessions without a config (or with no
    active_project_id) are surfaced under a separate 'Unscoped' header so the
    user can still browse them.  Sessions for other projects are hidden — the
    viewer is project-scoped.
    """
    import json as _json
    from core.session_log import SessionRepository

    _proj = _get_active_project()
    if _proj is None:
        return
    pid = _proj["id"]

    repo = SessionRepository()
    all_sessions = repo.list_sessions(limit=None)
    if not all_sessions:
        return

    mine: list = []
    other_unscoped: list = []
    for d in all_sessions:
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            other_unscoped.append(d)
            continue
        try:
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            other_unscoped.append(d)
            continue
        if cfg.get("active_project_id") == pid:
            mine.append(d)
        elif "active_project_id" not in cfg:
            other_unscoped.append(d)
        # else: belongs to a different project — skip

    # Most recent first
    mine            = sorted(mine,            key=lambda p: p.name, reverse=True)[:20]
    other_unscoped  = sorted(other_unscoped,  key=lambda p: p.name, reverse=True)[:20]

    if mine:
        _render_sessions_viewer(
            sessions=mine,
            title=f"Saved Sessions for '{_proj['name']}' ({len(mine)})",
        )
    if other_unscoped:
        _render_sessions_viewer(
            sessions=other_unscoped,
            title=f"Unscoped Sessions (no project tag) ({len(other_unscoped)})",
        )


def _render_response_comparison(response: str, validation_out: str, tool_focus: str) -> None:
    """Compare LLM response to validation stdout using simple regex extraction."""
    import re

    col_v, col_r = st.columns(2)

    if tool_focus == "run_nmap_scan" or "nmap" in validation_out.lower():
        # Extract port specs like "8080/tcp open"
        port_pattern = r'(\d{1,5}/(?:tcp|udp))\s+(\w+)'
        val_ports  = re.findall(port_pattern, validation_out.lower())
        resp_lower = response.lower()

        rows = []
        for port_spec, state in val_ports:
            port_num = port_spec.split("/")[0]
            found = port_num in resp_lower or port_spec in resp_lower
            rows.append((port_spec, state, found))

        with col_v:
            st.markdown("**Validation found**")
            for port_spec, state, _ in rows:
                st.markdown(f"`{port_spec}` — {state}")

        with col_r:
            st.markdown("**LLM response mentioned**")
            if rows:
                all_ok = all(f for _, _, f in rows)
                for port_spec, _, found in rows:
                    icon = "✓" if found else "✗"
                    colour = "#3fb950" if found else "#f85149"
                    st.markdown(
                        f'<span style="color:{colour};font-weight:700">{icon}</span> `{port_spec}`',
                        unsafe_allow_html=True,
                    )
                if all_ok:
                    st.success("All ports accounted for in response")
                else:
                    missing = [p for p, _, f in rows if not f]
                    st.warning(f"Missing from response: {', '.join(missing)}")
            else:
                st.info("No port/state pairs found in validation output.")

    elif tool_focus == "file_creator" or "/tmp" in validation_out:
        # For file creation: compare line count and content
        val_lines  = [l for l in validation_out.strip().split('\n') if l.strip()]
        resp_nums  = set(re.findall(r'\b\d+\b', response))
        val_nums   = set(re.findall(r'\b\d+\b', validation_out))
        common     = resp_nums & val_nums

        with col_v:
            st.markdown("**Validation output**")
            st.code(validation_out.strip()[:300], language=None)

        with col_r:
            st.markdown("**Numbers in response vs file**")
            if val_nums:
                matched_pct = int(len(common) / len(val_nums) * 100)
                missing = sorted(val_nums - resp_nums, key=lambda x: int(x) if x.isdigit() else 0)
                if missing:
                    st.warning(f"{matched_pct}% matched — missing: {', '.join(missing)}")
                else:
                    st.success(f"All {len(val_nums)} expected values present in response")
            else:
                st.info("No numeric values to compare.")

    else:
        # Generic: word overlap
        val_words  = set(re.findall(r'\b[a-z]{4,}\b', validation_out.lower()))
        resp_words = set(re.findall(r'\b[a-z]{4,}\b', response.lower()))
        common     = val_words & resp_words

        with col_v:
            st.markdown("**Key terms in validation**")
            st.caption(", ".join(sorted(val_words)[:20]) or "—")

        with col_r:
            st.markdown("**Overlap with response**")
            if val_words:
                pct = int(len(common) / len(val_words) * 100)
                st.metric("Term overlap", f"{pct}%", help="Shared words ≥4 chars between validation output and LLM response")
            else:
                st.info("No key terms to compare.")


def _render_bash_dashboard(project: dict) -> None:
    """Bash-bot specific dashboard — per-project history, no LLM metrics."""
    _hydrate_project_history_if_empty(project)
    pid = project["id"]
    history_key = f"run_history_{pid}"
    history: list = st.session_state.get(history_key, [])

    if not history:
        st.info("No runs yet for this project — go to **Execute** and run it.")
        return

    # Run selector
    tel: dict = history[-1]
    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            lbl = f"Run {len(history)-i}  —  {ts}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run", options=labels, index=0,
            help="Browse previous runs for this project",
            key=f"bash_dash_sel_{pid}",
        )
        sel_idx = labels.index(sel_label)
        tel = list(reversed(history))[sel_idx]

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
    val_results: list = tel.get("validation_results", [])
    if val_results:
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
                                 key=f"bash_vr_stdout_{pid}_{i}")
                if vr.get("stderr"):
                    st.text_area("Stderr", value=vr["stderr"], height=80,
                                 key=f"bash_vr_stderr_{pid}_{i}")
    elif tel.get("validation_exit_code") is not None:
        # Legacy telemetry (pre-validation_results) — fall back to aggregate display
        if tel.get("validation_passed"):
            st.success(f"PASS ✓  (exit code: {tel['validation_exit_code']})")
        else:
            st.error(f"FAIL ✗  (exit code: {tel['validation_exit_code']})")
        if tel.get("validation_stdout"):
            st.text_area("Stdout", value=tel["validation_stdout"], height=160,
                         key=f"bash_val_stdout_{pid}")
        if tel.get("validation_stderr"):
            st.text_area("Stderr", value=tel["validation_stderr"], height=100,
                         key=f"bash_val_stderr_{pid}")
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


def _render_llama_cli_dashboard(project: dict) -> None:
    """Llama-CLI-Bot dashboard — per-project history with prompt responses and validation."""
    _hydrate_project_history_if_empty(project)
    pid         = project["id"]
    history_key = f"run_history_{pid}"
    history: list = st.session_state.get(history_key, [])

    if not history:
        st.info("No runs yet for this project — go to **Execute** and run it.")
        return

    tel: dict = history[-1]
    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            lbl = f"Run {len(history) - i}  —  {ts}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run", options=labels, index=0,
            key=f"llama_dash_sel_{pid}",
        )
        tel = list(reversed(history))[labels.index(sel_label)]

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
        st.info("No validation command was run.")
    else:
        if tel.get("validation_passed"):
            st.success(f"PASS ✓  (exit code: {val_exit})")
        else:
            st.error(f"FAIL ✗  (exit code: {val_exit})")
        if tel.get("validation_stdout"):
            st.text_area("Stdout", value=tel["validation_stdout"], height=160,
                         key=f"llama_val_stdout_{pid}")
        if tel.get("validation_stderr"):
            st.text_area("Stderr", value=tel["validation_stderr"], height=100,
                         key=f"llama_val_stderr_{pid}")

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
    matrix = [m for m in (_run_matrix or st.session_state.get("llama_cli_metrics_matrix", []))
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

    # Project context banner — makes it obvious which project's runs are shown.
    if _proj:
        bc, br = st.columns([9, 1])
        with bc:
            st.caption(
                f"📁 Showing runs for project: **{_proj['name']}** "
                f"(`{_proj.get('type', '?')}`)"
            )
        with br:
            if st.button(
                "↻", key=f"refresh_hist_{_proj['id']}",
                help="Re-read run history from on-disk session logs (ModelScope/logs/sessions/).",
            ):
                # Drop both the in-memory list and the per-pid hydration flag so
                # the next call to _hydrate_project_history_if_empty re-scans.
                st.session_state.pop(f"run_history_{_proj['id']}", None)
                st.session_state.pop(f"_history_hydrated_{_proj['id']}", None)
                st.rerun()
        # Hydrate the standard flow's run history too — same behaviour as the
        # bot-type dashboards, so a fresh app start picks up prior runs.
        _hydrate_project_history_if_empty(_proj)

    # Sessions viewer is always rendered — even before any run in the current
    # session — so past runs from previous sessions are always browsable.
    # When a project is active, only that project's sessions are shown (plus
    # any unscoped ones) — so switching projects changes what history you see.
    _render_sessions_for_project()

    if not st.session_state.get("run_completed"):
        st.info("No evaluation run yet — go to **Execute Evaluation** and run a scenario.")
        return

    # ── Run history selector (fix #26) ────────────────────────────────────────
    # Read from the per-project history key when there's an active project so
    # the dropdown only shows that project's runs (matches the bash/llama-CLI
    # dashboards above and the Execute tab's per-project persistence).
    _pid = _proj["id"] if _proj else None
    history_key = f"run_history_{_pid}" if _pid else "run_history"
    history: list = st.session_state.get(history_key, [])
    tel: dict     = st.session_state.get("telemetry", {})

    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            sc  = h.get("run_scenario", "")[:30]
            lbl = f"Run {len(history)-i}  —  {ts}  |  {sc}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run",
            options=labels, index=0,
            help=f"Browse previous evaluation runs for this project "
                 f"({len(history)} stored, most recent first)",
            key=f"std_dash_sel_{_pid or 'default'}",
        )
        sel_idx   = labels.index(sel_label)
        tel = list(reversed(history))[sel_idx]

    # ── Run metadata (fix #19) ────────────────────────────────────────────────
    ts      = tel.get("run_timestamp", "")
    scenario = tel.get("run_scenario", "")
    model    = tel.get("run_model", "")
    backend  = tel.get("run_backend", "")
    if ts:
        st.caption(
            f"🕒 {ts}  |  📋 {scenario or '?'}  |  "
            f"🤖 {model or '?'} ({backend})"
        )

    # Aborted warning
    if tel.get("run_aborted"):
        st.warning("⚠️  This run was aborted — metrics may be incomplete.")

    # ── Top summary metrics (fix #18: clearer labels + tooltips) ─────────────
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Latency (s)",      f"{tel.get('total_latency', 0):.2f}",
              help="Total wall-clock time from run start to completion")
    c2.metric("Total Tokens (count)", tel.get("total_tokens", 0),
              help="Prompt tokens + completion tokens")
    c3.metric("Prompt (tokens)",       tel.get("prompt_tokens", 0),
              help="Tokens in all messages sent to the model")
    c4.metric("Completion (tokens)",   tel.get("completion_tokens", 0),
              help="Tokens generated by the model")
    _tps_str = (
        f"{tel.get('tokens_per_second', 0.0):.1f}"
        if tel.get("total_tokens", 0) > 0
        else "N/A"
    )
    c5.metric("Tokens/sec", _tps_str,
              help="Generation throughput (completion tokens / latency)")
    c6.metric("LLM Rounds (calls)",   tel.get("llm_rounds", 0),
              help="Number of LLM API calls made in this run")

    st.divider()

    # ── Export button (fix #27) ───────────────────────────────────────────────
    export_col, _ = st.columns([2, 5])
    with export_col:
        st.download_button(
            "⬇  Export Results (JSON)",
            data=json.dumps(tel, indent=2, default=str),
            file_name=f"spark_results_{tel.get('run_timestamp','run').replace(' ','_').replace(':','-')}.json",
            mime="application/json",
        )

    # ── Metrics evaluation ─────────────────────────────────────────────────────
    st.subheader("Metrics Evaluation")

    # Use the metrics stored in the telemetry for this specific run (so
    # historical runs show the correct metrics, not the current session config).
    _run_matrix = tel.get("metrics_matrix", [])
    matrix: list = [m for m in (_run_matrix or st.session_state.get("metrics_matrix", []))
                    if m.get("enabled")]

    _tool_focus = tel.get("run_tool_focus", "")
    if _tool_focus:
        st.caption(f"Metrics configured for tool: `{_tool_focus}`")

    _render_metrics_evaluation(matrix, tel)

    st.divider()

    # ── Tool calls detail ──────────────────────────────────────────────────────
    tool_calls: list = tel.get("tool_calls", [])
    if tool_calls:
        st.subheader(f"Tool Calls  ({len(tool_calls)})")
        for i, tc in enumerate(tool_calls):
            exit_code = tc.get("exit_code", "?")
            status    = "✓" if exit_code == 0 else "✗"
            label     = f"#{i+1}  {tc.get('tool','?')}  {status}  —  {tc.get('runtime',0):.3f} s"
            with st.expander(label):
                ca, cb = st.columns(2)
                with ca:
                    st.write("**Arguments**")
                    st.json(tc.get("args", {}))
                with cb:
                    st.write("**Result**")
                    result = tc.get("result", {})
                    st.json(result) if isinstance(result, dict) else st.code(str(result))
                st.caption(f"Exit code: {exit_code}")

    # ── Inefficiencies ─────────────────────────────────────────────────────────
    issues: list = tel.get("inefficiencies", [])
    if issues:
        st.subheader("Inefficiencies Detected")
        for iss in issues:
            st.warning(iss)

    st.divider()

    # ── Validation output ──────────────────────────────────────────────────────
    st.subheader("Validation Output")
    val_exit = tel.get("validation_exit_code")
    if val_exit is None:
        st.info("No validation command was run.")
    else:
        if tel.get("validation_passed"):
            st.success(f"PASS ✓  (exit code: {val_exit})")
        else:
            st.error(f"FAIL ✗  (exit code: {val_exit})")
        if tel.get("validation_stdout"):
            st.text_area("Stdout", value=tel["validation_stdout"], height=160)
        if tel.get("validation_stderr"):
            st.text_area("Stderr", value=tel["validation_stderr"], height=100)

    # ── Response vs Validation comparison ─────────────────────────────────────
    _resp = tel.get("llm_response", "")
    _vout = tel.get("validation_stdout", "")
    if _resp and _vout:
        st.divider()
        st.subheader("Response ↔ Validation Comparison")
        st.caption(
            "Regex-based check: key values extracted from the validation output "
            "are matched against the LLM's final response."
        )
        _render_response_comparison(_resp, _vout, tel.get("run_tool_focus", ""))

    # ── Final LLM response ─────────────────────────────────────────────────────
    if tel.get("llm_response"):
        st.divider()
        st.subheader("Final LLM Response")
        st.write(tel["llm_response"])

    # ── CAF Attack Tree & Judge Panel ──────────────────────────────────────────
    if tel.get("caf_trajectory"):
        st.divider()

        # Build a quick metrics_summary dict for the judge panel
        _matrix_for_summary = tel.get("metrics_matrix", []) or st.session_state.get("metrics_matrix", [])
        _metrics_summary: dict = {}
        for _m in _matrix_for_summary:
            if _m.get("type", "").startswith("caf_"):
                _metrics_summary[_m["type"]] = evaluate_metric(_m, tel)

        render_attack_tree(tel)
        render_judge_panel(tel, _metrics_summary)

    # ── AI Judge Evaluation ────────────────────────────────────────────────────
    if tel.get("judge_scores"):
        st.divider()
        st.subheader("AI Judge Evaluation")
        from ui.judge_config import render_judge_results
        render_judge_results(tel)
