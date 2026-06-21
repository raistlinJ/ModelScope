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
import time
from datetime import datetime
from typing import Callable

import requests

from config.defaults import MCP_SERVER_BASE_URL
from core.caf_state import StepTelemetry, infer_phase, score_evidence_confidence
from core.environment import BaseEnvironment
from core.mcp_manager import call_mcp_tool
from core.streaming import stream_ollama, stream_llama_cpp
from core.utils import strip_ansi as _strip_ansi  # noqa: F401 — re-exported for callers/tests


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
    fail_patterns: list[str],
    expected_stdout: str = "",
) -> dict:
    if not command.strip():
        return {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
    res         = env.execute(command, timeout=15)
    combined    = (res["stdout"] + res["stderr"]).lower()
    pattern_hit = any(p.lower() in combined for p in fail_patterns if p)
    passed      = res["exit_code"] == 0 and not pattern_hit
    if passed and expected_stdout.strip():
        passed = res["stdout"].strip() == expected_stdout.strip()
    return {
        "stdout":    res["stdout"],
        "stderr":    res["stderr"],
        "exit_code": res["exit_code"],
        "passed":    passed,
    }


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
