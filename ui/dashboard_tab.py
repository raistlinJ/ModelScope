import json
import streamlit as st
from config.metrics import METRIC_TYPES, CATEGORIES, evaluate_metric, format_criterion
from ui.caf_dashboard import render_attack_tree, render_judge_panel
from ui.components import badge, type_badge, CAT_COLOUR


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
                    colour = "#16a34a" if found else "#dc2626"
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


def render() -> None:
    st.header("Analytical Dashboard")

    if not st.session_state.get("run_completed"):
        st.info("No evaluation run yet — go to **Execute Evaluation** and run a scenario.")
        return

    # ── Run history selector (fix #26) ────────────────────────────────────────
    history: list = st.session_state.get("run_history", [])
    tel: dict     = st.session_state.get("telemetry", {})

    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            sc  = h.get("run_scenario", "")[:30]
            lbl = f"Run {len(history)-i}  —  {ts}  |  {sc}"
            labels.append(lbl)
        sel_label = st.selectbox("Select run", options=labels, index=0,
                                 help="Compare previous evaluation runs")
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
    c1.metric("Latency",      f"{tel.get('total_latency', 0):.2f} s",
              help="Total wall-clock time from run start to completion")
    c2.metric("Total Tokens", tel.get("total_tokens", 0),
              help="Prompt tokens + completion tokens")
    c3.metric("Prompt",       tel.get("prompt_tokens", 0),
              help="Tokens in all messages sent to the model")
    c4.metric("Completion",   tel.get("completion_tokens", 0),
              help="Tokens generated by the model")
    c5.metric("Tok / sec",    f"{tel.get('tokens_per_second', 0.0):.1f}",
              help="Generation throughput (completion tokens / latency)")
    c6.metric("LLM Rounds",   tel.get("llm_rounds", 0),
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

    if not matrix:
        st.info("No metrics enabled — configure them in **Configuration → Metrics Setup**.")
    else:
        results = [(m, evaluate_metric(m, tel)) for m in matrix]
        passed  = sum(1 for _, r in results if r is True)
        failed  = sum(1 for _, r in results if r is False)
        na      = sum(1 for _, r in results if r is None)

        s1, s2, s3, _ = st.columns([1, 1, 1, 4])
        s1.markdown(badge(f"PASS {passed}", "#16a34a"), unsafe_allow_html=True)
        s2.markdown(badge(f"FAIL {failed}", "#dc2626"), unsafe_allow_html=True)
        s3.markdown(badge(f"N/A  {na}",     "#475569"), unsafe_allow_html=True)
        st.write("")

        # Group by category
        by_cat: dict[str, list] = {c: [] for c in CATEGORIES}
        for m, r in results:
            cat = METRIC_TYPES.get(m.get("type", ""), {}).get("category", "Validation")
            by_cat.setdefault(cat, []).append((m, r))

        for cat in CATEGORIES:
            items = by_cat.get(cat, [])
            if not items:
                continue
            colour = CAT_COLOUR.get(cat, "#64748b")
            st.markdown(
                f'<div style="margin:12px 0 4px;font-weight:700;color:{colour}">'
                f'{cat.upper()}</div>',
                unsafe_allow_html=True,
            )
            hcols = st.columns([2, 3, 3, 4, 2])
            for lbl, col in zip(["ID", "Name", "Type", "Criterion", "Result"], hcols):
                col.markdown(f"*{lbl}*")

            for m, result in items:
                rc = st.columns([2, 3, 3, 4, 2])
                rc[0].code(m["id"])
                rc[1].write(m["name"])
                rc[2].markdown(type_badge(m.get("type", "")), unsafe_allow_html=True)
                rc[3].markdown(
                    f'<span class="criterion">{format_criterion(m)}</span>',
                    unsafe_allow_html=True,
                )
                if result is True:
                    rc[4].markdown(badge("PASS ✓", "#16a34a"), unsafe_allow_html=True)
                elif result is False:
                    rc[4].markdown(badge("FAIL ✗", "#dc2626"), unsafe_allow_html=True)
                else:
                    rc[4].markdown(badge("N/A",   "#475569"), unsafe_allow_html=True)

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
