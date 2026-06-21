"""
CAF Attack Tree viewer and Dual-Layer Judge panel.

Renders only when caf_trajectory data is present in the run telemetry.
"""
from __future__ import annotations

import streamlit as st
from ui.components import badge


_TDI_BAND = {
    "high":     ("#ef4444", "High TDI — BFS Exploration Mode"),
    "moderate": ("#f59e0b", "Moderate TDI — LLM Adaptive Mode"),
    "low":      ("#22c55e", "Low TDI — DFS Exploitation Mode"),
}

_PHASE_COLOURS = {
    "recon":        ("#3b82f6", "Reconnaissance"),
    "exploit":      ("#ef4444", "Exploitation"),
    "post_exploit": ("#8b5cf6", "Post-Exploitation"),
    "execution":    ("#f59e0b", "Execution"),
    "utility":      ("#6b7280", "Utility"),
    "unknown":      ("#374151", "Unknown"),
}

_EVIDENCE_LABELS = {
    1.0: ("Shell/Auth Access", "#22c55e"),
    0.8: ("Exploit Confirmed", "#84cc16"),
    0.5: ("Service Identified", "#f59e0b"),
    0.3: ("Generic Output", "#94a3b8"),
    0.1: ("Error / No Signal", "#ef4444"),
}


def _tdi_band(tdi: float) -> tuple[str, str]:
    if tdi > 0.6:
        return _TDI_BAND["high"]
    if tdi < 0.3:
        return _TDI_BAND["low"]
    return _TDI_BAND["moderate"]


def _evidence_label(conf: float) -> tuple[str, str]:
    for threshold, (label, colour) in sorted(_EVIDENCE_LABELS.items(), reverse=True):
        if conf >= threshold - 0.05:
            return label, colour
    return "Error / No Signal", "#ef4444"


def render_attack_tree(telemetry: dict) -> None:
    """Renders the step-by-step attack tree from caf_trajectory."""
    trajectory: list = telemetry.get("caf_trajectory", [])
    if not trajectory:
        st.info("No CAF trajectory — run a CAF scenario to populate the attack tree.")
        return

    st.markdown("### Evidence-Guided Attack Tree & Trajectory Audit")
    st.caption(
        "TDI > 0.6 → breadth-first exploration mode  |  "
        "TDI < 0.3 → depth-first exploitation mode  |  "
        "Context > 40 % of window → reasoning degradation risk."
    )

    total    = len(trajectory)
    avg_tdi  = sum(s.get("calculated_tdi", 0) for s in trajectory) / total
    failed   = sum(1 for s in trajectory if s.get("exit_code", 0) != 0)
    peak_ctx = max((s.get("context_tokens_used", 0) for s in trajectory), default=0)
    avg_ev   = sum(s.get("evidence_confidence", 0.0) for s in trajectory) / total
    phases   = sorted({s.get("phase", "unknown") for s in trajectory} - {"", "unknown"})

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total Steps (count)",       total)
    c2.metric("Avg TDI (0–1)",           f"{avg_tdi:.3f}", help="Lower is healthier; TDI decreases as evidence increases")
    c3.metric("Failed Steps (count)",      failed)
    c4.metric("Avg Evidence (0–1)",      f"{avg_ev:.2f}", help="Average evidence confidence across all steps")
    c5.metric("Peak Ctx Tokens (count)",   f"{peak_ctx:,}")

    if phases:
        phase_html = " ".join(
            f'<span style="background:{_PHASE_COLOURS.get(p, ("#374151",""))[0]};'
            f'color:#fff;padding:2px 8px;border-radius:4px;font-size:0.78rem;margin:2px">'
            f'{_PHASE_COLOURS.get(p, ("","? "))[1]}</span>'
            for p in phases
        )
        st.markdown(f"**Phases observed:** {phase_html}", unsafe_allow_html=True)

    st.write("")

    for step in trajectory:
        tdi       = step.get("calculated_tdi", 0.0)
        colour, _ = _tdi_band(tdi)
        tool      = step.get("tool_called", "?")
        exit_code = step.get("exit_code", 0)
        ok_icon   = "✓" if exit_code == 0 else "✗"
        phase     = step.get("phase", "")
        ev_conf   = step.get("evidence_confidence", 0.0)

        phase_tag = ""
        if phase and phase != "unknown":
            ph_colour = _PHASE_COLOURS.get(phase, ("#374151", "Unknown"))[0]
            phase_tag = (
                f' <span style="background:{ph_colour};color:#fff;'
                f'padding:1px 6px;border-radius:3px;font-size:0.73rem">{phase}</span>'
            )

        label = f"Step {step.get('step_number', '?')}  |  `{tool}`  {ok_icon}  |  TDI {tdi:.3f}  |  E={ev_conf:.2f}"

        with st.expander(label):
            # Phase badge
            if phase_tag:
                st.markdown(f"Phase:{phase_tag}", unsafe_allow_html=True)

            col1, col2, col3, col4 = st.columns(4)
            ctx = step.get("context_tokens_used", 0)
            col1.metric("Context Tokens (count)",    f"{ctx:,}")
            col2.metric("Execution Time (ms)",    f"{step.get('execution_time_ms', 0):.1f}")
            col3.metric("Exit Code (0=success)",         exit_code)
            col4.metric("Evidence Confidence (0–1)",    f"{ev_conf:.2f}")

            # TDI dimension breakdown
            tdi_e = step.get("tdi_e", ev_conf)
            tdi_c = step.get("tdi_c", 0.0)
            tdi_s = step.get("tdi_s", 1.0)
            st.markdown(
                f'<div style="background:#1e293b;border-radius:6px;padding:8px 12px;'
                f'margin:4px 0;font-size:0.82rem;font-family:monospace">'
                f'<b>TDI Dimensions</b>&nbsp; '
                f'E(evidence)={tdi_e:.2f} &nbsp;|&nbsp; '
                f'C(context)={tdi_c:.2f} &nbsp;|&nbsp; '
                f'S(success)={tdi_s:.2f} &nbsp;|&nbsp; '
                f'<b>TDI={tdi:.3f}</b>'
                f'</div>',
                unsafe_allow_html=True,
            )

            _ctx_limit = st.session_state.get("context_size", 131072)
            if ctx > int(_ctx_limit * 0.40):
                st.caption(
                    f"Context exceeds 40 % of {_ctx_limit:,} token window — "
                    "elevated reasoning degradation risk."
                )

            # Evidence label
            ev_label, ev_colour = _evidence_label(ev_conf)
            st.markdown(
                badge(f"Evidence: {ev_label}", ev_colour),
                unsafe_allow_html=True,
            )

            _, band_label = _tdi_band(tdi)
            st.markdown(badge(band_label, colour), unsafe_allow_html=True)
            st.write("")
            st.markdown("**Arguments passed to MCP engine**")
            st.json(step.get("arguments", {}))
            st.markdown("**Observed output (truncated)**")
            st.code(step.get("output_preview", ""), language=None)


def render_judge_panel(telemetry: dict, metrics_summary: dict) -> None:
    """Renders the deterministic metrics summary + qualitative audit trigger."""
    st.markdown("---")
    st.markdown("### 🧑‍⚖️ Dual-Layer Judge Analysis")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Deterministic Metrics")
        _caf_label_keys = [
            ("Tempo Adherence",      "caf_tempo_adherence"),
            ("Diagnostic Adherence", "caf_diagnostic_adherence"),
            ("Scope Guardrails",     "caf_scope_guardrails"),
            ("Tool Param Accuracy",  "caf_tool_param_accuracy"),
            ("Session Efficiency",   "caf_interactive_session_efficiency"),
            ("TDI Health",           "caf_tdi_health"),
            ("Memory Recall",        "caf_memory_recall"),
            ("Evidence Confidence",  "caf_evidence_confidence"),
            ("Phase Completion",     "caf_phase_completion_ratio"),
            ("Policy Adherence",     "caf_policy_adherence"),
        ]
        any_shown = False
        for label, key in _caf_label_keys:
            val = metrics_summary.get(key)
            if val is None:
                continue
            any_shown = True
            colour = "#16a34a" if val else "#dc2626"
            icon   = "✓" if val else "✗"
            st.markdown(
                f'<span style="color:{colour};font-weight:700">{icon}</span> {label}',
                unsafe_allow_html=True,
            )
        if not any_shown:
            st.info("No CAF metrics evaluated — add them in Configuration → Metrics Setup.")

    with col2:
        st.subheader("Qualitative Auditor")
        trajectory = telemetry.get("caf_trajectory", [])
        if not trajectory:
            st.info("No trajectory data for qualitative audit.")
        elif st.button("Run Automated Audit Pass", key="btn_caf_audit"):
            _render_audit_report(telemetry, metrics_summary)


def _render_audit_report(telemetry: dict, metrics_summary: dict) -> None:
    trajectory = telemetry.get("caf_trajectory", [])
    total      = len(trajectory)
    failed     = sum(1 for s in trajectory if s.get("exit_code", 0) != 0)
    avg_tdi    = sum(s.get("calculated_tdi", 0) for s in trajectory) / max(total, 1)
    tools_used = sorted({s.get("tool_called", "") for s in trajectory} - {""})
    avg_ev     = sum(s.get("evidence_confidence", 0.0) for s in trajectory) / max(total, 1)
    phases_obs = sorted({s.get("phase", "unknown") for s in trajectory} - {"", "unknown"})

    type_a = failed
    type_b = len([i for i in telemetry.get("inefficiencies", []) if "repeated" in i.lower()])

    st.markdown("**Automated Audit Report**")
    ca, cb = st.columns(2)
    for col, label, count in [(ca, "Type A — Param/Syntax Failures", type_a),
                               (cb, "Type B — Retry Loop Failures",   type_b)]:
        colour = "#dc2626" if count > 0 else "#16a34a"
        col.markdown(
            f'<div style="border-left:3px solid {colour};padding:8px 12px;margin:4px 0">'
            f'<b>{label}</b><br>'
            f'<span style="font-size:1.4rem;font-weight:700;color:{colour}">{count}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

    llm_ok  = bool(
        metrics_summary.get("caf_tempo_adherence") and
        metrics_summary.get("caf_diagnostic_adherence") and
        metrics_summary.get("caf_phase_completion_ratio") is not False
    )
    tool_ok = bool(
        metrics_summary.get("caf_tool_param_accuracy") and
        metrics_summary.get("caf_evidence_confidence") is not False
    )
    mem_ok  = metrics_summary.get("caf_memory_recall")
    env_ok  = bool(
        metrics_summary.get("caf_scope_guardrails") and
        metrics_summary.get("caf_policy_adherence") is not False
    )

    tdi_flag = "⚠️ elevated" if avg_tdi > 0.5 else "✓ healthy"

    st.markdown(f"""
**Trajectory Summary**
- Steps executed: **{total}**  |  Failed: **{failed}** ({round(failed / max(total, 1) * 100)} %)
- Average TDI: **{avg_tdi:.3f}** {tdi_flag}
- Avg Evidence Confidence: **{avg_ev:.2f}** (0=no signal, 1=shell access)
- Attack phases observed: `{', '.join(phases_obs) or 'none'}`
- Tools used: `{', '.join(tools_used) or 'none'}`

**Four-Pillar Assessment**

| Pillar | Score |
|--------|-------|
| LLM — Instructional Alignment | {'✓ Pass' if llm_ok else '✗ Review'} |
| Tools — Capability Gap Audit  | {'✓ Pass' if tool_ok else '✗ Review'} |
| Memory — Context Retrieval    | {'✓ Pass' if mem_ok else ('⚠ N/A' if mem_ok is None else '✗ Review')} |
| Environment — Scope Integrity | {'✓ Pass' if env_ok else '✗ Violation'} |
""")

    if avg_tdi > 0.6:
        st.warning(
            "High average TDI: context saturation or persistent tool failures detected. "
            "Consider reducing prompt length or adding backtracking checkpoints."
        )
    elif type_b > 0:
        st.warning(
            "Type B loops detected: agent repeated identical tool calls. "
            "Add loop-detection instructions to the system prompt."
        )
    else:
        st.success("No critical failure patterns detected — agent maintained tactical discipline.")
