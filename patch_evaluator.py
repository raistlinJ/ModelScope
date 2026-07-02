import re

with open("core/evaluator.py", "r") as f:
    content = f.read()

# 1. Add extraction
content = content.replace(
    '    val_sets   = config.get("validation_sets", [])',
    '    startup    = config.get("startup_commands", [])\n    completion = config.get("completion_commands", [])\n    val_sets   = config.get("validation_sets", [])'
)

# 2. Add helpers after telemetry initialization
insertion = """

    def _exec_cmd(cmd: str, label: str = "RUN", timeout_override: int = None) -> dict:
        t = timeout_override if timeout_override is not None else timeout
        if _use_sudo:
            if _sudo_pw:
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
        for step_idx, step in enumerate(steps):
            if cancel_ref[0]:
                on_log("[CANCEL] Cancelled by user")
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

    if _use_sudo:"""

content = content.replace(
    '    }\n\n    if _use_sudo:',
    '    }' + insertion
)

# 3. Add startup execution
content = content.replace(
    '    if tools:\n        if probe_mcp_server(mcp_server_url):',
    '    _run_step_list(startup, label="STARTUP")\n\n    if tools:\n        if probe_mcp_server(mcp_server_url):'
)

# 4. Add completion execution
content = content.replace(
    '    telemetry["total_latency"] = round(time.time() - start_t, 3)',
    '    if not cancel_ref[0]:\n        _run_step_list(completion, label="CLEANUP")\n\n    telemetry["total_latency"] = round(time.time() - start_t, 3)'
)

with open("core/evaluator.py", "w") as f:
    f.write(content)
