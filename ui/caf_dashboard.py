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


def _tdi_band(tdi: float) -> tuple[str, str]:
    if tdi > 0.6:
        return _TDI_BAND["high"]
    if tdi < 0.3:
        return _TDI_BAND["low"]
    return _TDI_BAND["moderate"]


def render_attack_tree(telemetry: dict) -> None:
    """Renders the step-by-step attack tree from caf_trajectory."""
    trajectory: list = telemetry.get("caf_trajectory", [])
    if not trajectory:
        st.info("No CAF trajectory — run a CAF scenario to populate the attack tree.")
        return

    st.markdown("### 🌲 Evidence-Guided Attack Tree & Trajectory Audit")
    st.caption(
        "TDI > 0.6 → breadth-first exploration mode  |  "
        "TDI < 0.3 → depth-first exploitation mode  |  "
        "Context > 40 % of window → reasoning degradation risk."
    )

    total   = len(trajectory)
    avg_tdi = sum(s.get("calculated_tdi", 0) for s in trajectory) / total
    failed  = sum(1 for s in trajectory if s.get("exit_code", 0) != 0)
    peak_ctx = max((s.get("context_tokens_used", 0) for s in trajectory), default=0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Steps",     total)
    c2.metric("Avg TDI",         f"{avg_tdi:.2f}", help="Lower is healthier")
    c3.metric("Failed Steps",    failed)
    c4.metric("Peak Ctx Tokens", f"{peak_ctx:,}")
    st.write("")

    for step in trajectory:
        tdi       = step.get("calculated_tdi", 0.0)
        colour, _ = _tdi_band(tdi)
        tool      = step.get("tool_called", "?")
        exit_code = step.get("exit_code", 0)
        ok_icon   = "✓" if exit_code == 0 else "✗"

        label = f"Step {step.get('step_number', '?')}  |  `{tool}`  {ok_icon}  |  TDI {tdi:.2f}"

        with st.expander(label):
            col1, col2, col3 = st.columns(3)
            ctx = step.get("context_tokens_used", 0)
            col1.metric("Context Tokens",  f"{ctx:,}")
            col2.metric("Execution Time",  f"{step.get('execution_time_ms', 0):.1f} ms")
            col3.metric("Exit Code",       exit_code)

            if ctx > 51200:
                st.caption("⚠️ Context exceeds 40 % of 128 k window — elevated reasoning degradation risk.")

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

    llm_ok  = bool(metrics_summary.get("caf_tempo_adherence") and
                   metrics_summary.get("caf_diagnostic_adherence"))
    tool_ok = bool(metrics_summary.get("caf_tool_param_accuracy"))
    mem_ok  = metrics_summary.get("caf_memory_recall")
    env_ok  = bool(metrics_summary.get("caf_scope_guardrails"))

    tdi_flag = "⚠️ elevated" if avg_tdi > 0.5 else "✓ healthy"

    st.markdown(f"""
**Trajectory Summary**
- Steps executed: **{total}**  |  Failed: **{failed}** ({round(failed / max(total, 1) * 100)} %)
- Average TDI: **{avg_tdi:.2f}** {tdi_flag}
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
