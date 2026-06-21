"""
Pre-flight UI section for ModelScope's Configuration tab.

Renders the two-layer test suite results with a terminal-style console.
All test logic lives in core/preflight.py; this module only handles display.
"""
import streamlit as st
from core.preflight import (
    TestResult,
    run_platform_layer,
    run_evaluation_layer,
    run_all,
)


# ── Console rendering ─────────────────────────────────────────────────────────

_LAYER_LABEL = {
    "platform":   "Platform Regression",
    "evaluation": "Evaluation Integrity",
}

_PASS_COLOUR   = "var(--success, #22c55e)"
_FAIL_COLOUR   = "var(--error,   #ef4444)"
_SKIP_COLOUR   = "var(--muted,   #9e8a62)"
_ACCENT_COLOUR = "var(--accent,  #d97706)"


def _result_html(r: TestResult) -> str:
    if r.passed is True:
        colour, icon = _PASS_COLOUR, "✓"
    elif r.passed is False:
        colour, icon = _FAIL_COLOUR, "✗"
    else:
        colour, icon = _SKIP_COLOUR, "○"

    dur = f'  <span style="font-size:0.72rem;color:{_SKIP_COLOUR}">({r.duration_ms:.0f}ms)</span>' \
          if r.duration_ms else ""

    return (
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:0.82rem;'
        f'line-height:1.7;padding:1px 0">'
        f'<span style="color:{colour};font-weight:700">{icon}</span> '
        f'<span style="color:var(--text,#f0e8d4);font-weight:600">{r.name}</span> '
        f'<span style="color:{_SKIP_COLOUR}">— {r.detail}</span>'
        f'{dur}</div>'
    )


def _layer_header_html(label: str) -> str:
    return (
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:0.72rem;'
        f'font-weight:700;letter-spacing:1px;text-transform:uppercase;'
        f'color:{_ACCENT_COLOUR};margin:0.8rem 0 0.3rem">'
        f'── {label} {"─" * max(0, 50 - len(label))}</div>'
    )


def _render_console(results: list[TestResult], placeholder) -> None:
    if not results:
        placeholder.markdown(
            '<div style="font-family:\'JetBrains Mono\',monospace;'
            'font-size:0.82rem;color:var(--muted,#9e8a62);padding:0.5rem 0">'
            'No results yet — run a check above.</div>',
            unsafe_allow_html=True,
        )
        return

    # Summary counts
    passed  = sum(1 for r in results if r.passed is True)
    failed  = sum(1 for r in results if r.passed is False)
    skipped = sum(1 for r in results if r.passed is None)

    if failed == 0:
        summary_colour = _PASS_COLOUR
        summary_text   = f"All {passed} check(s) passed"
    else:
        summary_colour = _FAIL_COLOUR
        summary_text   = f"{failed} failed  ·  {passed} passed  ·  {skipped} skipped"

    lines_html = [
        f'<div style="font-family:\'JetBrains Mono\',monospace;font-size:0.78rem;'
        f'font-weight:700;color:{summary_colour};margin-bottom:0.6rem">'
        f'{summary_text}</div>'
    ]

    cur_layer = None
    for r in results:
        if r.layer != cur_layer:
            cur_layer = r.layer
            lines_html.append(_layer_header_html(_LAYER_LABEL.get(cur_layer, cur_layer)))
        lines_html.append(_result_html(r))

    placeholder.markdown(
        '<div style="background:var(--surface,#13100a);border:1px solid var(--border,#2e2818);'
        'padding:1rem 1.2rem;border-radius:2px">'
        + "".join(lines_html)
        + "</div>",
        unsafe_allow_html=True,
    )


# ── Main section renderer ─────────────────────────────────────────────────────

def render() -> None:
    """Render the Pre-flight section inside the Config tab."""

    st.markdown(
        "<p style='color:var(--muted);font-size:0.82rem;margin:0 0 1rem'>"
        "Run these checks before a benchmark to confirm the platform and evaluation "
        "pipeline are correctly configured. Layer 1 verifies infrastructure; "
        "Layer 2 verifies the scoring engine's alignment with your metric setup."
        "</p>",
        unsafe_allow_html=True,
    )

    # ── Action buttons ────────────────────────────────────────────────────────
    col_p, col_e, col_all = st.columns(3)

    with col_p:
        platform_btn = st.button(
            "Layer 1 — Platform",
            use_container_width=True,
            key="btn_preflight_platform",
            help="Verify platform infrastructure: state machine, config pipeline, backend connectivity, filesystem access",
        )
    with col_e:
        eval_btn = st.button(
            "Layer 2 — Evaluation",
            use_container_width=True,
            key="btn_preflight_eval",
            help="Verify evaluation integrity: metric validity, known telemetry patterns, validation logic",
        )
    with col_all:
        all_btn = st.button(
            "Run All",
            use_container_width=True,
            key="btn_preflight_all",
            type="primary",
            help="Run both layers together",
        )

    # Optional LLM smoke test toggle (expensive — disabled by default)
    smoke_col, _ = st.columns([3, 4])
    with smoke_col:
        include_smoke = st.checkbox(
            "Include LLM smoke test",
            value=False,
            key="preflight_include_smoke",
            help=(
                "Runs a one-round minimal evaluation against the configured LLM backend. "
                "Requires a live backend. Can take up to 90 seconds."
            ),
        )

    # ── Run and store results ─────────────────────────────────────────────────
    state = dict(st.session_state)

    if platform_btn:
        with st.spinner("Running platform checks…"):
            results = run_platform_layer(state)
        st.session_state["_preflight_results"] = results

    if eval_btn:
        with st.spinner("Running evaluation checks…"):
            results = run_evaluation_layer(state, include_llm_smoke=False)
        if include_smoke:
            with st.spinner("Running LLM smoke test (up to 90s)…"):
                from core.preflight import check_llm_smoke
                results.append(check_llm_smoke(state))
        st.session_state["_preflight_results"] = results

    if all_btn:
        with st.spinner("Running all pre-flight checks…"):
            results = run_platform_layer(state) + run_evaluation_layer(state, include_llm_smoke=False)
        if include_smoke:
            with st.spinner("Running LLM smoke test (up to 90s)…"):
                from core.preflight import check_llm_smoke
                results.append(check_llm_smoke(state))
        st.session_state["_preflight_results"] = results

    # ── Results console ───────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    console = st.empty()
    _render_console(st.session_state.get("_preflight_results", []), console)

    # Clear button
    if st.session_state.get("_preflight_results"):
        _, clear_col = st.columns([6, 1])
        with clear_col:
            if st.button("Clear", key="btn_preflight_clear", use_container_width=True):
                st.session_state["_preflight_results"] = []
                st.rerun()
