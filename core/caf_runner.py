"""CyberAgentFlow remote execution via SSH.

Separated from evaluator.py to keep each module focused:
  - evaluator.py  — local LLM loop, tool execution, telemetry
  - caf_runner.py — SSH delegation to a remote CAF CLI, artifact pull

run_evaluation() in evaluator.py calls run_caf_ssh_evaluation() here via a
lazy import to avoid a module-level circular dependency.
"""
from __future__ import annotations

import json
import pathlib
import re
import shlex
import time

from core.caf_state import StepTelemetry, infer_phase
from core.utils import strip_ansi as _strip_ansi  # noqa: F401 — re-exported; tests import from here


# ── Internal helpers ───────────────────────────────────────────────────────────


def _caf_provider_flags(config: dict) -> str:
    """Map ModelScope backend config to CAF CLI provider flags."""
    backend = config.get("backend_type", "llama.cpp")
    url     = shlex.quote(config.get("llm_url", "").rstrip("/"))
    if backend == "ollama":
        return f"--provider ollama_direct --url {url}"
    return f"--provider openai --url {url}"


def _parse_caf_run_id(output: str) -> str | None:
    """Extract run_id from CAF CLI output: '[run] Transcript: runs/<id>/transcript.md'"""
    m = re.search(r'\[run\]\s+Transcript:\s+runs/([^/\s]+)/', output)
    return m.group(1) if m else None


# ── Artifact pulling ───────────────────────────────────────────────────────────

def _pull_caf_artifacts(env, run_id: str, local_dest: str, on_log) -> dict:
    """Pull transcript.md, metadata.json, and tool_calls/*.json from the remote
    runs/<run_id>/ directory.  Returns parsed metadata dict."""
    dest = pathlib.Path(local_dest) / run_id
    dest.mkdir(parents=True, exist_ok=True)

    metadata: dict = {}

    # Diagnose the remote run directory before attempting individual pulls.
    try:
        ls_result = env.execute(f"ls runs/{run_id}/ 2>/dev/null || echo 'DIR_NOT_FOUND'", timeout=10)
        ls_out = _strip_ansi(ls_result.get("stdout", "")).strip()
        if ls_out == "DIR_NOT_FOUND" or not ls_out:
            find_result = env.execute(
                f"find . -name 'metadata.json' -path '*{run_id}*' 2>/dev/null | head -5",
                timeout=15,
            )
            found = _strip_ansi(find_result.get("stdout", "")).strip()
            on_log(f"[CAF] Searching for run artifacts: {found or 'not found'}")
        else:
            on_log(f"[CAF] Run directory contents: {ls_out}")
    except Exception as diag_exc:
        on_log(f"[CAF] Could not list run directory: {diag_exc}")

    for fname in ("transcript.md", "metadata.json"):
        remote_path = f"runs/{run_id}/{fname}"
        try:
            content = env.read_file(remote_path)
            (dest / fname).write_text(content, encoding="utf-8")
            if fname == "metadata.json":
                metadata = json.loads(content)
            on_log(f"[CAF] Pulled {fname} ({len(content)} bytes)")
        except Exception as exc:
            on_log(f"[WARN] Could not pull {fname}: {exc}")

    tools_dir = dest / "tool_calls"
    tools_dir.mkdir(exist_ok=True)
    try:
        ls_result = env.execute(f"ls runs/{run_id}/tool_calls/ 2>/dev/null", timeout=10)
        for fname in ls_result.get("stdout", "").split():
            if not fname.endswith(".json"):
                continue
            try:
                content = env.read_file(f"runs/{run_id}/tool_calls/{fname}")
                (tools_dir / fname).write_text(content, encoding="utf-8")
            except Exception:
                pass
        on_log("[CAF] Pulled tool_calls/")
    except Exception as exc:
        on_log(f"[WARN] Could not list tool_calls: {exc}")

    return metadata


# ── Telemetry assembly ─────────────────────────────────────────────────────────

def _telemetry_from_caf(
    metadata: dict,
    local_run_dir: str,
    run_id: str,
    start_t: float,
    config: dict,
    val_result: dict,
) -> dict:
    """Build a ModelScope telemetry dict from pulled CAF artifacts."""
    from core.evaluator import _init_telemetry, _calculate_step_tdi

    telemetry = _init_telemetry(config)
    telemetry["run_timestamp"]   = metadata.get("start_time", telemetry["run_timestamp"])
    telemetry["run_model"]       = metadata.get("model", config.get("selected_model") or "(server default)")
    telemetry["total_latency"]   = round(time.time() - start_t, 3)
    telemetry["run_aborted"]     = metadata.get("status", "completed") not in ("completed",)
    telemetry["llm_rounds"]      = 1

    tools_path = pathlib.Path(local_run_dir) / run_id / "tool_calls"
    caf_tool_calls: list[dict] = []
    caf_trajectory: list[dict] = []
    context_window = int(metadata.get("context_window", 8192))
    if tools_path.exists():
        for seq_idx, tc_file in enumerate(sorted(tools_path.glob("*.json"))):
            try:
                tc = json.loads(tc_file.read_text(encoding="utf-8"))
                tool_name   = tc.get("tool", "")
                exit_code   = tc.get("exit_code", 0)
                result_text = tc.get("result", "")
                duration_ms = tc.get("duration_ms", 0)
                result_dict = {
                    "stdout":    result_text,
                    "exit_code": exit_code,
                    "stderr":    tc.get("stderr", ""),
                }
                caf_tool_calls.append({
                    "tool":      tool_name,
                    "args":      tc.get("args", {}),
                    "result":    result_dict,
                    "runtime":   round(duration_ms / 1000, 3),
                    "exit_code": exit_code,
                })
                tdi, e, c, s, ev_conf = _calculate_step_tdi(
                    tool=tool_name,
                    result=result_dict,
                    tokens=0,
                    recent_steps=caf_trajectory,
                    context_size=context_window,
                    exit_code=exit_code,
                )
                step = StepTelemetry(
                    step_number=seq_idx,
                    tool_called=tool_name,
                    arguments=tc.get("args", {}),
                    exit_code=exit_code,
                    output_preview=result_text[:500],
                    execution_time_ms=float(duration_ms),
                    context_tokens_used=0,
                    calculated_tdi=tdi,
                    tdi_e=e,
                    tdi_c=c,
                    tdi_s=s,
                    evidence_confidence=ev_conf,
                    phase=infer_phase(tool_name),
                )
                caf_trajectory.append(step.to_dict())
            except Exception:
                pass

    telemetry["tool_calls"]     = caf_tool_calls
    telemetry["caf_trajectory"] = caf_trajectory

    # FIX: inefficiencies was never computed for SSH runs, so no_repeated_calls and
    # goal_achievement always scored PASS vacuously. Import _check_inefficiencies from
    # evaluator (same import already used for _init_telemetry/_calculate_step_tdi above)
    # to give SSH runs the same loop-detection guarantees as local runs.
    from core.evaluator import _check_inefficiencies
    telemetry["inefficiencies"] = _check_inefficiencies(caf_tool_calls)

    transcript_path = pathlib.Path(local_run_dir) / run_id / "transcript.md"
    if transcript_path.exists():
        telemetry["llm_response"] = transcript_path.read_text(encoding="utf-8")[:2000]

    telemetry.update({
        "validation_stdout":    val_result.get("stdout", ""),
        "validation_stderr":    val_result.get("stderr", ""),
        "validation_exit_code": val_result.get("exit_code"),
        "validation_passed":    val_result.get("passed"),
    })
    return telemetry


# ── Public entry point ─────────────────────────────────────────────────────────

def run_caf_ssh_evaluation(
    env,
    config: dict,
    on_log,
    local_run_history_dir: str = "/tmp/modelscope_caf_runs",
    input_queue=None,
) -> dict:
    """Execute a CAF benchmark on a remote Kali machine via SSH.

    When env supports execute_streaming(), output is streamed in real-time via
    on_log("[STREAM] …") calls. input_queue (queue.Queue) allows the UI to
    inject stdin for CAF's interactive decision prompts ([approval], [timeout]).
    Falls back to blocking execute() when streaming is unavailable.
    """
    from core.evaluator import _init_telemetry, _run_validation

    start_t = time.time()

    model   = shlex.quote(config.get("selected_model") or "")
    prompt  = shlex.quote(config.get("user_prompt", ""))
    scope   = shlex.quote(config.get("caf_scope", "Narrow").lower())
    _urgency_raw = config.get("caf_urgency", "Speed").lower()
    _URGENCY_CLI = {"stealth": "stealthy"}
    urgency = shlex.quote(_URGENCY_CLI.get(_urgency_raw, _urgency_raw))
    prov    = _caf_provider_flags(config)

    cmd = (
        f"./start_cli.sh run "
        f"{prov} "
        f"--model {model} "
        f"--scope {scope} "
        f"--urgency {urgency} "
        f"{prompt}"
    )
    on_log(f"[CAF] Remote command: {cmd}")
    on_log(f"[CAF] Executing on {env.host} (this may take several minutes)…")

    if hasattr(env, "execute_streaming") and callable(env.execute_streaming):
        _line_buf: list[str] = [""]

        def _on_chunk(text: str) -> None:
            cleaned = _strip_ansi(text).replace("\r\n", "\n").replace("\r", "\n")
            _line_buf[0] += cleaned
            while "\n" in _line_buf[0]:
                line, _line_buf[0] = _line_buf[0].split("\n", 1)
                line = line.strip()
                if line:
                    on_log(f"[STREAM] {line}")

        cancel_ref = config.get("cancel_requested_ref", [False])
        result = env.execute_streaming(
            cmd, timeout=600, on_chunk=_on_chunk,
            input_queue=input_queue, cancel_ref=cancel_ref,
        )
        if _line_buf[0].strip():
            on_log(f"[STREAM] {_line_buf[0].strip()}")
        combined_output = _strip_ansi(result["stdout"] + result.get("stderr", ""))
    else:
        result = env.execute(cmd, timeout=600)
        combined_output = _strip_ansi(result["stdout"] + result["stderr"])
        if result["stdout"]:
            on_log(f"[CAF OUTPUT]\n{_strip_ansi(result['stdout'])[:2000]}")
        if result["stderr"]:
            on_log(f"[CAF STDERR]\n{_strip_ansi(result['stderr'])[:500]}")

    on_log(f"[CAF] Exit code: {result['exit_code']}")

    run_id = _parse_caf_run_id(combined_output)
    if not run_id:
        on_log("[WARN] Could not detect CAF run_id from output — artifact pull skipped")
        telemetry = _init_telemetry(config)
        telemetry["run_aborted"]   = result["exit_code"] != 0
        telemetry["llm_response"]  = combined_output[:2000]
        telemetry["total_latency"] = round(time.time() - start_t, 3)
        return telemetry

    on_log(f"[CAF] Run ID: {run_id}")
    metadata = _pull_caf_artifacts(env, run_id, local_run_history_dir, on_log)

    val_result: dict = {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
    val_cmd = config.get("validation_command", "")
    if val_cmd and result["exit_code"] == 0:
        on_log(f"[VALIDATE] Running: {val_cmd}")
        val_result = _run_validation(
            env, val_cmd,
            config.get("fail_patterns", []),
            config.get("expected_stdout", ""),
        )
        status = "PASS ✓" if val_result["passed"] else "FAIL ✗"
        on_log(f"[VALIDATE] {status}  (exit_code={val_result['exit_code']})")

    telemetry = _telemetry_from_caf(
        metadata, local_run_history_dir, run_id, start_t, config, val_result,
    )
    on_log(
        f"[COMPLETE] {telemetry['total_latency']}s  |  "
        f"{len(telemetry['tool_calls'])} tool call(s)  |  "
        f"run_id={run_id}"
    )
    return telemetry
