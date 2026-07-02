"""Local LLM evaluation loop: drives the agent turn/tool-call cycle.

This module owns the *local* execution path — it sends prompts to a llama.cpp
or Ollama backend, executes any tool calls the model emits against the supplied
``BaseEnvironment``, accumulates telemetry (latency, tokens, per-step CAF Task
Difficulty Index) and runs the post-run validation command.

Remote CyberAgentFlow runs are delegated to :mod:`core.caf_runner`;
``run_evaluation`` dispatches there when the environment advertises
``is_remote_caf`` (or ``config["execution_mode"] == "caf_ssh"``).
"""
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from typing import Callable

import requests

from config.defaults import MCP_SERVER_BASE_URL
from core.caf_state import StepTelemetry, infer_phase, score_evidence_confidence
from core.environment import BaseEnvironment
from core.mcp_manager import call_mcp_tool, probe_mcp_server
from core.streaming import stream_ollama, stream_llama_cpp
from core.utils import strip_ansi as _strip_ansi  # noqa: F401 — re-exported for callers/tests


# ── Fail pattern evaluation ────────────────────────────────────────────────────

def _check_fail_patterns(patterns: list, output: str, env) -> tuple:
    """Returns (failed: bool, reason: str). Handles legacy str and typed dict patterns."""
    for fp in patterns:
        if isinstance(fp, str):
            fp = {"type": "string", "value": fp}
        t, v = fp.get("type", "string"), fp.get("value", "")
        if t == "string":
            if v.lower() in output.lower():
                return True, f"fail pattern matched: {v!r}"
        elif t == "command":
            try:
                res = env.execute(v, timeout=10)
                if res["exit_code"] != 0:
                    return True, f"fail command exited {res['exit_code']}: {v!r}"
            except Exception as exc:
                return True, f"fail command error: {exc}"
        elif t == "prompt":
            if v.lower() in output.lower():
                return True, f"prompt pattern matched: {v!r}"
    return False, ""


# ── Tool schema loading ────────────────────────────────────────────────────────

def _load_tool_schemas(
    mcp_script_path: str,
    enabled_tools: dict | None = None,
    on_log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Read tools.json and return OpenAI-format schemas.

    When enabled_tools is None or empty, all tools are returned (fallback mode).
    When enabled_tools is provided, only enabled entries are returned.
    """
    tools_file = os.path.join(os.path.dirname(mcp_script_path), "tools.json")
    if not os.path.exists(tools_file):
        if on_log:
            on_log(f"[WARN] tools.json not found at {tools_file}")
        return []
    try:
        with open(tools_file) as fh:
            data = json.load(fh)
        filter_fn = (
            (lambda t: enabled_tools.get(t.get("name", "")))
            if enabled_tools
            else (lambda t: bool(t.get("name")))
        )
        return [
            {
                "type": "function",
                "function": {
                    "name":        t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters":  t.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            for t in data
            if isinstance(t, dict) and filter_fn(t)
        ]
    except Exception as exc:
        if on_log:
            on_log(f"[WARN] Could not load tool schemas: {exc}")
        return []


# ── Managed llama-server startup ───────────────────────────────────────────────

def _start_managed_llama_server(
    binary: str,
    model_path: str,
    context_size: int,
    port: int,
    on_log: Callable[[str], None],
) -> subprocess.Popen:
    """Start llama-server binary and wait for readiness.

    Raises RuntimeError if server doesn't become ready within 30s.
    Returns the Popen object; caller must manage teardown.
    """
    cmd = [
        binary,
        "-m", model_path,
        "-c", str(context_size),
        "--port", str(port),
        "--host", "127.0.0.1",
    ]
    on_log(f"[SERVER] Starting: {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except Exception as exc:
        raise RuntimeError(f"Failed to start server: {exc}")

    # Poll for readiness
    for attempt in range(30):
        try:
            resp = requests.get(f"http://127.0.0.1:{port}/health", timeout=1.0)
            if resp.status_code == 200:
                on_log(f"[SERVER] Ready on port {port}")
                return proc
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)

    proc.terminate()
    raise RuntimeError(f"Server did not become ready after 30s on port {port}")


# ── Agent loop for tool-aware evaluation ───────────────────────────────────────

def _run_llm_agent_loop(
    base_url: str,
    model: str,
    prompt: str,
    tools: list,
    context_size: int,
    mcp_running: bool,
    mcp_server_url: str,
    env: BaseEnvironment,
    cancel_ref: list,
    on_log: Callable[[str], None],
    max_turns: int = 8,
    deadline: float | None = None,
) -> tuple:
    """Run agentic loop with tool calls. Returns (tool_calls_log, final_response)."""
    messages: list[dict] = [{"role": "user", "content": prompt}]
    tool_calls_log = []
    final_response = ""

    for round_num in range(max_turns):
        if cancel_ref[0]:
            on_log("[CANCEL] Evaluation cancelled by user")
            break

        # Bug 2: wall-clock timeout guard
        if deadline is not None and time.time() >= deadline:
            on_log(f"[TIMEOUT] Evaluation exceeded time limit — stopping after {round_num} turn(s)")
            cancel_ref[0] = True
            break

        on_log(f"[LLM] Turn {round_num + 1}")
        resp = stream_llama_cpp(
            base_url=base_url,
            model=model,
            messages=messages,
            tools=tools,
            context_size=context_size,
            on_log=on_log,
        )

        msg = resp.get("message", {})
        content: str = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []

        # Fallback: parse inline tool calls (Qwen, SmolLM, etc.)
        if not tool_calls_raw and content and "<tool_call>" in content:
            tool_calls_raw = _parse_inline_tool_calls(content)
            if tool_calls_raw:
                names = ", ".join(tc["function"]["name"] for tc in tool_calls_raw)
                on_log(f"[TOOLS] Parsed {len(tool_calls_raw)} inline: {names}")

        if content:
            on_log(f"[RESPONSE] {content[:400]}")
            final_response = content

        if not tool_calls_raw:
            break

        # Execute tools
        for tc in tool_calls_raw:
            tool_name = tc["function"]["name"]
            # Bug 1 fix: arguments are a JSON string inside tc["function"], not at tc level
            try:
                tool_args = json.loads(tc["function"].get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                tool_args = {}
            on_log(f"[TOOL] {tool_name}({json.dumps(tool_args)[:100]})")
            result = _execute_tool(env, tool_name, tool_args, mcp_running, mcp_server_url)
            on_log(f"[RESULT] {json.dumps(result)[:200]}")

            tool_calls_log.append({
                "tool": tool_name,
                "args": tool_args,
                "result": result,
                "exit_code": result.get("exit_code", 0),
            })

            # Add to conversation
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": f"Tool {tool_name} returned: {json.dumps(result)[:500]}"
            })

    return tool_calls_log, final_response


# ── Tool execution ─────────────────────────────────────────────────────────────

# Legal characters in a scan target: hostnames, IPv4/IPv6 literals, CIDR masks.
# Anything else (whitespace, shell metacharacters, newlines) is rejected outright.
_NMAP_TARGET_RE = re.compile(r"^[A-Za-z0-9.:/_-]+$")


def _execute_tool_in_env(env: BaseEnvironment, tool_name: str, tool_args: dict) -> dict:
    if tool_name == "file_creator":
        return env.write_file(tool_args.get("path", ""), tool_args.get("content", ""))

    if tool_name == "run_nmap_scan":
        target    = tool_args.get("target", "127.0.0.1")
        arguments = tool_args.get("arguments", "-F")
        # Reject any target that is not a bare hostname / IP / CIDR. The previous
        # denylist ({; & | ` $ >}) missed newlines, parentheses, whitespace and
        # quotes — all of which break out of a `shell=True` command. An allowlist
        # of the characters legal in a host/IP/CIDR closes every one of those.
        if not target or not _NMAP_TARGET_RE.match(target):
            return {"error": "Invalid scan target"}
        # Tokenise arguments through shlex so embedded separators cannot survive,
        # then re-quote every token as defence in depth.
        try:
            arg_tokens = shlex.split(arguments)
        except ValueError:
            return {"error": "Malformed arguments"}
        safe_args   = " ".join(shlex.quote(tok) for tok in arg_tokens)
        safe_target = shlex.quote(target)
        return env.execute(f"nmap {safe_args} {safe_target}".strip(), timeout=30)

    # filesystem/write_file — mirrors the MCP filesystem server's write_file tool
    if tool_name == "write_file":
        path    = tool_args.get("path", "")
        content = tool_args.get("content", "")
        return env.write_file(path, content)

    # filesystem/read_file — mirrors the MCP filesystem server's read_file tool
    if tool_name == "read_file":
        path = tool_args.get("path", "")
        try:
            content = env.read_file(path)
            return {"content": content}
        except Exception as exc:
            return {"error": str(exc)}

    return {"error": f"Unknown tool: {tool_name}"}


def _execute_tool(
    env: BaseEnvironment,
    tool_name: str,
    tool_args: dict,
    mcp_running: bool,
    mcp_server_url: str = MCP_SERVER_BASE_URL,
) -> dict:
    if mcp_running:
        result = call_mcp_tool(tool_name, tool_args, base_url=mcp_server_url)
        if "error" not in result:
            return result
    return _execute_tool_in_env(env, tool_name, tool_args)


# ── Validation ─────────────────────────────────────────────────────────────────

def _run_validation(
    env: BaseEnvironment,
    command: str,
    fail_patterns: list,
    expected_stdout: str = "",
) -> dict:
    if not command.strip():
        return {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
    res         = env.execute(command, timeout=15)
    combined    = res["stdout"] + res["stderr"]
    failed, _   = _check_fail_patterns(fail_patterns, combined, env)
    passed      = res["exit_code"] == 0 and not failed
    if passed and expected_stdout.strip():
        passed = res["stdout"].strip() == expected_stdout.strip()
    return {
        "stdout":    res["stdout"],
        "stderr":    res["stderr"],
        "exit_code": res["exit_code"],
        "passed":    passed,
    }


def _run_validation_sets(
    env: BaseEnvironment,
    validation_sets: list,
    on_log: Callable[[str], None],
    cancel_ref: list[bool] = [False],
) -> tuple[bool, list]:
    """
    Execute all validation sets.
    Each validation set contains steps, which contain commands and expected outputs.
    Returns (all_passed, results_list).
    """
    if not validation_sets:
        on_log("[VALIDATE] No validation sets configured")
        return True, []

    overall_passed = True
    results = []

    for vset in validation_sets:
        set_name = vset.get("name", "Unnamed Set")
        set_desc = vset.get("description", "")
        on_log(f"[VALIDATE SET] Starting set: {set_name} ({set_desc})")

        if not vset.get("enabled", True):
            on_log(f"[VALIDATE SET] Skipping set (Disabled)")
            continue

        set_passed = True
        step_results = []

        for step_idx, step in enumerate(vset.get("steps", [])):
            if cancel_ref[0]:
                on_log("[CANCEL] Validation cancelled by user")
                break

            delay = float(step.get("delay_seconds", 0.0))
            if delay > 0:
                on_log(f"[DELAY] Step {step_idx + 1}: waiting {delay:.1f}s")
                time.sleep(delay)

            for cmd_obj in step.get("commands", []):
                if cancel_ref[0]:
                    break
                if not cmd_obj.get("enabled", True):
                    continue

                cmd_text = cmd_obj.get("command", "").strip()
                if not cmd_text:
                    continue

                timeout = int(cmd_obj.get("timeout_seconds", 60))
                on_log(f"[VALIDATE CMD] Running: {cmd_text}")

                res = env.execute(cmd_text, timeout=timeout)
                stdout = res.get("stdout", "")
                stderr = res.get("stderr", "")
                exit_code = res.get("exit_code", -1)

                # Verify output
                out_type = cmd_obj.get("expected_output_type", "Ignore")
                expected = cmd_obj.get("expected_output", "")
                cmd_passed = (exit_code == 0)

                reason = ""
                if not cmd_passed:
                    reason = f"Exit code was {exit_code}"
                else:
                    if out_type == "Exact String":
                        if stdout.strip() != expected.strip():
                            cmd_passed = False
                            reason = f"Output did not match exact string. Expected: {expected!r}, Got: {stdout!r}"
                    elif out_type == "Regex":
                        try:
                            if not re.search(expected, stdout):
                                cmd_passed = False
                                reason = f"Output did not match regex pattern {expected!r}. Got: {stdout!r}"
                        except re.error as e:
                            cmd_passed = False
                            reason = f"Invalid regex pattern: {expected!r} ({e})"
                    elif out_type == "No output":
                        if stdout.strip():
                            cmd_passed = False
                            reason = f"Output was expected to be empty, but got: {stdout!r}"

                status_str = "PASS ✓" if cmd_passed else f"FAIL ✗ ({reason})"
                on_log(f"[VALIDATE CMD RESULT] {cmd_text!r} → {status_str}")

                if not cmd_passed:
                    set_passed = False

                step_results.append({
                    "command": cmd_text,
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "passed": cmd_passed,
                    "reason": reason,
                    "expected_output_type": out_type,
                    "expected_output": expected
                })

        if not set_passed:
            overall_passed = False

        results.append({
            "name": set_name,
            "description": set_desc,
            "passed": set_passed,
            "steps": step_results
        })

    return overall_passed, results


# ── Inline tool-call parsing (fallback for models that use <tool_call> tags) ──

def _parse_inline_tool_calls(content: str) -> list[dict]:
    matches = re.findall(r'<tool_call>(.*?)</tool_call>', content, re.DOTALL)
    result: list[dict] = []
    for raw in matches:
        raw = raw.strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        calls = data if isinstance(data, list) else [data]
        for call in calls:
            if not isinstance(call, dict) or "name" not in call:
                continue
            args = call.get("arguments", call.get("parameters", {}))
            if isinstance(args, dict):
                args = json.dumps(args)
            result.append({
                "id":   f"call_{len(result)}",
                "type": "function",
                "function": {
                    "name":      call["name"],
                    "arguments": str(args) if args else "{}",
                },
            })
    return result


# ── Loop detection ─────────────────────────────────────────────────────────────

def _check_inefficiencies(tool_calls: list[dict]) -> list[str]:
    seen: dict[tuple, int] = {}
    issues = []
    for call in tool_calls:
        tool = call.get("tool")
        key  = (tool, json.dumps(call.get("args", {}), sort_keys=True))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            issues.append(f"Repeated call: {tool} with identical arguments")
    return issues


# ── CAF Task Difficulty Index ──────────────────────────────────────────────────

def _calculate_step_tdi(
    tool: str,
    result: dict,
    tokens: int,
    recent_steps: list[dict],
    context_size: int = 4096,
    exit_code: int = 0,
) -> tuple[float, float, float, float, float]:
    """
    3-component Task Difficulty Index (PENTESTGPT V2, H-dimension dropped).

    TDI = 0.4·(1-E) + 0.3·C + 0.3·(1-S)

    Components:
      E — evidence confidence (0–1): how actionable is the current output?
      C — context load ratio (0–1): fraction of context window consumed
      S — recent success rate (0–1): fraction of last 3 steps that succeeded

    Returns (tdi, e, c, s, evidence_confidence).
    High TDI (>0.6) → BFS exploration mode; low (<0.3) → DFS exploitation.
    """
    output_str = str(result.get("stdout", "") or result)
    evidence_confidence = score_evidence_confidence(tool, output_str, exit_code)

    c = min(tokens / max(context_size, 1), 1.0)

    recent = recent_steps[-3:] if recent_steps else []
    successes = sum(1 for s in recent if s.get("exit_code", 0) == 0)
    s = successes / max(len(recent), 1) if recent else 1.0

    tdi = 0.4 * (1.0 - evidence_confidence) + 0.3 * c + 0.3 * (1.0 - s)
    return round(tdi, 3), evidence_confidence, round(c, 3), round(s, 3), evidence_confidence


# ── Evaluation helpers ─────────────────────────────────────────────────────────

def _init_telemetry(config: dict) -> dict:
    """Initialise the telemetry accumulator for a new run."""
    return {
        "run_timestamp":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_scenario":    config.get("active_scenario", ""),
        "run_model":       config.get("selected_model") or "(server default)",
        "run_backend":     config.get("backend_type", "llama.cpp"),
        "run_tool_focus":  config.get("tool_focus", ""),
        # Performance
        "total_latency":      0.0,
        "prompt_tokens":      0,
        "completion_tokens":  0,
        "total_tokens":       0,
        "tokens_per_second":  0.0,
        "llm_rounds":         0,
        # Tool execution
        "tool_calls":         [],
        # Validation
        "validation_stdout":    "",
        "validation_stderr":    "",
        "validation_exit_code": None,
        "validation_passed":    None,
        # Quality
        "inefficiencies": [],
        "llm_response":   "",
        "run_aborted":    False,
        "metrics_matrix": config.get("metrics_matrix", []),
        # CAF 4-Pillar
        "caf_trajectory": [],
        "caf_config": {
            "scope":              config.get("caf_scope", "Narrow"),
            "urgency":            config.get("caf_urgency", "Speed"),
            "allowed_subnets":    config.get("caf_allowed_subnets", []),
            "target_credentials": config.get("caf_target_credentials", []),
        },
    }


def _call_llm(
    backend: str,
    base_url: str,
    model: str,
    messages: list,
    tool_schemas: list,
    context_size: int,
    on_log: Callable,
) -> dict:
    """Dispatch one LLM round to the correct backend adapter."""
    if backend == "ollama":
        return stream_ollama(base_url, model, messages, tool_schemas, context_size, on_log)
    return stream_llama_cpp(base_url, model, messages, tool_schemas, context_size, on_log)


def _process_tool_calls(
    env: BaseEnvironment,
    tool_calls_raw: list,
    messages: list,
    telemetry: dict,
    config: dict,
    on_log: Callable,
    cancel_ref: list[bool],
) -> bool:
    """
    Execute each tool call, update telemetry and the message chain.
    Returns True if the run was aborted via cancel_ref.
    """
    mcp_running    = config.get("mcp_running", False)
    mcp_server_url = config.get("mcp_server_url", MCP_SERVER_BASE_URL)

    for tc in tool_calls_raw:
        if cancel_ref[0]:
            on_log("[CANCEL] Tool execution cancelled")
            return True

        fn        = tc.get("function", {})
        tool_name = fn.get("name", "")
        try:
            tool_args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            tool_args = {}

        on_log(f"[TOOL CALL] {tool_name}({json.dumps(tool_args)})")
        t0      = time.time()
        result  = _execute_tool(env, tool_name, tool_args, mcp_running, mcp_server_url)
        elapsed = round(time.time() - t0, 3)

        on_log(f"[TOOL RESULT] {tool_name} → {str(result)[:300]}  ({elapsed}s)")
        step_exit = 0 if "error" not in result else 1
        telemetry["tool_calls"].append({
            "tool":      tool_name,
            "args":      tool_args,
            "result":    result,
            "runtime":   elapsed,
            "exit_code": step_exit,
        })

        # CAF 4-Pillar: per-step telemetry
        tokens_so_far = telemetry["prompt_tokens"] + telemetry["completion_tokens"]
        tdi, e, c, s, ev_conf = _calculate_step_tdi(
            tool=tool_name,
            result=result,
            tokens=tokens_so_far,
            recent_steps=telemetry["caf_trajectory"],
            context_size=config.get("context_size", 4096),
            exit_code=step_exit,
        )
        caf_step = StepTelemetry(
            step_number=len(telemetry["caf_trajectory"]),
            tool_called=tool_name,
            arguments=tool_args,
            exit_code=step_exit,
            output_preview=str(result)[:500],
            execution_time_ms=round(elapsed * 1000, 1),
            context_tokens_used=tokens_so_far,
            calculated_tdi=tdi,
            tdi_e=e,
            tdi_c=c,
            tdi_s=s,
            evidence_confidence=ev_conf,
            phase=infer_phase(tool_name),
        )
        telemetry["caf_trajectory"].append(caf_step.to_dict())
        on_log(f"[CAF] Step {caf_step.step_number} TDI={tdi:.3f}  E={e:.2f}  C={c:.2f}  S={s:.2f}  phase={caf_step.phase}")

        messages.append({
            "role":         "tool",
            "tool_call_id": tc.get("id", "call_0"),
            "content":      json.dumps(result),
        })

    return False


def _finalize_telemetry(telemetry: dict, start_t: float) -> None:
    """Calculate derived timing and throughput metrics in-place."""
    telemetry["total_latency"] = round(time.time() - start_t, 3)
    telemetry["total_tokens"]  = telemetry["prompt_tokens"] + telemetry["completion_tokens"]
    if telemetry["total_latency"] > 0 and telemetry["completion_tokens"] > 0:
        telemetry["tokens_per_second"] = round(
            telemetry["completion_tokens"] / telemetry["total_latency"], 1
        )


def _run_ai_judge(telemetry: dict, config: dict, on_log: Callable[[str], None]) -> None:
    """Score the final response with the optional frontier-model judge."""
    if not config.get("judge_enabled"):
        return
    if config.get("judge_mode") == "Generate ground truth only":
        on_log("[JUDGE] Skipped — ground-truth generation mode is selected")
        return
    api_key = (config.get("judge_api_key") or "").strip()
    if not api_key:
        on_log("[JUDGE] Skipped — no API key configured")
        telemetry["judge_error"] = "No API key configured"
        return
    response = telemetry.get("llm_response", "")
    if not response.strip():
        on_log("[JUDGE] Skipped — no LLM response to score")
        telemetry["judge_error"] = "No LLM response to score"
        return

    try:
        from core.judge import FrontierJudge
        judge = FrontierJudge(
            provider=config.get("judge_provider", "anthropic"),
            model=config.get("judge_model", ""),
            api_key=api_key,
            temperature=float(config.get("judge_temperature", 0.0)),
        )
        on_log(f"[JUDGE] Scoring response with {judge.provider}/{judge.model}")
        score = judge.score_response(
            prompt=config.get("user_prompt", ""),
            response=response,
            ground_truth=config.get("expected_stdout") or None,
        )
    except Exception as exc:
        on_log(f"[JUDGE] Failed: {exc}")
        telemetry["judge_error"] = str(exc)
        return

    if score is None:
        on_log("[JUDGE] Failed — no score returned")
        telemetry["judge_error"] = "No score returned"
        return

    dims = ["correctness", "coherence", "goal_alignment", "safety", "efficiency"]
    telemetry["judge_scores"] = {
        dim: {
            "score": int(getattr(score, dim, 0)),
            "justification": score.justifications.get(dim, ""),
        }
        for dim in dims
    }
    telemetry["judge_aggregate_score"] = score.aggregate_score
    telemetry["judge_raw_response"] = score.raw_response
    on_log(f"[JUDGE] Aggregate score: {score.aggregate_score:.1f}/100")


# ── CAF SSH execution — delegated to core.caf_runner ──────────────────────────
# run_caf_ssh_evaluation is imported lazily inside run_evaluation() to avoid a
# module-level circular import: caf_runner imports _init_telemetry and
# _run_validation from this module at load time.
#
# Backward-compatible re-exports: the CAF helpers physically live in
# core.caf_runner (their correct home), but historically lived here. Existing
# callers and tests still do ``from core.evaluator import _pull_caf_artifacts``.
# A module-level __getattr__ (PEP 562) resolves those names lazily, so the old
# import path keeps working without re-introducing the circular import.
_CAF_REEXPORTS = frozenset({
    "run_caf_ssh_evaluation",
    "_pull_caf_artifacts",
    "_telemetry_from_caf",
    "_parse_caf_run_id",
    "_caf_provider_flags",
})


def __getattr__(name: str):
    if name in _CAF_REEXPORTS:
        from core import caf_runner
        return getattr(caf_runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── Bash-Bot execution ────────────────────────────────────────────────────────

def run_bash_evaluation(env: BaseEnvironment, config: dict, on_log: Callable[[str], None]) -> dict:
    """Run startup → validation → completion commands in sequence (no LLM involved).

    startup and completion accept either a legacy List[str] or the step format:
        [{"delay_seconds": float, "commands": [{"command": str, "enabled": bool,
          "long_running": bool, "timeout_seconds": int}]}]

    validation_commands remains a flat List[str] (pass/fail semantics differ).
    """
    start_t    = time.time()
    timeout    = config.get("bash_timeout", 60)
    cancel_ref = config.get("cancel_requested_ref", [False])
    startup    = config.get("startup_commands", [])
    completion = config.get("completion_commands", [])
    val_cmds   = config.get("validation_commands", [])
    fail_pats  = config.get("fail_patterns", [])

    telemetry: dict = {
        "run_timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_bot_type":         "bash_bot",
        "tool_calls":           [],
        "validation_stdout":    "",
        "validation_stderr":    "",
        "validation_exit_code": None,
        "validation_passed":    None,
        "validation_results":   [],
        "run_aborted":          False,
        "metrics_matrix":       config.get("metrics_matrix", []),
    }

    _sudo_pw   = (config.get("sudo_password") or "").strip()
    _use_sudo  = bool(config.get("sudo"))
    if _use_sudo:
        mode = "via sudo bash -c (password provided)" if _sudo_pw else "via sudo (no password — ensure NOPASSWD)"
        on_log(f"[BASH] sudo access enabled — commands will run as root {mode}")
        
        # Preflight check for sudo auth
        _sudo_check_cmd = f"sudo -k; echo {shlex.quote(_sudo_pw)} | sudo -S -v" if _sudo_pw else "sudo -k; sudo -n -v"
        _check_res = env.execute(_sudo_check_cmd, timeout=10)
        if _check_res.get("exit_code", -1) != 0:
            err_msg = _check_res.get("stderr", "") or _check_res.get("stdout", "Unknown error")
            err_clean = err_msg.replace("sudo: ", "").strip().capitalize()
            on_log(f"[ERROR] Sudo authentication failed: {err_clean}")
            telemetry["run_aborted"] = True
            telemetry["error"] = f"Sudo authentication failed: {err_clean}"
            return telemetry
    on_log("[BASH] Starting bash evaluation")

    def _exec_cmd(cmd: str, label: str = "RUN", timeout_override: int | None = None) -> dict:
        t = timeout_override if timeout_override is not None else timeout
        if _use_sudo:
            if _sudo_pw:
                # Use sudo bash -c so we authenticate via stdin (no TTY required).
                # Log the original command only — never log the password.
                actual_cmd = f"echo {shlex.quote(_sudo_pw)} | sudo -S bash -c {shlex.quote(cmd)}"
            else:
                actual_cmd = f"sudo {cmd}"
        else:
            actual_cmd = cmd
        on_log(f"[{label}] {cmd}")
        res = env.execute(actual_cmd, timeout=t)
        if res.get("stdout"):
            on_log(f"[STDOUT] {res['stdout'][:800]}")
        if res.get("stderr"):
            on_log(f"[STDERR] {res['stderr'][:400]}")
        return res

    def _run_step_list(steps: list, label: str = "RUN") -> bool:
        """
        Execute a step-format or legacy string list.
        Returns True on normal completion, False if cancelled/aborted.
        Commands within each step run sequentially after the step's delay.
        """
        for step_idx, step in enumerate(steps):
            if cancel_ref[0]:
                on_log("[CANCEL] Cancelled by user")
                telemetry["run_aborted"] = True
                return False

            if isinstance(step, str):
                # Legacy format: bare string command
                cmd_text = step.strip()
                if not cmd_text:
                    continue
                res = _exec_cmd(cmd_text, label=label)
                telemetry["tool_calls"].append({
                    "tool":      "bash",
                    "args":      {"command": cmd_text},
                    "result":    res,
                    "exit_code": res.get("exit_code", -1),
                })
                continue

            # Step-format dict
            delay = float(step.get("delay_seconds", 0))
            if delay > 0:
                on_log(f"[DELAY] Step {step_idx + 1}: waiting {delay:.1f}s")
                time.sleep(delay)

            for cmd_obj in step.get("commands", []):
                if cancel_ref[0]:
                    on_log("[CANCEL] Cancelled by user")
                    telemetry["run_aborted"] = True
                    return False
                if not cmd_obj.get("enabled", True):
                    continue
                cmd_text = cmd_obj.get("command", "").strip()
                if not cmd_text:
                    continue
                t = (3600 if cmd_obj.get("long_running")
                     else int(cmd_obj.get("timeout_seconds", timeout)))
                res = _exec_cmd(cmd_text, label=label, timeout_override=t)
                telemetry["tool_calls"].append({
                    "tool":      "bash",
                    "args":      {"command": cmd_text},
                    "result":    res,
                    "exit_code": res.get("exit_code", -1),
                })
        return True

    # Startup commands
    _run_step_list(startup, label="RUN")

    # Validation
    val_sets = config.get("validation_sets", [])
    if not telemetry["run_aborted"] and val_sets:
        all_passed, set_results = _run_validation_sets(env, val_sets, on_log, cancel_ref)
        telemetry["validation_passed"] = all_passed
        telemetry["validation_sets_results"] = set_results
        # Backwards compatibility: populate stdout/stderr
        telemetry["validation_stdout"] = "\n".join(
            cmd_res.get("stdout", "") for vset in set_results for cmd_res in vset.get("steps", [])
        )
        telemetry["validation_stderr"] = "\n".join(
            cmd_res.get("stderr", "") for vset in set_results for cmd_res in vset.get("steps", [])
        )
        if set_results:
            last_set = set_results[-1]
            if last_set.get("steps"):
                telemetry["validation_exit_code"] = last_set["steps"][-1].get("exit_code")
    elif not telemetry["run_aborted"] and val_cmds:
        # Legacy fallback
        all_passed = True
        for cmd in val_cmds:
            if cancel_ref[0]:
                break
            val    = _run_validation(env, cmd, fail_pats)
            status = "PASS ✓" if val["passed"] else "FAIL ✗"
            on_log(f"[VALIDATE] {cmd!r}  →  {status}  (exit_code={val['exit_code']})")
            if not val["passed"]:
                all_passed = False
            telemetry["validation_results"].append({
                "cmd":       cmd,
                "passed":    val["passed"],
                "exit_code": val["exit_code"],
                "stdout":    val.get("stdout", ""),
                "stderr":    val.get("stderr", ""),
            })
            telemetry["validation_stdout"] += val.get("stdout", "")
            telemetry["validation_stderr"] += val.get("stderr", "")
            telemetry["validation_exit_code"] = val["exit_code"]
        telemetry["validation_passed"] = all_passed
    else:
        on_log("[VALIDATE] No validation configured")

    # Completion / cleanup commands
    if not cancel_ref[0]:
        _run_step_list(completion, label="CLEANUP")

    telemetry["total_latency"] = round(time.time() - start_t, 3)
    on_log(f"[COMPLETE] {telemetry['total_latency']}s elapsed")
    return telemetry


# ── Llama-CLI-Bot execution ────────────────────────────────────────────────────

def run_llama_cli_evaluation(env: BaseEnvironment, config: dict, on_log: Callable[[str, str], None]) -> dict:
    """Run prompts via llama-cli binary and/or shell commands, then validate."""
    import os
    import shlex
    start_t    = time.time()
    timeout    = config.get("timeout", 120)
    cancel_ref = config.get("cancel_requested_ref", [False])

    # Bug 2: compute a wall-clock deadline for the entire evaluation
    eval_deadline = start_t + timeout

    # Bug 3: read max_iter from metrics_matrix (defaults to 8 if not configured)
    max_iter = 8
    for m in config.get("metrics_matrix", []):
        if m.get("type") == "max_iterations":
            max_iter = int(m.get("params", {}).get("max_iter", 8))
            break
    backend    = config.get("backend", "llama.cpp")
    binary     = config.get("binary_path", "") or "llama-cli"
    model_dir     = config.get("model_dir", "")
    model_name    = config.get("model_name", "")
    tokens        = config.get("tokens", 32768)
    temperature   = config.get("temperature", 0.8)
    en_temp       = config.get("en_temp", False)
    gpu_layers    = config.get("gpu_layers", 99)
    en_gpu_layers = config.get("en_gpu_layers", False)
    threads       = config.get("threads", 4)
    en_threads    = config.get("en_threads", False)
    flash_attn    = config.get("flash_attn", False)
    en_top_k      = config.get("en_top_k", False)
    top_k         = config.get("top_k", 40)
    en_top_p      = config.get("en_top_p", False)
    top_p         = config.get("top_p", 0.9)
    en_min_p      = config.get("en_min_p", False)
    min_p         = config.get("min_p", 0.1)
    en_repeat_penalty = config.get("en_repeat_penalty", False)
    repeat_penalty = config.get("repeat_penalty", 1.1)
    en_freq_penalty = config.get("en_freq_penalty", False)
    freq_penalty   = config.get("freq_penalty", 0.0)
    en_predict    = config.get("en_predict", False)
    predict       = config.get("predict", 512)
    en_seed       = config.get("en_seed", False)
    seed          = config.get("seed", -1)
    en_rope_freq_base = config.get("en_rope_freq_base", False)
    rope_freq_base = config.get("rope_freq_base", 10000.0)
    en_rope_freq_scale = config.get("en_rope_freq_scale", False)
    rope_freq_scale = config.get("rope_freq_scale", 1.0)
    custom_flags  = config.get("custom_flags", "")
    _use_sudo  = bool(config.get("sudo"))
    _sudo_pw   = (config.get("sudo_password") or "").strip()
    sudo_pfx   = "sudo " if _use_sudo else ""
    prompts    = config.get("prompts", [])
    commands   = config.get("commands", [])
    startup    = config.get("startup_commands", [])
    completion = config.get("completion_commands", [])
    val_sets   = config.get("validation_sets", [])

    telemetry: dict = {
        "run_timestamp":        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_bot_type":         "llama_cli_bot",
        "run_backend":          backend,
        "run_model":            model_name,
        "tool_calls":           [],
        "prompt_responses":     [],
        "validation_stdout":    "",
        "validation_stderr":    "",
        "validation_exit_code": None,
        "validation_passed":    None,
        "run_aborted":          False,
        "interrupted_by_user":  False,
        "metrics_matrix":       config.get("metrics_matrix", []),
    }

    def _exec_cmd(cmd: str, label: str = "RUN", timeout_override: int = None) -> dict:
        t = timeout_override if timeout_override is not None else timeout
        if _use_sudo:
            if _sudo_pw:
                actual_cmd = f"echo {shlex.quote(_sudo_pw)} | sudo -S bash -c {shlex.quote(cmd)}"
            else:
                actual_cmd = f"sudo {cmd}"
        else:
            actual_cmd = cmd
        on_log(f"[{label}] {cmd}", "shell")
        res = env.execute(actual_cmd, timeout=t)
        if res.get("stdout"):
            on_log(f"[STDOUT] {res['stdout'][:800]}", "shell")
        if res.get("stderr"):
            on_log(f"[STDERR] {res['stderr'][:400]}", "shell")
        return res

    def _exec_llama_prompt(prompt_text: str, label: str = "PROMPT") -> dict:
        if not prompt_text:
            return {}
        
        nonlocal binary
        if backend.lower().startswith("openai"):
            base_url   = (config.get("openai_base_url") or "").strip()
            if not base_url:
                on_log("[ERROR] No Base URL configured for OpenAI backend.", "llama")
                return {"exit_code": 1, "stderr": "No Base URL"}
            
            on_log(f"[{label}] {prompt_text[:80]}...", "llama")
            try:
                tool_calls, response = _run_llm_agent_loop(
                    base_url, model_name, prompt_text, tools, tokens,
                    mcp_running, mcp_server_url, env, cancel_ref, lambda m: on_log(m, "llama"),
                    max_turns=max_iter, deadline=eval_deadline,
                )
                on_log(f"[RESPONSE] {response[:400]}", "llama")
                telemetry["prompt_responses"].append({"prompt": prompt_text, "response": response})
                telemetry["tool_calls"].extend(tool_calls)
                return {"stdout": response, "exit_code": 0}
            except Exception as exc:
                on_log(f"[ERROR] HTTP request failed: {exc}", "llama")
                return {"stdout": "", "stderr": str(exc), "exit_code": 1}
                
        else: # llama-cli
            if os.path.basename(binary) in ("llama-server", "llama-server.exe"):
                corrected = os.path.join(os.path.dirname(binary), "llama-cli")
                on_log(f"[WARN] Auto-correcting llama-server to llama-cli: {corrected}", "llama")
                binary = corrected

            model_path = os.path.join(model_dir, model_name) if model_dir and model_name else model_name
            if config.get("execution_target", "local") == "local":
                model_path = os.path.abspath(os.path.expanduser(model_path))
            if not model_path:
                on_log("[ERROR] No model selected.", "llama")
                return {"exit_code": 1}

            sys_prompt_parts = [
                "You are an autonomous AI agent. You MUST call the appropriate tool rather than describing steps.",
                "When you need to perform an action, respond ONLY with a tool call in this exact format (no other text):",
                '<tool_call>{"name": "tool_name", "arguments": {"arg1": "value1"}}</tool_call>',
                "",
                "Available tools:",
            ]
            for t in tools:
                fn = t["function"]
                props = fn.get("parameters", {}).get("properties", {})
                arg_names = ", ".join(props.keys())
                if arg_names:
                    sys_prompt_parts.append(f'  {fn["name"]}({arg_names}): {fn["description"]}')
                else:
                    sys_prompt_parts.append(f'  {fn["name"]}: {fn["description"]}')
            tool_sys_prompt = "\n".join(sys_prompt_parts) if tools else ""

            safe_prompt = shlex.quote(prompt_text)
            sys_flag = f" -sys {shlex.quote(tool_sys_prompt)}" if tool_sys_prompt else ""
            custom_flag_str = f" {custom_flags.strip()}" if custom_flags.strip() else ""
            model_path_quoted = f'\"$HOME/\"{shlex.quote(model_path[2:])}' if model_path.startswith("~/") else shlex.quote(model_path)
            
            cmd = (
                f"{binary}"
                f" -m {model_path_quoted}"
                f" -c {tokens}"
                f"{f' --temp {temperature}' if en_temp else ''}"
                f"{f' -ngl {gpu_layers}' if en_gpu_layers else ''}"
                f"{f' -t {threads}' if en_threads else ''}"
                f"{f' --top-k {top_k}' if en_top_k else ''}"
                f"{f' --top-p {top_p}' if en_top_p else ''}"
                f"{f' --min-p {min_p}' if en_min_p else ''}"
                f"{f' --repeat-penalty {repeat_penalty}' if en_repeat_penalty else ''}"
                f"{f' --freq-penalty {freq_penalty}' if en_freq_penalty else ''}"
                f"{f' -n {predict}' if en_predict else ' -n 512'}"
                f"{f' --seed {seed}' if en_seed else ''}"
                f"{f' --rope-freq-base {rope_freq_base}' if en_rope_freq_base else ''}"
                f"{f' --rope-freq-scale {rope_freq_scale}' if en_rope_freq_scale else ''}"
                f"{' -fa' if flash_attn else ''}"
                f"{sys_flag}"
                f"{custom_flag_str}"
                f" --prompt {safe_prompt}"
                f" --simple-io --no-display-prompt --single-turn"
            )
            if _use_sudo:
                if _sudo_pw:
                    cmd = f"echo {shlex.quote(_sudo_pw)} | sudo -S bash -c {shlex.quote(cmd)}"
                else:
                    cmd = f"sudo {cmd}"

            on_log(f"[{label}] {prompt_text[:80]}...", "llama")
            res = env.execute(cmd, timeout=timeout)
            
            if res.get("stderr"):
                on_log(f"[BACKEND] {res['stderr'][:600]}", "shell")
            if res.get("exit_code", 0) != 0 and not res.get("stdout", "").strip():
                on_log(f"[ERROR] llama-cli exited with code {res['exit_code']}", "llama")
                
            response = res.get("stdout", "").strip()
            on_log(f"[RESPONSE] {response[:400]}", "llama")
            telemetry["prompt_responses"].append({"prompt": prompt_text, "response": response})
            telemetry["tool_calls"].append({
                "tool": "llama-cli",
                "args": {"prompt": prompt_text},
                "result": res,
                "exit_code": res.get("exit_code", -1),
            })
            
            # Execute inline tool calls
            inline_calls = _parse_inline_tool_calls(response)
            for tc in inline_calls:
                fn   = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                on_log(f"[TOOL] Executing {name}({args})", "llama")
                result = _execute_tool(env, name, args, mcp_running, mcp_server_url)
                on_log(f"[TOOL RESULT] {str(result)[:300]}", "llama")
                telemetry["tool_calls"].append({
                    "tool": name,
                    "args": args,
                    "result": result,
                    "exit_code": result.get("exit_code", 0),
                })
            return res

    def _run_step_list(steps: list, label: str = "RUN") -> bool:
        for step_idx, step in enumerate(steps):
            if cancel_ref[0]:
                on_log("[CANCEL] Cancelled by user", "shell")
                telemetry["run_aborted"] = True
                return False

            if isinstance(step, str):
                cmd_text = step.strip()
                if not cmd_text:
                    continue
                res = _exec_cmd(cmd_text, label=label)
                telemetry["tool_calls"].append({
                    "tool":      "bash",
                    "args":      {"command": cmd_text},
                    "result":    res,
                    "exit_code": res.get("exit_code", -1),
                })
                continue

            delay = float(step.get("delay_seconds", 0))
            if delay > 0:
                on_log(f"[DELAY] Step {step_idx + 1}: waiting {delay:.1f}s", "shell")
                time.sleep(delay)

            for cmd_obj in step.get("commands", []):
                if cancel_ref[0]:
                    on_log("[CANCEL] Cancelled by user", "shell")
                    telemetry["run_aborted"] = True
                    return False
                if not cmd_obj.get("enabled", True):
                    continue
                
                cmd_type = cmd_obj.get("type", "command")
                if cmd_type == "prompt":
                    prompt_text = (cmd_obj.get("prompt", "") or cmd_obj.get("command", "")).strip()
                    if prompt_text:
                        _exec_llama_prompt(prompt_text, label="PROMPT")
                else:
                    cmd_text = cmd_obj.get("command", "").strip()
                    if not cmd_text:
                        continue
                    t = (3600 if cmd_obj.get("long_running") else int(cmd_obj.get("timeout_seconds", timeout)))
                    res = _exec_cmd(cmd_text, label=label, timeout_override=t)
                    telemetry["tool_calls"].append({
                        "tool":      "bash",
                        "args":      {"command": cmd_text},
                        "result":    res,
                        "exit_code": res.get("exit_code", -1),
                    })
        return True

    if _use_sudo:
        mode = "via sudo bash -c (password provided)" if _sudo_pw else "via sudo (no password — ensure NOPASSWD)"
        on_log(f"[LLAMA-CLI] sudo access enabled — commands will run as root {mode}", "shell")
        
        # Preflight check for sudo auth
        _sudo_check_cmd = f"sudo -k; echo {shlex.quote(_sudo_pw)} | sudo -S -v" if _sudo_pw else "sudo -k; sudo -n -v"
        _check_res = env.execute(_sudo_check_cmd, timeout=10)
        if _check_res.get("exit_code", -1) != 0:
            err_msg = _check_res.get("stderr", "") or _check_res.get("stdout", "Unknown error")
            err_clean = err_msg.replace("sudo: ", "").strip().capitalize()
            on_log(f"[ERROR] Sudo authentication failed: {err_clean}", "shell")
            telemetry["run_aborted"] = True
            telemetry["error"] = f"Sudo authentication failed: {err_clean}"
            return telemetry

    # If binary_path is a directory, assume llama-cli lives inside it
    if binary and os.path.isdir(binary):
        binary = os.path.join(binary, "llama-cli")

    mcp_servers    = config.get("mcp_servers", [])
    mcp_server_url = config.get("mcp_server_url", "http://127.0.0.1:9191")
    tools          = []
    mcp_running    = False

    # Load tool schemas for all backends
    if mcp_servers:
        try:
            tools = _load_tool_schemas("./mcp-server/index.js", on_log=lambda m: on_log(m, "shell"))
            if tools:
                names = [t["function"]["name"] for t in tools]
                on_log(f"[TOOLS] Loaded {len(tools)}: {', '.join(names[:5])}", "shell")
        except Exception as exc:
            on_log(f"[WARN] Could not load tool schemas: {exc}", "shell")

    # Probe for MCP broker using the session handshake — not a dummy tool call.
    # call_mcp_tool("dummy", {}) always returns {"error": ...} (unknown tool),
    # so the old probe could never set mcp_running = True. probe_mcp_server()
    # only checks whether the JSON-RPC initialize exchange succeeds.
    # Ensure MCP servers are running before executing steps that might contain prompts
    if tools:
        if probe_mcp_server(mcp_server_url):
            mcp_running = True
            on_log(f"[MCP] Broker detected at {mcp_server_url}", "shell")
        else:
            on_log(f"[WARN] MCP broker not responding at {mcp_server_url} — tool calls will use local fallbacks", "shell")

    # Validation — runs even if the run was cancelled/timed-out
    if val_sets:
        if telemetry["run_aborted"]:
            on_log("[WARN] Run was cancelled or timed out — validation still proceeding")
        all_passed, set_results = _run_validation_sets(env, val_sets, on_log, cancel_ref)
        telemetry["validation_passed"] = all_passed
        telemetry["validation_sets_results"] = set_results
        telemetry["validation_stdout"] = "\n".join(
            cmd_res.get("stdout", "") for vset in set_results for cmd_res in vset.get("steps", [])
        )
        telemetry["validation_stderr"] = "\n".join(
            cmd_res.get("stderr", "") for vset in set_results for cmd_res in vset.get("steps", [])
        )
        if set_results:
            last_set = set_results[-1]
            if last_set.get("steps"):
                telemetry["validation_exit_code"] = last_set["steps"][-1].get("exit_code")

    if not cancel_ref[0]:
        _run_step_list(completion, label="CLEANUP")

    telemetry["total_latency"] = round(time.time() - start_t, 3)
    on_log(f"[COMPLETE] {telemetry['total_latency']}s elapsed")
    return telemetry


# ── Main entry point ───────────────────────────────────────────────────────────

def run_evaluation(env: BaseEnvironment, config: dict, on_log: Callable[[str], None]) -> dict:
    """
    Execute the full LLM + tool-use evaluation loop.

    config keys:
        backend_type, llm_url, selected_model, context_size,
        sys_prompt, user_prompt, mcp_url, mcp_tools,
        validation_command, fail_patterns, mcp_running,
        active_scenario, cancel_requested_ref,
        caf_scope, caf_urgency, caf_allowed_subnets, caf_target_credentials,
        execution_mode  ("local" | "caf_ssh"; auto-detected from env when absent)

    Dispatch is driven by an explicit ``execution_mode``. When the caller does
    not set it, the mode is inferred once from the environment's
    ``is_remote_caf`` capability flag so legacy callers keep working — but the
    generic entry point no longer hard-codes a dependency on a specific
    environment subclass.
    """
    start_t    = time.time()

    mode = config.get("execution_mode") or (
        "caf_ssh" if getattr(env, "is_remote_caf", False) else "local"
    )

    # Bash-Bot mode: run shell commands directly, no LLM
    if mode == "bash":
        return run_bash_evaluation(env, config, on_log)

    # CAF SSH mode: delegate entire execution to the remote CAF CLI
    if mode == "caf_ssh":
        from core.caf_runner import run_caf_ssh_evaluation
        on_log(f"[INIT] SSH target: {env.username}@{env.host}  |  CAF dir: {env.remote_cwd}")
        telemetry = run_caf_ssh_evaluation(env, config, on_log)
        _run_ai_judge(telemetry, config, on_log)
        return telemetry

    backend    = config.get("backend_type", "llama.cpp")
    base_url   = config.get("llm_url", "").rstrip("/")
    model      = config.get("selected_model") or ""
    cancel_ref = config.get("cancel_requested_ref", [False])
    mcp_url    = config.get("mcp_url", "")

    telemetry = _init_telemetry(config)

    on_log(f"[INIT] Backend: {backend}  |  Model: {model or '(server default)'}")
    on_log(f"[INIT] URL: {base_url}  |  Context: {config.get('context_size', 4096)} tokens")
    _sys = config.get("sys_prompt", "")
    _usr = config.get("user_prompt", "")
    on_log(f"[SYS] {_sys[:200]}{'…' if len(_sys) > 200 else ''}")
    on_log(f"[USR] {_usr[:200]}{'…' if len(_usr) > 200 else ''}")

    # Tool schema loading with fallback (enabled_tools=None → load all)
    tool_schemas = _load_tool_schemas(mcp_url, config.get("mcp_tools", {}), on_log)
    if not tool_schemas and mcp_url:
        tool_schemas = _load_tool_schemas(mcp_url, on_log=on_log)
        if tool_schemas:
            on_log(f"[TOOLS] mcp_tools was empty — auto-loaded {len(tool_schemas)} tool(s) from tools.json")
    if tool_schemas:
        names = [t["function"]["name"] for t in tool_schemas]
        on_log(f"[TOOLS] {len(tool_schemas)} tool(s) active: {', '.join(names)}")
    else:
        on_log("[TOOLS] No tools loaded — running without tool use")

    # Pre-run cleanup
    for cleanup_path in config.get("pre_run_cleanup", []):
        try:
            if env.delete_file(cleanup_path):
                on_log(f"[CLEANUP] Removed: {cleanup_path}")
        except Exception as e:
            on_log(f"[WARN] Cleanup failed for {cleanup_path}: {e}")

    messages: list[dict] = [
        {"role": "system", "content": config.get("sys_prompt", "")},
        {"role": "user",   "content": config.get("user_prompt", "")},
    ]

    for round_num in range(8):
        if cancel_ref[0]:
            on_log("[CANCEL] Evaluation cancelled by user")
            telemetry["run_aborted"] = True
            break

        on_log(f"[LLM] Agent turn {round_num + 1} — sending to {backend}…")
        try:
            resp = _call_llm(
                backend, base_url, model, messages,
                tool_schemas, config.get("context_size", 4096), on_log,
            )
        except requests.exceptions.ConnectionError:
            on_log(f"[ERROR] Cannot connect to {backend} at {base_url} — is the server running?")
            telemetry["run_aborted"] = True
            break
        except requests.exceptions.HTTPError as e:
            on_log(f"[ERROR] HTTP {e.response.status_code}: {e.response.text[:300]}")
            telemetry["run_aborted"] = True
            break
        except Exception as e:
            on_log(f"[ERROR] {e}")
            telemetry["run_aborted"] = True
            break

        telemetry["llm_rounds"] += 1
        usage = resp.get("usage", {})
        telemetry["prompt_tokens"]     += usage.get("prompt_tokens", 0)
        telemetry["completion_tokens"] += usage.get("completion_tokens", 0)
        total_so_far = telemetry["prompt_tokens"] + telemetry["completion_tokens"]
        ctx_size     = config.get("context_size", 4096)
        on_log(
            f"[TOKENS] {total_so_far}/{ctx_size} ctx  "
            f"(prompt {telemetry['prompt_tokens']} + "
            f"completion {telemetry['completion_tokens']})"
        )

        msg            = resp.get("message", {})
        content: str   = msg.get("content") or ""
        tool_calls_raw = msg.get("tool_calls") or []

        # Fallback: parse inline <tool_call> tags (SmolLM2, Qwen, etc.)
        if not tool_calls_raw and content and "<tool_call>" in content:
            tool_calls_raw = _parse_inline_tool_calls(content)
            if tool_calls_raw:
                names = ", ".join(tc["function"]["name"] for tc in tool_calls_raw)
                on_log(f"[TOOLS] Parsed {len(tool_calls_raw)} inline tool call(s): {names}")

        if content:
            clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
            clean = re.sub(r'<tool_call>.*?</tool_call>', '', clean, flags=re.DOTALL).strip()
            display = re.sub(r'\n{3,}', '\n\n', clean or content)
            on_log(f"[RESPONSE] {display[:500]}{'…' if len(display) > 500 else ''}")
            telemetry["llm_response"] = clean or content

        if not tool_calls_raw:
            on_log("[DONE] LLM gave final answer — no further tool calls")
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls_raw})
        if _process_tool_calls(env, tool_calls_raw, messages, telemetry, config, on_log, cancel_ref):
            telemetry["run_aborted"] = True
            break

    _finalize_telemetry(telemetry, start_t)
    telemetry["inefficiencies"] = _check_inefficiencies(telemetry["tool_calls"])
    for issue in telemetry["inefficiencies"]:
        on_log(f"[WARN] Inefficiency: {issue}")

    val_cmd      = config.get("validation_command", "")
    run_aborted  = telemetry["run_aborted"]
    had_activity = telemetry["llm_rounds"] > 0 or bool(telemetry["tool_calls"])

    if val_cmd and (not run_aborted or had_activity):
        on_log(f"[VALIDATE] Running: {val_cmd}")
        val = _run_validation(
            env, val_cmd,
            config.get("fail_patterns", []),
            config.get("expected_stdout", ""),
        )
        telemetry.update({
            "validation_stdout":    val["stdout"],
            "validation_stderr":    val["stderr"],
            "validation_exit_code": val["exit_code"],
            "validation_passed":    val["passed"],
        })
        status = "PASS ✓" if val["passed"] else "FAIL ✗"
        on_log(f"[VALIDATE] {status}  (exit_code={val['exit_code']})")
        if val["stdout"]:
            on_log(f"[VALIDATE OUTPUT]\n{val['stdout'].strip()[:600]}")
    elif run_aborted and not had_activity:
        on_log("[VALIDATE] Skipped — run aborted before LLM responded")
        telemetry["validation_passed"] = None
    else:
        on_log("[VALIDATE] No validation command configured")

    _run_ai_judge(telemetry, config, on_log)

    if run_aborted:
        on_log(f"[ABORTED] Run aborted  |  {telemetry['total_latency']}s elapsed")
    else:
        on_log(
            f"[COMPLETE] {telemetry['total_latency']}s  |  "
            f"{telemetry['total_tokens']} tokens  |  "
            f"{len(telemetry['tool_calls'])} tool call(s)"
        )
    return telemetry
