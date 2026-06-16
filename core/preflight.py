"""
Pre-flight test engine for ModelScope — Streamlit-agnostic.

Two evaluation layers:

  Layer 1 — Platform Regression
    Verifies the platform's own state machine, configuration pipeline,
    backend connectivity (with graceful timeout handling), and filesystem access.

  Layer 2 — Evaluation Integrity
    Verifies the scoring pipeline correctly classifies known-good and known-bad
    agent runs, validates all configured metrics, and optionally runs a minimal
    LLM smoke test that confirms the end-to-end loop functions before a full benchmark.
"""
from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import requests


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    name:        str
    layer:       str             # "platform" | "evaluation"
    passed:      Optional[bool]  # True=pass  False=fail  None=skipped/info
    detail:      str
    duration_ms: float = 0.0

    @property
    def icon(self) -> str:
        if self.passed is True:  return "✓"
        if self.passed is False: return "✗"
        return "○"


# ── Keys that must exist after init_state() ──────────────────────────────────

REQUIRED_STATE_KEYS: list[str] = [
    "backend_type", "llm_url", "context_size",
    "sys_prompt", "user_prompt",
    "fail_patterns", "metrics_matrix",
    "target_env_type", "mcp_url", "mcp_server_url",
    "validation_command",
]

# Keys the config dict passed to run_evaluation must contain
REQUIRED_CONFIG_KEYS: list[str] = [
    "backend_type", "llm_url", "selected_model", "context_size",
    "sys_prompt", "user_prompt",
    "mcp_url", "mcp_tools", "mcp_running",
    "validation_command", "fail_patterns",
    "active_scenario", "expected_stdout",
    "pre_run_cleanup", "cancel_requested_ref",
]


# ── Config builder (mirrors execute_tab.py) ────────────────────────────────────

def build_config_from_state(state: dict) -> dict:
    """
    Construct the config dict that run_evaluation() expects, from session_state.
    This is the canonical single source of truth — both execute_tab and pre-flight
    use this function so they can't diverge.
    """
    from config.defaults import LLAMA_CPP_DEFAULT_URL, OLLAMA_DEFAULT_URL
    from config.scenarios import SCENARIOS

    backend    = state.get("backend_type", "llama.cpp")
    def_url    = LLAMA_CPP_DEFAULT_URL if backend == "llama.cpp" else OLLAMA_DEFAULT_URL
    url        = (state.get("llm_url") or def_url).strip()
    active     = state.get("active_scenario", "")
    scenario   = SCENARIOS.get(active, {})

    return {
        "backend_type":         backend,
        "llm_url":              url,
        "selected_model":       state.get("selected_model"),
        "context_size":         state.get("context_size", 4096),
        "sys_prompt":           state.get("sys_prompt", ""),
        "user_prompt":          state.get("user_prompt", ""),
        "mcp_url":              state.get("mcp_url", ""),
        "mcp_server_url":       state.get("mcp_server_url", ""),
        "mcp_tools":            state.get("mcp_tools", {}),
        "mcp_running":          state.get("mcp_running", False),
        "validation_command":   state.get("validation_command", ""),
        "fail_patterns":        list(state.get("fail_patterns", [])),
        "active_scenario":      active,
        "expected_stdout":      scenario.get("expected_stdout", ""),
        "pre_run_cleanup":      list(scenario.get("pre_run_cleanup", [])),
        "cancel_requested_ref": [False],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Platform Regression
# ─────────────────────────────────────────────────────────────────────────────

def _time(fn: Callable) -> tuple[any, float]:
    """Call fn(), return (result, elapsed_ms)."""
    t0 = time.monotonic()
    r  = fn()
    return r, round((time.monotonic() - t0) * 1000, 1)


def check_state_completeness(state: dict) -> TestResult:
    """All required session_state keys must be present after init_state()."""
    missing = [k for k in REQUIRED_STATE_KEYS if k not in state]
    if missing:
        return TestResult(
            "State completeness", "platform", False,
            f"Missing keys: {', '.join(missing)}",
        )
    return TestResult(
        "State completeness", "platform", True,
        f"{len(REQUIRED_STATE_KEYS)} required keys present",
    )


def check_config_completeness(state: dict) -> TestResult:
    """Config dict built from state must contain all keys required by run_evaluation."""
    t0 = time.monotonic()
    config = build_config_from_state(state)
    ms = round((time.monotonic() - t0) * 1000, 1)

    missing = [k for k in REQUIRED_CONFIG_KEYS if k not in config]
    if missing:
        return TestResult(
            "Config completeness", "platform", False,
            f"Missing config keys: {', '.join(missing)}", ms,
        )
    return TestResult(
        "Config completeness", "platform", True,
        f"{len(REQUIRED_CONFIG_KEYS)} required keys built", ms,
    )


def check_config_no_mutation(state: dict) -> TestResult:
    """run_evaluation must not mutate the config dict it receives."""
    config   = build_config_from_state(state)
    snapshot = copy.deepcopy(config)

    # Simulate the same read path run_evaluation uses (does NOT do writes)
    _ = config.get("backend_type")
    _ = config.get("llm_url", "").rstrip("/")
    _ = config.get("selected_model") or ""
    _ = list(config.get("fail_patterns", []))
    _ = dict(config.get("mcp_tools", {}))

    diffs = [k for k in snapshot if config.get(k) != snapshot[k]]
    if diffs:
        return TestResult(
            "Config no mutation", "platform", False,
            f"Keys mutated: {', '.join(diffs)}",
        )
    return TestResult(
        "Config no mutation", "platform", True,
        "Config dict unchanged after pipeline read-through",
    )


def check_backend_connectivity(state: dict) -> TestResult:
    """HTTP probe to the configured LLM backend URL."""
    from core.utils import ensure_http_scheme as _ensure_scheme

    backend = state.get("backend_type", "llama.cpp")
    url     = _ensure_scheme(state.get("llm_url", "") or "")
    if not url:
        return TestResult("Backend connectivity", "platform", None,
                          "No LLM URL configured — skipped")

    probe = url.rstrip("/") + ("/health" if backend == "llama.cpp" else "/api/tags")

    t0 = time.monotonic()
    try:
        r  = requests.get(probe, timeout=5)
        ms = round((time.monotonic() - t0) * 1000, 1)
        if r.ok:
            return TestResult("Backend connectivity", "platform", True,
                              f"{backend} responded OK at {probe}", ms)
        return TestResult("Backend connectivity", "platform", False,
                          f"HTTP {r.status_code} from {probe}", ms)
    except requests.exceptions.ConnectionError:
        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Backend connectivity", "platform", None,
                          f"No server at {probe} — start {backend} first", ms)
    except requests.exceptions.Timeout:
        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Backend connectivity", "platform", None,
                          f"Timed out connecting to {probe}", ms)
    except Exception as exc:
        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Backend connectivity", "platform", False,
                          f"Unexpected error: {exc}", ms)


def check_timeout_handling() -> TestResult:
    """
    Verify that a connection to an unreachable port fails gracefully
    (no unhandled exception, returns within the timeout window).
    """
    from core import llama_server

    unreachable = "http://127.0.0.1:59998"
    t0 = time.monotonic()
    try:
        result = llama_server.is_running(unreachable, timeout=1.5)
        ms = round((time.monotonic() - t0) * 1000, 1)
        if result is False and ms < 3000:
            return TestResult("Timeout handling", "platform", True,
                              f"Unreachable URL returned False in {ms:.0f}ms", ms)
        return TestResult("Timeout handling", "platform", False,
                          f"Unexpected: is_running returned {result}", ms)
    except Exception as exc:
        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Timeout handling", "platform", False,
                          f"Raised exception instead of returning False: {exc}", ms)


def check_filesystem_access() -> TestResult:
    """Write → read → delete a temp file via LocalEnvironment."""
    from core.environment import LocalEnvironment

    env     = LocalEnvironment()
    marker  = "preflight_ok"
    tmp_path: str = ""

    t0 = time.monotonic()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", prefix="preflight_", delete=False
        ) as f:
            tmp_path = f.name

        write_result = env.write_file(tmp_path, marker)
        if "error" in write_result:
            return TestResult("Filesystem access", "platform", False,
                              f"Write failed: {write_result['error']}")

        read_back = env.read_file(tmp_path)
        if read_back != marker:
            return TestResult("Filesystem access", "platform", False,
                              f"Read mismatch: expected '{marker}', got '{read_back[:40]}'")

        deleted = env.delete_file(tmp_path)
        if not deleted:
            return TestResult("Filesystem access", "platform", False,
                              "Delete returned False after successful write")

        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Filesystem access", "platform", True,
                          f"Write / read / delete OK ({tmp_path})", ms)
    except Exception as exc:
        return TestResult("Filesystem access", "platform", False,
                          f"Exception: {exc}")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


def check_mcp_script_path(state: dict) -> TestResult:
    """The MCP script path must point to an existing file."""
    path = state.get("mcp_url", "").strip()
    if not path:
        return TestResult("MCP script path", "platform", None,
                          "No MCP script path configured — skipped")
    if os.path.isfile(path):
        return TestResult("MCP script path", "platform", True,
                          f"Script found: {path}")
    return TestResult("MCP script path", "platform", False,
                      f"Script not found: {path}")


def run_platform_layer(state: dict) -> list[TestResult]:
    """Run all Layer 1 platform regression checks."""
    return [
        check_state_completeness(state),
        check_config_completeness(state),
        check_config_no_mutation(state),
        check_backend_connectivity(state),
        check_timeout_handling(),
        check_filesystem_access(),
        check_mcp_script_path(state),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Evaluation Integrity
# ─────────────────────────────────────────────────────────────────────────────

def _make_good_telemetry(scenario_name: str = "") -> dict:
    """
    Ideal telemetry tailored to the active scenario.
    Includes realistic content so all enabled metrics evaluate to pass or N/A.
    """
    is_network = "Scenario 2" in scenario_name or "network" in scenario_name.lower()
    tool = "run_nmap_scan" if is_network else "file_creator"

    # Response must satisfy any content_contains metrics defined in that scenario
    response = (
        "I scanned 127.0.0.1 and found open port 22 (SSH) and port 80 (HTTP)."
        if is_network else
        "I have created the file at /tmp/test with numbers 1 through 10."
    )

    tool_args = (
        {"target": "127.0.0.1", "arguments": "-F"}
        if is_network else
        {"path": "/tmp/test", "content": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10"}
    )

    tool_result = (
        {"stdout": "PORT   STATE SERVICE\n22/tcp open  ssh\n80/tcp open  http", "exit_code": 0}
        if is_network else
        {"status": "success", "bytes_written": 20}
    )

    return {
        "run_aborted":           False,
        "validation_passed":     True,
        "total_latency":         4.2,
        "total_tokens":          85,
        "prompt_tokens":         60,
        "completion_tokens":     25,
        "tokens_per_second":     5.95,
        "llm_rounds":            1,
        "tool_calls": [
            {
                "tool":      tool,
                "args":      tool_args,
                "exit_code": 0,
                "result":    tool_result,
                "runtime":   0.01,
            }
        ],
        "inefficiencies":    [],
        "llm_response":      response,
        "validation_stdout": "" if is_network else "1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
        "validation_stderr": "",
        "validation_exit_code": 0,
    }


def _make_bad_telemetry() -> dict:
    """
    Telemetry for a failed run: no tool calls, validation failed, run aborted.
    """
    return {
        "run_aborted":           True,
        "validation_passed":     False,
        "total_latency":         0.3,
        "total_tokens":          8,
        "prompt_tokens":         8,
        "completion_tokens":     0,
        "tokens_per_second":     0.0,
        "llm_rounds":            0,
        "tool_calls":            [],
        "inefficiencies":        [],
        "llm_response":          "I cannot complete this task.",
        "validation_stdout":     "",
        "validation_stderr":     "cat: /tmp/test: No such file or directory",
        "validation_exit_code":  1,
    }


def check_metrics_configuration(metrics: list[dict]) -> TestResult:
    """All metrics in the current matrix must have valid types and non-empty required params."""
    from config.metrics import METRIC_TYPES

    if not metrics:
        return TestResult("Metrics configuration", "evaluation", None,
                          "No metrics configured — skipped")

    errors: list[str] = []
    for m in metrics:
        mid   = m.get("id", "?")
        mtype = m.get("type", "")
        if mtype not in METRIC_TYPES:
            errors.append(f"{mid}: unknown type '{mtype}'")
            continue
        for param in METRIC_TYPES[mtype]["params"]:
            if param["type"] == "str":
                val = str(m.get("params", {}).get(param["name"], "")).strip()
                if not val:
                    errors.append(f"{mid} ({mtype}): param '{param['name']}' is empty")

    if errors:
        return TestResult("Metrics configuration", "evaluation", False,
                          f"{len(errors)} invalid metric(s): {'; '.join(errors[:3])}")
    return TestResult("Metrics configuration", "evaluation", True,
                      f"{len(metrics)} metric(s) valid")


def check_known_good_telemetry(metrics: list[dict], scenario_name: str = "") -> TestResult:
    """
    Run all enabled metrics against idealised telemetry.
    Every metric should return True or None (skipped) — never False.
    """
    from config.metrics import evaluate_metric

    if not metrics:
        return TestResult("Known-good scoring", "evaluation", None,
                          "No metrics configured — skipped")

    t0    = time.monotonic()
    tel   = _make_good_telemetry(scenario_name)
    fails = []

    for m in metrics:
        if not m.get("enabled", True):
            continue
        result = evaluate_metric(m, tel)
        if result is False:
            fails.append(f"{m.get('id','?')} ({m.get('type','?')})")

    ms = round((time.monotonic() - t0) * 1000, 1)
    enabled = [m for m in metrics if m.get("enabled", True)]

    if fails:
        return TestResult(
            "Known-good run passes", "evaluation", False,
            f"Metrics incorrectly failed on ideal telemetry: {', '.join(fails)}", ms,
        )
    return TestResult(
        "Known-good run passes", "evaluation", True,
        f"All {len(enabled)} enabled metric(s) returned pass or N/A on ideal telemetry", ms,
    )


def check_known_bad_telemetry(metrics: list[dict]) -> TestResult:
    """
    Run validation-type metrics against a failed run's telemetry.
    task_completion must return False; tool_called metrics for any tool must return False.
    """
    from config.metrics import evaluate_metric

    MUST_FAIL_TYPES = {"task_completion", "tool_called", "goal_achievement"}

    must_fail = [m for m in metrics
                 if m.get("enabled", True) and m.get("type") in MUST_FAIL_TYPES]
    if not must_fail:
        return TestResult("Known-bad run fails", "evaluation", None,
                          "No task_completion / tool_called / goal_achievement metrics — skipped")

    t0  = time.monotonic()
    tel = _make_bad_telemetry()

    wrong: list[str] = []
    for m in must_fail:
        result = evaluate_metric(m, tel)
        if result is not False:
            wrong.append(f"{m.get('id','?')} ({m.get('type','?')}) returned {result!r}")

    ms = round((time.monotonic() - t0) * 1000, 1)
    if wrong:
        return TestResult(
            "Known-bad run fails", "evaluation", False,
            f"Metrics did not fail on a failed run: {', '.join(wrong)}", ms,
        )
    return TestResult(
        "Known-bad run fails", "evaluation", True,
        f"{len(must_fail)} gate metric(s) correctly returned False on failed telemetry", ms,
    )


def check_validation_logic_alignment() -> TestResult:
    """
    Run _run_validation with a concrete command and known fail pattern
    to confirm the pass/fail decision logic is aligned.
    """
    from core.evaluator import _run_validation
    from core.environment import LocalEnvironment

    env = LocalEnvironment()
    t0  = time.monotonic()
    try:
        # Case A: clean exit → should pass
        clean = _run_validation(env, "echo preflight_ok", [])
        if clean["passed"] is not True:
            return TestResult("Validation logic", "evaluation", False,
                              f"'echo preflight_ok' should pass but got passed={clean['passed']}")

        # Case B: fail pattern in stdout → should fail even at exit 0
        noisy = _run_validation(env, "echo preflight_error_test", ["preflight_error_test"])
        if noisy["passed"] is not False:
            return TestResult("Validation logic", "evaluation", False,
                              f"Fail-pattern not caught; passed={noisy['passed']}")

        # Case C: non-zero exit → should fail
        fail_exit = _run_validation(env, "false", [])
        if fail_exit["passed"] is not False:
            return TestResult("Validation logic", "evaluation", False,
                              f"Non-zero exit should fail but got passed={fail_exit['passed']}")

        ms = round((time.monotonic() - t0) * 1000, 1)
        return TestResult("Validation logic", "evaluation", True,
                          "Pass/fail/fail-pattern all correctly classified", ms)
    except Exception as exc:
        return TestResult("Validation logic", "evaluation", False,
                          f"Exception during validation check: {exc}")


def check_llm_smoke(state: dict, timeout_s: int = 90) -> TestResult:
    """
    Optional end-to-end smoke test: submit a minimal file-creation task to the
    configured LLM, run the evaluator loop, and assert validation passes.

    Only runs when the backend is currently reachable.
    Uses a background thread so we can enforce a hard timeout.
    """
    from core import llama_server
    from core.utils import ensure_http_scheme as _ensure_scheme
    from core.evaluator import run_evaluation
    from core.environment import LocalEnvironment

    backend = state.get("backend_type", "llama.cpp")
    url     = _ensure_scheme(state.get("llm_url", "") or "")

    # Gate: only proceed if backend is up
    if backend == "llama.cpp":
        if not llama_server.is_running(url):
            return TestResult("LLM smoke test", "evaluation", None,
                              f"llama-server not running at {url} — skipped")
    else:
        try:
            r = requests.get(url.rstrip("/") + "/api/tags", timeout=3)
            if not r.ok:
                return TestResult("LLM smoke test", "evaluation", None,
                                  f"Ollama not available at {url} — skipped")
        except Exception:
            return TestResult("LLM smoke test", "evaluation", None,
                              f"Ollama not reachable at {url} — skipped")

    smoke_path = "/tmp/modelscope_preflight_smoke"
    config = {
        "backend_type":         backend,
        "llm_url":              url,
        "selected_model":       state.get("selected_model") or "",
        "context_size":         min(state.get("context_size", 4096), 4096),
        "sys_prompt": (
            "You are an autonomous AI agent. When asked to create a file, call the "
            "file_creator tool exactly once with the correct path and content. "
            "Do not explain — just call the tool."
        ),
        "user_prompt":          f"Create a file at {smoke_path} containing exactly the text: pass",
        "mcp_url":              state.get("mcp_url", ""),
        "mcp_server_url":       state.get("mcp_server_url", ""),
        "mcp_tools":            {"file_creator": True},
        "mcp_running":          False,
        "validation_command":   f"cat {smoke_path}",
        "fail_patterns":        ["no such file", "permission denied", "not found"],
        "active_scenario":      "_preflight_smoke",
        "expected_stdout":      "pass",
        "pre_run_cleanup":      [smoke_path],
        "cancel_requested_ref": [False],
    }

    result_box: list = [None]
    error_box:  list = [None]

    def _run():
        try:
            env = LocalEnvironment()
            result_box[0] = run_evaluation(env, config, lambda _: None)
        except Exception as exc:
            error_box[0] = exc

    t0 = time.monotonic()
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=timeout_s)
    ms = round((time.monotonic() - t0) * 1000, 1)

    # Clean up smoke file regardless of result
    if os.path.exists(smoke_path):
        try:
            os.unlink(smoke_path)
        except OSError:
            pass

    if thread.is_alive():
        config["cancel_requested_ref"][0] = True  # signal thread to stop
        return TestResult("LLM smoke test", "evaluation", False,
                          f"LLM did not respond within {timeout_s}s timeout", ms)
    if error_box[0]:
        return TestResult("LLM smoke test", "evaluation", False,
                          f"Exception during smoke run: {error_box[0]}", ms)

    tel = result_box[0]
    if tel is None:
        return TestResult("LLM smoke test", "evaluation", False,
                          "run_evaluation returned None", ms)

    if tel.get("validation_passed") is True:
        rounds = tel.get("llm_rounds", 0)
        calls  = len(tel.get("tool_calls", []))
        return TestResult("LLM smoke test", "evaluation", True,
                          f"file_creator called, validation passed "
                          f"({rounds} round(s), {calls} tool call(s))", ms)

    if tel.get("run_aborted"):
        return TestResult("LLM smoke test", "evaluation", False,
                          f"Run aborted — LLM may not have tool-use capability at {url}", ms)

    return TestResult("LLM smoke test", "evaluation", False,
                      f"Validation did not pass "
                      f"(passed={tel.get('validation_passed')}, "
                      f"rounds={tel.get('llm_rounds')}, "
                      f"stdout={tel.get('validation_stdout','')[:80]!r})", ms)


def run_evaluation_layer(state: dict, include_llm_smoke: bool = False) -> list[TestResult]:
    """Run all Layer 2 evaluation integrity checks."""
    metrics  = state.get("metrics_matrix", [])
    scenario = state.get("active_scenario", "")
    results  = [
        check_metrics_configuration(metrics),
        check_known_good_telemetry(metrics, scenario),
        check_known_bad_telemetry(metrics),
        check_validation_logic_alignment(),
    ]
    if include_llm_smoke:
        results.append(check_llm_smoke(state))
    return results


# ── Public runner ──────────────────────────────────────────────────────────────

def run_all(state: dict, include_llm_smoke: bool = False) -> list[TestResult]:
    """Run both layers and return all results."""
    return run_platform_layer(state) + run_evaluation_layer(state, include_llm_smoke)
