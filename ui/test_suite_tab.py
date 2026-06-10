"""
Test Suite Visualization tab for ModelScope's Configuration section.

Design philosophy (inspired by Cypress Cloud, mabl, FitNesse, GitHub CI):
  - Cypress Cloud:   rich pass/fail grid, grouped by spec file, progress bar
  - mabl:            step-level detail, test journeys, coverage overview
  - FitNesse:        tabular acceptance-test results, behavior mapping
  - GitHub CI:       hierarchical tree, category drill-down, raw-log access
  - StackBlitz:      live/preview-style execution with real-time feedback
"""
from __future__ import annotations

import html as _html
import streamlit as st
from core.test_runner import (
    TestItem, TestRunResult,
    run_tests,
    CATEGORY_ORDER, CATEGORY_LABELS, CATEGORY_COLOURS,
)

# ── Behavior / Coverage Map ───────────────────────────────────────────────────
# Maps human-readable app features → substrings that identify covering tests.
# Used to render the "Coverage Map" section.

_COVERAGE_MAP: list[dict] = [
    {
        "area":  "Config",
        "label": "URL scheme normalization",
        "matchers": ["ensure_scheme", "bare_hostname"],
        "description": (
            "Guards that bare hostnames (no http://) are silently fixed "
            "before any network call — prevents MissingSchema errors."
        ),
    },
    {
        "area":  "Config",
        "label": "GGUF model scanning",
        "matchers": ["scan_gguf", "ScanGgufModels", "is_inference_model"],
        "description": (
            "Verifies the directory scanner skips vocab-only files, handles "
            "nested paths, and gracefully returns [] for bad inputs."
        ),
    },
    {
        "area":  "Config",
        "label": "Ollama model fetching",
        "matchers": ["fetch_ollama", "FetchOllamaModels"],
        "description": (
            "Covers connection errors, timeouts, HTTP errors, and empty URLs — "
            "always returns a (list, str) tuple, never crashes."
        ),
    },
    {
        "area":  "Config",
        "label": "Metrics evaluation engine",
        "matchers": [
            "TestTaskCompletion", "TestToolCalled", "TestToolNotCalled",
            "TestToolSequence", "TestToolCallCount", "TestToolSuccessRate",
            "TestNoRepeatedCalls", "TestToolOutputContains",
            "TestContentContains", "TestContentNotContains",
            "TestContentRegex", "TestLatency", "TestTokenLimit",
            "TestMaxIterations", "TestTokensPerSecond", "TestPathEfficiency",
            "TestGoalAchievement", "TestToolUsageEfficiency", "TestNoErrorOutput",
            "TestMakeMetric", "test_all_metric_types", "format_criterion",
        ],
        "description": (
            "Full coverage of all 19 metric types — every branch, edge case "
            "(empty params → None), and boundary value."
        ),
    },
    {
        "area":  "Config",
        "label": "Metric accuracy & differentiation",
        "matchers": [
            "TestGoalAchievement", "TestToolCallCountVsToolUsageEfficiency",
            "TestNoRepeatedCalls", "TestNoErrorOutput", "TestPathEfficiency",
            "TestContentContains", "TestContentRegex", "TestToolCalledChecks",
            "TestTaskCompletion", "TestMaxIterations",
            "test_metric_accuracy",
        ],
        "description": (
            "Per-metric accuracy tests: goal_achievement composite conditions, "
            "tool_call_count vs tool_usage_efficiency divergence on redundancy, "
            "no_repeated_calls edge cases, no_error_output hidden-failure detection, "
            "path_efficiency backtracking and extra-step budgets."
        ),
    },
    {
        "area":  "Config",
        "label": "Scenario config structure",
        "matchers": ["test_scenarios", "test_scenario_metrics"],
        "description": "Verifies all scenarios have required keys and well-formed default metrics.",
    },
    {
        "area":  "Config",
        "label": "MCP script path resolution",
        "matchers": ["mcp_script_path", "mcp-server"],
        "description": (
            "Regression guard: path must use 'mcp-server/', never the old "
            "'mcp-nmap-server/' which never existed on disk."
        ),
    },
    {
        "area":  "Config",
        "label": "Pre-flight layer checks",
        "matchers": [
            "TestStateCompleteness", "TestConfigCompleteness",
            "TestConfigNoMutation", "TestBackendConnectivity",
            "TestFilesystemAccess", "TestMcpScriptPath",
            "TestRunPlatformLayer", "TestMetricsConfiguration",
            "TestKnownGoodTelemetry", "TestKnownBadTelemetry",
            "TestValidationLogicAlignment", "TestLlmSmoke",
            "TestRunEvaluationLayer",
        ],
        "description": (
            "Two-layer pre-flight suite: platform (state, config, backend, "
            "filesystem) and evaluation (metric validity, known telemetry)."
        ),
    },
    {
        "area":  "Execute",
        "label": "Evaluation loop control",
        "matchers": [
            "test_max_8_rounds", "test_early_termination",
            "test_cancel_before", "test_cancel_after",
            "test_token_accumulation", "test_tokens_per_second",
            "test_abort_no_activity", "test_abort_with_activity",
            "test_ollama_backend", "test_connection_error",
            "test_repeated_tool_calls",
        ],
        "description": (
            "Loop hard-limit (8 rounds), early exit on text answer, "
            "pre/mid-loop cancel, token accumulation across rounds, "
            "abort/validation interaction."
        ),
    },
    {
        "area":  "Execute",
        "label": "Validation command logic",
        "matchers": ["TestRunValidation"],
        "description": (
            "Covers empty command → skip, nonzero exit → fail, fail-pattern "
            "matching (case-insensitive), expected_stdout comparison."
        ),
    },
    {
        "area":  "Execute",
        "label": "Inefficiency detection",
        "matchers": ["TestCheckInefficiencies", "inefficien"],
        "description": "Detects duplicate tool+args pairs; ignores same-tool with different args.",
    },
    {
        "area":  "Execute",
        "label": "Tool execution dispatch",
        "matchers": [
            "TestFileCreator", "TestRunNmapScan", "TestUnknownTool",
        ],
        "description": (
            "file_creator and run_nmap_scan dispatching — including "
            "injection-char blocking for nmap arguments."
        ),
    },
    {
        "area":  "Execute",
        "label": "Tool call type field guard",
        "matchers": ["ToolCallTypeField", "type.*function", "tool_call.*type"],
        "description": (
            "Every assembled tool_call must carry type='function' or "
            "llama.cpp returns HTTP 500 on the re-sent assistant message."
        ),
    },
    {
        "area":  "Server",
        "label": "llama-server lifecycle",
        "matchers": [
            "test_start_fresh", "test_start_binary_not_found",
            "test_start_already_running", "test_start_restarts",
            "test_stop_terminates", "test_stop_no_process",
        ],
        "description": "Start, stop, restart; binary-not-found; already-running reuse; ctx-too-small restart.",
    },
    {
        "area":  "Server",
        "label": "Crash & state detection",
        "matchers": [
            "test_poll_ready_detects_crash", "test_poll_ready_sets_running",
            "test_poll_ready_not_running", "test_poll_ready_no_process",
            "poll_ready_crash_detection", "stop_clears_running_state",
        ],
        "description": (
            "poll_ready() sets llama_server_crashed=True when the subprocess "
            "exits — stops the UI from showing 'Loading…' forever."
        ),
    },
    {
        "area":  "Server",
        "label": "MCP server management",
        "matchers": [
            "test_start_mcp", "test_stop_mcp",
            "test_load_tools", "test_fetch_tools",
            "test_discover_tools", "test_call_mcp_tool",
        ],
        "description": (
            "MCP start/stop, tool discovery (live server → JSON fallback), "
            "tool call success/error/network-failure paths."
        ),
    },
]

# ── Regression annotations ────────────────────────────────────────────────────
# Each entry describes one known bug and the test(s) that guard against it.

_REGRESSIONS = [
    {
        "num": 1,
        "title": "URL missing http:// raises MissingSchema",
        "fix": "Added _ensure_scheme() normalization in core/models.py",
        "tests": ["test_ensure_scheme_prepends_http",
                  "test_fetch_ollama_bare_hostname_no_exception"],
    },
    {
        "num": 2,
        "title": "fetch_ollama_models returned None on error",
        "fix": "Always returns (list, str) tuple — callers can safely unpack",
        "tests": ["test_fetch_ollama_always_returns_tuple_on_error"],
    },
    {
        "num": 3,
        "title": "llama-server stuck in 'Starting…' when process crashes",
        "fix": "poll_ready() checks proc.poll() and sets crashed flag",
        "tests": ["test_poll_ready_crash_detection"],
    },
    {
        "num": 4,
        "title": "Stop button showed 'Running & ready' + 'Server stopped' simultaneously",
        "fix": "stop() sets llama_server_running=False before returning",
        "tests": ["test_stop_clears_running_state"],
    },
    {
        "num": 5,
        "title": "MCP default path pointed to non-existent directory",
        "fix": "defaults.py now uses mcp-server/index.js",
        "tests": ["test_mcp_script_path_correct_directory"],
    },
    {
        "num": 6,
        "title": "Tool call missing 'type: function' caused HTTP 500",
        "fix": "_stream_llama_cpp now sets type='function' on every tool call",
        "tests": [
            "test_stream_llama_cpp_tool_call_has_type_field",
            "TestToolCallTypeField",
        ],
    },
    {
        "num": 7,
        "title": "file_deleter removed — tool no longer in MCP server or evaluator",
        "fix": "Removed file_deleter from tools.json, tools.js, and _execute_tool_in_env",
        "tests": ["TestUnknownTool"],
    },
    {
        "num": 8,
        "title": "SmolLM2 exit_code=1 — inline <tool_call> tags not recognized",
        "fix": "_parse_inline_tool_calls() fallback parser added to run_evaluation()",
        "tests": [],
    },
]


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _pill(text: str, css_class: str) -> str:
    return f'<span class="ts-count-pill {css_class}">{text}</span>'


def _status_dot(item: TestItem) -> tuple[str, str]:
    """Return (dot_char, css_class) for a test item."""
    if item.status == "PASSED":
        return "✓", "ts-dot-pass"
    if item.status in ("FAILED", "ERROR"):
        return "✗", "ts-dot-fail"
    return "○", "ts-dot-skip"


def _regression_icon(item: TestItem) -> str:
    """Inline-styled pass/fail icon for the regression annotation table."""
    clr = "var(--success)" if item.status == "PASSED" else "var(--error)"
    sym = "✓" if item.status == "PASSED" else "✗"
    return f"<span style='color:{clr};margin-right:2px'>{sym}</span>"


def _row_html(item: TestItem) -> str:
    dot, dot_cls = _status_dot(item)
    row_cls = (
        "ts-test-row-pass"  if item.status == "PASSED"            else
        "ts-test-row-fail"  if item.status in ("FAILED", "ERROR") else
        "ts-test-row-skip"
    )
    name = _html.escape(item.short_name)
    dur  = f"<span class='ts-test-dur'>{item.duration_ms:.0f}ms</span>" \
           if item.duration_ms else ""
    err  = (
        f"<div class='ts-test-err'>↳ {_html.escape(item.error_summary[:120])}</div>"
        if item.error_summary else ""
    )
    return (
        f"<div class='ts-test-row {row_cls}'>"
        f"<span class='ts-dot {dot_cls}'>{dot}</span>"
        f"<div style='flex:1;min-width:0'>"
        f"<span class='ts-test-name'>{name}</span>{err}</div>"
        f"{dur}"
        f"</div>"
    )


def _module_block(module: str, items: list[TestItem]) -> str:
    passed  = sum(1 for i in items if i.status == "PASSED")
    failed  = sum(1 for i in items if i.status in ("FAILED", "ERROR"))
    skipped = sum(1 for i in items if i.status in ("SKIPPED", "XFAILED", "XPASSED"))

    header_colour = "#ef4444" if failed else "#22c55e"
    short_path    = _html.escape(module.replace("tests/", ""))
    count_text    = f"{passed}P"
    if failed:
        count_text += f"  {failed}F"
    if skipped:
        count_text += f"  {skipped}S"

    rows_html = "".join(_row_html(i) for i in items)

    return (
        f"<div style='margin-bottom:1rem'>"
        f"<div class='ts-file-header'>"
        f"<span style='color:{header_colour}'>▶</span>&nbsp;"
        f"<span style='opacity:0.7'>tests/</span>{short_path}"
        f"<span style='float:right;color:var(--muted);font-size:0.69rem'>"
        f"{count_text}"
        f"</span>"
        f"</div>"
        f"{rows_html}"
        f"</div>"
    )


def _progress_bar_html(pct: float, passed: int, failed: int,
                       skipped: int, total: int, dur: float) -> str:
    bar_colour  = "var(--success)" if failed == 0 else "#f59e0b" if failed <= 2 else "var(--error)"
    pct_display = f"{pct:.1f}%"

    return f"""
<div style="margin:8px 0 16px">
  <div style="display:flex;gap:12px;align-items:center;margin-bottom:8px;flex-wrap:wrap">
    <span class="ts-count-pill ts-count-pass">✓ {passed} passed</span>
    <span class="ts-count-pill ts-count-fail">✗ {failed} failed</span>
    <span class="ts-count-pill ts-count-skip">○ {skipped} skipped</span>
    <span style="color:var(--muted);font-family:'JetBrains Mono',monospace;
                 font-size:0.73rem;margin-left:auto">{total} total&nbsp;&nbsp;⏱ {dur:.2f}s</span>
  </div>
  <div class="ts-progress-wrap">
    <div class="ts-progress-bar" style="width:{min(pct,100):.1f}%;background:{bar_colour}"></div>
  </div>
  <div style="text-align:right;font-family:'JetBrains Mono',monospace;
              font-size:0.71rem;color:var(--muted);margin-top:2px">{pct_display} passing</div>
</div>
"""


# ── Coverage map renderer ─────────────────────────────────────────────────────

def _count_coverage(entry: dict, items: list[TestItem]) -> int:
    """Count how many test items match any of this entry's matchers."""
    count = 0
    for item in items:
        haystack = item.node_id + " " + item.name + " " + item.class_
        if any(m.lower() in haystack.lower() for m in entry["matchers"]):
            count += 1
    return count


def _render_coverage_map(result: TestRunResult) -> None:
    st.markdown(
        "<p style='color:var(--muted);font-size:0.8rem;margin:0 0 12px'>"
        "Each row maps an app feature to the tests that guard it. "
        "Green bars indicate how many tests cover that behavior."
        "</p>",
        unsafe_allow_html=True,
    )

    prev_area = None
    rows_html = ""
    for entry in _COVERAGE_MAP:
        if entry["area"] != prev_area:
            prev_area = entry["area"]
            rows_html += (
                f"<div style='font-family:\"JetBrains Mono\",monospace;"
                f"font-size:0.68rem;font-weight:700;letter-spacing:1px;"
                f"text-transform:uppercase;color:var(--accent);"
                f"padding:10px 0 4px;border-bottom:1px solid var(--border);"
                f"margin-bottom:6px'>{entry['area']}</div>"
            )

        count = _count_coverage(entry, result.items)
        bar_w = min(100, count * 8)   # 8px per test, max 100%

        desc    = entry["description"]
        success = "success" if count > 0 else "error"
        check   = "✓" if count > 0 else "!"
        plural  = "s" if count != 1 else ""
        rows_html += (
            f"<div class='ts-cov-row' style='margin-bottom:8px'>"
            f"<div style='flex:1;min-width:0'>"
            f"<div class='ts-cov-feature'>{entry['label']}</div>"
            f"<div style='background:rgba(34,197,94,0.25);width:{bar_w}%;height:3px;"
            f"border-radius:1px;margin:3px 0 2px'></div>"
            f"<div class='ts-cov-count' title='{desc}'>"
            f"{count} test{plural} covering this feature"
            f"</div>"
            f"</div>"
            f"<div style='font-family:\"JetBrains Mono\",monospace;font-size:0.73rem;"
            f"color:var(--{success});flex-shrink:0;"
            f"padding-left:8px;font-weight:700'>"
            f"{check}"
            f"</div>"
            f"</div>"
        )

    st.markdown(
        f"<div style='background:var(--surface);border:1px solid var(--border);"
        f"padding:16px;border-radius:2px'>{rows_html}</div>",
        unsafe_allow_html=True,
    )


# ── Regression panel ──────────────────────────────────────────────────────────

def _render_regression_panel(result: TestRunResult) -> None:
    """Render the pinned regression-guard section."""
    regression_items = [i for i in result.items if i.category == "regression"]

    passed  = sum(1 for i in regression_items if i.status == "PASSED")
    failed  = sum(1 for i in regression_items if i.status in ("FAILED", "ERROR"))
    total   = len(regression_items)

    status_colour = "var(--success)" if failed == 0 else "var(--error)"
    status_icon   = "✓ All clear" if failed == 0 else f"✗ {failed} failing"
    border_colour = "#22c55e30" if failed == 0 else "#ef444430"
    left_border   = "var(--success)" if failed == 0 else "var(--error)"

    header_html = (
        f"<div style='display:flex;align-items:center;gap:10px;"
        f"margin-bottom:8px'>"
        f"<span style='font-family:\"JetBrains Mono\",monospace;font-size:0.7rem;"
        f"font-weight:700;letter-spacing:1px;text-transform:uppercase;"
        f"color:{status_colour}'>{status_icon}</span>"
        f"<span style='font-family:\"JetBrains Mono\",monospace;font-size:0.71rem;"
        f"color:var(--muted)'>{total} guards  ·  {passed} pass  ·  {failed} fail</span>"
        f"</div>"
    )

    rows_html = "".join(_row_html(i) for i in regression_items)

    # Annotation table — maps each regression number to its guard tests
    ann_rows = ""
    for r in _REGRESSIONS:
        matched = [
            i for i in regression_items
            if any(t.lower() in i.node_id.lower() or t.lower() in i.name.lower()
                   for t in r["tests"])
        ]
        status_icons = "".join(_regression_icon(i) for i in matched) or "—"
        ann_rows += (
            f"<tr style='border-bottom:1px solid var(--border)'>"
            f"<td style='padding:4px 8px 4px 0;color:var(--muted);"
            f"font-size:0.72rem;white-space:nowrap'>#{r['num']}</td>"
            f"<td style='padding:4px 8px;font-size:0.78rem;color:var(--text)'>"
            f"{r['title']}</td>"
            f"<td style='padding:4px 0 4px 8px;font-size:0.72rem'>{status_icons}</td>"
            f"</tr>"
        )

    ann_html = (
        f"<table style='width:100%;border-collapse:collapse;"
        f"font-family:\"JetBrains Mono\",monospace;margin-top:10px'>"
        f"<thead><tr style='border-bottom:1px solid var(--border)'>"
        f"<th style='text-align:left;padding:4px 8px 4px 0;color:var(--muted);"
        f"font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px'>#</th>"
        f"<th style='text-align:left;padding:4px 8px;color:var(--muted);"
        f"font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px'>Known Bug</th>"
        f"<th style='text-align:left;padding:4px 0 4px 8px;color:var(--muted);"
        f"font-size:0.68rem;text-transform:uppercase;letter-spacing:0.5px'>Guards</th>"
        f"</tr></thead><tbody>{ann_rows}</tbody></table>"
    )

    st.markdown(
        f"<div style='background:var(--surface);border:1px solid {border_colour};"
        f"border-left:3px solid {left_border};padding:14px 16px;border-radius:2px'>"
        f"{header_html}{rows_html}{ann_html}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Toast helper ─────────────────────────────────────────────────────────────

def _toast_result(r: TestRunResult) -> None:
    if r.error_msg:
        st.toast(f"pytest error: {r.error_msg[:80]}", icon="🚨")
    elif r.failed == 0:
        st.toast(f"{r.passed}/{r.total} passed  ·  {r.total_duration_s:.1f}s", icon="✅")
    else:
        st.toast(f"{r.failed} failed  ·  {r.passed} passed", icon="⚠️")


# ── Main render ───────────────────────────────────────────────────────────────

def render() -> None:
    """Render the Test Suite Visualization sub-tab."""

    st.markdown(
        "<p style='color:var(--muted);font-size:0.82rem;margin:0 0 6px'>"
        "Visual regression dashboard for the ModelScope test suite — "
        "inspired by Cypress Cloud, mabl, and GitHub CI. "
        "Run the full suite, drill into categories, verify regression guards, "
        "and review the behavior coverage map."
        "</p>",
        unsafe_allow_html=True,
    )

    # ── Run controls ──────────────────────────────────────────────────────────
    col_all, col_unit, col_func, col_smoke, col_reg, _ = st.columns([1, 1, 1, 1, 1, 2])

    run_all   = col_all.button("▶ Run All",        use_container_width=True,
                               key="ts_run_all",   type="primary")
    run_unit  = col_unit.button("▶ Unit",           use_container_width=True,
                                key="ts_run_unit")
    run_func  = col_func.button("▶ Functional",     use_container_width=True,
                                key="ts_run_func")
    run_smoke = col_smoke.button("▶ Smoke",          use_container_width=True,
                                 key="ts_run_smoke")
    run_reg   = col_reg.button("▶ Regression",      use_container_width=True,
                               key="ts_run_reg")

    # Run tests on button press
    if run_all:
        with st.spinner("Running full test suite…"):
            result = run_tests()
        st.session_state["_ts_result"] = result
        _toast_result(result)

    elif run_unit:
        with st.spinner("Running unit tests…"):
            result = run_tests(test_path="tests/unit")
        st.session_state["_ts_result"] = result
        _toast_result(result)

    elif run_func:
        with st.spinner("Running functional tests…"):
            result = run_tests(test_path="tests/functional")
        st.session_state["_ts_result"] = result
        _toast_result(result)

    elif run_smoke:
        with st.spinner("Running smoke tests…"):
            result = run_tests(test_path="tests/smoke")
        st.session_state["_ts_result"] = result
        _toast_result(result)

    elif run_reg:
        with st.spinner("Running regression guards…"):
            result = run_tests(test_path="tests/unit/test_regression.py")
        st.session_state["_ts_result"] = result
        _toast_result(result)

    # ── No result yet ─────────────────────────────────────────────────────────
    result: TestRunResult | None = st.session_state.get("_ts_result")
    if result is None:
        st.info(
            "Press **▶ Run All** to execute the full test suite, "
            "or choose a category above to run a subset."
        )
        return

    # ── Error running pytest ──────────────────────────────────────────────────
    if result.error_msg:
        st.error(f"pytest could not run: {result.error_msg}")
        return

    # ── Summary bar ───────────────────────────────────────────────────────────
    run_badge = (
        f"<span class='ts-run-stamp'>Last run: {result.timestamp}</span>"
        if result.timestamp else ""
    )
    st.markdown(
        f"<div style='display:flex;align-items:center;gap:6px;"
        f"margin-bottom:2px'>"
        f"<span style='font-family:\"Space Grotesk\",sans-serif;font-weight:700;"
        f"font-size:0.85rem;color:var(--text)'>Results</span>"
        f"{run_badge}"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        _progress_bar_html(
            result.pass_pct,
            result.passed, result.failed, result.skipped,
            result.total, result.total_duration_s,
        ),
        unsafe_allow_html=True,
    )

    # Clear button
    _, clear_col = st.columns([8, 1])
    with clear_col:
        if st.button("Clear", key="ts_clear", use_container_width=True):
            st.session_state.pop("_ts_result", None)
            st.rerun()

    st.divider()

    # ── Tabs: Regression | All | Unit | Functional | Smoke ────────────────────
    cat_counts  = {cat: 0 for cat in CATEGORY_ORDER}
    cat_failing = {cat: 0 for cat in CATEGORY_ORDER}
    for item in result.items:
        cat = item.category
        if cat not in cat_counts:
            cat_counts[cat]  = 0
            cat_failing[cat] = 0
        cat_counts[cat] += 1
        if item.status in ("FAILED", "ERROR"):
            cat_failing[cat] += 1

    tab_labels: list[str] = ["🔴 Regression"]
    tab_cats:   list[str] = ["regression"]

    for cat in ["smoke", "unit", "functional", "other"]:
        n = cat_counts.get(cat, 0)
        if n == 0:
            continue
        f = cat_failing.get(cat, 0)
        icon = "❌" if f else "✅"
        tab_labels.append(f"{icon} {CATEGORY_LABELS.get(cat, cat.title())}  ({n})")
        tab_cats.append(cat)

    tab_labels.append("📋 All Tests")
    tab_cats.append("__all__")
    tab_labels.append("🗺 Coverage Map")
    tab_cats.append("__coverage__")
    tab_labels.append("📄 Raw Log")
    tab_cats.append("__raw__")

    tabs = st.tabs(tab_labels)

    for tab, cat in zip(tabs, tab_cats):
        with tab:
            if cat == "regression":
                _render_regression_panel(result)

            elif cat == "__coverage__":
                _render_coverage_map(result)

            elif cat == "__raw__":
                with st.expander("pytest output", expanded=False):
                    st.code(result.raw_output, language=None)

            elif cat == "__all__":
                _render_category_results(result, None)

            else:
                _render_category_results(result, cat)


# ── Category results renderer ─────────────────────────────────────────────────

def _render_category_results(result: TestRunResult, category: str | None) -> None:
    items = result.items if category is None else [
        i for i in result.items if i.category == category
    ]

    if not items:
        st.info("No tests in this category for the last run.")
        return

    passed  = sum(1 for i in items if i.status == "PASSED")
    failed  = sum(1 for i in items if i.status in ("FAILED", "ERROR"))
    skipped = sum(1 for i in items if i.status in ("SKIPPED", "XFAILED", "XPASSED"))

    # Mini summary
    st.markdown(
        f"<div style='display:flex;gap:8px;align-items:center;margin-bottom:12px'>"
        + _pill(f"✓ {passed}", "ts-count-pass")
        + _pill(f"✗ {failed}", "ts-count-fail")
        + _pill(f"○ {skipped}", "ts-count-skip")
        + f"<span style='color:var(--muted);font-family:\"JetBrains Mono\",monospace;"
          f"font-size:0.7rem'>{len(items)} tests</span>"
          f"</div>",
        unsafe_allow_html=True,
    )

    # Failures first, then passing (Cypress Cloud convention)
    by_module: dict[str, list[TestItem]] = {}
    for item in items:
        by_module.setdefault(item.module, []).append(item)

    # Sort modules: failed first
    def _mod_key(mod: str) -> tuple:
        has_fail = any(i.status in ("FAILED", "ERROR") for i in by_module[mod])
        return (0 if has_fail else 1, mod)

    html_blocks = ""
    for mod in sorted(by_module.keys(), key=_mod_key):
        html_blocks += _module_block(mod, by_module[mod])

    st.markdown(
        f"<div style='margin-top:4px'>{html_blocks}</div>",
        unsafe_allow_html=True,
    )
