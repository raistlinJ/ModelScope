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


# ── ANSI helper ────────────────────────────────────────────────────────────────

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r'\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])', '', text)


# ── Tool schema loading ────────────────────────────────────────────────────────

def _load_tool_schemas(
    mcp_script_path: str,
    enabled_tools: dict,
    on_log: Callable[[str], None] | None = None,
) -> list[dict]:
    """Read tools.json and return OpenAI-format schemas for every enabled tool."""
    tools_file = os.path.join(os.path.dirname(mcp_script_path), "tools.json")
    if not os.path.exists(tools_file):
        if on_log:
            on_log(f"[WARN] tools.json not found at {tools_file}")
        return []
    try:
        with open(tools_file) as fh:
            data = json.load(fh)
        return [
            {
                "type": "function",
                "function": {
                    "name":        tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters":  tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            for tool in data
            if isinstance(tool, dict) and enabled_tools.get(tool.get("name", ""))
        ]
    except Exception as exc:
        if on_log:
            on_log(f"[WARN] Could not load tool schemas: {exc}")
        return []


def _load_all_tool_schemas(mcp_script_path: str) -> list[dict]:
    """Return schemas for ALL tools in tools.json (fallback when mcp_tools is empty)."""
    tools_file = os.path.join(os.path.dirname(mcp_script_path), "tools.json")
    if not os.path.exists(tools_file):
        return []
    try:
        with open(tools_file) as fh:
            data = json.load(fh)
        return [
            {
                "type": "function",
                "function": {
                    "name":        t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters":  t.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            for t in data if isinstance(t, dict) and t.get("name")
        ]
    except Exception:
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


# ── CAF SSH execution ──────────────────────────────────────────────────────────

def _caf_provider_flags(config: dict) -> str:
    """Map ModelScope backend config to CAF CLI provider flags."""
    backend = config.get("backend_type", "llama.cpp")
    url     = shlex.quote(config.get("llm_url", "").rstrip("/"))
    if backend == "ollama":
        return f"--provider ollama_direct --url {url}"
    # llama.cpp serves an OpenAI-compatible API
    return f"--provider openai --url {url}"


def _parse_caf_run_id(output: str) -> str | None:
    """Extract run_id from CAF CLI output line: '[run] Transcript: runs/<id>/transcript.md'"""
    m = re.search(r'\[run\]\s+Transcript:\s+runs/([^/\s]+)/', output)
    return m.group(1) if m else None


def _pull_caf_artifacts(
    env,
    run_id: str,
    local_dest: str,
    on_log,
) -> dict:
    """
    Pull transcript.md, metadata.json, and tool_calls/*.json from the remote
    runs/<run_id>/ directory back to local_dest.
    Returns parsed metadata dict.
    """
    import pathlib
    dest = pathlib.Path(local_dest) / run_id
    dest.mkdir(parents=True, exist_ok=True)

    metadata: dict = {}

    # Diagnostic: confirm what actually lives in the remote run directory before
    # attempting individual file pulls. This catches two known failure modes:
    # (a) the path doesn't exist at all (CAF wrote elsewhere), or
    # (b) the shell cwd and SFTP cwd disagree (tilde-expansion mismatch in
    #     SSHEnvironment.connect() where sftp.chdir("~/...") is a no-op).
    try:
        ls_result = env.execute(f"ls runs/{run_id}/ 2>/dev/null || echo 'DIR_NOT_FOUND'", timeout=10)
        ls_out = _strip_ansi(ls_result.get("stdout", "")).strip()
        if ls_out == "DIR_NOT_FOUND" or not ls_out:
            # Directory absent under remote_cwd — search the whole home tree
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

    # Pull tool_calls/*.json
    tools_dir = dest / "tool_calls"
    tools_dir.mkdir(exist_ok=True)
    try:
        ls_result = env.execute(f"ls runs/{run_id}/tool_calls/ 2>/dev/null", timeout=10)
        for fname in ls_result.get("stdout", "").split():
            if not fname.endswith(".json"):
                continue
            remote_path = f"runs/{run_id}/tool_calls/{fname}"
            try:
                content = env.read_file(remote_path)
                (tools_dir / fname).write_text(content, encoding="utf-8")
            except Exception:
                pass
        on_log(f"[CAF] Pulled tool_calls/")
    except Exception as exc:
        on_log(f"[WARN] Could not list tool_calls: {exc}")

    return metadata


def _telemetry_from_caf(
    metadata: dict,
    local_run_dir: str,
    run_id: str,
    start_t: float,
    config: dict,
    val_result: dict,
) -> dict:
    """Build a ModelScope telemetry dict from pulled CAF artifacts."""
    import pathlib

    telemetry = _init_telemetry(config)
    telemetry["run_timestamp"]   = metadata.get("start_time", telemetry["run_timestamp"])
    telemetry["run_model"]       = metadata.get("model", config.get("selected_model") or "(server default)")
    telemetry["total_latency"]   = round(time.time() - start_t, 3)
    telemetry["run_aborted"]     = metadata.get("status", "completed") not in ("completed",)
    telemetry["llm_rounds"]      = 1

    # Parse tool calls from pulled tool_calls/*.json
    tools_path = pathlib.Path(local_run_dir) / run_id / "tool_calls"
    caf_tool_calls: list[dict] = []
    caf_trajectory: list[dict] = []
    context_window = int(metadata.get("context_window", 8192))
    if tools_path.exists():
        sorted_files = sorted(tools_path.glob("*.json"))
        for seq_idx, tc_file in enumerate(sorted_files):
            try:
                tc = json.loads(tc_file.read_text(encoding="utf-8"))
                tool_name = tc.get("tool", "")
                exit_code = tc.get("exit_code", 0)
                result_text = tc.get("result", "")
                duration_ms = tc.get("duration_ms", 0)
                result_dict = {"stdout": result_text, "exit_code": exit_code,
                               "stderr": tc.get("stderr", "")}
                caf_tool_calls.append({
                    "tool":      tool_name,
                    "args":      tc.get("args", {}),
                    "result":    result_dict,
                    "runtime":   round(duration_ms / 1000, 3),
                    "exit_code": exit_code,
                })
                # Build trajectory steps with TDI dimensions
                tdi, e, c, s, ev_conf = _calculate_step_tdi(
                    tool=tool_name,
                    result=result_dict,
                    tokens=0,                   # CAF doesn't log token counts
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

    # Read transcript for llm_response
    transcript_path = pathlib.Path(local_run_dir) / run_id / "transcript.md"
    if transcript_path.exists():
        telemetry["llm_response"] = transcript_path.read_text(encoding="utf-8")[:2000]

    # Validation
    telemetry.update({
        "validation_stdout":    val_result.get("stdout", ""),
        "validation_stderr":    val_result.get("stderr", ""),
        "validation_exit_code": val_result.get("exit_code"),
        "validation_passed":    val_result.get("passed"),
    })

    return telemetry


def run_caf_ssh_evaluation(
    env,
    config: dict,
    on_log,
    local_run_history_dir: str = "/tmp/modelscope_caf_runs",
) -> dict:
    """
    Execute a CAF benchmark on a remote Kali machine via SSH.
    Fires ./start_cli.sh run, pulls artifacts, builds telemetry.
    """
    start_t = time.time()

    model   = shlex.quote(config.get("selected_model") or "")
    # shlex.quote handles every shell-significant character in the prompt,
    # including the single quotes the previous hand-rolled escaping covered.
    prompt  = shlex.quote(config.get("user_prompt", ""))
    scope   = shlex.quote(config.get("caf_scope", "Narrow").lower())
    # Map UI enum → CAF CLI value.  CAF accepts "stealthy" (with y); the UI
    # shows the cleaner "Stealth" label.  All other values pass through as-is.
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

    result = env.execute(cmd, timeout=600)
    combined_output = _strip_ansi(result["stdout"] + result["stderr"])

    on_log(f"[CAF] Exit code: {result['exit_code']}")
    if result["stdout"]:
        on_log(f"[CAF OUTPUT]\n{_strip_ansi(result['stdout'])[:2000]}")
    if result["stderr"]:
        on_log(f"[CAF STDERR]\n{_strip_ansi(result['stderr'])[:500]}")

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

    # Validation command (run remotely via env)
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
        metadata, local_run_history_dir, run_id, start_t, config, val_result
    )

    on_log(
        f"[COMPLETE] {telemetry['total_latency']}s  |  "
        f"{len(telemetry['tool_calls'])} tool call(s)  |  "
        f"run_id={run_id}"
    )
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
        "caf_ssh" if getattr(env, "is_remote_caf", False) is True else "local"
    )

    # CAF SSH mode: delegate entire execution to the remote CAF CLI
    if mode == "caf_ssh":
        on_log(f"[INIT] SSH target: {env.username}@{env.host}  |  CAF dir: {env.remote_cwd}")
        return run_caf_ssh_evaluation(env, config, on_log)

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

    # Tool schema loading with fallback
    tool_schemas = _load_tool_schemas(mcp_url, config.get("mcp_tools", {}), on_log)
    if not tool_schemas and mcp_url:
        tool_schemas = _load_all_tool_schemas(mcp_url)
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

    if run_aborted:
        on_log(f"[ABORTED] Run aborted  |  {telemetry['total_latency']}s elapsed")
    else:
        on_log(
            f"[COMPLETE] {telemetry['total_latency']}s  |  "
            f"{telemetry['total_tokens']} tokens  |  "
            f"{len(telemetry['tool_calls'])} tool call(s)"
        )
    return telemetry
