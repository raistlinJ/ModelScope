"""CAF CLI Run bot plugin."""

from __future__ import annotations

import json
import os
import pathlib
import queue
import re
import shlex
import subprocess
import threading
import time
import copy
from typing import Any, Callable, Mapping

from core.bot_types.base import (
    COMMON_DASHBOARD_METRIC_SPECS,
    LLM_HELPER_DEFAULTS,
    BotTypePlugin,
    StatusItem,
)
from core.utils import effective_verify_ssl, strip_ansi


CAF_CLI_RUN_STATE_KEY_MAP = {
    "caf_cli_execution_target": "execution_target",
    "caf_cli_ssh_host": "ssh_host",
    "caf_cli_ssh_port": "ssh_port",
    "caf_cli_ssh_user": "ssh_user",
    "caf_cli_ssh_password": "ssh_password",
    "caf_cli_directory": "caf_cli_directory",
    "caf_cli_command": "caf_cli_command",
    "caf_cli_provider": "caf_cli_provider",
    "caf_cli_url": "caf_cli_url",
    "caf_cli_model": "selected_model",
    "caf_cli_api_key": "caf_cli_api_key",
    "caf_cli_verify_ssl": "caf_cli_verify_ssl",
    "caf_cli_tool_catalog": "caf_cli_tool_catalog",
    "caf_cli_enabled_tools": "caf_cli_enabled_tools",
    "caf_cli_scope": "caf_scope",
    "caf_cli_urgency": "caf_urgency",
    "caf_cli_context_window": "caf_cli_context_window",
    "caf_cli_max_turns": "caf_cli_max_turns",
    "caf_cli_tool_timeout": "caf_cli_tool_timeout",
    "caf_cli_tool_output_chars": "caf_cli_tool_output_chars",
    "caf_cli_allow": "caf_cli_allow",
    "caf_cli_disallow": "caf_cli_disallow",
    "caf_cli_scope_enabled": "caf_cli_scope_enabled",
    "caf_cli_urgency_enabled": "caf_cli_urgency_enabled",
    "caf_cli_verbose": "caf_cli_verbose",
    "caf_cli_dangerous_no_prompt": "caf_cli_dangerous_no_prompt",
    "caf_cli_timeout": "caf_cli_timeout",
    "caf_cli_startup_commands": "startup_commands",
    "caf_cli_completion_commands": "completion_commands",
    "caf_cli_validation_sets": "validation_sets",
    "caf_cli_run_bot_metric_thresholds": "metric_thresholds",
    **{f"caf_cli_{key}": key for key in LLM_HELPER_DEFAULTS},
}

CAF_CLI_RUN_SESSION_DEFAULTS = {
    "caf_cli_execution_target": "local",
    "caf_cli_ssh_host": "",
    "caf_cli_ssh_port": 22,
    "caf_cli_ssh_user": "root",
    "caf_cli_ssh_password": "",
    "caf_cli_directory": "~/modelscope",
    "caf_cli_command": "./start_cli.sh",
    "caf_cli_provider": "ollama_direct",
    "caf_cli_url": "http://localhost:11434",
    "caf_cli_model": "",
    "caf_cli_api_key": "",
    "caf_cli_verify_ssl": True,
    "caf_cli_tool_catalog": [],
    "caf_cli_enabled_tools": [],
    "caf_cli_scope": "Narrow",
    "caf_cli_urgency": "Balanced",
    "caf_cli_context_window": 8192,
    "caf_cli_max_turns": 20,
    "caf_cli_tool_timeout": 120,
    "caf_cli_tool_output_chars": 6000,
    "caf_cli_allow": "*",
    "caf_cli_disallow": "",
    "caf_cli_scope_enabled": True,
    "caf_cli_urgency_enabled": True,
    "caf_cli_verbose": False,
    "caf_cli_dangerous_no_prompt": False,
    "caf_cli_timeout": 600,
    "caf_cli_startup_commands": [],
    "caf_cli_completion_commands": [],
    "caf_cli_validation_sets": [],
    "caf_cli_val_editor_nonce": 0,
    "caf_cli_val_active_set_idx": 0,
    "caf_cli_run_bot_metric_thresholds": {},
    "caf_cli_discovered_models": [],
    "caf_cli_test_result": None,
    **{f"caf_cli_{key}": value for key, value in LLM_HELPER_DEFAULTS.items()},
}


def _caf_flags(config: dict[str, Any]) -> str:
    flags: list[str] = []

    def value(flag: str, key: str) -> None:
        raw = config.get(key)
        if raw not in (None, ""):
            flags.extend([flag, shlex.quote(str(raw))])

    def positive_int(flag: str, key: str) -> None:
        try:
            raw = int(config.get(key) or 0)
        except (TypeError, ValueError):
            return
        if raw > 0:
            flags.extend([flag, str(raw)])

    value("--provider", "caf_cli_provider")
    value("--url", "caf_cli_url")
    value("--model", "selected_model")
    value("--api-key", "caf_cli_api_key")
    value("--tools-config", "caf_cli_tools_config")
    positive_int("--context-window", "caf_cli_context_window")
    positive_int("--max-turns", "caf_cli_max_turns")
    positive_int("--tool-timeout", "caf_cli_tool_timeout")
    positive_int("--tool-output-chars", "caf_cli_tool_output_chars")
    if config.get("caf_cli_scope_enabled", True):
        flags.extend(["--scope", shlex.quote(str(config.get("caf_scope") or "narrow").lower())])
    else:
        flags.append("--no-scope")
    if config.get("caf_cli_urgency_enabled", True):
        flags.extend(["--urgency", shlex.quote(str(config.get("caf_urgency") or "balanced").lower())])
    else:
        flags.append("--no-urgency")
    for flag, key in (("--allow", "caf_cli_allow"), ("--disallow", "caf_cli_disallow")):
        for entry in str(config.get(key) or "").replace("\n", ",").split(","):
            if entry.strip():
                flags.extend([flag, shlex.quote(entry.strip())])
    if config.get("caf_cli_verify_ssl", True) is False:
        flags.append("--no-ssl-verify")
    if config.get("caf_cli_verbose"):
        flags.append("--verbose")
    if config.get("caf_cli_dangerous_no_prompt"):
        flags.append("--dangerous-no-prompt")
    return " ".join(flags)


def _run_command(config: dict[str, Any]) -> str:
    """Build one non-interactive CAF ``run`` command for a validation prompt."""
    try:
        launcher = shlex.join(shlex.split(str(config.get("caf_cli_command") or "./start_cli.sh")))
    except ValueError as exc:
        raise ValueError(f"Invalid CAF CLI command: {exc}") from exc
    objective = str(config.get("user_prompt") or "").strip()
    continue_run = str(config.get("caf_cli_continue_run") or "").strip()
    continue_flag = f" --continue {shlex.quote(continue_run)}" if continue_run else ""
    # CAF's launcher is usually a Python process. Keep output unbuffered so
    # the Execute log can receive progress before the one-shot run exits.
    command = f"PYTHONUNBUFFERED=1 {launcher} run{continue_flag} {_caf_flags(config)} -- {shlex.quote(objective)}"
    if config.get("execution_target") == "local":
        _proj_id = config.get("id")
        _default_dir = f"~/modelscope/{_proj_id}" if _proj_id else "~/modelscope"
        directory = os.path.expanduser(str(config.get("caf_cli_directory") or _default_dir))
        command = f"cd {shlex.quote(directory)} && {command}"
    return command


def _display_command(command: str, config: dict[str, Any]) -> str:
    api_key = str(config.get("caf_cli_api_key") or "")
    if api_key:
        return command.replace(shlex.quote(api_key), "[REDACTED]")
    return re.sub(r"(--api-key\s+)(?:'[^']*'|\S+)", r"\1[REDACTED]", command)


def _caf_run_id(output: str) -> str | None:
    match = re.search(r"\[(?:chat|run)\]\s+Transcript:\s+runs/([^/\s]+)/", output)
    return match.group(1) if match else None


def _clean_execution_log_text(value: Any) -> str:
    """Keep readable log text while dropping terminal control characters."""
    text = strip_ansi(str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char for char in text if char in ("\n", "\t") or ord(char) >= 32)


def _environment_for_config(config: dict[str, Any]):
    """Create the environment on which CAF itself will execute."""
    from core.environment import create_environment

    return create_environment(
        ssh=config.get("execution_target") == "ssh",
        host=config.get("ssh_host", ""),
        port=int(config.get("ssh_port") or 22),
        username=config.get("ssh_user", "root"),
        password=config.get("ssh_password") or None,
        remote_cwd=config.get("caf_cli_directory") or None,
        project_id=config.get("id"),
    )


def _model_catalog_command(config: dict[str, Any]) -> str:
    """Return a provider-model query executed from CAF's target machine."""
    provider = str(config.get("caf_cli_provider") or "").lower()
    base_url = str(config.get("caf_cli_url") or "").rstrip("/")
    if not base_url:
        raise ValueError("Provider URL is required to fetch models.")
    if provider == "ollama_direct":
        endpoint = f"{base_url}/api/tags"
    elif provider == "claude":
        raise ValueError("Claude does not expose a model-catalog endpoint for this fetch action; enter its model ID manually.")
    else:
        endpoint = f"{base_url if base_url.endswith('/v1') else base_url + '/v1'}/models"
    headers = ""
    api_key = str(config.get("caf_cli_api_key") or "")
    if api_key:
        headers = f" -H {shlex.quote('Authorization: Bearer ' + api_key)}"
    insecure = " --insecure" if config.get("caf_cli_verify_ssl", True) is False else ""
    return f"curl --fail --silent --show-error --max-time 15{insecure}{headers} {shlex.quote(endpoint)}"


def fetch_caf_models(config: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    """Fetch provider models from the same local/SSH target that runs CAF."""
    try:
        command = _model_catalog_command(config)
    except ValueError as exc:
        return [], str(exc)
    env = _environment_for_config(config)
    try:
        result = env.execute(command, timeout=20)
    finally:
        if hasattr(env, "close"):
            env.close()
    if result.get("exit_code") != 0:
        return [], result.get("stderr") or result.get("stdout") or "Model fetch failed."
    try:
        payload = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        return [], "Model endpoint returned invalid JSON."
    raw_models = payload.get("models", []) if str(config.get("caf_cli_provider") or "").lower() == "ollama_direct" else payload.get("data", [])
    models = []
    for item in raw_models if isinstance(raw_models, list) else []:
        name = (item.get("name") or item.get("id")) if isinstance(item, dict) else ""
        if name:
            models.append({"name": str(name)})
    return models, ""


def test_caf_cli(config: dict[str, Any]) -> tuple[bool, str]:
    """Verify SSH reachability, CAF directory access, and the CLI launcher."""
    try:
        launcher = shlex.join(shlex.split(str(config.get("caf_cli_command") or "./start_cli.sh")))
    except ValueError as exc:
        return False, f"Invalid CAF CLI command: {exc}"
    _proj_id = config.get("id")
    _default_dir = f"~/modelscope/{_proj_id}" if _proj_id else "~/modelscope"
    directory = os.path.expanduser(str(config.get("caf_cli_directory") or _default_dir))
    is_ssh = config.get("execution_target") == "ssh"
    if not is_ssh and not os.path.isdir(directory):
        return False, f"CAF directory not found on this machine: {directory}"

    env = _environment_for_config(config)
    try:
        if is_ssh and hasattr(env, "connect"):
            try:
                env.connect()
            except Exception as exc:
                return False, f"SSH connection failed: {exc}"

        directory_check = env.execute("pwd", timeout=15)
        if directory_check.get("exit_code") != 0:
            location = "SSH target" if is_ssh else "this machine"
            detail = directory_check.get("stderr") or directory_check.get("stdout") or "directory check failed"
            return False, f"CAF directory is unavailable on the {location}: {detail}"

        command = f"{launcher} run --help"
        if not is_ssh:
            command = f"cd {shlex.quote(directory)} && {command}"
        result = env.execute(command, timeout=30)
    finally:
        if hasattr(env, "close"):
            env.close()
    if result.get("exit_code") == 0:
        return True, "CAF CLI is available on the selected execution target."
    detail = result.get("stderr") or result.get("stdout") or "no output"
    return False, f"CAF CLI command failed in {directory}: {detail}"


def _tools_config_path(config: dict[str, Any]) -> str:
    return str(config.get("caf_cli_tools_config") or "kali_tools.json").strip()


def _read_tools_command(config: dict[str, Any]) -> str:
    path = _tools_config_path(config)
    if config.get("execution_target") == "local":
        _proj_id = config.get("id")
        _default_dir = f"~/modelscope/{_proj_id}" if _proj_id else "~/modelscope"
        directory = os.path.expanduser(str(config.get("caf_cli_directory") or _default_dir))
        return f"cd {shlex.quote(directory)} && cat {shlex.quote(path)}"
    if path.startswith("~/"):
        return f'cat "$HOME/{path[2:]}"'
    return f"cat {shlex.quote(path)}"


def fetch_caf_tools(config: dict[str, Any]) -> tuple[list[dict], str]:
    """Read CAF's target-local kali_tools.json catalog."""
    env = _environment_for_config(config)
    try:
        result = env.execute(_read_tools_command(config), timeout=20)
    finally:
        if hasattr(env, "close"):
            env.close()
    if result.get("exit_code") != 0:
        return [], result.get("stderr") or result.get("stdout") or "Could not read CAF tools config."
    try:
        payload = json.loads(result.get("stdout") or "{}")
    except json.JSONDecodeError:
        return [], "CAF tools config contains invalid JSON."
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        return [], "CAF tools config does not contain a tools list."
    return [tool for tool in tools if isinstance(tool, dict) and tool.get("name")], ""


def _prepare_selected_tools(env: Any, config: dict[str, Any]) -> dict[str, Any]:
    """Write a filtered target-local catalog when the user selected CAF tools."""
    catalog = config.get("caf_cli_tool_catalog", [])
    enabled = set(config.get("caf_cli_enabled_tools", []))
    if not catalog:
        return config
    selected = [tool for tool in catalog if tool.get("name") in enabled]
    target_path = ".modelscope_caf_tools.json"
    if config.get("execution_target") == "local":
        target_path = str(pathlib.Path(os.path.expanduser(str(config.get("caf_cli_directory")))) / target_path)
    result = env.write_file(target_path, json.dumps({"tools": selected}, indent=2))
    if result.get("error"):
        raise RuntimeError(f"Could not write selected CAF tools config: {result['error']}")
    return {**config, "caf_cli_tools_config": target_path}


def _transcript(env: Any, config: dict[str, Any], run_id: str, on_log: Callable[[str], None]) -> tuple[str, dict]:
    root = pathlib.Path("runs")
    if config.get("execution_target") == "local":
        _proj_id = config.get("id")
        _default_dir = f"~/modelscope/{_proj_id}" if _proj_id else "~/modelscope"
        root = pathlib.Path(os.path.expanduser(str(config.get("caf_cli_directory") or _default_dir))) / "runs"
    try:
        transcript = env.read_file(str(root / run_id / "transcript.md"))
        metadata = json.loads(env.read_file(str(root / run_id / "metadata.json")))
        return transcript, metadata
    except Exception as exc:
        on_log(f"[WARN] Could not read CAF transcript: {exc}")
        return "", {}


def _execute_caf_run_live(
    env: Any,
    command: str,
    *,
    timeout: int,
    on_chunk: Callable[[str], None],
    cancel_ref: list[bool] | None = None,
) -> dict[str, Any]:
    """Stream a one-shot CAF run without allocating an SSH PTY.

    CAF's ``chat`` command receives a one-shot prompt through a pipe.  A PTY
    changes that into an interactive REPL over SSH, so this deliberately uses
    a plain SSH channel while polling stdout and stderr.
    """
    from core.environment import LocalEnvironment, SSHEnvironment

    if isinstance(env, LocalEnvironment):
        chunks: queue.Queue[tuple[str, bytes | None]] = queue.Queue()
        process = subprocess.Popen(
            command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:

            def read_stream(name: str, stream: Any) -> None:
                try:
                    # Bypass BufferedReader.read(), which waits to fill its buffer
                    # and would otherwise hold small CLI updates until the run exits.
                    while data := os.read(stream.fileno(), 4096):
                        chunks.put((name, data))
                finally:
                    chunks.put((name, None))

            readers = [
                threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
                threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
            ]
            for reader in readers:
                reader.start()

            stdout, stderr, closed_streams = [], [], 0
            deadline = time.monotonic() + timeout
            cancelled = False
            timed_out = False
            while closed_streams < 2:
                if cancel_ref and cancel_ref[0] and process.poll() is None:
                    cancelled = True
                    try:
                        os.killpg(process.pid, 15)
                    except OSError:
                        process.terminate()
                if time.monotonic() >= deadline and process.poll() is None:
                    cancelled = True
                    timed_out = True
                    try:
                        os.killpg(process.pid, 15)
                    except OSError:
                        process.terminate()
                try:
                    name, data = chunks.get(timeout=0.1)
                except queue.Empty:
                    if process.poll() is not None and not any(reader.is_alive() for reader in readers):
                        break
                    continue
                if data is None:
                    closed_streams += 1
                    continue
                text = data.decode("utf-8", errors="replace")
                (stdout if name == "stdout" else stderr).append(text)
                on_chunk(text)

            try:
                exit_code = process.wait(timeout=2) if process.poll() is None else process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = -1
            if cancelled and exit_code == 0:
                exit_code = -1
            if timed_out:
                stderr.append("Timed out")
            return {"stdout": "".join(stdout), "stderr": "".join(stderr), "exit_code": exit_code}
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except Exception:
                    process.kill()

    if isinstance(env, SSHEnvironment):
        try:
            env.connect()
            channel = env.get_client().get_transport().open_session()
            full_command = f"cd {shlex.quote(env.remote_cwd)} && export TERM=xterm; {command}"
            channel.exec_command(full_command)
            stdout, stderr = [], []
            deadline = time.monotonic() + timeout
            cancelled = False
            while time.monotonic() < deadline:
                if cancel_ref and cancel_ref[0]:
                    cancelled = True
                    channel.close()
                    break
                while channel.recv_ready():
                    text = channel.recv(4096).decode("utf-8", errors="replace")
                    stdout.append(text)
                    on_chunk(text)
                while channel.recv_stderr_ready():
                    text = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    stderr.append(text)
                    on_chunk(text)
                if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                    break
                time.sleep(0.05)
            else:
                cancelled = True
                stderr.append("Timed out")
                channel.close()

            # Paramiko marks a channel closed as part of a normal remote command
            # shutdown.  ``closed`` therefore is not a failure signal once the
            # exit status is ready; treating it as one turned successful CAF
            # runs (including runs with a completed transcript) into ``-1``.
            exit_code = -1 if cancelled else channel.recv_exit_status()
            try:
                channel.close()
            except Exception:
                pass
            return {"stdout": "".join(stdout), "stderr": "".join(stderr), "exit_code": exit_code}
        except Exception as exc:
            return {"stdout": "", "stderr": str(exc), "exit_code": -1}

    # Unit-test doubles and custom plugin environments retain the original,
    # blocking contract instead of relying on a concrete environment class.
    result = env.execute(command, timeout=timeout)
    on_chunk(result.get("stdout", ""))
    on_chunk(result.get("stderr", ""))
    return result


def _run_caf_validation_prompt(
    env: Any,
    config: dict[str, Any],
    prompt: str,
    on_log: Callable[[str], None],
) -> dict[str, Any]:
    """Run one validation prompt through CAF's own non-interactive CLI process."""
    run_config = _prepare_selected_tools(env, {**config, "user_prompt": prompt})
    command = _run_command(run_config)
    on_log("[VALIDATE CAF] Running prompt through CyberAgentFlow CLI (one-shot run)")

    def stream(text: str) -> None:
        for line in strip_ansi(text).replace("\r", "\n").splitlines():
            if line.strip():
                on_log(f"[VALIDATE CAF] {line.strip()}")

    timeout = int(config.get("caf_cli_timeout") or 600)
    result = _execute_caf_run_live(
        env, command, timeout=timeout, on_chunk=stream,
        cancel_ref=config.get("cancel_requested_ref"),
    )

    raw_output = strip_ansi(result.get("stdout", "") + result.get("stderr", ""))
    response = raw_output
    run_id = _caf_run_id(raw_output)
    if run_id:
        transcript, _ = _transcript(env, config, run_id, on_log)
        response = transcript or raw_output
    return {
        "stdout": response,
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
        "caf_run_id": run_id,
    }


def _run_target_llm_helper_prompt(
    env: Any, command: dict[str, Any], config: dict[str, Any], context: list[dict[str, str]], on_log: Callable[[str], None],
) -> dict[str, Any]:
    """Run a Startup/Completion LLM Judge prompt from the target, outside CAF."""
    if not config.get("llm_helper_enabled"):
        return {"exit_code": 1, "stderr": "LLM Judge is disabled."}
    try:
        from core.judge import LLMJudge

        judge = LLMJudge.from_config(config)
        preserve_context = bool(command.get("preserve_context", True))
        messages = list(context) if preserve_context else []
        system_prompt = str(command.get("system_prompt") or "").strip()
        user_prompt = str(command.get("user_prompt") or "").strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if user_prompt:
            messages.append({"role": "user", "content": user_prompt})
        if not messages:
            return {"exit_code": 1, "stderr": "LLM Judge prompt is empty."}

        headers = ["Content-Type: application/json"]
        if judge.backend == "OpenAI-Compatible":
            endpoint = f"{judge.openai_url}/v1/chat/completions"
            if judge.api_key:
                headers.append(f"Authorization: Bearer {judge.api_key}")
            payload = {"model": judge.model, "messages": messages, "temperature": judge.temperature, "max_tokens": judge.max_tokens}
            verify_ssl = effective_verify_ssl(endpoint, judge.verify_ssl)
        else:
            endpoint = f"{judge.ollama_url}/api/chat"
            payload = {"model": judge.model, "messages": messages, "stream": False, "options": {"temperature": judge.temperature}}
            verify_ssl = True
        args = ["curl", "--fail", "--silent", "--show-error", "--max-time", "120"]
        if not verify_ssl:
            args.append("--insecure")
        for header in headers:
            args.extend(["-H", header])
        args.extend(["-d", json.dumps(payload), endpoint])
        on_log(f"[PROMPT HELPER] Running LLM Judge from target with {judge.backend}/{judge.model or '(server default)'}")
        result = env.execute(" ".join(shlex.quote(arg) for arg in args), timeout=130)
        if result.get("exit_code") != 0:
            return {"exit_code": result.get("exit_code", 1), "stderr": result.get("stderr") or result.get("stdout") or "Target LLM Judge request failed"}
        data = json.loads(result.get("stdout") or "{}")
        response = (
            data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if judge.backend == "OpenAI-Compatible"
            else data.get("message", {}).get("content", "")
        )
        response = str(response)
        if preserve_context:
            context.extend(messages[len(context):])
            context.append({"role": "assistant", "content": response})
        on_log(f"[RESPONSE] {response[:800]}")
        return {"exit_code": 0, "stdout": response}
    except Exception as exc:
        on_log(f"[ERROR] LLM Judge prompt failed: {exc}")
        return {"exit_code": 1, "stderr": str(exc)}


def _run_caf_command_steps(
    env: Any, steps: list[Any], *, label: str, default_timeout: int, on_log: Callable[[str], None],
    config: dict[str, Any], helper_context: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Run Startup/Completion command steps on CAF's selected target."""
    tool_calls: list[dict[str, Any]] = []
    cancel_ref = config.get("cancel_requested_ref", [False])

    def cancelled() -> bool:
        return bool(cancel_ref and cancel_ref[0])

    for step_index, raw_step in enumerate(steps):
        if cancelled():
            on_log(f"[CANCEL] Skipping remaining {label.lower()} commands")
            break
        step = raw_step if isinstance(raw_step, dict) else {"commands": [raw_step]}
        delay = float(step.get("delay_seconds", 0) or 0)
        if delay > 0:
            on_log(f"[DELAY] {label.title()} step {step_index + 1}: waiting {delay:.1f}s")
            until = time.monotonic() + delay
            while time.monotonic() < until:
                if cancelled():
                    on_log(f"[CANCEL] {label.title()} delay cancelled")
                    return tool_calls
                time.sleep(min(0.1, until - time.monotonic()))
        for raw_command in step.get("commands", []):
            if cancelled():
                on_log(f"[CANCEL] Skipping remaining {label.lower()} commands")
                return tool_calls
            if isinstance(raw_command, str):
                command = {"command": raw_command, "enabled": True}
            elif isinstance(raw_command, dict):
                command = raw_command
            else:
                continue
            if not command.get("enabled", True):
                continue
            if command.get("type") == "prompt":
                result = _run_target_llm_helper_prompt(env, command, config, helper_context, on_log)
                tool_calls.append({
                    "tool": "llm_helper", "args": {"prompt": command}, "result": result,
                    "exit_code": result.get("exit_code", -1),
                })
                continue
            text = str(command.get("command") or "").strip()
            if not text:
                continue
            timeout = 3600 if command.get("long_running") else int(command.get("timeout_seconds") or default_timeout)
            on_log(f"[{label}] {text}")
            result = env.execute(text, timeout=timeout)
            if result.get("stdout"):
                on_log(f"[STDOUT] {result['stdout'][:800]}")
            if result.get("stderr"):
                on_log(f"[STDERR] {result['stderr'][:400]}")
            tool_calls.append({
                "tool": "bash", "args": {"command": text}, "result": result,
                "exit_code": result.get("exit_code", -1),
            })
    return tool_calls


def _caf_validation_sets_without_system_prompts(validation_sets: Any) -> list[Any]:
    """CAF owns its system prompt, so validation checks provide user input only."""
    cleaned = copy.deepcopy(validation_sets) if isinstance(validation_sets, list) else []
    for validation_set in cleaned:
        if not isinstance(validation_set, dict):
            continue
        for step in validation_set.get("steps", []):
            if not isinstance(step, dict):
                continue
            for command in step.get("commands", []):
                if isinstance(command, dict) and command.get("type") == "prompt":
                    command.pop("system_prompt", None)
    return cleaned


def run_caf_cli_run(env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
    """Run validation against CAF; prompt checks each use CAF's one-shot run."""
    from core.evaluator import _init_telemetry, _run_validation_sets

    started = time.time()
    command_timeout = int(config.get("caf_cli_timeout") or 600)
    cancel_ref = config.get("cancel_requested_ref", [False])
    helper_context: list[dict[str, str]] = []
    tool_calls = _run_caf_command_steps(
        env, config.get("startup_commands", []), label="STARTUP", default_timeout=command_timeout, on_log=on_log,
        config=config, helper_context=helper_context,
    )
    context_run_id = [None]
    responses: list[str] = []
    prompt_responses: list[dict[str, str]] = []

    def execute_caf_validation_prompt(prompt: str, _label: str, preserve_context: bool) -> dict[str, Any]:
        prompt_config = dict(config)
        if preserve_context and context_run_id[0]:
            prompt_config["caf_cli_continue_run"] = context_run_id[0]
        response = _run_caf_validation_prompt(env, prompt_config, prompt, on_log)
        if preserve_context and response.get("caf_run_id"):
            context_run_id[0] = response["caf_run_id"]
        if response.get("stdout"):
            output = str(response["stdout"])
            responses.append(output)
            prompt_responses.append({"prompt": prompt, "response": output})
        return response

    # _run_validation_sets uses this callback for the same prompt-step contract
    # as llama-cli. Command steps still execute directly on the target via env.
    validation_config = {
        **config,
        "type": "llama_cli_bot",
        "execute_prompt_callback": execute_caf_validation_prompt,
    }
    if cancel_ref and cancel_ref[0]:
        on_log("[CANCEL] CAF execution stopped before validation")
        passed, validation_results = False, []
    else:
        passed, validation_results = _run_validation_sets(
            env, _caf_validation_sets_without_system_prompts(config.get("validation_sets", [])), on_log,
            cancel_ref=cancel_ref, config=validation_config,
        )
    if not (cancel_ref and cancel_ref[0]):
        tool_calls.extend(_run_caf_command_steps(
            env, config.get("completion_commands", []), label="CLEANUP", default_timeout=command_timeout, on_log=on_log,
            config=config, helper_context=helper_context,
        ))

    telemetry = _init_telemetry(config)
    telemetry["run_bot_type"] = "caf_cli_run_bot"
    telemetry["run_backend"] = f"CyberAgentFlow CLI ({config.get('caf_cli_provider') or 'not configured'})"
    telemetry["run_model"] = config.get("selected_model") or "(not configured)"
    telemetry["total_latency"] = round(time.time() - started, 3)
    telemetry["llm_rounds"] = len(responses)
    telemetry["run_aborted"] = bool(cancel_ref and cancel_ref[0])
    telemetry["validation_passed"] = passed
    telemetry["validation_sets_results"] = validation_results
    telemetry["tool_calls"] = tool_calls
    telemetry["prompt_responses"] = prompt_responses
    telemetry["llm_response"] = responses[-1] if responses else ""
    validation_steps = [
        step for validation_set in validation_results for step in validation_set.get("steps", [])
        if isinstance(step, dict)
    ]
    telemetry["validation_stdout"] = "\n".join(str(step.get("stdout") or "") for step in validation_steps)
    telemetry["validation_stderr"] = "\n".join(str(step.get("stderr") or "") for step in validation_steps)
    telemetry["validation_exit_code"] = validation_steps[-1].get("exit_code") if validation_steps else None
    if context_run_id[0]:
        telemetry["caf_run_id"] = context_run_id[0]
    on_log(f"[COMPLETE] {telemetry['total_latency']}s | validation={'PASS' if passed else 'FAIL'}")
    return telemetry


def _run_caf_cli_background(
    plugin: "CafCliRunPlugin", project_id: str, run_config: dict[str, Any], shared: dict[str, Any],
) -> None:
    """Run CAF outside Streamlit's UI thread while publishing only plain shared state."""
    from core.session_log import SessionLog

    cancel_ref = [False]
    shared["cancel_ref"] = cancel_ref
    run_config = {**run_config, "cancel_requested_ref": cancel_ref}
    session_log = SessionLog()

    def on_log(message: str) -> None:
        if shared.get("cancel_requested"):
            cancel_ref[0] = True
        clean_message = _clean_execution_log_text(message)
        shared.setdefault("logs", []).append(clean_message)
        session_log.log(clean_message)

    telemetry: dict[str, Any]
    try:
        telemetry = plugin.run_evaluation(None, run_config, on_log)
    except Exception as exc:
        on_log(f"[ERROR] CAF CLI evaluation failed: {exc}")
        telemetry = {"run_aborted": True, "error": str(exc)}
    finally:
        if shared.get("cancel_requested"):
            cancel_ref[0] = True

    if cancel_ref[0]:
        telemetry["run_aborted"] = True
    persisted_config = {key: value for key, value in run_config.items() if key != "cancel_requested_ref"}
    session_log.save_telemetry(telemetry)
    session_log.save_config(persisted_config)
    session_log.close()
    shared["telemetry"] = telemetry
    shared["completed"] = True
    shared["project_id"] = project_id


class CafCliRunPlugin(BotTypePlugin):
    """Run CAF validation prompts locally or over a standard SSH shell."""

    type_id = "caf_cli_run_bot"
    label = "CAF CLI Run"
    icon = "💬"
    default_project_name = "CAF CLI Run"
    state_key_map = CAF_CLI_RUN_STATE_KEY_MAP
    session_defaults = CAF_CLI_RUN_SESSION_DEFAULTS
    owned_prefixes = ("caf_cli_val_", "_caf_cli_val_", "caf_cli_tool_en_", "caf_cli_llm_helper_", "caf_cli_is_fetching_", "_caf_cli_run_bot_metric_threshold_", "_sc_caf_cli_")
    metric_specs = COMMON_DASHBOARD_METRIC_SPECS

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        return {
            "execution_target": "local", "ssh_host": "", "ssh_port": 22, "ssh_user": "root", "ssh_password": "",
            "caf_cli_directory": "~/modelscope", "caf_cli_command": "./start_cli.sh",
            "caf_cli_provider": "ollama_direct", "caf_cli_url": "http://localhost:11434", "selected_model": "",
            "caf_cli_api_key": "", "caf_cli_verify_ssl": True,
            "caf_cli_tool_catalog": [], "caf_cli_enabled_tools": [],
            "caf_scope": "Narrow", "caf_urgency": "Balanced", "caf_cli_context_window": 8192, "caf_cli_max_turns": 20,
            "caf_cli_tool_timeout": 120, "caf_cli_tool_output_chars": 6000, "caf_cli_allow": "*", "caf_cli_disallow": "",
            "caf_cli_scope_enabled": True, "caf_cli_urgency_enabled": True, "caf_cli_verbose": False,
            "caf_cli_dangerous_no_prompt": False, "caf_cli_timeout": 600,
            "startup_commands": [], "completion_commands": [], "validation_sets": [], "metric_thresholds": {},
            "metrics_matrix": [], **LLM_HELPER_DEFAULTS,
        }

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        target = session_state.get("caf_cli_execution_target", "local")
        ready = target == "local" or bool(str(session_state.get("caf_cli_ssh_host", "")).strip())
        return [StatusItem(f"CAF target: {target.upper()}", "up" if ready else "wait")]

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config["type"] = self.type_id
        config.pop("user_prompt", None)  # Legacy Execute-page chat objective.
        config["validation_sets"] = _caf_validation_sets_without_system_prompts(config.get("validation_sets", []))
        if "caf_cli_verify_ssl" not in config:
            config["caf_cli_verify_ssl"] = not bool(config.get("caf_cli_no_ssl_verify", False))
        for key, value in self.default_config().items():
            config.setdefault(key, value)
        return config

    def flush_config(self, project: dict[str, Any]) -> None:
        self.flush_mapped_config(project)
        project["config"]["validation_sets"] = _caf_validation_sets_without_system_prompts(
            project["config"].get("validation_sets", [])
        )

    def render_config(self, project: dict[str, Any]) -> None:
        import streamlit as st
        from ui.config_tab import _render_metric_thresholds_config

        st.divider()
        runtime_tab, validation_tab, metrics_tab = st.tabs(["🖥  Runtime", "✅  Validation", "📊  Metrics Config"])
        with runtime_tab:
            self._render_runtime()
        with validation_tab:
            from ui.config_tab import _render_validation_sets_ui
            _render_validation_sets_ui(project, "caf_cli", self.flush_config)
        with metrics_tab:
            _render_metric_thresholds_config(project, self.type_id, self.flush_config)
        self.flush_config(project)

    def _render_runtime(self) -> None:
        import streamlit as st

        with st.expander("Execution Target", expanded=True):
            target = st.radio("Mode", ["local", "ssh"], horizontal=True, key="caf_cli_execution_target", format_func=lambda value: "Local" if value == "local" else "SSH (Remote)")
            if target == "ssh":
                col_host, col_port = st.columns([4, 1])
                with col_host:
                    st.text_input("Host", key="caf_cli_ssh_host", placeholder="192.168.1.100")
                with col_port:
                    st.number_input("Port", min_value=1, max_value=65535, key="caf_cli_ssh_port")
                col_user, col_password = st.columns(2)
                with col_user:
                    st.text_input("Username", key="caf_cli_ssh_user")
                with col_password:
                    st.text_input("Password", type="password", key="caf_cli_ssh_password")
            st.text_input("CAF directory", key="caf_cli_directory")
            st.text_input("CLI command", key="caf_cli_command", help="Normally ./start_cli.sh; validation prompts invoke one-shot run.")
            _, test_col, _ = st.columns([1, 2, 1])
            with test_col:
                if st.button("TEST ENVIRONMENT", key="btn_caf_cli_test", type="primary", use_container_width=True):
                    self._test_cli()
            test_result = st.session_state.get("caf_cli_test_result")
            if test_result:
                (st.success if test_result[0] else st.error)(test_result[1])

        with st.expander("CyberAgentFlow CLI", expanded=True):
            st.caption("CyberAgentFlow uses this provider from its selected execution-target environment.")
            provider = st.selectbox("Provider", ["ollama_direct", "openai", "litellm", "claude"], key="caf_cli_provider")
            url_col, fetch_col = st.columns([4, 1])
            with url_col:
                provider_url = st.text_input("Provider URL", key="caf_cli_url")
            with fetch_col:
                st.write("")
                st.write("")
                if st.button("Fetch", key="btn_caf_cli_fetch_models", use_container_width=True):
                    self._fetch_models()
            if provider != "ollama_direct":
                st.text_input("API Key (optional)", key="caf_cli_api_key", type="password")
            if provider_url.strip().lower().startswith("https://"):
                st.checkbox("Require SSL certificate verification", key="caf_cli_verify_ssl")

            discovered = st.session_state.get("caf_cli_discovered_models", [])
            names = [item["name"] for item in discovered if item.get("name")]
            if names:
                current = st.session_state.get("caf_cli_model", "")
                st.selectbox("Model", names, index=names.index(current) if current in names else 0, key="caf_cli_model")
            else:
                st.text_input("Model", key="caf_cli_model", help="Fetch models from CAF's execution target, or enter a model ID manually.")

            with st.expander("Tools", expanded=False):
                st.caption("Fetches the tools configured on CAF's selected execution target.")
                if st.button("Fetch", key="btn_caf_cli_fetch_tools", use_container_width=True):
                    self._fetch_tools()
                catalog = st.session_state.get("caf_cli_tool_catalog", [])
                if catalog:
                    enabled = set(st.session_state.get("caf_cli_enabled_tools", []))
                    st.caption("Selected tools are written to a filtered CAF tools config when validation prompts run.")
                    with st.container(height=320, border=False):
                        for tool in catalog:
                            name = str(tool["name"])
                            checked = st.checkbox(name, value=name in enabled, key=f"caf_cli_tool_en_{name}", help=tool.get("description", ""))
                            if checked:
                                enabled.add(name)
                            else:
                                enabled.discard(name)
                    st.session_state["caf_cli_enabled_tools"] = sorted(enabled)
                else:
                    st.caption("No tools fetched yet.")

            with st.expander("Run Policy & Limits", expanded=False):
                scope, urgency, timeout = st.columns(3)
                with scope:
                    st.checkbox("Use scope", key="caf_cli_scope_enabled")
                    st.selectbox("Scope", ["Narrow", "Medium-Narrow", "Medium", "Medium-Broad", "Broad"], key="caf_cli_scope", disabled=not st.session_state.get("caf_cli_scope_enabled", True))
                with urgency:
                    st.checkbox("Use urgency", key="caf_cli_urgency_enabled")
                    st.selectbox("Urgency", ["Stealthy", "Methodical", "Balanced", "Fast", "Speed"], key="caf_cli_urgency", disabled=not st.session_state.get("caf_cli_urgency_enabled", True))
                with timeout:
                    st.number_input("Run timeout (seconds)", min_value=1, key="caf_cli_timeout")
                context, turns, tool_timeout = st.columns(3)
                with context:
                    st.number_input("Context window", min_value=1024, key="caf_cli_context_window")
                with turns:
                    st.number_input("Maximum turns", min_value=1, max_value=100, key="caf_cli_max_turns")
                with tool_timeout:
                    st.number_input("Tool timeout", min_value=1, max_value=3600, key="caf_cli_tool_timeout")
                st.number_input("Tool output characters", min_value=0, key="caf_cli_tool_output_chars")
                st.text_input("Allowed targets", key="caf_cli_allow")
                st.text_input("Disallowed targets", key="caf_cli_disallow")
                st.checkbox("Verbose output", key="caf_cli_verbose")
                st.checkbox("Auto-approve dangerous commands", key="caf_cli_dangerous_no_prompt")

        with st.expander("Commands", expanded=True):
            from ui.config_tab import _render_command_steps, _render_llm_prompt_helper_tab

            judge_tab, startup_tab, completion_tab = st.tabs(["🤖 LLM Judge", "▶  Startup", "⏹  Completion"])
            with judge_tab:
                st.caption("The judge request runs from the selected execution target, outside CyberAgentFlow.")
                _render_llm_prompt_helper_tab("caf_cli")
            with startup_tab:
                st.caption("Commands run on the execution target before validation begins.")
                _render_command_steps("caf_cli_startup_commands", "caf_cli_startup", "e.g. /bin/bash setup.sh")
            with completion_tab:
                st.caption("Cleanup commands run on the execution target after validation finishes.")
                _render_command_steps("caf_cli_completion_commands", "caf_cli_completion", "e.g. rm -rf /tmp/test_workdir")

    def _fetch_models(self) -> None:
        import streamlit as st

        config = {config_key: st.session_state.get(state_key) for state_key, config_key in self.state_key_map.items()}
        with st.spinner("Fetching models from CAF's execution target…"):
            models, error = fetch_caf_models(config)
        st.session_state["caf_cli_discovered_models"] = models
        if error:
            st.error(error)
            return
        names = [item["name"] for item in models]
        if names and st.session_state.get("caf_cli_model") not in names:
            st.session_state["caf_cli_model"] = names[0]
        st.success(f"Found {len(models)} model(s) on CAF's execution target.")

    def _test_cli(self) -> None:
        import streamlit as st

        config = {config_key: st.session_state.get(state_key) for state_key, config_key in self.state_key_map.items()}
        with st.spinner("Checking CAF CLI on its execution target…"):
            st.session_state["caf_cli_test_result"] = test_caf_cli(config)

    def _fetch_tools(self) -> None:
        import streamlit as st

        config = {config_key: st.session_state.get(state_key) for state_key, config_key in self.state_key_map.items()}
        with st.spinner("Fetching CAF tools from the execution target…"):
            tools, error = fetch_caf_tools(config)
        if error:
            st.error(error)
            return
        st.session_state["caf_cli_tool_catalog"] = tools
        names = [str(tool["name"]) for tool in tools]
        existing = set(st.session_state.get("caf_cli_enabled_tools", []))
        st.session_state["caf_cli_enabled_tools"] = [name for name in names if name in existing] or names
        st.success(f"Found {len(tools)} CAF tool(s) on the execution target.")

    def render_execute(self, project: dict[str, Any]) -> None:
        import streamlit as st
        from ui.terminal import render_terminal

        self.flush_config(project)
        config = project["config"]
        target = config.get("execution_target", "local")
        ready = bool(config.get("selected_model")) and (target == "local" or bool(config.get("ssh_host")))

        st.markdown(f"### {project['name']}")
        with st.container(border=True):
            st.markdown("**⚙️ Run Configuration**")
            with st.container(border=True):
                ssh_info = (
                    f" ({config.get('ssh_user', 'root')}@{config.get('ssh_host', '?')}:{config.get('ssh_port', 22)})"
                    if target == "ssh" else ""
                )
                st.markdown(
                    "**Execution Configuration** "
                    f"&nbsp;&nbsp;<span style='color: #888; font-size: 0.9em'>|&nbsp;&nbsp; "
                    f"Target: {target.upper()}{ssh_info} &nbsp;&nbsp;|&nbsp;&nbsp; "
                    f"Timeout: {config.get('caf_cli_timeout', 600)}s</span>",
                    unsafe_allow_html=True,
                )
                with st.expander("**Model Info**", expanded=True):
                    st.caption(f"Provider: **{config.get('caf_cli_provider', 'not configured')}**")
                    st.caption(f"Model: **{config.get('selected_model') or 'not selected'}**")
                    st.caption(f"Provider URL: `{config.get('caf_cli_url') or 'not configured'}`")
                    st.caption(f"CAF directory: `{config.get('caf_cli_directory') or 'not configured'}`")
                    st.caption(f"CLI command: `{config.get('caf_cli_command') or 'not configured'}`")

                for title, commands_key, phase in (("**Startup**", "startup_commands", "Startup"),):
                    with st.expander(title, expanded=False):
                        steps = config.get(commands_key, [])
                        if not steps:
                            st.caption(f"No {phase.lower()} commands configured — add them in the Config tab.")
                        for step_index, raw_step in enumerate(steps):
                            step = raw_step if isinstance(raw_step, dict) else {"commands": [raw_step]}
                            delay = float(step.get("delay_seconds", 0) or 0)
                            delay_text = f" (+{delay:g}s delay)" if delay else ""
                            st.caption(f"Step {step_index + 1}{delay_text}")
                            for command in step.get("commands", []):
                                if isinstance(command, dict) and command.get("type") == "prompt":
                                    with st.container(border=True):
                                        st.markdown("💬 **LLM Judge**")
                                        if command.get("system_prompt"):
                                            with st.expander("System Prompt", expanded=False):
                                                st.code(command["system_prompt"], language="text")
                                        if command.get("user_prompt"):
                                            with st.expander("User Prompt", expanded=False):
                                                st.code(command["user_prompt"], language="text")
                                    continue
                                text = command if isinstance(command, str) else command.get("command", "")
                                if text:
                                    st.code(text, language="bash")

                with st.expander("**Validation**", expanded=False):
                    validation_sets = config.get("validation_sets", [])
                    if not validation_sets:
                        st.caption("No validation sets configured — add them in the Config tab.")
                    for set_index, validation_set in enumerate(validation_sets):
                        selected_key = f"caf_cli_exec_vset_{set_index}_selected"
                        st.session_state.setdefault(selected_key, True)
                        description = validation_set.get("description", "")
                        label = f"**{set_index + 1}. {validation_set.get('name', 'Validation Set')}**"
                        if description:
                            label += f" — {description}"
                        with st.expander(label, expanded=st.session_state[selected_key]):
                            enabled_set = st.checkbox("Enable this Validation Set", key=selected_key)
                            for step_index, step in enumerate(validation_set.get("steps", [])):
                                st.caption(f"Step {step_index + 1}:")
                                for command_index, command in enumerate(step.get("commands", [])):
                                    if not isinstance(command, dict):
                                        continue
                                    command_key = f"caf_cli_exec_vset_{set_index}_step_{step_index}_cmd_{command_index}_selected"
                                    st.session_state.setdefault(command_key, command.get("enabled", True))
                                    enabled_command = st.checkbox(
                                        f"Enable check {command_index + 1}", key=command_key,
                                        disabled=not enabled_set,
                                    )
                                    if command.get("type") == "prompt":
                                        with st.container(border=True):
                                            st.markdown("💬 **Configured CYBERAGENTFLOW CLI**")
                                            if command.get("system_prompt"):
                                                with st.expander("System Prompt", expanded=False):
                                                    st.code(command["system_prompt"], language="text")
                                            if command.get("user_prompt"):
                                                with st.expander("User Prompt", expanded=False):
                                                    st.code(command["user_prompt"], language="text")
                                    elif command.get("command"):
                                        text = command["command"]
                                        if enabled_set and enabled_command:
                                            st.code(text, language="bash")
                                        else:
                                            st.markdown(f"~~`{text}`~~ *(skipped)*")

                with st.expander("**Completion**", expanded=False):
                    steps = config.get("completion_commands", [])
                    if not steps:
                        st.caption("No completion commands configured — add them in the Config tab.")
                    for step_index, raw_step in enumerate(steps):
                        step = raw_step if isinstance(raw_step, dict) else {"commands": [raw_step]}
                        delay = float(step.get("delay_seconds", 0) or 0)
                        delay_text = f" (+{delay:g}s delay)" if delay else ""
                        st.caption(f"Step {step_index + 1}{delay_text}")
                        for command in step.get("commands", []):
                            if isinstance(command, dict) and command.get("type") == "prompt":
                                with st.container(border=True):
                                    st.markdown("💬 **LLM Judge**")
                                    if command.get("system_prompt"):
                                        with st.expander("System Prompt", expanded=False):
                                            st.code(command["system_prompt"], language="text")
                                    if command.get("user_prompt"):
                                        with st.expander("User Prompt", expanded=False):
                                            st.code(command["user_prompt"], language="text")
                                continue
                            text = command if isinstance(command, str) else command.get("command", "")
                            if text:
                                st.code(text, language="bash")

        self.flush_config(project)
        run_in_progress = st.session_state.get("_run_in_progress", False)
        col_run, col_cancel, col_clear = st.columns([3, 1, 1])
        with col_run:
            run = st.button(
                "▶  Execute", key="btn_caf_cli_exec_run", type="primary", use_container_width=True,
                disabled=not ready or run_in_progress,
            )
        with col_cancel:
            if st.button("⏹  Stop", key="btn_caf_cli_exec_cancel", use_container_width=True,
                         disabled=not run_in_progress):
                st.session_state["cancel_requested"] = True
                shared = st.session_state.get("caf_cli_exec_shared", {})
                shared["cancel_requested"] = True
                cancel_ref = shared.get("cancel_ref")
                if isinstance(cancel_ref, list) and cancel_ref:
                    cancel_ref[0] = True
                st.rerun()
        with col_clear:
            if st.button("Clear Log", key="btn_caf_cli_exec_clear", use_container_width=True):
                st.session_state["caf_cli_exec_logs"] = []
                st.session_state["run_completed"] = False
                st.session_state["telemetry"] = {}
                st.rerun()

        st.markdown("**Execution Log**")
        log_box = st.empty()

        def render_logs() -> None:
            entries = [
                {"text": _clean_execution_log_text(entry)}
                for entry in st.session_state.get("caf_cli_exec_logs", [])[-200:]
            ]
            render_terminal(log_box, entries, lambda _line: "", height=360)

        if run and not run_in_progress:
            selected_sets = []
            for set_index, validation_set in enumerate(config.get("validation_sets", [])):
                if not st.session_state.get(f"caf_cli_exec_vset_{set_index}_selected", True):
                    continue
                selected_set = copy.deepcopy(validation_set)
                for step_index, step in enumerate(selected_set.get("steps", [])):
                    for command_index, command in enumerate(step.get("commands", [])):
                        if isinstance(command, dict):
                            command["enabled"] = st.session_state.get(
                                f"caf_cli_exec_vset_{set_index}_step_{step_index}_cmd_{command_index}_selected",
                                command.get("enabled", True),
                            )
                selected_sets.append(selected_set)
            run_config = {**config, "validation_sets": selected_sets, "active_project_id": project["id"]}
            st.session_state["caf_cli_exec_logs"] = []
            st.session_state["run_completed"] = False
            st.session_state["telemetry"] = {}
            st.session_state["cancel_requested"] = False
            st.session_state["_run_in_progress"] = True
            shared = {
                "cancel_requested": False,
                "logs": [],
                "completed": False,
                "telemetry": {},
            }
            st.session_state["caf_cli_exec_shared"] = shared
            thread = threading.Thread(
                target=_run_caf_cli_background,
                args=(self, project["id"], run_config, shared),
                daemon=True,
            )
            thread.start()
            st.session_state["caf_cli_exec_thread"] = thread
            st.rerun()

        @st.fragment(run_every="0.5s")
        def _poll_caf_execution() -> None:
            shared = st.session_state.get("caf_cli_exec_shared", {})
            st.session_state["caf_cli_exec_logs"] = shared.get("logs", [])
            render_logs()
            thread = st.session_state.get("caf_cli_exec_thread")
            if thread and thread.is_alive():
                if st.session_state.get("cancel_requested"):
                    shared["cancel_requested"] = True
                    cancel_ref = shared.get("cancel_ref")
                    if isinstance(cancel_ref, list) and cancel_ref:
                        cancel_ref[0] = True
                if shared.get("cancel_requested"):
                    st.caption("Stop requested — waiting for the active CAF command to return.")
                return

            st.session_state["_run_in_progress"] = False
            st.session_state.pop("caf_cli_exec_thread", None)
            if shared.get("completed"):
                telemetry = shared.get("telemetry", {})
                st.session_state["telemetry"] = telemetry
                st.session_state["run_completed"] = True
                from config.defaults import MAX_RUN_HISTORY
                history_key = f"run_history_{project['id']}"
                history = [*st.session_state.get(history_key, []), telemetry]
                st.session_state[history_key] = history[-MAX_RUN_HISTORY:]
            st.rerun()

        if run_in_progress:
            _poll_caf_execution()
        else:
            render_logs()

        if st.session_state.get("run_completed") and st.session_state.get("telemetry"):
            telemetry = st.session_state["telemetry"]
            if telemetry.get("run_aborted"):
                st.warning("CAF CLI evaluation did not complete.")
            elif telemetry.get("validation_passed") is False:
                st.error("CAF CLI evaluation completed, but validation failed.")
            else:
                st.success("CAF CLI evaluation completed.")

    def run_evaluation(self, env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
        run_env = _environment_for_config(config)
        try:
            return run_caf_cli_run(run_env, config, on_log)
        finally:
            if hasattr(run_env, "close"):
                run_env.close()
