"""Unit tests for core.batch_progress — the per-container progress model.

Progress is inferred from the evaluator's own log lines, so these tests pin
the exact prefixes that count as a unit of work (and, just as importantly,
the near-miss prefixes that must not).
"""

from core.batch_progress import (
    ABORTED,
    COMPLETE,
    FAILED,
    PASSED,
    PENDING,
    RUNNING,
    SKIPPED,
    batch_summary,
    container_summary,
    finish_container,
    new_container_state,
    observe_log,
    plan_unit_total,
    skip_container,
    start_container,
)


def _steps(*commands):
    return [{"commands": [{"command": command} for command in commands]}]


# ── Planning ──────────────────────────────────────────────────────────────────

def test_plan_unit_total_counts_only_commands_that_will_run():
    startup = [{"commands": [
        {"command": "echo hi"},
        {"command": "   "},
        {"command": "echo nope", "enabled": False},
        {"type": "prompt", "user_prompt": "judge this"},
        {"type": "prompt"},
    ]}]
    validation_sets = [{"steps": _steps("test -f /tmp/x", "true")}]
    completion = _steps("rm -f /tmp/x")

    assert plan_unit_total(startup, validation_sets, completion) == 5


def test_plan_unit_total_tolerates_missing_and_malformed_sections():
    assert plan_unit_total(None, None, None) == 0
    assert plan_unit_total(["raw command", "  "], [{"no_steps": True}], []) == 1


# ── Log observation ───────────────────────────────────────────────────────────

def test_observe_log_tracks_phase_step_and_percentage():
    state = new_container_state("100", "kali", total_units=4)
    start_container(state)

    observe_log(state, "[SERVER] Starting managed llama-server")
    assert state["phase"] == ""
    assert state["percent"] == 0

    observe_log(state, "[STARTUP] apt-get install -y curl")
    assert state["phase"] == "startup"
    assert state["units_started"] == 1
    assert state["current_step"] == "apt-get install -y curl"
    assert state["percent"] == 25

    observe_log(state, "[STDOUT] reading package lists")
    assert state["units_started"] == 1


def test_observe_log_does_not_count_validation_result_or_judge_echo():
    state = new_container_state("100", total_units=4)

    observe_log(state, "[VALIDATE SET] Starting set: Smoke (checks)")
    assert state["phase"] == "validation"
    assert state["units_started"] == 0

    observe_log(state, "[VALIDATE CMD] Running: test -f /tmp/x")
    observe_log(state, "[VALIDATE CMD RESULT] 'test -f /tmp/x' → PASS ✓")
    assert state["units_started"] == 1

    # A validation judge prompt was already counted by its [VALIDATE CMD] line.
    observe_log(state, "[PROMPT HELPER] Sending to http://host/v1/chat/completions")
    assert state["units_started"] == 1


def test_observe_log_counts_a_judge_prompt_outside_validation():
    state = new_container_state("100", total_units=4)

    observe_log(state, "[CLEANUP] rm -f /tmp/x")
    observe_log(state, "[PROMPT HELPER] Sending to http://host/v1/chat/completions")

    assert state["phase"] == "completion"
    assert state["units_started"] == 2
    assert state["current_step"] == "LLM Judge prompt"


def test_a_running_container_never_reports_one_hundred_percent():
    state = new_container_state("100", total_units=2)

    observe_log(state, "[STARTUP] one")
    observe_log(state, "[STARTUP] two")
    observe_log(state, "[STARTUP] unplanned extra")

    assert state["percent"] == 99


# ── Terminal states ───────────────────────────────────────────────────────────

def test_finish_container_maps_telemetry_onto_a_terminal_state():
    passed = new_container_state("100", total_units=2)
    finish_container(passed, {"validation_passed": True, "total_latency": 3.5})
    assert passed["state"] == PASSED
    assert passed["percent"] == 100
    assert passed["phase"] == "done"

    failed = new_container_state("101", total_units=2)
    finish_container(failed, {"validation_passed": False})
    assert failed["state"] == FAILED

    unvalidated = new_container_state("102", total_units=2)
    finish_container(unvalidated, {"validation_passed": None})
    assert unvalidated["state"] == COMPLETE


def test_an_aborted_container_keeps_the_progress_it_actually_made():
    state = new_container_state("100", total_units=4)
    observe_log(state, "[STARTUP] one")

    finish_container(state, {"run_aborted": True}, cancelled=True)

    assert state["state"] == ABORTED
    assert state["percent"] == 25
    assert state["current_step"] == "one"


def test_skip_container_only_applies_to_containers_that_never_started():
    untouched = new_container_state("101")
    skip_container(untouched)
    assert untouched["state"] == SKIPPED

    finished = new_container_state("100")
    finish_container(finished, {"validation_passed": True})
    skip_container(finished)
    assert finished["state"] == PASSED


# ── Summaries ─────────────────────────────────────────────────────────────────

def test_container_summary_drops_logs_but_keeps_the_run_verdict():
    state = new_container_state("100", "kali", total_units=2)
    state["logs_setup"].append({"text": "[STARTUP] one"})
    finish_container(state, {"validation_passed": True, "total_latency": 2.0})

    summary = container_summary(state)

    assert "logs_setup" not in summary
    assert "telemetry" not in summary
    assert summary["vmid"] == "100"
    assert summary["name"] == "kali"
    assert summary["state"] == PASSED
    assert summary["validation_passed"] is True
    assert summary["total_latency"] == 2.0


def test_batch_summary_reports_execution_coverage():
    assert batch_summary([])["level"] == "not_started"
    assert batch_summary([{"state": PENDING}, {"state": PENDING}])["level"] == "not_started"

    partial = batch_summary([{"state": PASSED, "percent": 100}, {"state": RUNNING, "percent": 50}])
    assert partial["level"] == "partial"
    assert partial["finished"] == 1
    assert partial["running"] == 1
    assert partial["percent"] == 75

    done = batch_summary([{"state": PASSED}, {"state": FAILED}, {"state": SKIPPED}])
    assert done["level"] == "complete"
    assert done["finished"] == 3
    assert done["failed"] == 1
