"""CAF CLI Run bot plugin."""

from __future__ import annotations

import base64
import ipaddress
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
from urllib.parse import urlsplit

import requests

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
    "caf_cli_sudo": "sudo",
    "caf_cli_directory": "caf_cli_directory",
    "caf_cli_command": "caf_cli_command",
    "caf_cli_transport": "caf_cli_transport",
    "caf_cli_app_url": "caf_cli_app_url",
    "caf_cli_app_server_command": "caf_cli_app_server_command",
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
    "caf_cli_sudo": False,
    "caf_cli_directory": "~/modelscope",
    "caf_cli_command": "./start_cli.sh",
    "caf_cli_transport": "api",
    "caf_cli_app_url": "http://127.0.0.1:5055",
    "caf_cli_app_server_command": "python3 mcp_kali.py",
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


class CafActiveSessionError(RuntimeError):
    """CAF is busy; the UI must obtain confirmation before interrupting it."""


class CafAppRestartRequiredError(RuntimeError):
    """A legacy CAF app must be restarted before durable API mode can run."""


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


def _caf_sudo_password(config: Mapping[str, Any]) -> str:
    """Use the selected target user's password for sudo authentication."""
    return str(config.get("ssh_password") or "").strip()


def _caf_with_sudo(command: str, config: Mapping[str, Any]) -> str:
    """Wrap one target-shell command in non-interactive sudo when enabled."""
    if not config.get("sudo"):
        return command
    password = _caf_sudo_password(config)
    if password:
        return f"printf '%s\\n' {shlex.quote(password)} | sudo -S -p '' bash -c {shlex.quote(command)}"
    return f"sudo -n bash -c {shlex.quote(command)}"


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
    return _caf_with_sudo(command, config)


def _display_command(command: str, config: dict[str, Any]) -> str:
    api_key = str(config.get("caf_cli_api_key") or "")
    if api_key:
        return command.replace(shlex.quote(api_key), "[REDACTED]")
    return re.sub(r"(--api-key\s+)(?:'[^']*'|\S+)", r"\1[REDACTED]", command)


def _caf_run_id(output: str) -> str | None:
    """Extract a CAF run ID from either its start or completion event.

    The CLI emits ``[started] run_id=…`` before the first tool call, while its
    transcript line is emitted only after the run is completely finished.  The
    early event lets ModelScope recover a completed run when an SSH stream
    stalls before the final transcript line arrives.
    """
    for pattern in (
        r"\[(?:chat|run)\]\s+Transcript:\s+runs/([^/\s]+)/",
        r"\[started\]\s+run_id=([^\s]+)",
    ):
        match = re.search(pattern, output)
        if match:
            return match.group(1)
    return None


def _clean_execution_log_text(value: Any) -> str:
    """Keep readable log text while dropping terminal control characters."""
    text = strip_ansi(str(value or "")).replace("\r\n", "\n").replace("\r", "\n")
    return "".join(char for char in text if char in ("\n", "\t") or ord(char) >= 32)


def _is_transient_caf_wait(value: str) -> bool:
    """Identify CAF's redrawable terminal spinner, not meaningful output."""
    text = str(value or "").strip()
    if "waiting" not in text.lower():
        return False
    remainder = re.sub(
        r"(?i)waiting(?:…|\.\.\.)?|caf>|[\s─━—-]+",
        "",
        text,
    )
    return not remainder


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
        result = env.execute(_caf_with_sudo(command, config), timeout=30)
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


def _caf_run_completed(env: Any, config: dict[str, Any], run_id: str) -> bool:
    """Return whether CAF has durably completed ``run_id`` on its target.

    This deliberately reads CAF's artifact rather than trusting the parent CLI
    channel.  A remote SSH stream can go quiet after CAF has written its final
    transcript and metadata, leaving a ModelScope background thread waiting
    until its much longer outer timeout.
    """
    root = pathlib.Path("runs")
    if config.get("execution_target") == "local":
        _proj_id = config.get("id")
        _default_dir = f"~/modelscope/{_proj_id}" if _proj_id else "~/modelscope"
        root = pathlib.Path(os.path.expanduser(str(config.get("caf_cli_directory") or _default_dir))) / "runs"
    try:
        metadata = json.loads(env.read_file(str(root / run_id / "metadata.json")))
        return metadata.get("status") == "completed"
    except Exception:
        return False


def _caf_run_ids(env: Any, config: dict[str, Any]) -> set[str]:
    """List durable CAF runs when the execution environment supports it.

    This gives the SSH recovery path a fallback when its channel closed before
    the CLI had emitted the early ``[started] run_id=…`` event.
    """
    from core.environment import LocalEnvironment, SSHEnvironment

    try:
        if isinstance(env, LocalEnvironment):
            project_id = config.get("id")
            default_dir = f"~/modelscope/{project_id}" if project_id else "~/modelscope"
            root = pathlib.Path(os.path.expanduser(str(config.get("caf_cli_directory") or default_dir))) / "runs"
            return {entry.name for entry in root.iterdir() if entry.is_dir()}
        if isinstance(env, SSHEnvironment):
            result = env.execute("find runs -mindepth 1 -maxdepth 1 -type d -printf '%f\\n'", timeout=10)
            if not isinstance(result, dict) or result.get("exit_code") != 0:
                return set()
            return {line.strip() for line in str(result.get("stdout") or "").splitlines() if line.strip()}
    except Exception:
        pass
    return set()


def _execute_caf_run_live(
    env: Any,
    command: str,
    *,
    timeout: int,
    on_chunk: Callable[[str], None],
    cancel_ref: list[bool] | None = None,
    completed_artifact: Callable[[], bool] | None = None,
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
            recovered_from_artifact = False
            channel_exit_code: int | None = None
            last_chunk_at = time.monotonic()
            next_artifact_check_at = last_chunk_at + 10.0

            def drain_ready_output() -> bool:
                """Drain both SSH streams and report whether any bytes arrived."""
                received = False
                while channel.recv_ready():
                    text = channel.recv(4096).decode("utf-8", errors="replace")
                    stdout.append(text)
                    on_chunk(text)
                    received = True
                while channel.recv_stderr_ready():
                    text = channel.recv_stderr(4096).decode("utf-8", errors="replace")
                    stderr.append(text)
                    on_chunk(text)
                    received = True
                return received

            while time.monotonic() < deadline:
                if cancel_ref and cancel_ref[0]:
                    cancelled = True
                    channel.close()
                    break

                if drain_ready_output():
                    last_chunk_at = time.monotonic()
                    next_artifact_check_at = last_chunk_at + 10.0

                if (
                    channel_exit_code is None
                    and channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    channel_exit_code = channel.recv_exit_status()
                    # The outer SSH command can report a nonzero exit while
                    # CAF's child run is still finalising its durable artifact.
                    # This is the intermittent case that made a fully
                    # completed /24 scan look failed in ModelScope.  Check the
                    # authoritative metadata before accepting that exit.
                    if completed_artifact is not None and completed_artifact():
                        recovered_from_artifact = True
                        stderr.append("CAF completed; recovered after an early SSH channel exit.\n")
                        break
                    if channel_exit_code == 0 or completed_artifact is None:
                        break
                    # Keep polling the already-known CAF run rather than
                    # returning a transient SSH failure.  No more terminal
                    # bytes are expected after a closed channel.
                    next_artifact_check_at = time.monotonic() + 1.0

                # CAF writes metadata.json when it is done.  If the parent
                # CLI's SSH stream has gone silent, this is a reliable way to
                # finish promptly and recover the transcript below instead of
                # waiting for the outer run timeout.
                now = time.monotonic()
                if (
                    completed_artifact is not None
                    and now >= next_artifact_check_at
                    and now - last_chunk_at >= 10.0
                ):
                    next_artifact_check_at = now + 5.0
                    if completed_artifact():
                        recovered_from_artifact = True
                        stderr.append("CAF completed; recovered from its run artifacts after SSH stream inactivity.\n")
                        channel.close()
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
            exit_code = 0 if recovered_from_artifact else (
                -1 if cancelled else (
                    channel_exit_code if channel_exit_code is not None else channel.recv_exit_status()
                )
            )
            try:
                channel.close()
            except Exception:
                pass
            return {
                "stdout": "".join(stdout), "stderr": "".join(stderr), "exit_code": exit_code,
                "recovered_from_artifact": recovered_from_artifact,
            }
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
    observed_run_id = [None]
    preexisting_run_ids = _caf_run_ids(env, config)
    waiting_started_at: list[float | None] = [None]

    def finish_wait() -> None:
        """Close the single redrawable wait row before real output resumes."""
        if waiting_started_at[0] is None:
            return
        elapsed = max(0, int(time.monotonic() - waiting_started_at[0]))
        on_log(f"[CAF WAIT DONE] CAF resumed after {elapsed}s")
        waiting_started_at[0] = None

    def stream(text: str) -> None:
        # CAF redraws its prompt spinner with bare carriage returns.  Preserve
        # meaningful newline-delimited output, but turn those redraws into a
        # single counter row instead of printing one "waiting" line per frame.
        clean_text = strip_ansi(text).replace("\r\n", "\n")
        if run_id := _caf_run_id(clean_text):
            observed_run_id[0] = run_id
        for fragment in clean_text.split("\n"):
            for raw_line in fragment.split("\r"):
                line = raw_line.strip()
                if not line:
                    continue
                if _is_transient_caf_wait(line):
                    if waiting_started_at[0] is None:
                        waiting_started_at[0] = time.monotonic()
                    elapsed = max(0, int(time.monotonic() - waiting_started_at[0]))
                    on_log(f"[CAF WAIT] Waiting for CAF… {elapsed}s")
                    continue

                # CAF sometimes leaves its spinner suffix on a real status
                # event.  The event itself is useful; only the suffix is not.
                line = re.sub(r"\s+waiting(?:…|\.\.\.)\s*$", "", line, flags=re.IGNORECASE).strip()
                if not line:
                    continue
                finish_wait()
                on_log(f"[VALIDATE CAF] {line}")

    def completed_artifact() -> bool:
        run_id = observed_run_id[0]
        if run_id:
            return _caf_run_completed(env, config, run_id)
        # A broken SSH stream may lose the [started] event entirely.  The only
        # CAF runs that appeared after this prompt started are candidates; use
        # their metadata as the authoritative completion signal.
        for candidate in sorted(_caf_run_ids(env, config) - preexisting_run_ids, reverse=True):
            if _caf_run_completed(env, config, candidate):
                observed_run_id[0] = candidate
                on_log(f"[VALIDATE CAF] Recovered run ID {candidate} from CAF artifacts after SSH stream loss.")
                return True
        return False

    timeout = int(config.get("caf_cli_timeout") or 600)
    result = _execute_caf_run_live(
        env, command, timeout=timeout, on_chunk=stream,
        cancel_ref=config.get("cancel_requested_ref"),
        completed_artifact=completed_artifact,
    )
    finish_wait()

    raw_output = strip_ansi(result.get("stdout", "") + result.get("stderr", ""))
    response = raw_output
    run_id = _caf_run_id(raw_output) or observed_run_id[0]
    if run_id:
        transcript, _ = _transcript(env, config, run_id, on_log)
        response = transcript or raw_output
    if result.get("recovered_from_artifact"):
        on_log("[VALIDATE CAF] CAF completed remotely; recovered the completed transcript after SSH stream inactivity.")
    return {
        "stdout": response,
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code", -1),
        "caf_run_id": run_id,
    }


def _caf_network_policy(config: dict[str, Any]) -> dict[str, list[str]]:
    """Build CAF's API network-policy object from the shared UI fields."""
    def entries(key: str) -> list[str]:
        return [
            item.strip() for item in str(config.get(key) or "").replace("\n", ",").split(",")
            if item.strip()
        ]

    return {
        "allow": entries("caf_cli_allow") or ["*"],
        "disallow": entries("caf_cli_disallow"),
    }


def _caf_selected_tools_config(config: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Return the same selected native-tool catalog used by the CLI runner."""
    catalog = config.get("caf_cli_tool_catalog", [])
    if not isinstance(catalog, list) or not catalog:
        raise RuntimeError("CAF API mode needs a fetched tool catalog. Use Fetch in the Tools section first.")
    enabled = set(config.get("caf_cli_enabled_tools", []))
    tools = [tool for tool in catalog if isinstance(tool, dict) and tool.get("name") in enabled]
    if not tools:
        raise RuntimeError("CAF API mode needs at least one selected tool.")
    return {"tools": tools}


def _nmap_cidr_target_count(arguments: Any) -> tuple[str, int] | None:
    """Return the first CIDR target and its address count from nmap args."""
    raw_args = arguments.get("args") if isinstance(arguments, dict) else arguments
    if not isinstance(raw_args, str):
        return None
    try:
        tokens = shlex.split(raw_args)
    except ValueError:
        tokens = raw_args.split()
    for token in tokens:
        if "/" not in token:
            continue
        try:
            network = ipaddress.ip_network(token, strict=False)
        except ValueError:
            continue
        return token, network.num_addresses
    return None


class _CafAppSession:
    """One CAF web-app session, consumed through its REST and SSE endpoints.

    The app owns a durable run directory and emits structured JSON events over
    SSE.  Using it avoids interpreting terminal control sequences flowing
    through a nested SSH shell and gives the caller a transcript endpoint for
    authoritative completion data.
    """

    def __init__(self, env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> None:
        self.env = env
        self.config = config
        self.on_log = on_log
        self.http = requests.Session()
        self.run_id: str | None = None
        self._tunnel: Any = None
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stopped = False
        self._last_event_sequence = 0
        self._durable_events_available: bool | None = None
        self._durable_event_long_poll_available = False
        self._prompt_id: str | None = None
        self._active_tool_progress: dict[str, str] = {}
        self._tool_progress_stop: threading.Event | None = None
        self._tool_progress_thread: threading.Thread | None = None
        # ModelScope owns only the CAF app instance it launched itself.  An
        # already-running app may belong to a user working in CAF directly, so
        # it is deliberately left running when this validation finishes.
        self._managed_app_pid: str | None = None
        self._managed_app_log: str | None = None
        self._session_started_by_modelscope = False
        self.base_url = self._connect_url()

    def _connect_url(self) -> str:
        raw_url = str(self.config.get("caf_cli_app_url") or "http://127.0.0.1:5055").rstrip("/")
        parsed = urlsplit(raw_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise RuntimeError("CAF app URL must be an absolute HTTP(S) URL.")
        if self.config.get("execution_target") != "ssh":
            return raw_url

        from core.environment import SSHEnvironment

        if not isinstance(self.env, SSHEnvironment):
            raise RuntimeError("CAF API mode requires an SSH environment for this project.")
        # Do not proxy HTTP over a hand-rolled local TCP listener here.  CAF
        # can return a durable replay response, yet the paramiko forwarder can
        # drop that response while closing a direct-tcpip channel.  Running
        # curl on the already-authenticated remote host uses the SSH command
        # channel instead and reaches CAF's loopback listener directly.
        self.on_log("[CAF API] Using CAF App API through the SSH command channel.")
        return raw_url

    def _ssh_api_request(
        self,
        method: str,
        path: str,
        *,
        timeout: tuple[float, float] | float,
        headers: dict[str, str],
        json_body: Any = None,
    ) -> requests.Response:
        """Execute one CAF API call on the SSH host without TCP forwarding."""
        if isinstance(timeout, tuple):
            connect_timeout, read_timeout = timeout
        else:
            connect_timeout = read_timeout = timeout
        url = f"{self.base_url}{path}"
        body = "" if json_body is None else json.dumps(json_body, separators=(",", ":"), default=str)
        encoded_body = base64.b64encode(body.encode("utf-8")).decode("ascii")
        header_args = " ".join(
            f"--header {shlex.quote(f'{key}: {value}')}" for key, value in headers.items()
        )
        if json_body is not None and not any(key.lower() == "content-type" for key in headers):
            header_args += " --header 'Content-Type: application/json'"
        # Base64 keeps arbitrary JSON out of shell syntax.  The response is
        # encoded for the same reason and framed by the first status line.
        command = (
            "body_file=$(mktemp); response_file=$(mktemp); "
            f"printf %s {shlex.quote(encoded_body)} | base64 -d > \"$body_file\"; "
            f"http_status=$(curl --silent --show-error --request {shlex.quote(method.upper())} "
            f"--connect-timeout {max(1, int(connect_timeout))} "
            f"--max-time {max(1, int(connect_timeout + read_timeout))} "
            f"{header_args} --data-binary @\"$body_file\" --output \"$response_file\" "
            f"--write-out '%{{http_code}}' {shlex.quote(url)}); curl_status=$?; "
            "printf '__CAF_HTTP_STATUS__%s\\n' \"$http_status\"; base64 -w0 \"$response_file\"; printf '\\n'; "
            "rm -f \"$body_file\" \"$response_file\"; exit $curl_status"
        )
        result = self.env.execute(command, timeout=max(10, int(connect_timeout + read_timeout + 5)))
        stdout = str(result.get("stdout") or "")
        status_line, separator, encoded_response = stdout.partition("\n")
        if result.get("exit_code") != 0 or not status_line.startswith("__CAF_HTTP_STATUS__"):
            detail = str(result.get("stderr") or result.get("stdout") or "remote curl failed").strip()
            raise requests.ConnectionError(detail)
        try:
            status_code = int(status_line.removeprefix("__CAF_HTTP_STATUS__"))
            content = base64.b64decode(encoded_response.strip() or "", validate=True)
        except (ValueError, TypeError) as exc:
            raise requests.ConnectionError("CAF SSH API call returned an invalid response frame") from exc
        response = requests.Response()
        response.status_code = status_code
        response.url = url
        response._content = content
        response.headers["Content-Type"] = "application/json"
        return response

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", (10, 45))
        headers = {"Connection": "close", **dict(kwargs.pop("headers", {}) or {})}
        # A direct-tcpip channel is a poor place to retain an HTTP keep-alive
        # connection across a long model/tool phase: Flask or the SSH peer can
        # close that idle socket, then the next replay read fails with a peer
        # reset even though CAF itself is still healthy.  Close each response
        # and retry idempotent reads once on a fresh channel.
        attempts = 3 if method.upper() == "GET" and getattr(self, "config", {}).get("execution_target") == "ssh" else 2 if method.upper() == "GET" else 1
        last_error: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                if getattr(self, "config", {}).get("execution_target") == "ssh":
                    response = self._ssh_api_request(
                        method, path, timeout=timeout, headers=headers, json_body=kwargs.pop("json", None),
                    )
                else:
                    response = self.http.request(
                        method, f"{self.base_url}{path}", timeout=timeout, headers=headers, **kwargs,
                    )
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    self.http.close()
                    time.sleep(0.1)
        raise RuntimeError(f"CAF app request {method} {path} failed: {last_error}") from last_error

    def _app_status(self) -> dict[str, Any] | None:
        """Return CAF's status only when the app is already reachable."""
        try:
            response = self._request("GET", "/api/session/status", timeout=(2, 2))
            data = response.json()
            return data if isinstance(data, dict) else {}
        except RuntimeError:
            return None

    def _app_capabilities(self) -> dict[str, Any] | None:
        """Return CAF capabilities, or ``None`` for pre-replay app versions."""
        try:
            response = self._request("GET", "/api/capabilities", timeout=(2, 2))
            data = response.json()
            return data if isinstance(data, dict) else None
        except RuntimeError:
            return None

    def _restart_existing_app(self) -> None:
        """Stop the listener on CAF's API port after an explicit UI approval."""
        parsed = urlsplit(str(self.config.get("caf_cli_app_url") or "http://127.0.0.1:5055"))
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.on_log("[CAF APP] Stopping the legacy CAF app at your request …")
        try:
            self._request("POST", "/api/session/stop", json={}, timeout=(5, 5))
        except RuntimeError:
            # The listener may already be unhealthy; the process-level stop
            # below is still the requested recovery action.
            pass
        find_command = _caf_with_sudo(f"fuser -n tcp {port} 2>&1", self.config)
        found = self.env.execute(find_command, timeout=10)
        raw = f"{found.get('stdout') or ''}\n{found.get('stderr') or ''}"
        # fuser formats listeners as ``5055/tcp: 123 456``.  Parse only the
        # part after the colon so the port number is never mistaken for a PID.
        _, separator, pid_text = raw.partition(":")
        pids = [pid for pid in re.findall(r"\b\d+\b", pid_text) if pid.isdigit()]
        if not pids:
            detail = raw.strip() or "no process ID returned"
            raise RuntimeError(
                f"Could not identify the CAF app listening on port {port}. "
                f"Enable CAF sudo and verify the app is reachable. ({detail})"
            )
        stop_command = _caf_with_sudo(f"kill -TERM {' '.join(pids)}", self.config)
        stopped = self.env.execute(stop_command, timeout=10)
        if stopped.get("exit_code") != 0:
            detail = stopped.get("stderr") or stopped.get("stdout") or "unknown error"
            raise RuntimeError(f"Could not stop the legacy CAF app: {detail}")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._app_status() is None:
                return
            time.sleep(0.25)
        raise RuntimeError("CAF app did not release its API port after the stop request.")

    def _launch_managed_app(self) -> None:
        """Start CAF in the selected target and retain its exact PID.

        ``start_ws.sh`` is CAF's project launcher: it selects the repository
        virtualenv and ``exec``s app.py.  Capturing its background PID lets us
        stop exactly this ModelScope-created process later without touching a
        separately started CAF server.
        """
        log_path = f"/tmp/modelscope_caf_app_{os.getpid()}_{int(time.time() * 1000)}.log"
        command = f"nohup ./start_ws.sh </dev/null > {shlex.quote(log_path)} 2>&1 & echo $!"
        if self.config.get("execution_target") != "ssh":
            directory = os.path.expanduser(str(self.config.get("caf_cli_directory") or "~/modelscope"))
            command = f"cd {shlex.quote(directory)} && {command}"
        command = _caf_with_sudo(command, self.config)
        target = getattr(self.env, "host", "local machine")
        privilege = " with sudo" if self.config.get("sudo") else ""
        self.on_log(f"[CAF APP] Starting managed CAF app on {target}{privilege} …")
        result = self.env.execute(command, timeout=15)
        output = str(result.get("stdout") or "").strip()
        pid = output.splitlines()[-1].strip() if output else ""
        if result.get("exit_code") != 0 or not pid.isdigit():
            detail = result.get("stderr") or result.get("stdout") or "no PID returned"
            raise RuntimeError(f"Could not start CAF app: {detail}")
        self._managed_app_pid = pid
        self._managed_app_log = log_path

    def _managed_app_alive(self) -> bool:
        if not self._managed_app_pid:
            return False
        command = _caf_with_sudo(f"kill -0 {self._managed_app_pid} 2>/dev/null", self.config)
        result = self.env.execute(command, timeout=10)
        return result.get("exit_code") == 0

    def _managed_app_log_tail(self) -> str:
        if not self._managed_app_log:
            return ""
        command = _caf_with_sudo(f"tail -c 1200 {shlex.quote(self._managed_app_log)} 2>/dev/null", self.config)
        result = self.env.execute(command, timeout=10)
        return str(result.get("stdout") or "").strip()

    def _ensure_app_running(self) -> dict[str, Any]:
        """Use an existing CAF app or start one owned by this execution."""
        status = self._app_status()
        if status is not None:
            capabilities = self._app_capabilities() or {}
            if capabilities.get("durable_event_replay") is True:
                self._durable_event_long_poll_available = bool(
                    capabilities.get("durable_event_long_poll") is True
                )
                self.on_log("[CAF APP] Reusing the running CAF app.")
                return status
            if not self.config.get("caf_cli_restart_incompatible_app"):
                raise CafAppRestartRequiredError(
                    "The running CAF app does not support durable event replay; restarting it is required."
                )
            self._restart_existing_app()

        self._launch_managed_app()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status = self._app_status()
            if status is not None:
                # This process was just launched from CAF's current project
                # launcher.  Newer servers support ``wait``; an older server
                # safely ignores the optional query parameter and still
                # returns cursor results, so prefer the low-churn protocol.
                self._durable_event_long_poll_available = True
                self.on_log(f"[CAF APP] Ready (managed PID {self._managed_app_pid}).")
                return status
            if not self._managed_app_alive():
                tail = self._managed_app_log_tail()
                self._managed_app_pid = None
                raise RuntimeError("CAF app exited while starting." + (f" Log: {tail}" if tail else ""))
            time.sleep(0.5)
        tail = self._managed_app_log_tail()
        self._stop_managed_app()
        raise RuntimeError("CAF app did not become ready within 45 seconds." + (f" Log: {tail}" if tail else ""))

    def _stop_managed_app(self) -> None:
        """Stop only the app ModelScope started for this validation."""
        if not self._managed_app_pid:
            return
        pid = self._managed_app_pid
        self._managed_app_pid = None
        try:
            command = _caf_with_sudo(f"kill -TERM {pid} 2>/dev/null", self.config)
            self.env.execute(command, timeout=10)
            for _ in range(8):
                probe = _caf_with_sudo(f"kill -0 {pid} 2>/dev/null", self.config)
                if self.env.execute(probe, timeout=10).get("exit_code") != 0:
                    self.on_log(f"[CAF APP] Stopped managed CAF app (PID {pid}).")
                    return
                time.sleep(0.25)
            force = _caf_with_sudo(f"kill -KILL {pid} 2>/dev/null", self.config)
            self.env.execute(force, timeout=10)
            self.on_log(f"[CAF APP] Force-stopped managed CAF app (PID {pid}).")
        except Exception:
            pass

    def _payload(self) -> dict[str, Any]:
        return {
            "url": self.config.get("caf_cli_url"),
            "provider": self.config.get("caf_cli_provider"),
            "api_key": self.config.get("caf_cli_api_key") or "",
            "ssl_verify": bool(self.config.get("caf_cli_verify_ssl", True)),
            "model": self.config.get("selected_model"),
            "server_command": self.config.get("caf_cli_app_server_command") or "python3 mcp_kali.py",
            "tools_config": _caf_selected_tools_config(self.config),
            "context_window": int(self.config.get("caf_cli_context_window") or 8192),
            "max_turns": int(self.config.get("caf_cli_max_turns") or 20),
            "tool_timeout": int(self.config.get("caf_cli_tool_timeout") or 120),
            "network_policy": _caf_network_policy(self.config),
            "auto_approve_dangerous": bool(self.config.get("caf_cli_dangerous_no_prompt")),
        }

    def start(self) -> None:
        status = self._ensure_app_running()
        if status.get("status") in {"starting", "running", "stopping"}:
            if not self.config.get("caf_cli_stop_active_session"):
                raise CafActiveSessionError(
                    "CAF app already has an active session; stopping it may interrupt a running process."
                )
            self.on_log("[CAF APP] Stopping the active CAF session at your request …")
            stopped = self._request("POST", "/api/session/stop", json={}).json()
            if not stopped.get("success"):
                raise RuntimeError(stopped.get("error") or "CAF app could not stop the active session.")
            # CAF resets its API state immediately, while its agent thread
            # tears down asynchronously.  Give that cleanup a brief head
            # start before allocating a fresh session.
            time.sleep(1)
        data = self._request("POST", "/api/session/start", json=self._payload()).json()
        if not data.get("success") or not data.get("run_id"):
            raise RuntimeError(data.get("error") or "CAF app did not start a session.")
        self.run_id = str(data["run_id"])
        self._session_started_by_modelscope = True
        self.on_log(f"[CAF API] Started run {self.run_id} with {len(data.get('tools') or [])} tool(s)")
        # CAF API mode intentionally requires the cursor-based durable journal.
        # A one-shot SSE consumer can lose terminal events during tools calls,
        # which is exactly the intermittent /24 failure this transport replaces.
        try:
            replay = self._request(
                "GET", f"/api/sessions/{self.run_id}/events?after=0&limit=1", timeout=(5, 5),
            ).json()
        except RuntimeError as exc:
            raise CafAppRestartRequiredError(
                "CAF did not provide the durable event replay endpoint; restart the CAF app and retry."
            ) from exc
        if not isinstance(replay, dict) or "events" not in replay:
            raise CafAppRestartRequiredError("CAF returned an invalid durable event replay response; restart the CAF app and retry.")
        self._durable_events_available = True
        self.on_log("[CAF API] Using durable event replay protocol.")

    def _replay_durable_events(self, *, wait: bool = True) -> None:
        """Backfill events CAF committed while SSE was unavailable.

        CAF API mode requires this cursor-based event journal.
        """
        if not self.run_id:
            return
        try:
            long_poll_available = wait and bool(getattr(self, "_durable_event_long_poll_available", False))
            long_poll = "&wait=10" if long_poll_available else ""
            response = self._request(
                "GET",
                f"/api/sessions/{self.run_id}/events?after={self._last_event_sequence}{long_poll}",
                timeout=(5, 20 if long_poll_available else 15),
            )
            data = response.json()
        except RuntimeError as exc:
            # A durable-capable app may still be stopped or briefly unreachable
            # mid-run.  Only a true 404 means the server was replaced by an
            # older implementation and merits the restart confirmation.
            if "404" in str(exc):
                raise CafAppRestartRequiredError(
                    "CAF durable event replay is no longer available; restart the CAF app and retry."
                ) from exc
            raise RuntimeError(f"CAF durable event replay request failed: {exc}") from exc
        self._durable_events_available = True
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            sequence = event.get("sequence")
            if not isinstance(sequence, int) or sequence <= self._last_event_sequence:
                continue
            self._last_event_sequence = sequence
            self._events.put(event)

    def _durable_prompt_status(self) -> dict[str, Any] | None:
        if not self._prompt_id:
            return None
        try:
            data = self._request("GET", f"/api/prompts/{self._prompt_id}", timeout=(5, 10)).json()
        except RuntimeError as exc:
            if "404" in str(exc):
                raise CafAppRestartRequiredError(
                    "CAF prompt status is no longer available; restart the CAF app and retry."
                ) from exc
            raise RuntimeError(f"CAF prompt-status request failed: {exc}") from exc
        self._durable_events_available = True
        return data if isinstance(data, dict) else None

    def _log_event(self, event: dict[str, Any]) -> str:
        kind = str(event.get("type") or "")
        if kind == "response":
            text = str(event.get("text") or "")
            if text:
                self.on_log(f"[VALIDATE CAF] Assistant: {text}")
            return text
        if kind == "tool_call":
            tool = str(event.get("tool") or "tool")
            arguments = event.get("args") or {}
            self.on_log(f"[VALIDATE CAF] [tool] {tool} {json.dumps(arguments)}")
            label = tool
            if tool == "nmap":
                cidr = _nmap_cidr_target_count(arguments)
                if cidr:
                    target, count = cidr
                    label = f"{tool} {target} — {count:,} targets"
            getattr(self, "_active_tool_progress", {}).update({tool: label})
            self._start_tool_progress(tool, label)
        elif kind == "tool_result":
            tool = str(event.get("tool") or "tool")
            getattr(self, "_active_tool_progress", {}).pop(tool, None)
            self._stop_tool_progress(tool)
            self.on_log(
                f"[VALIDATE CAF] [result] {tool} "
                f"exit={event.get('exit_code', '?')} duration_ms={event.get('duration_ms', '?')}"
            )
        elif kind == "tool_status":
            tool = str(event.get("tool") or "tool")
            elapsed = max(0, int(event.get("elapsed_seconds") or 0))
            base = getattr(self, "_active_tool_progress", {}).get(tool, tool)
            stdout_len = max(0, int(event.get("stdout_len") or 0))
            stderr_len = max(0, int(event.get("stderr_len") or 0))
            output_detail = f", {stdout_len + stderr_len:,} bytes received" if stdout_len or stderr_len else ""
            self.on_log(f"[CAF TOOL PROGRESS] {base} — running {elapsed}s{output_detail}")
        elif kind == "tool_timeout_decision":
            tool = str(event.get("tool") or "tool")
            elapsed = max(0, int(event.get("elapsed_seconds") or 0))
            message = str(event.get("message") or "Waiting for a tool-timeout decision.")
            self.on_log(f"[VALIDATE CAF] [paused] {tool} at {elapsed}s — {message}")
        elif kind == "tool_timeout_auto_continued":
            message = str(event.get("message") or "Auto-continuing tool timeout checkpoint.")
            self.on_log(f"[VALIDATE CAF] [continued] {message}")
        elif kind == "status":
            message = str(event.get("message") or "")
            if message:
                self.on_log(f"[VALIDATE CAF] [status] {message}")
        elif kind == "chat_done":
            message = str(event.get("message") or "Prompt completed.")
            self.on_log(f"[VALIDATE CAF] [done] {message}")
        elif kind == "error":
            self.on_log(f"[VALIDATE CAF] [error] {event.get('message') or 'Unknown CAF error'}")
        return ""

    def _start_tool_progress(self, tool: str, label: str) -> None:
        """Publish one in-place elapsed-time row while a CAF tool is active."""
        self._stop_tool_progress()
        stop = threading.Event()
        self._tool_progress_stop = stop
        started = time.monotonic()

        def publish() -> None:
            while not stop.wait(1.0):
                elapsed = max(1, int(time.monotonic() - started))
                self.on_log(f"[CAF TOOL PROGRESS] {label} — running {elapsed}s")

        thread = threading.Thread(target=publish, name=f"caf-progress-{tool}", daemon=True)
        self._tool_progress_thread = thread
        thread.start()

    def _stop_tool_progress(self, tool: str | None = None) -> None:
        """Stop the active display timer, optionally only for a matching tool."""
        progress = getattr(self, "_active_tool_progress", {})
        if tool is not None and tool in progress:
            return
        stop = getattr(self, "_tool_progress_stop", None)
        if stop is not None:
            stop.set()
        self._tool_progress_stop = None
        self._tool_progress_thread = None

    def _record_event(self, event: dict[str, Any], responses: list[str]) -> str:
        """Log one replayed event and return an error it reported, if any."""
        text = self._log_event(event)
        if text:
            responses.append(text)
        if str(event.get("type") or "") == "error":
            return str(event.get("message") or "CAF app reported an error.")
        return ""

    def _drain_replayed_events(self, responses: list[str]) -> str:
        """Emit every durable event already queued by a cursor replay."""
        error = ""
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return error
            recorded_error = self._record_event(event, responses)
            if recorded_error:
                error = recorded_error

    def run_prompt(self, prompt: str, *, preserve_context: bool, cancel_ref: list[bool]) -> dict[str, Any]:
        # A caller that does not preserve context creates a fresh app session
        # (and therefore a fresh SSH tunnel) rather than trying to reuse a
        # stopped connection here.
        if self.run_id is None:
            self.start()
        payload = {
            "prompt": prompt,
            "scope_enabled": bool(self.config.get("caf_cli_scope_enabled", True)),
            "urgency_enabled": bool(self.config.get("caf_cli_urgency_enabled", True)),
            "scope": str(self.config.get("caf_scope") or "narrow").lower(),
            "urgency": str(self.config.get("caf_urgency") or "balanced").lower(),
        }
        data = self._request("POST", "/api/session/chat", json=payload).json()
        if not data.get("success"):
            return {"stdout": "", "stderr": data.get("error") or "CAF app rejected prompt.", "exit_code": -1}
        self._prompt_id = str(data.get("prompt_id") or "") or None

        deadline = time.monotonic() + int(self.config.get("caf_cli_timeout") or 600)
        responses: list[str] = []
        error = ""
        next_replay_at = time.monotonic()
        next_prompt_status_at = next_replay_at
        while time.monotonic() < deadline:
            if cancel_ref and cancel_ref[0]:
                try:
                    self._request("POST", "/api/session/cancel_prompt", json={})
                except RuntimeError:
                    pass
                return {"stdout": "\n".join(responses), "stderr": "Cancelled", "exit_code": -1}
            try:
                event = self._events.get(timeout=0.25)
            except queue.Empty:
                now = time.monotonic()
                if now >= next_replay_at:
                    try:
                        self._replay_durable_events()
                    except CafAppRestartRequiredError:
                        raise
                    except RuntimeError as exc:
                        # A read-only replay request can briefly contend with a
                        # large event write.  The journal cursor makes retrying
                        # safe, so do not abort an otherwise-running tool call.
                        self.on_log(f"[CAF API] Event replay delayed; retrying: {exc}")
                    next_replay_at = now + (0.0 if getattr(self, "_durable_event_long_poll_available", False) else 1.0)
                if now >= next_prompt_status_at:
                    try:
                        prompt_status = self._durable_prompt_status()
                    except CafAppRestartRequiredError:
                        raise
                    except RuntimeError as exc:
                        self.on_log(f"[CAF API] Prompt-state poll delayed; continuing event replay: {exc}")
                        prompt_status = None
                    next_prompt_status_at = now + (10.0 if getattr(self, "_durable_event_long_poll_available", False) else 5.0)
                    if prompt_status and prompt_status.get("status") in {"completed", "failed", "cancelled"}:
                        # The status endpoint can settle before a preceding
                        # replay read recovers from an SSH-channel reset.  Do
                        # one immediate cursor backfill so the visible log
                        # still includes the tool result and final response.
                        try:
                            self._replay_durable_events(wait=False)
                        except RuntimeError:
                            pass
                        replay_error = self._drain_replayed_events(responses)
                        if replay_error:
                            error = replay_error
                        output = self.transcript() or "\n".join(responses)
                        status = str(prompt_status.get("status"))
                        self.on_log(f"[CAF API] Durable prompt state: {status}.")
                        return {
                            "stdout": output,
                            "stderr": str(prompt_status.get("error") or error),
                            "exit_code": 0 if status == "completed" and not error else -1,
                        }
                continue
            kind = str(event.get("type") or "")
            recorded_error = self._record_event(event, responses)
            if recorded_error:
                error = recorded_error
            if kind == "chat_done":
                return {"stdout": "\n".join(responses), "stderr": error, "exit_code": 0 if not error else -1}
        return {"stdout": "\n".join(responses), "stderr": error or "CAF app prompt timed out.", "exit_code": -1}

    def transcript(self) -> str:
        if not self.run_id:
            return ""
        try:
            data = self._request("GET", f"/api/sessions/{self.run_id}/transcript").json()
            return str(data.get("content") or "")
        except RuntimeError:
            return ""

    def stop_active_session(self) -> None:
        """Immediately stop CAF's current session, even from another client."""
        if self._app_status() is None:
            raise RuntimeError("CAF app is not reachable; no active session could be stopped.")
        data = self._request("POST", "/api/session/stop", json={}, timeout=(5, 10)).json()
        if not data.get("success"):
            raise RuntimeError(data.get("error") or "CAF app rejected the stop request.")
        self.on_log("[CAF APP] Stop signal sent to the active CAF session.")

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._stop_tool_progress()
        if self._session_started_by_modelscope:
            try:
                self._request("POST", "/api/session/stop", json={})
            except RuntimeError:
                pass
            self._session_started_by_modelscope = False
        self._stop_managed_app()
        if self._tunnel is not None:
            self._tunnel.close()


def run_caf_api_run(env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
    """Run CAF validation prompts through the CAF web app's REST/SSE API."""
    from core.evaluator import _init_telemetry, _run_validation_sets

    started = time.time()
    command_timeout = int(config.get("caf_cli_timeout") or 600)
    cancel_ref = config.get("cancel_requested_ref", [False])
    helper_context: list[dict[str, str]] = []
    tool_calls = _run_caf_command_steps(
        env, config.get("startup_commands", []), label="STARTUP", default_timeout=command_timeout, on_log=on_log,
        config=config, helper_context=helper_context,
    )
    api_ref: list[_CafAppSession | None] = [None]
    responses: list[str] = []
    prompt_responses: list[dict[str, str]] = []

    def execute_api_prompt(prompt: str, _label: str, preserve_context: bool) -> dict[str, Any]:
        if api_ref[0] is not None and not preserve_context:
            api_ref[0].stop()
            api_ref[0] = None
        if api_ref[0] is None:
            api_ref[0] = _CafAppSession(env, config, on_log)
        api = api_ref[0]
        result = api.run_prompt(prompt, preserve_context=preserve_context, cancel_ref=cancel_ref)
        output = result.get("stdout") or api.transcript()
        if output:
            output = str(output)
            responses.append(output)
            prompt_responses.append({"prompt": prompt, "response": output})
        return {**result, "stdout": output, "caf_run_id": api.run_id}

    validation_config = {
        **config,
        "type": "llama_cli_bot",
        "execute_prompt_callback": execute_api_prompt,
    }
    try:
        if cancel_ref and cancel_ref[0]:
            on_log("[CANCEL] CAF execution stopped before validation")
            passed, validation_results = False, []
        else:
            passed, validation_results = _run_validation_sets(
                env, _caf_validation_sets_without_system_prompts(config.get("validation_sets", [])), on_log,
                cancel_ref=cancel_ref, config=validation_config,
            )
    finally:
        if api_ref[0] is not None:
            api_ref[0].stop()

    if not (cancel_ref and cancel_ref[0]):
        tool_calls.extend(_run_caf_command_steps(
            env, config.get("completion_commands", []), label="CLEANUP", default_timeout=command_timeout, on_log=on_log,
            config=config, helper_context=helper_context,
        ))

    telemetry = _init_telemetry(config)
    telemetry.update({
        "run_bot_type": "caf_cli_run_bot",
        "run_backend": f"CyberAgentFlow API ({config.get('caf_cli_provider') or 'not configured'})",
        "run_model": config.get("selected_model") or "(not configured)",
        "total_latency": round(time.time() - started, 3),
        "llm_rounds": len(responses),
        "run_aborted": bool(cancel_ref and cancel_ref[0]),
        "validation_passed": passed,
        "validation_sets_results": validation_results,
        "tool_calls": tool_calls,
        "prompt_responses": prompt_responses,
        "llm_response": responses[-1] if responses else "",
        "caf_run_id": api_ref[0].run_id if api_ref[0] is not None else None,
    })
    validation_steps = [
        step for validation_set in validation_results for step in validation_set.get("steps", [])
        if isinstance(step, dict)
    ]
    telemetry["validation_stdout"] = "\n".join(str(step.get("stdout") or "") for step in validation_steps)
    telemetry["validation_stderr"] = "\n".join(str(step.get("stderr") or "") for step in validation_steps)
    telemetry["validation_exit_code"] = validation_steps[-1].get("exit_code") if validation_steps else None
    on_log(f"[COMPLETE] {telemetry['total_latency']}s | validation={'PASS' if passed else 'FAIL'}")
    return telemetry


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
            result = env.execute(_caf_with_sudo(text, config), timeout=timeout)
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
    log_lock = threading.Lock()

    def on_log(message: str) -> None:
        # Tool-progress timers and the evaluation worker both publish here.
        # Keep their in-place counter updates serialized for Streamlit.
        with log_lock:
            if shared.get("cancel_requested"):
                cancel_ref[0] = True
            clean_message = _clean_execution_log_text(message)
            logs = shared.setdefault("logs", [])
            # The wait row is deliberately updated in place so Streamlit displays
            # an elapsed-time counter instead of an ever-growing spinner history.
            if clean_message.startswith("[CAF WAIT]") and logs and str(logs[-1]).startswith("[CAF WAIT]"):
                logs[-1] = clean_message
                return
            if clean_message.startswith("[CAF TOOL PROGRESS]") and logs and str(logs[-1]).startswith("[CAF TOOL PROGRESS]"):
                logs[-1] = clean_message
                return
            logs.append(clean_message)
            session_log.log(clean_message)

    telemetry: dict[str, Any]
    try:
        telemetry = plugin.run_evaluation(None, run_config, on_log)
    except CafActiveSessionError as exc:
        on_log("[CAF APP] A CAF session is already active. Awaiting confirmation before interrupting it.")
        telemetry = {
            "run_aborted": True,
            "error": str(exc),
            "caf_active_session_conflict": True,
        }
    except CafAppRestartRequiredError as exc:
        on_log("[CAF APP] The running CAF app is a legacy version. Awaiting confirmation to restart it.")
        telemetry = {
            "run_aborted": True,
            "error": str(exc),
            "caf_app_restart_required": True,
        }
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


def _stop_caf_api_session_background(config: dict[str, Any], shared: dict[str, Any]) -> None:
    """Deliver Execute-page Stop immediately instead of waiting for a prompt poll."""
    def on_log(message: str) -> None:
        shared.setdefault("logs", []).append(_clean_execution_log_text(message))

    env = _environment_for_config(config)
    api: _CafAppSession | None = None
    try:
        api = _CafAppSession(env, config, on_log)
        api.stop_active_session()
    except Exception as exc:
        on_log(f"[CAF APP] Stop request failed: {exc}")
    finally:
        if api is not None:
            api.stop()
        if hasattr(env, "close"):
            env.close()
        shared["caf_api_stop_requested"] = False


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
            "sudo": False,
            "caf_cli_directory": "~/modelscope", "caf_cli_command": "./start_cli.sh",
            "caf_cli_transport": "api", "caf_cli_app_url": "http://127.0.0.1:5055",
            "caf_cli_app_server_command": "python3 mcp_kali.py",
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
        # These fields are required by API/SSE mode; keeping visible defaults
        # in persisted projects is clearer than relying on runtime fallbacks.
        for key in ("caf_cli_app_url", "caf_cli_app_server_command"):
            if not str(config.get(key) or "").strip():
                config[key] = self.default_config()[key]
        if config.get("caf_cli_transport") not in {"cli", "api"}:
            config["caf_cli_transport"] = "api"
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
            st.checkbox(
                "Run commands with sudo",
                key="caf_cli_sudo",
                help="Start/stop the managed CAF app and run direct command steps as root via the configured SSH user's password.",
            )
            st.text_input("CAF directory", key="caf_cli_directory")
            st.text_input("CLI command", key="caf_cli_command", help="Normally ./start_cli.sh; validation prompts invoke one-shot run.")
            transport = st.selectbox(
                "CAF transport",
                ["cli", "api"],
                key="caf_cli_transport",
                format_func=lambda value: "CLI over SSH" if value == "cli" else "CAF App API (SSE)",
                help="API mode uses CyberAgentFlow's structured event stream and transcript API instead of CLI stdout.",
            )
            if transport == "api":
                st.caption(
                    "ModelScope starts CAF on the selected target when needed, "
                    "tunnels to its local API, and stops only the app instance it started."
                )
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
        active_session_conflict = bool(st.session_state.get("caf_cli_active_session_conflict"))
        app_restart_required = bool(st.session_state.get("caf_cli_app_restart_required"))
        stop_active_and_retry = False
        restart_app_and_retry = False
        col_run, col_cancel, col_clear = st.columns([3, 1, 1])
        with col_run:
            run = st.button(
                "▶  Execute", key="btn_caf_cli_exec_run", type="primary", use_container_width=True,
                disabled=not ready or run_in_progress or active_session_conflict or app_restart_required,
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
                if (
                    config.get("caf_cli_transport") == "api"
                    and not shared.get("caf_api_stop_requested")
                ):
                    shared["caf_api_stop_requested"] = True
                    shared.setdefault("logs", []).append("[CAF APP] Sending immediate stop request to CAF …")
                    threading.Thread(
                        target=_stop_caf_api_session_background,
                        args=(dict(config), shared),
                        daemon=True,
                    ).start()
                st.rerun()
        with col_clear:
            if st.button("Clear Log", key="btn_caf_cli_exec_clear", use_container_width=True):
                st.session_state["caf_cli_exec_logs"] = []
                st.session_state["run_completed"] = False
                st.session_state["telemetry"] = {}
                st.session_state.pop("caf_cli_active_session_conflict", None)
                st.session_state.pop("caf_cli_app_restart_required", None)
                st.rerun()

        if active_session_conflict and not run_in_progress:
            st.warning("CAF already has an active session. Stopping it may interrupt a running tool or prompt.")
            confirm_col, keep_col, _ = st.columns([2, 2, 1])
            with confirm_col:
                stop_active_and_retry = st.button(
                    "Stop active CAF session and retry",
                    key="btn_caf_cli_stop_active_and_retry",
                    type="primary",
                    use_container_width=True,
                )
            with keep_col:
                if st.button("Keep current CAF session", key="btn_caf_cli_keep_active", use_container_width=True):
                    st.session_state.pop("caf_cli_active_session_conflict", None)
                    st.rerun()

        if app_restart_required and not run_in_progress:
            st.warning(
                "The running CAF app is an older version without durable event replay. "
                "Restarting it may interrupt any work still running in CAF."
            )
            restart_col, keep_col, _ = st.columns([2, 2, 1])
            with restart_col:
                restart_app_and_retry = st.button(
                    "Restart CAF app and retry",
                    key="btn_caf_cli_restart_app_and_retry",
                    type="primary",
                    use_container_width=True,
                )
            with keep_col:
                if st.button("Keep current CAF app", key="btn_caf_cli_keep_legacy_app", use_container_width=True):
                    st.session_state.pop("caf_cli_app_restart_required", None)
                    st.rerun()

        st.markdown("**Execution Log**")
        log_box = st.empty()

        def render_logs() -> None:
            entries = [
                {"text": _clean_execution_log_text(entry)}
                for entry in st.session_state.get("caf_cli_exec_logs", [])[-200:]
            ]
            render_terminal(log_box, entries, lambda _line: "", height=360, follow_newest=True)

        if (run or stop_active_and_retry or restart_app_and_retry) and not run_in_progress:
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
            run_config = {
                **config,
                "validation_sets": selected_sets,
                "active_project_id": project["id"],
                "caf_cli_stop_active_session": stop_active_and_retry,
                "caf_cli_restart_incompatible_app": restart_app_and_retry,
            }
            st.session_state["caf_cli_exec_logs"] = []
            st.session_state["run_completed"] = False
            st.session_state["telemetry"] = {}
            st.session_state["cancel_requested"] = False
            st.session_state.pop("caf_cli_active_session_conflict", None)
            st.session_state.pop("caf_cli_app_restart_required", None)
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
                if telemetry.get("caf_active_session_conflict"):
                    st.session_state["caf_cli_active_session_conflict"] = True
                if telemetry.get("caf_app_restart_required"):
                    st.session_state["caf_cli_app_restart_required"] = True
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
            if config.get("caf_cli_transport") == "api":
                return run_caf_api_run(run_env, config, on_log)
            return run_caf_cli_run(run_env, config, on_log)
        finally:
            if hasattr(run_env, "close"):
                run_env.close()
