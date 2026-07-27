"""Per-container progress tracking for batch bots.

A batch bot runs the same startup → validation → completion workflow once per
target container. The evaluator has no progress callback, so progress is
derived from the log stream it already emits: every unit of work announces
itself on a line with a known prefix, and the expected number of units comes
from the same config the evaluator is handed.

Everything here is plain data (dicts) so the worker thread can mutate a
container's state in place while Streamlit renders the very same dict.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping


PENDING = "pending"
RUNNING = "running"
PASSED = "passed"
FAILED = "failed"
ABORTED = "aborted"
COMPLETE = "complete"
SKIPPED = "skipped"

FINISHED_STATES = frozenset({PASSED, FAILED, ABORTED, COMPLETE, SKIPPED})

# Log prefixes that announce one unit of work. "[VALIDATE CMD RESULT]" reports
# a finished command and deliberately does not match "[VALIDATE CMD]".
_UNIT_PREFIXES: dict[str, str] = {
    "[STARTUP]": "startup",
    "[RUN]": "startup",
    "[CLEANUP]": "completion",
    "[VALIDATE CMD]": "validation",
}

# A judge prompt in a startup/completion step announces itself only through its
# outbound request; inside a validation set "[VALIDATE CMD]" already counted it.
_PROMPT_PREFIX = "[PROMPT HELPER]"

_PHASE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("[STARTUP]", "startup"),
    ("[RUN]", "startup"),
    ("[VALIDATE", "validation"),
    ("[CLEANUP]", "completion"),
    ("[COMPLETE]", "done"),
)

PHASE_LABELS: dict[str, str] = {
    "": "Preparing",
    "startup": "Startup",
    "validation": "Validation",
    "completion": "Completion",
    "done": "Finished",
}

_SUMMARY_FIELDS = (
    "vmid", "name", "state", "phase", "units_started",
    "total_units", "percent", "current_step",
)


# ── Planning ──────────────────────────────────────────────────────────────────

def _is_enabled_command(cmd_obj: Any) -> bool:
    if isinstance(cmd_obj, str):
        return bool(cmd_obj.strip())
    if not isinstance(cmd_obj, Mapping):
        return False
    if not cmd_obj.get("enabled", True):
        return False
    if cmd_obj.get("type") == "prompt":
        return bool(cmd_obj.get("system_prompt", "") or cmd_obj.get("user_prompt", ""))
    return bool(str(cmd_obj.get("command", "")).strip())


def _count_commands(steps: Any) -> int:
    total = 0
    for step in steps if isinstance(steps, list) else []:
        if isinstance(step, str):
            total += 1 if step.strip() else 0
        elif isinstance(step, Mapping):
            total += sum(1 for cmd in step.get("commands", []) if _is_enabled_command(cmd))
    return total


def plan_unit_total(startup: Any, validation_sets: Any, completion: Any) -> int:
    """Number of work units one container run is expected to announce."""
    total = _count_commands(startup) + _count_commands(completion)
    for vset in validation_sets if isinstance(validation_sets, list) else []:
        if isinstance(vset, Mapping):
            total += _count_commands(vset.get("steps", []))
    return total


# ── Container state ───────────────────────────────────────────────────────────

def new_container_state(vmid: Any, name: str = "", total_units: int = 0) -> dict[str, Any]:
    """A single container's live progress record."""
    return {
        "vmid": str(vmid),
        "name": name or "",
        "state": PENDING,
        "phase": "",
        "units_started": 0,
        "total_units": int(total_units or 0),
        "percent": 0,
        "current_step": "",
        "telemetry": {},
        "logs_setup": [],
        "logs_validation": [],
    }


def _percent(state: Mapping[str, Any]) -> int:
    total = int(state.get("total_units", 0) or 0)
    if total <= 0:
        return 0
    # A unit counts as started, not finished, so a running container never
    # reaches 100% — that is reserved for finish_container().
    return min(99, round(100 * int(state.get("units_started", 0)) / total))


def _short(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def start_container(state: dict[str, Any]) -> None:
    state["state"] = RUNNING
    state["current_step"] = "Preparing…"


def observe_log(state: dict[str, Any], text: str) -> None:
    """Advance one container's progress from a single evaluator log line."""
    text = (text or "").strip()
    if not text:
        return

    for prefix, phase in _PHASE_PREFIXES:
        if text.startswith(prefix):
            state["phase"] = phase
            break

    matched = next((prefix for prefix in _UNIT_PREFIXES if text.startswith(prefix)), "")
    if matched:
        detail = text[len(matched):].strip()
    elif text.startswith(_PROMPT_PREFIX) and state.get("phase") != "validation":
        detail = "LLM Judge prompt"
    else:
        return

    state["units_started"] = int(state.get("units_started", 0)) + 1
    state["current_step"] = _short(detail)
    state["percent"] = _percent(state)


def finish_container(
    state: dict[str, Any], telemetry: Mapping[str, Any] | None, cancelled: bool = False,
) -> None:
    """Record one container's outcome once its evaluation returns."""
    telemetry = dict(telemetry) if isinstance(telemetry, Mapping) else {}
    state["telemetry"] = telemetry
    state["phase"] = "done"
    if telemetry.get("run_aborted") or cancelled:
        state["state"] = ABORTED
        state["percent"] = _percent(state)
        return
    if telemetry.get("validation_passed") is False:
        state["state"] = FAILED
    elif telemetry.get("validation_passed") is True:
        state["state"] = PASSED
    else:
        state["state"] = COMPLETE
    state["percent"] = 100
    state["current_step"] = "Finished"


def skip_container(state: dict[str, Any]) -> None:
    """Mark a container that never ran because the batch stopped early."""
    if state.get("state") == PENDING:
        state["state"] = SKIPPED
        state["current_step"] = "Skipped — run stopped"


def container_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    """Durable, log-free record of one container, for telemetry and history."""
    summary = {field: state.get(field) for field in _SUMMARY_FIELDS}
    telemetry = state.get("telemetry") or {}
    summary["validation_passed"] = telemetry.get("validation_passed")
    summary["total_latency"] = telemetry.get("total_latency")
    return summary


def batch_summary(states: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Roll container records up into one execution-progress verdict."""
    states = [state for state in states if isinstance(state, Mapping)]
    total = len(states)
    finished = sum(1 for state in states if state.get("state") in FINISHED_STATES)
    running = sum(1 for state in states if state.get("state") == RUNNING)
    if not total:
        level = "not_started"
    elif finished == total:
        level = "complete"
    elif finished or running:
        level = "partial"
    else:
        level = "not_started"
    return {
        "total": total,
        "finished": finished,
        "running": running,
        "failed": sum(1 for state in states if state.get("state") in {FAILED, ABORTED}),
        "percent": round(sum(int(state.get("percent", 0) or 0) for state in states) / total) if total else 0,
        "level": level,
    }
