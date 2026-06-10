import json
import os
import re
import time
from datetime import datetime
from typing import Callable

import requests

from config.defaults import MCP_SERVER_BASE_URL
from core.mcp_manager import call_mcp_tool
from core.environment import BaseEnvironment


# ── Tool schema loading ────────────────────────────────────────────────────────

def _load_tool_schemas(
    mcp_script_path: str,
    enabled_tools: dict,
    on_log: Callable[[str], None] | None = None,
) -> list[dict]:
    """
    Read tools.json and return OpenAI-format schemas for every enabled tool.
    Falls back gracefully with logged errors instead of silent empty returns. (fix #8)
    """
    tools_file = os.path.join(os.path.dirname(mcp_script_path), "tools.json")
    if not os.path.exists(tools_file):
        if on_log:
            on_log(f"[WARN] tools.json not found at {tools_file}")
        return []
    try:
        with open(tools_file) as fh:
            data = json.load(fh)
        schemas = []
        for tool in data:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", "")
            if enabled_tools.get(name):
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "inputSchema", {"type": "object", "properties": {}}
                        ),
                    },
                })
        return schemas
    except Exception as exc:
        if on_log:
            on_log(f"[WARN] Could not load tool schemas: {exc}")
        return []


# ── Fallback: load all tools from tools.json (when mcp_tools state is empty) ──

def _load_all_tool_schemas(mcp_script_path: str) -> list[dict]:
    """Return schemas for ALL tools in tools.json, used as a fallback. (fix #1)"""
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
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get(
                        "inputSchema", {"type": "object", "properties": {}}
                    ),
                },
            }
            for t in data if isinstance(t, dict) and t.get("name")
        ]
    except Exception:
        return []


# ── Tool execution in environment ─────────────────────────────────────────────

def _execute_tool_in_env(env: BaseEnvironment, tool_name: str, tool_args: dict) -> dict:
    if tool_name == "file_creator":
        path    = tool_args.get("path", "")
        content = tool_args.get("content", "")
        return env.write_file(path, content)

    if tool_name == "run_nmap_scan":
        target    = tool_args.get("target", "127.0.0.1")
        arguments = tool_args.get("arguments", "-F")
        if any(c in target + arguments for c in (";", "&", "|", "`", "$", ">")):
            return {"error": "Disallowed characters in arguments"}
        
        return env.execute(f"nmap {arguments} {target}", timeout=30)

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


# ── LLM API calls — with streaming support ────────────────────────────────────

def _flush_buf(buf: str, in_think: bool, on_log: Callable) -> str:
    """Flush a token buffer with the correct [THINKING] / [LLM] prefix."""
    if buf.strip():
        on_log(f"{'[THINKING]' if in_think else '[LLM]'} {buf.rstrip()}")
    return ""


def _process_think_tags(token: str, buf: str, in_think: bool, on_log: Callable) -> tuple[str, bool]:
    """
    Process `token` against the running `buf`, emitting log lines on
    <think> / </think> boundaries. Returns (new_buf, new_in_think).
    """
    buf += token
    while True:
        tag = "<think>" if not in_think else "</think>"
        idx = buf.find(tag)
        if idx < 0:
            break
        before = buf[:idx]
        _flush_buf(before, in_think, on_log)
        buf      = buf[idx + len(tag):]
        in_think = not in_think
    return buf, in_think


def _stream_ollama(
    base_url: str, model: str, messages: list,
    tools: list, context_size: int, on_log: Callable,
) -> dict:
    """
    Streaming call to Ollama /api/chat.
    Emits [THINKING] for <think>...</think> content, [LLM] for response tokens.
    """
    payload: dict = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"num_ctx": context_size},
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        base_url.rstrip("/") + "/api/chat", json=payload,
        stream=True, timeout=300,
    )
    resp.raise_for_status()

    accumulated = ""
    usage: dict = {}
    msg: dict = {}
    buf      = ""
    in_think = False

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        try:
            chunk = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        token = chunk.get("message", {}).get("content", "")
        accumulated += token
        buf, in_think = _process_think_tags(token, buf, in_think, on_log)

        # Periodic flush: every 80 chars or on newline
        if len(buf) >= 80 or "\n" in buf:
            buf = _flush_buf(buf, in_think, on_log)

        if chunk.get("done"):
            buf = _flush_buf(buf, in_think, on_log)
            for tc in chunk.get("message", {}).get("tool_calls") or []:
                fn_name = tc.get("function", {}).get("name", "")
                if fn_name:
                    on_log(f"[THINKING] → selecting tool: {fn_name}")
            usage = {
                "prompt_tokens":     chunk.get("prompt_eval_count", 0),
                "completion_tokens": chunk.get("eval_count", 0),
            }
            msg = chunk.get("message", {})
            msg["content"] = accumulated
            break

    # Normalize tool_call arguments to JSON strings
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        if isinstance(fn.get("arguments"), dict):
            fn["arguments"] = json.dumps(fn["arguments"])

    return {"message": msg, "usage": usage}


def _stream_llama_cpp(
    base_url: str, model: str, messages: list,
    tools: list, context_size: int, on_log: Callable,
) -> dict:
    """
    Streaming call to llama.cpp /v1/chat/completions (OpenAI SSE format).
    Emits [THINKING] for <think>...</think> content, [LLM] for response tokens.
    """
    payload: dict = {
        "messages":       messages,
        "stream":         True,
        "n_ctx":          context_size,
        "stream_options": {"include_usage": True},
    }
    if model:
        payload["model"] = model
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        base_url.rstrip("/") + "/v1/chat/completions", json=payload,
        stream=True, timeout=300,
    )
    resp.raise_for_status()

    accumulated    = ""
    tool_calls_raw: list = []
    usage: dict    = {}
    buf            = ""
    in_think       = False

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        payload_str = line[6:].strip()
        if payload_str == "[DONE]":
            break
        try:
            chunk = json.loads(payload_str)
        except json.JSONDecodeError:
            continue

        choice = (chunk.get("choices") or [{}])[0]
        delta  = choice.get("delta", {})

        token = delta.get("content") or ""
        accumulated += token
        buf, in_think = _process_think_tags(token, buf, in_think, on_log)

        # Periodic flush: every 80 chars or on newline
        if len(buf) >= 80 or "\n" in buf:
            buf = _flush_buf(buf, in_think, on_log)

        # Accumulate tool calls (may arrive as incremental deltas)
        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta.get("index", 0)
            while len(tool_calls_raw) <= idx:
                tool_calls_raw.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            tc = tool_calls_raw[idx]
            fn_name = tc_delta.get("function", {}).get("name", "")
            tc["id"]                       += tc_delta.get("id", "")
            tc["function"]["name"]         += fn_name
            tc["function"]["arguments"]    += tc_delta.get("function", {}).get("arguments", "")
            if fn_name:
                on_log(f"[THINKING] → tool: {tc['function']['name']}")

        if chunk.get("usage"):
            usage = chunk["usage"]

    # Flush remaining buffer
    _flush_buf(buf, in_think, on_log)

    msg: dict = {"role": "assistant", "content": accumulated}
    if tool_calls_raw:
        msg["tool_calls"] = [tc for tc in tool_calls_raw if tc["function"]["name"]]

    return {
        "message": msg,
        "usage": {
            "prompt_tokens":     usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        },
    }


# ── Validation ─────────────────────────────────────────────────────────────────

def _run_validation(
    env: BaseEnvironment,
    command: str,
    fail_patterns: list[str],
    expected_stdout: str = "",
) -> dict:
    if not command.strip():
        return {"stdout": "", "stderr": "", "exit_code": None, "passed": None}
    
    res = env.execute(command, timeout=15)
    combined    = (res["stdout"] + res["stderr"]).lower()
    pattern_hit = any(p.lower() in combined for p in fail_patterns if p)
    passed      = res["exit_code"] == 0 and not pattern_hit
    # If an expected stdout is defined, the actual output must match it exactly
    if passed and expected_stdout.strip():
        passed = res["stdout"].strip() == expected_stdout.strip()
    return {
        "stdout":    res["stdout"],
        "stderr":    res["stderr"],
        "exit_code": res["exit_code"],
        "passed":    passed,
    }


def _parse_inline_tool_calls(content: str) -> list[dict]:
    """
    Fallback parser for models (e.g. SmolLM2) that output tool calls as text
    inside <tool_call>...</tool_call> tags instead of using the OpenAI function-
    calling format.  Handles both array  [{"name":…}]  and single-object formats.
    """
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


def _check_inefficiencies(tool_calls: list[dict]) -> list[str]:
    seen: dict[tuple, int] = {}
    issues = []
    for call in tool_calls:
        key = (call.get("tool"), json.dumps(call.get("args", {}), sort_keys=True))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] == 2:
            issues.append(f"Repeated call: {call['tool']} with identical arguments")
    return issues


# ── Main entry point ───────────────────────────────────────────────────────────

def run_evaluation(env: BaseEnvironment, config: dict, on_log: Callable[[str], None]) -> dict:
    """
    Execute the full LLM + tool-use evaluation loop.

    config keys:
        backend_type, llm_url, selected_model, context_size,
        sys_prompt, user_prompt, mcp_url, mcp_tools,
        validation_command, fail_patterns, mcp_running,
        active_scenario, cancel_requested_ref
    """
    telemetry: dict = {
        # Run metadata (fix #19)
        "run_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_scenario":  config.get("active_scenario", ""),
        "run_model":     config.get("selected_model") or "(server default)",
        "run_backend":   config.get("backend_type", "llama.cpp"),
        "run_tool_focus": config.get("tool_focus", ""),
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
        "validation_stdout":  "",
        "validation_stderr":  "",
        "validation_exit_code": None,
        "validation_passed":  None,
        # Quality
        "inefficiencies":  [],
        "llm_response":    "",
        # Abort flag (fix #2)
        "run_aborted":     False,
        # Metrics used for this run (stored so dashboard shows correct metrics)
        "metrics_matrix":  config.get("metrics_matrix", []),
    }

    start_t      = time.time()
    backend      = config.get("backend_type", "llama.cpp")
    base_url     = config.get("llm_url", "").rstrip("/")
    model        = config.get("selected_model") or ""
    mcp_url      = config.get("mcp_url", "")
    enabled_tools= config.get("mcp_tools", {})
    fail_patterns= config.get("fail_patterns", [])
    mcp_running  = config.get("mcp_running", False)
    mcp_server_url = config.get("mcp_server_url", MCP_SERVER_BASE_URL)
    cancel_ref   = config.get("cancel_requested_ref", [False])

    on_log(f"[INIT] Backend: {backend}  |  Model: {model or '(server default)'}")
    on_log(f"[INIT] URL: {base_url}  |  Context: {config.get('context_size', 4096)} tokens")
    # Log prompts so user can see them in the live terminal
    _sys = config.get("sys_prompt", "")
    _usr = config.get("user_prompt", "")
    on_log(f"[SYS] {_sys[:200]}{'…' if len(_sys) > 200 else ''}")
    on_log(f"[USR] {_usr[:200]}{'…' if len(_usr) > 200 else ''}")

    # ── Tool schema loading with fallback (fix #1) ─────────────────────────────
    tool_schemas = _load_tool_schemas(mcp_url, enabled_tools, on_log)
    if not tool_schemas and mcp_url:
        # mcp_tools might be empty (user never clicked Fetch Tools) — auto-load all
        tool_schemas = _load_all_tool_schemas(mcp_url)
        if tool_schemas:
            on_log(
                f"[TOOLS] mcp_tools was empty — auto-loaded {len(tool_schemas)} "
                f"tool(s) from tools.json"
            )
    if tool_schemas:
        names = [t["function"]["name"] for t in tool_schemas]
        on_log(f"[TOOLS] {len(tool_schemas)} tool(s) active: {', '.join(names)}")
    else:
        on_log("[TOOLS] No tools loaded — running without tool use")

    # Pre-run file cleanup (allows re-testing file creation scenarios)
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

    max_rounds = 8
    for round_num in range(max_rounds):
        # Check cancel flag (fix #16)
        if cancel_ref[0]:
            on_log("[CANCEL] Evaluation cancelled by user")
            telemetry["run_aborted"] = True
            break

        on_log(f"[LLM] Agent turn {round_num + 1} — sending to {backend}…")
        try:
            if backend == "ollama":
                resp = _stream_ollama(
                    base_url, model, messages, tool_schemas,
                    config.get("context_size", 4096), on_log,
                )
            else:
                resp = _stream_llama_cpp(
                    base_url, model, messages, tool_schemas,
                    config.get("context_size", 4096), on_log,
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

        # ── Fallback: parse inline <tool_call> tags (SmolLM2, Qwen, etc.) ───────
        if not tool_calls_raw and content and "<tool_call>" in content:
            tool_calls_raw = _parse_inline_tool_calls(content)
            if tool_calls_raw:
                names = ", ".join(tc["function"]["name"] for tc in tool_calls_raw)
                on_log(f"[TOOLS] Parsed {len(tool_calls_raw)} inline tool call(s): {names}")

        # Display: strip <think> and <tool_call> tags, leaving only prose
        if content:
            clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
            clean = re.sub(r'<tool_call>.*?</tool_call>', '', clean, flags=re.DOTALL).strip()
            display = clean if clean else content
            # Collapse excessive blank lines for cleaner terminal display
            display_compact = re.sub(r'\n{3,}', '\n\n', display)
            snippet = display_compact[:500] + ("…" if len(display_compact) > 500 else "")
            on_log(f"[RESPONSE] {snippet}")
            telemetry["llm_response"] = display

        if not tool_calls_raw:
            on_log("[DONE] LLM gave final answer — no further tool calls")
            break

        messages.append({"role": "assistant", "content": content, "tool_calls": tool_calls_raw})

        for tc in tool_calls_raw:
            if cancel_ref[0]:
                on_log("[CANCEL] Tool execution cancelled")
                telemetry["run_aborted"] = True
                break

            fn        = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                tool_args = json.loads(fn.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            on_log(f"[TOOL CALL] {tool_name}({json.dumps(tool_args)})")
            t0      = time.time()
            result  = _execute_tool(
                env, tool_name, tool_args, mcp_running, mcp_server_url
            )
            elapsed = round(time.time() - t0, 3)

            on_log(f"[TOOL RESULT] {tool_name} → {str(result)[:300]}  ({elapsed}s)")
            telemetry["tool_calls"].append({
                "tool":      tool_name,
                "args":      tool_args,
                "result":    result,
                "runtime":   elapsed,
                "exit_code": 0 if "error" not in result else 1,
            })
            messages.append({
                "role":         "tool",
                "tool_call_id": tc.get("id", "call_0"),
                "content":      json.dumps(result),
            })

        if telemetry["run_aborted"]:
            break

    # Finalize timing
    telemetry["total_latency"] = round(time.time() - start_t, 3)
    telemetry["total_tokens"]  = telemetry["prompt_tokens"] + telemetry["completion_tokens"]
    if telemetry["total_latency"] > 0 and telemetry["completion_tokens"] > 0:
        telemetry["tokens_per_second"] = round(
            telemetry["completion_tokens"] / telemetry["total_latency"], 1
        )

    telemetry["inefficiencies"] = _check_inefficiencies(telemetry["tool_calls"])
    for issue in telemetry["inefficiencies"]:
        on_log(f"[WARN] Inefficiency: {issue}")

    # Validation — skip if run was aborted with no tool activity (fix #2)
    val_cmd     = config.get("validation_command", "")
    run_aborted = telemetry["run_aborted"]
    had_activity= telemetry["llm_rounds"] > 0 or bool(telemetry["tool_calls"])

    if val_cmd and (not run_aborted or had_activity):
        on_log(f"[VALIDATE] Running: {val_cmd}")
        val = _run_validation(env, val_cmd, fail_patterns, config.get("expected_stdout", ""))
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
