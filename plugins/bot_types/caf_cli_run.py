"""CAF CLI Run bot plugin."""

from __future__ import annotations

import ast
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
import uuid
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


class CafSessionStartTimeoutError(RuntimeError):
    """CAF did not acknowledge a new session before the bounded API deadline."""


_REMOTE_JOB_REGISTRY_PATH = pathlib.Path.home() / ".modelscope" / "caf_remote_jobs.json"
_REMOTE_JOB_REGISTRY_LOCK = threading.Lock()


def _remote_job_key(config: Mapping[str, Any]) -> str:
    return str(config.get("active_project_id") or config.get("id") or "").strip()


def _caf_execution_status(shared: Mapping[str, Any]) -> tuple[str, str]:
    """Return a human-readable execution state and a Streamlit severity."""
    if shared.get("completed"):
        telemetry = shared.get("telemetry") or {}
        if telemetry.get("run_aborted"):
            return "Stopped", "warning"
        if telemetry.get("validation_passed") is False:
            return "Completed — validation failed", "error"
        return "Completed — succeeded", "success"
    terminal = str(shared.get("caf_remote_job_terminal_status") or "")
    if terminal:
        return f"Remote CAF job ended ({terminal}); final evaluation is settling", "warning"
    if shared.get("cancel_requested"):
        return "Stopping remote CAF job…", "warning"
    if shared.get("caf_remote_job_id"):
        return "Running", "info"
    return "Idle", "info"


def _load_remote_job_registry() -> dict[str, Any]:
    try:
        data = json.loads(_REMOTE_JOB_REGISTRY_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_remote_job_registry(data: dict[str, Any]) -> None:
    _REMOTE_JOB_REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _REMOTE_JOB_REGISTRY_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, _REMOTE_JOB_REGISTRY_PATH)


def _remember_remote_job(config: Mapping[str, Any], job_id: str) -> None:
    key = _remote_job_key(config)
    if not key:
        return
    with _REMOTE_JOB_REGISTRY_LOCK:
        registry = _load_remote_job_registry()
        registry[key] = {
            "job_id": job_id, "host": str(config.get("ssh_host") or ""),
            "port": int(config.get("ssh_port") or 22), "directory": str(config.get("caf_cli_directory") or ""),
            "started_at": time.time(),
        }
        _save_remote_job_registry(registry)


def _tracked_remote_job(config: Mapping[str, Any]) -> dict[str, Any] | None:
    key = _remote_job_key(config)
    if not key:
        return None
    with _REMOTE_JOB_REGISTRY_LOCK:
        entry = _load_remote_job_registry().get(key)
    if not isinstance(entry, dict) or not str(entry.get("job_id") or "").strip():
        return None
    if entry.get("host") != str(config.get("ssh_host") or "") or entry.get("directory") != str(config.get("caf_cli_directory") or ""):
        return None
    return entry


def _forget_remote_job(config: Mapping[str, Any], job_id: str | None = None) -> None:
    key = _remote_job_key(config)
    if not key:
        return
    with _REMOTE_JOB_REGISTRY_LOCK:
        registry = _load_remote_job_registry()
        entry = registry.get(key)
        if not isinstance(entry, dict) or (job_id and entry.get("job_id") != job_id):
            return
        registry.pop(key, None)
        _save_remote_job_registry(registry)


def _list_remote_caf_jobs(config: dict[str, Any], limit: int = 20) -> tuple[list[dict[str, Any]], str]:
    """Read durable remote job states over SFTP without using the SSH shell."""
    env = _environment_for_config(config)
    try:
        if hasattr(env, "connect"):
            env.connect()
        sftp = getattr(env, "_sftp", None)
        if sftp is None:
            return [], "Remote job listing requires an SSH execution target."
        entries = sorted(
            sftp.listdir_attr(".modelscope_jobs"),
            key=lambda item: float(getattr(item, "st_mtime", 0) or 0),
            reverse=True,
        )
        jobs: list[dict[str, Any]] = []
        # State reads are relatively expensive over a high-latency SFTP
        # session.  Inspect only the most recent candidates needed to fill the
        # list, rather than every historical job directory on each poll.
        for entry in entries[:max(1, limit * 2)]:
            job_id = str(getattr(entry, "filename", ""))
            if not job_id.startswith("modelscope-"):
                continue
            try:
                state = json.loads(env.read_file(f".modelscope_jobs/{job_id}/state.json"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict):
                continue
            jobs.append({
                "job_id": job_id,
                "status": str(state.get("status") or "unknown"),
                "prompt_id": str(state.get("prompt_id") or ""),
                "updated_at": float(state.get("updated_at") or getattr(entry, "st_mtime", 0) or 0),
            })
        jobs.sort(key=lambda job: job["updated_at"], reverse=True)
        return jobs[:max(1, limit)], ""
    except Exception as exc:
        return [], f"Could not list remote CAF jobs: {exc}"
    finally:
        if hasattr(env, "close"):
            env.close()


def _request_remote_caf_job_refresh(config: dict[str, Any], cache: dict[str, Any], limit: int = 20) -> None:
    """Refresh one shared job-list cache without blocking Streamlit rendering."""
    if cache.get("refreshing"):
        return
    cache["refreshing"] = True

    def worker() -> None:
        try:
            jobs, error = _list_remote_caf_jobs(config, limit=limit)
            signature = json.dumps({"jobs": jobs, "error": error}, sort_keys=True, default=str)
            changed = signature != cache.get("signature")
            cache["jobs"] = jobs
            cache["error"] = error
            cache["updated_at"] = time.time()
            cache["signature"] = signature
            if changed:
                cache["generation"] = int(cache.get("generation") or 0) + 1
        finally:
            cache["refreshing"] = False

    threading.Thread(target=worker, daemon=True).start()


def _purge_finished_remote_caf_jobs(config: dict[str, Any], job_ids: list[str]) -> tuple[int, str]:
    """Remove only explicitly selected, already-terminal durable job directories."""
    terminal_ids = [job_id for job_id in job_ids if re.fullmatch(r"modelscope-[A-Za-z0-9_-]+", job_id)]
    if not terminal_ids:
        return 0, ""
    env = _environment_for_config(config)
    removed = 0
    errors: list[str] = []
    try:
        for job_id in terminal_ids:
            result = env.execute(
                f"venv/bin/python remote_job.py purge --job-dir {shlex.quote('.modelscope_jobs/' + job_id)}",
                timeout=20,
            )
            if result.get("exit_code") == 0:
                removed += 1
            else:
                errors.append(f"{job_id}: {result.get('stderr') or result.get('stdout') or 'purge failed'}")
    except Exception as exc:
        errors.append(str(exc))
    finally:
        if hasattr(env, "close"):
            env.close()
    return removed, "\n".join(errors)


def _request_finished_remote_caf_job_purge(config: dict[str, Any], cache: dict[str, Any]) -> None:
    """Purge terminal jobs in a worker so the Execute page stays responsive."""
    if cache.get("purging"):
        return
    job_ids = [
        str(job.get("job_id") or "")
        for job in cache.get("jobs") or []
        if str(job.get("status") or "") in {"completed", "failed", "cancelled"}
    ]
    cache["purging"] = True

    def worker() -> None:
        try:
            removed, error = _purge_finished_remote_caf_jobs(config, job_ids)
            cache["message"] = f"Removed {removed} finished remote CAF job(s)." + (f" {error}" if error else "")
            cache["jobs"] = [
                job for job in cache.get("jobs") or []
                if str(job.get("job_id") or "") not in set(job_ids)
            ]
            cache["updated_at"] = time.time()
            cache["generation"] = int(cache.get("generation") or 0) + 1
        finally:
            cache["purging"] = False

    threading.Thread(target=worker, daemon=True).start()


def _render_remote_caf_jobs_panel(st: Any, project_id: str, remote_job_config: dict[str, Any]) -> None:
    """Render the durable remote CAF job panel exactly once per app render."""
    jobs_cache_key = f"caf_cli_remote_jobs_{project_id}"
    cached_jobs = st.session_state.setdefault(
        jobs_cache_key,
        {
            "jobs": [], "error": "", "updated_at": 0.0, "refreshing": False,
            "purging": False, "generation": 0, "rendered_generation": 0,
        },
    )
    with st.expander("Remote CAF Jobs", expanded=False):
        refresh_col, clear_col, status_col = st.columns([1, 2, 3])
        with refresh_col:
            if st.button("Refresh", key="btn_caf_cli_refresh_remote_jobs", use_container_width=True):
                _request_remote_caf_job_refresh(remote_job_config, cached_jobs)
        with clear_col:
            if st.button(
                "Clear finished", key="btn_caf_cli_clear_finished_jobs", use_container_width=True,
                disabled=bool(cached_jobs.get("purging")),
            ):
                _request_finished_remote_caf_job_purge(remote_job_config, cached_jobs)
        with status_col:
            if cached_jobs.get("refreshing"):
                st.caption("Updating…")
            elif cached_jobs.get("purging"):
                st.caption("Clearing finished jobs…")
            elif cached_jobs.get("updated_at"):
                st.caption("Auto-updates every 4 seconds")
        if cached_jobs.get("message"):
            st.caption(str(cached_jobs.pop("message")))
        if cached_jobs.get("error"):
            st.caption(str(cached_jobs["error"]))
        jobs = list(cached_jobs.get("jobs") or [])
        if not jobs and not cached_jobs.get("refreshing"):
            st.caption("No durable CAF jobs found on this SSH target.")
        with st.container(height=340, border=True):
            for job in jobs:
                job_id = str(job.get("job_id") or "")
                status = str(job.get("status") or "unknown")
                active = status in {"queued", "starting", "ready", "running"}
                updated_at = float(job.get("updated_at") or 0)
                updated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(updated_at)) if updated_at else "unknown time"
                row, attach_col, kill_col = st.columns([5, 1, 1])
                with row:
                    prompt_note = " — prompt active" if job.get("prompt_id") else ""
                    st.caption(f"`{job_id}` · **{status}** · {updated}{prompt_note}")
                with attach_col:
                    if st.button("Attach", key=f"btn_caf_cli_attach_{job_id}", disabled=not active, use_container_width=True):
                        shared = {
                            "cancel_requested": False, "logs": [], "tool_output": [],
                            "completed": False, "telemetry": {}, "caf_remote_job_id": job_id,
                            "caf_remote_job_recovery_pending": True,
                        }
                        _remember_remote_job(remote_job_config, job_id)
                        st.session_state["caf_cli_exec_shared"] = shared
                        st.session_state["caf_cli_exec_logs"] = []
                        st.session_state["_run_in_progress"] = False
                        st.rerun(scope="app")
                with kill_col:
                    if st.button("Kill", key=f"btn_caf_cli_kill_{job_id}", disabled=not active, use_container_width=True):
                        shared = {
                            "cancel_requested": True,
                            "logs": [f"[CAF JOB] Sending immediate cancel request to remote job {job_id} …"],
                            "tool_output": [], "completed": False, "telemetry": {},
                            "caf_remote_job_id": job_id, "caf_job_stop_requested": True,
                        }
                        _remember_remote_job(remote_job_config, job_id)
                        st.session_state["caf_cli_exec_shared"] = shared
                        st.session_state["caf_cli_exec_logs"] = shared["logs"]
                        st.session_state["_run_in_progress"] = False
                        threading.Thread(
                            target=_stop_caf_remote_job_background,
                            args=(dict(remote_job_config), shared), kwargs={"force": True}, daemon=True,
                        ).start()
                        _request_remote_caf_job_refresh(remote_job_config, cached_jobs)
                        st.rerun(scope="app")


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
    # Older remote MCP workers could serialize a timeout buffer with ``str``
    # and leave a whole Python bytes literal (``b'…'``) in the journal.  Keep
    # replay readable while those already-recorded events drain; current CAF
    # workers decode their buffers before publishing them.
    candidate = text.strip()
    if len(candidate) >= 3 and candidate.startswith(("b'", 'b"')):
        try:
            decoded = ast.literal_eval(candidate)
        except (SyntaxError, ValueError):
            decoded = None
        if isinstance(decoded, bytes):
            text = decoded.decode("utf-8", errors="replace")
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
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stopped = False
        self._last_event_sequence = 0
        self._durable_events_available: bool | None = None
        self._durable_event_long_poll_available = False
        self._prompt_id: str | None = None
        self._active_tool_progress: dict[str, str] = {}
        self._last_tool_output_preview: dict[str, str] = {}
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
        return raw_url

    def _request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", (10, 45))
        headers = {"Connection": "close", **dict(kwargs.pop("headers", {}) or {})}
        attempts = 2 if method.upper() == "GET" else 1
        last_error: requests.RequestException | None = None
        for attempt in range(attempts):
            try:
                response = self.http.request(
                    method, f"{self.base_url}{path}", timeout=timeout, headers=headers, **kwargs,
                )
                if isinstance(getattr(response, "status_code", None), int) and response.status_code >= 400:
                    # Preserve CAF's JSON error in the local App API log.
                    try:
                        detail = str(response.json().get("error") or "").strip()
                    except (ValueError, AttributeError):
                        detail = ""
                    if detail:
                        response.reason = detail
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
                if (
                    getattr(self, "config", {}).get("caf_cli_dangerous_no_prompt")
                    and capabilities.get("auto_continue_tool_timeouts") is not True
                ):
                    raise CafAppRestartRequiredError(
                        "The running CAF app does not support auto-continuing tool timeouts; restarting it is required."
                    )
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
                self._durable_event_long_poll_available = True
                self.on_log(f"[CAF APP] Ready (managed PID {self._managed_app_pid}).")
                return status
            if not self._managed_app_alive():
                tail = self._managed_app_log_tail()
                self._managed_app_pid = None
                raise RuntimeError("CAF app exited while starting." + (f" Log: {tail}" if tail else ""))
            time.sleep(0.25)
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
            # CAF tears down its worker asynchronously.  Wait for it to
            # finish before allocating a replacement session; otherwise the
            # old cleanup can invalidate the new session's loop.
            stop_deadline = time.monotonic() + 20.0
            while time.monotonic() < stop_deadline:
                stopped_status = self._app_status()
                if stopped_status and stopped_status.get("status") in {"idle", "stopped"}:
                    break
                time.sleep(0.25)
            else:
                raise CafSessionStartTimeoutError(
                    "CAF is still stopping the previous session. Wait briefly and retry."
                )
        payload = self._payload()
        try:
            # A start request normally returns once MCP discovery is complete.
            # Do not let an impaired SSH command channel hold the UI forever:
            # after this bound the UI offers an explicit retry instead.
            data = self._request("POST", "/api/session/start", json=payload, timeout=(5, 20)).json()
        except RuntimeError as start_error:
            raise CafSessionStartTimeoutError(
                "CAF did not acknowledge session start within 20 seconds. "
                "It may have started remotely; retry to check again."
            ) from start_error
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
            long_poll_available = (
                wait
                and bool(getattr(self, "_durable_event_long_poll_available", False))
            )
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
            getattr(self, "_last_tool_output_preview", {}).pop(tool, None)
            self._start_tool_progress(tool, label)
        elif kind == "tool_result":
            tool = str(event.get("tool") or "tool")
            getattr(self, "_active_tool_progress", {}).pop(tool, None)
            getattr(self, "_last_tool_output_preview", {}).pop(tool, None)
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
            output_preview = str(event.get("output_preview") or "").strip()
            previews = getattr(self, "_last_tool_output_preview", None)
            if previews is None:
                previews = {}
                self._last_tool_output_preview = previews
            if output_preview and previews.get(tool) != output_preview:
                previews[tool] = output_preview
                self.on_log(f"[VALIDATE CAF] [output] {output_preview}")
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
        while True:
            if time.monotonic() >= deadline:
                active_tools = getattr(self, "_active_tool_progress", {})
                if self.config.get("caf_cli_dangerous_no_prompt") and active_tools:
                    extension = max(60, int(self.config.get("caf_cli_tool_timeout") or 300))
                    deadline = time.monotonic() + extension
                    self.on_log(
                        "[CAF API] Run deadline reached while an auto-approved tool is still running; "
                        f"continuing monitoring for {extension} seconds."
                    )
                    continue
                break
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


class _CafRemoteJobSession(_CafAppSession):
    """One durable CAF worker, controlled only through SSH files/commands."""

    def __init__(self, env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> None:
        self.env = env
        self.config = config
        self.on_log = on_log
        self.run_id: str | None = None
        self.job_id: str | None = None
        self.job_dir: str | None = None
        self._last_event_sequence = 0
        self._active_tool_progress: dict[str, str] = {}
        self._last_tool_output_preview: dict[str, str] = {}
        self._tool_progress_stop: threading.Event | None = None
        self._tool_progress_thread: threading.Thread | None = None
        self._stopped = False

    def _runner(self, action: str, *args: str, timeout: int = 15) -> dict[str, Any]:
        if not self.job_dir:
            raise RuntimeError("Remote CAF job has not been created.")
        command = " ".join([
            "venv/bin/python", "remote_job.py", shlex.quote(action),
            "--job-dir", shlex.quote(self.job_dir), *[shlex.quote(arg) for arg in args],
        ])
        result = self.env.execute(command, timeout=timeout)
        if result.get("exit_code") != 0:
            raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "remote CAF job command failed").strip())
        return result

    @staticmethod
    def _json_result(result: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.loads(str(result.get("stdout") or "").strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError("Remote CAF job returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise RuntimeError("Remote CAF job returned an invalid response.")
        return data

    def start(self) -> None:
        if hasattr(self.env, "connect"):
            self.env.connect()
        remote_root = str(getattr(self.env, "remote_cwd", "") or "").rstrip("/")
        if not remote_root:
            raise RuntimeError("Remote CAF job transport requires an SSH execution environment.")
        self.job_id = f"modelscope-{uuid.uuid4().hex}"
        self.run_id = self.job_id
        self.job_dir = f".modelscope_jobs/{self.job_id}"
        tools_path = f"{remote_root}/{self.job_dir}/kali_tools.json"
        server_command = f"{shlex.quote(remote_root + '/venv/bin/python')} {shlex.quote(remote_root + '/mcp_kali.py')}"
        spec = {
            "job_id": self.job_id, "run_id": self.run_id,
            "url": self.config.get("caf_cli_url"), "provider": self.config.get("caf_cli_provider"),
            "api_key": self.config.get("caf_cli_api_key") or "", "ssl_verify": bool(self.config.get("caf_cli_verify_ssl", True)),
            "model": self.config.get("selected_model"), "server_command": server_command,
            "tools_config_path": tools_path, "context_window": int(self.config.get("caf_cli_context_window") or 8192),
            "max_turns": int(self.config.get("caf_cli_max_turns") or 20),
            "tool_timeout": int(self.config.get("caf_cli_tool_timeout") or 120),
            "network_policy": _caf_network_policy(self.config),
            "auto_approve_dangerous": bool(self.config.get("caf_cli_dangerous_no_prompt")),
        }
        for path, contents in (
            (f"{self.job_dir}/spec.json", json.dumps(spec)),
            (f"{self.job_dir}/kali_tools.json", json.dumps(_caf_selected_tools_config(self.config))),
        ):
            written = self.env.write_file(path, contents)
            if written.get("error"):
                raise RuntimeError(f"Could not prepare remote CAF job: {written['error']}")
        self.env.execute(f"chmod 700 {shlex.quote(self.job_dir)} && chmod 600 {shlex.quote(self.job_dir)}/spec.json {shlex.quote(self.job_dir)}/kali_tools.json", timeout=15)
        started = self._json_result(self._runner("start"))
        if not started.get("success"):
            raise RuntimeError("Remote CAF job could not be started.")
        # Record the ID before the worker becomes ready: a browser refresh or
        # Streamlit source reload during startup must still be able to reattach
        # and stop this exact remote process.
        _remember_remote_job(self.config, self.job_id)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            state = self._json_result(self._runner("status"))
            if state.get("status") == "ready":
                self.on_log(f"[CAF JOB] Started durable remote job {self.job_id} with {len(state.get('tools') or [])} tool(s).")
                return
            if state.get("status") == "failed":
                _forget_remote_job(self.config, self.job_id)
                raise RuntimeError(str(state.get("error") or "Remote CAF job failed while starting."))
            time.sleep(0.25)
        raise CafSessionStartTimeoutError("Remote CAF job did not become ready within 45 seconds. Retry the request.")

    def _events(self) -> list[dict[str, Any]]:
        data = self._json_result(self._runner("events", "--after", str(self._last_event_sequence), "--limit", "10"))
        events = [event for event in data.get("events") or [] if isinstance(event, dict)]
        for event in events:
            sequence = event.get("sequence")
            if isinstance(sequence, int):
                self._last_event_sequence = max(self._last_event_sequence, sequence)
        return events

    def _cancel(self) -> None:
        if self.job_dir:
            try:
                self._runner("cancel")
            except RuntimeError as exc:
                self.on_log(f"[CAF JOB] Cancel request delayed: {exc}")

    def prompt_transcript(self, prompt_id: str) -> list[dict[str, Any]]:
        """Read the complete durable transcript for one remote prompt.

        SSH command replay deliberately bounds individual event pages.  The
        journal itself is the authoritative artifact, so use SFTP here to
        retain full assistant replies and completed tool output for analytics.
        """
        if not self.job_dir or not prompt_id:
            return []
        try:
            raw = self.env.read_file(f"{self.job_dir}/events.jsonl")
        except Exception as exc:
            self.on_log(f"[CAF JOB] Full transcript read delayed: {exc}")
            return []
        events: list[dict[str, Any]] = []
        for line in str(raw).splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                isinstance(event, dict)
                and str(event.get("prompt_id") or "") == prompt_id
                and event.get("type") in {"response", "tool_call", "tool_result", "error"}
            ):
                events.append(event)
        return events

    def run_prompt(self, prompt: str, *, preserve_context: bool, cancel_ref: list[bool]) -> dict[str, Any]:
        if self.job_id is None:
            self.start()
        prompt_id = uuid.uuid4().hex
        request = {
            "prompt_id": prompt_id, "prompt": prompt,
            "scope": str(self.config.get("caf_scope") or "narrow").lower() if self.config.get("caf_cli_scope_enabled", True) else None,
            "urgency": str(self.config.get("caf_urgency") or "balanced").lower() if self.config.get("caf_cli_urgency_enabled", True) else None,
        }
        request_path = f"{self.job_dir}/requests/{self._last_event_sequence + 1:08d}_{prompt_id}.json"
        written = self.env.write_file(request_path, json.dumps(request))
        if written.get("error"):
            return {"stdout": "", "stderr": str(written["error"]), "exit_code": -1}
        deadline = time.monotonic() + int(self.config.get("caf_cli_timeout") or 600)
        responses: list[str] = []
        error = ""
        while True:
            if cancel_ref and cancel_ref[0]:
                self._cancel()
                return {"stdout": "\n".join(responses), "stderr": "Cancelled", "exit_code": -1}
            try:
                events = self._events()
            except RuntimeError as exc:
                # Event replay is an optimization for transcript fidelity, not
                # the sole source of truth for completion.  Still poll the
                # durable job state below when this SSH command channel stalls.
                self.on_log(f"[CAF JOB] Event read delayed; retrying: {exc}")
                events = []
            for event in events:
                event_prompt_id = str(event.get("prompt_id") or "")
                if event_prompt_id and event_prompt_id != prompt_id:
                    continue
                recorded_error = self._record_event(event, responses)
                if recorded_error:
                    error = recorded_error
                if event.get("type") == "prompt_done" and event_prompt_id == prompt_id:
                    return {
                        "stdout": "\n".join(responses), "stderr": error,
                        "exit_code": 0 if not error else -1, "caf_run_id": self.run_id,
                        "caf_prompt_id": prompt_id,
                    }
            try:
                state = self._json_result(self._runner("status"))
                status = str(state.get("status") or "")
                # The remote worker persists this exact prompt ID before it
                # writes `prompt_done` to the journal.  If an SSH command
                # channel resets at that instant, the durable state remains a
                # reliable completion acknowledgement and avoids converting a
                # completed, output-ignored prompt into exit code -1.
                if str(state.get("completed_prompt_id") or "") == prompt_id:
                    completed_error = str(state.get("completed_prompt_error") or error or "")
                    self.on_log("[CAF JOB] Durable prompt state confirms completion; recovered the terminal acknowledgement.")
                    return {
                        "stdout": "\n".join(responses), "stderr": completed_error,
                        "exit_code": 0 if not completed_error else -1, "caf_run_id": self.run_id,
                        "caf_prompt_id": prompt_id,
                    }
                if status == "failed":
                    return {"stdout": "\n".join(responses), "stderr": str(state.get("error") or error), "exit_code": -1}
                if status == "cancelled":
                    return {"stdout": "\n".join(responses), "stderr": "Cancelled", "exit_code": -1}
                if status == "completed":
                    return {
                        "stdout": "\n".join(responses),
                        "stderr": error or "Remote CAF job completed before this prompt returned a result.",
                        "exit_code": -1,
                    }
            except RuntimeError as exc:
                self.on_log(f"[CAF JOB] Prompt-state read delayed; retrying: {exc}")
            if time.monotonic() >= deadline:
                if self.config.get("caf_cli_dangerous_no_prompt") and self._active_tool_progress:
                    extension = max(60, int(self.config.get("caf_cli_tool_timeout") or 300))
                    deadline = time.monotonic() + extension
                    self.on_log(f"[CAF JOB] Run deadline reached while an auto-approved tool is active; continuing for {extension} seconds.")
                else:
                    return {"stdout": "\n".join(responses), "stderr": error or "Remote CAF job prompt timed out.", "exit_code": -1}
            time.sleep(0.5)

    def stop(self) -> None:
        self._stop_tool_progress()
        if self._stopped:
            return
        self._stopped = True
        if self.job_dir:
            try:
                self._runner("close")
            except RuntimeError:
                pass


def run_caf_remote_job(env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
    """Run validation through CAF's remote job journal, not its web API."""
    from core.evaluator import _init_telemetry, _run_validation_sets

    started = time.time()
    cancel_ref = config.get("cancel_requested_ref", [False])
    tool_calls = _run_caf_command_steps(env, config.get("startup_commands", []), label="STARTUP", default_timeout=int(config.get("caf_cli_timeout") or 600), on_log=on_log, config=config, helper_context=[])
    job_ref: list[_CafRemoteJobSession | None] = [None]
    responses: list[dict[str, str]] = []
    caf_transcript_events: list[dict[str, Any]] = []

    def execute_prompt(prompt: str, _label: str, preserve_context: bool) -> dict[str, Any]:
        if job_ref[0] is not None and not preserve_context:
            job_ref[0].stop()
            job_ref[0] = None
        if job_ref[0] is None:
            job_ref[0] = _CafRemoteJobSession(env, config, on_log)
        result = job_ref[0].run_prompt(prompt, preserve_context=preserve_context, cancel_ref=cancel_ref)
        if result.get("stdout"):
            responses.append({"prompt": prompt, "response": str(result["stdout"])})
        prompt_id = str(result.get("caf_prompt_id") or "")
        if prompt_id:
            caf_transcript_events.extend(job_ref[0].prompt_transcript(prompt_id))
        return result

    validation_config = {**config, "type": "llama_cli_bot", "execute_prompt_callback": execute_prompt}
    try:
        passed, validation_results = _run_validation_sets(env, _caf_validation_sets_without_system_prompts(config.get("validation_sets", [])), on_log, cancel_ref=cancel_ref, config=validation_config)
    finally:
        if job_ref[0] is not None:
            job_ref[0].stop()
    telemetry = _init_telemetry(config)
    telemetry.update({"run_bot_type": "caf_cli_run_bot", "run_backend": "CyberAgentFlow remote job", "run_model": config.get("selected_model") or "(not configured)", "total_latency": round(time.time() - started, 3), "validation_passed": passed, "validation_sets_results": validation_results, "tool_calls": tool_calls, "prompt_responses": responses, "caf_transcript_events": caf_transcript_events, "run_aborted": bool(cancel_ref and cancel_ref[0])})
    if job_ref[0] is not None:
        telemetry["caf_run_id"] = job_ref[0].run_id
    on_log(f"[COMPLETE] {telemetry['total_latency']}s | validation={'PASS' if passed else 'FAIL'}")
    return telemetry


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
            output_prefix = "[VALIDATE CAF] [output] "
            if clean_message.startswith(output_prefix):
                output = clean_message[len(output_prefix):]
                tool_output = shared.setdefault("tool_output", [])
                seen_output = shared.setdefault("tool_output_seen", set())
                if output not in seen_output:
                    seen_output.add(output)
                    tool_output.append(output)
            job_match = re.match(r"\[CAF JOB\] Started durable remote job ([A-Za-z0-9_-]+)", clean_message)
            if job_match:
                shared["caf_remote_job_id"] = job_match.group(1)
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
    except CafSessionStartTimeoutError as exc:
        on_log("[CAF APP] Session start was not acknowledged in time. Awaiting your retry decision.")
        telemetry = {
            "run_aborted": True,
            "error": str(exc),
            "caf_session_start_timeout": True,
        }
    except Exception as exc:
        on_log(f"[ERROR] CAF CLI evaluation failed: {exc}")
        telemetry = {"run_aborted": True, "error": str(exc)}
    finally:
        if shared.get("cancel_requested"):
            cancel_ref[0] = True

    if cancel_ref[0]:
        telemetry["run_aborted"] = True
    if telemetry.get("run_aborted"):
        shared["execution_status"] = "Stopped"
        on_log("[EXECUTION COMPLETE] Stopped.")
    elif telemetry.get("validation_passed") is False:
        shared["execution_status"] = "Completed — validation failed"
        on_log("[EXECUTION COMPLETE] Completed — validation failed.")
    else:
        shared["execution_status"] = "Completed — succeeded"
        on_log("[EXECUTION COMPLETE] Completed — succeeded.")
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


def _stop_caf_remote_job_background(config: dict[str, Any], shared: dict[str, Any], *, force: bool = False) -> None:
    """Cancel a durable remote job even if its foreground event read stalled."""
    job_id = str(shared.get("caf_remote_job_id") or "").strip()
    if not job_id:
        shared.setdefault("logs", []).append("[CAF JOB] No remote job ID is available to cancel.")
        shared["caf_job_stop_requested"] = False
        return
    env = _environment_for_config(config)
    job_dir = shlex.quote(".modelscope_jobs/" + job_id)
    try:
        result = env.execute(
            f"venv/bin/python remote_job.py cancel {'--force ' if force else ''}--job-dir {job_dir}",
            timeout=15,
        )
        if result.get("exit_code") != 0:
            raise RuntimeError(str(result.get("stderr") or result.get("stdout") or "remote cancel command failed"))
        if force:
            shared.setdefault("logs", []).append("[CAF JOB] Remote job force-killed.")
            shared["caf_remote_job_output_finished"] = True
            shared["caf_remote_job_terminal_status"] = "cancelled"
            _forget_remote_job(config, job_id)
            return
        shared.setdefault("logs", []).append("[CAF JOB] Cancel request sent; waiting briefly for CAF to stop …")
        for _ in range(16):
            time.sleep(0.5)
            status_result = env.execute(
                f"venv/bin/python remote_job.py status --job-dir {job_dir}", timeout=15,
            )
            try:
                status = json.loads(str(status_result.get("stdout") or "{}")).get("status")
            except json.JSONDecodeError:
                status = None
            if status in {"completed", "failed", "cancelled"}:
                shared.setdefault("logs", []).append(f"[CAF JOB] Remote job stopped ({status}).")
                shared["caf_remote_job_output_finished"] = True
                shared["caf_remote_job_terminal_status"] = str(status)
                _forget_remote_job(config, job_id)
                return
        result = env.execute(
            f"venv/bin/python remote_job.py cancel --force --job-dir {job_dir}", timeout=15,
        )
        if result.get("exit_code") == 0:
            shared.setdefault("logs", []).append("[CAF JOB] Remote job was force-stopped after graceful cancellation timed out.")
            shared["caf_remote_job_output_finished"] = True
            shared["caf_remote_job_terminal_status"] = "cancelled"
            _forget_remote_job(config, job_id)
        else:
            shared.setdefault("logs", []).append(f"[CAF JOB] Force-stop failed: {result.get('stderr') or result.get('stdout')}")
    except Exception as exc:
        shared.setdefault("logs", []).append(f"[CAF JOB] Cancel request failed: {exc}")
    finally:
        if hasattr(env, "close"):
            env.close()
        shared["caf_job_stop_requested"] = False


def _collect_remote_job_output(config: dict[str, Any], shared: dict[str, Any], job_id: str) -> None:
    """Mirror durable tool previews into the UI through SFTP journal reads."""
    env = _environment_for_config(config)
    after = 0
    byte_offset = 0
    pending = b""
    transport_state: dict[str, Any] = {
        "error": "", "last_notice": 0.0, "failures": 0, "retry_after": 0.0,
    }
    seen = shared.setdefault("tool_output_seen", set())
    replayed_log_lines = {str(line) for line in shared.setdefault("logs", [])}

    def reconnect_after_failure(exc: Exception) -> None:
        """Discard a dead SSH/SFTP client and bound reconnect churn."""
        nonlocal env
        transport_state["error"] = str(exc)
        transport_state["failures"] = int(transport_state.get("failures") or 0) + 1
        delay = min(5.0, 0.25 * (2 ** min(4, int(transport_state["failures"]) - 1)))
        transport_state["retry_after"] = time.monotonic() + delay
        try:
            if hasattr(env, "close"):
                env.close()
        finally:
            # A disconnected SFTP channel can remain attached to an otherwise
            # active Paramiko transport.  Use a new environment on retry.
            env = _environment_for_config(config)

    def mark_connection_healthy() -> None:
        transport_state["error"] = ""
        transport_state["failures"] = 0
        transport_state["retry_after"] = 0.0

    def state() -> str | None:
        if time.monotonic() < float(transport_state.get("retry_after") or 0.0):
            return None
        try:
            value = str(json.loads(env.read_file(f".modelscope_jobs/{job_id}/state.json")).get("status") or "")
            mark_connection_healthy()
            return value
        except Exception as exc:
            reconnect_after_failure(exc)
            return None

    def journal_chunk() -> bytes:
        nonlocal byte_offset
        if time.monotonic() < float(transport_state.get("retry_after") or 0.0):
            return b""
        try:
            if hasattr(env, "connect"):
                env.connect()
            sftp = getattr(env, "_sftp", None)
            if sftp is None:
                return b""
            path = f".modelscope_jobs/{job_id}/events.jsonl"
            with sftp.open(path, "rb") as handle:
                handle.seek(byte_offset)
                chunk = handle.read(64 * 1024)
            byte_offset += len(chunk)
            mark_connection_healthy()
            return chunk
        except Exception as exc:
            reconnect_after_failure(exc)
            return b""

    def replay_log(event: dict[str, Any]) -> None:
        """Keep the Execute Log current when its foreground SSH read stalls."""
        kind = str(event.get("type") or "")
        line = ""
        if kind == "status":
            message = str(event.get("message") or "").strip()
            if message:
                line = f"[VALIDATE CAF] [status] {message}"
        elif kind == "response":
            text = str(event.get("text") or event.get("content") or "").strip()
            if text:
                line = f"[VALIDATE CAF] Assistant: {text}"
        elif kind == "tool_call":
            tool = str(event.get("tool") or "tool")
            line = f"[VALIDATE CAF] [tool] {tool} {json.dumps(event.get('args') or {}, default=str)}"
        elif kind == "tool_result":
            line = (
                f"[VALIDATE CAF] [result] {event.get('tool') or 'tool'} "
                f"exit={event.get('exit_code', '?')} duration_ms={event.get('duration_ms', '?')}"
            )
        elif kind == "tool_status":
            tool = str(event.get("tool") or "tool")
            elapsed = max(0, int(event.get("elapsed_seconds") or 0))
            total = max(0, int(event.get("stdout_len") or 0)) + max(0, int(event.get("stderr_len") or 0))
            line = f"[CAF TOOL PROGRESS] {tool} — running {elapsed}s"
            if total:
                line += f", {total:,} bytes received"
            prefix = f"[CAF TOOL PROGRESS] {tool} —"
            logs = shared.setdefault("logs", [])
            for index in range(len(logs) - 1, -1, -1):
                if str(logs[index]).startswith(prefix):
                    logs[index] = line
                    return
        elif kind == "error":
            message = str(event.get("message") or "").strip()
            if message:
                line = f"[VALIDATE CAF] [error] {message}"
                shared["caf_remote_prompt_error"] = message
        elif kind == "prompt_done":
            line = "[VALIDATE CAF] [prompt] CAF prompt completed."
            shared["caf_remote_prompt_completed"] = True
        if line and line not in replayed_log_lines:
            replayed_log_lines.add(line)
            shared.setdefault("logs", []).append(line)

    try:
        while not shared.get("caf_remote_job_output_finished"):
            job_status = state()
            if job_status is None and transport_state["error"]:
                now = time.monotonic()
                if now - float(transport_state["last_notice"]) >= 15:
                    detail = str(transport_state["error"]).strip() or "SSH/SFTP connection is unavailable"
                    shared.setdefault("logs", []).append(f"[CAF JOB] SFTP journal read delayed: {detail}")
                    transport_state["last_notice"] = now
            terminal_status = job_status if job_status in {"completed", "failed", "cancelled"} else None
            # A persisted ID can outlive a browser/session while the remote
            # worker finishes.  Check it before replaying its old journal so a
            # refresh never claims to have reattached to an already-cancelled
            # job (or replays a large completed transcript first).
            if shared.get("caf_remote_job_recovery_pending"):
                if terminal_status:
                    # Do not claim an attachment to an already-finished job,
                    # but still drain every event left in its journal below.
                    shared["caf_remote_job_recovery_pending"] = False
                elif job_status not in {"queued", "starting", "ready", "running"}:
                    time.sleep(0.5)
                    continue
                else:
                    shared["caf_remote_job_recovery_pending"] = False
                    shared.setdefault("logs", []).append(f"[CAF JOB] Reattached to durable remote job {job_id} after refresh.")
            chunk = journal_chunk()
            if chunk:
                data = pending + chunk
                lines = data.splitlines(keepends=True)
                pending = b""
                if lines and not lines[-1].endswith((b"\n", b"\r")):
                    pending = lines.pop()
                for line in lines:
                    try:
                        event = json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    sequence = event.get("sequence")
                    if isinstance(sequence, int):
                        if sequence <= after:
                            continue
                        after = sequence
                    replay_log(event)
                    if (
                        event.get("type") == "prompt_done"
                        and shared.get("caf_remote_prompt_error")
                        and not shared.get("caf_job_stop_requested")
                    ):
                        # CAF has made a terminal prompt error explicit.  Do
                        # not leave the Execute controls disabled while a
                        # separate foreground SSH read waits to time out.
                        shared["cancel_requested"] = True
                        shared["caf_job_stop_requested"] = True
                        shared.setdefault("logs", []).append(
                            "[CAF JOB] CAF ended the prompt with an error; stopping the stranded remote job …"
                        )
                        threading.Thread(
                            target=_stop_caf_remote_job_background,
                            args=(dict(config), shared),
                            daemon=True,
                        ).start()
                    event_type = str(event.get("type") or "")
                    if event_type == "tool_result":
                        tool = str(event.get("tool") or "tool")
                        output = str(event.get("result") or event.get("output") or "").strip()
                        if output:
                            rendered_output = f"[{tool}]\n{output}"
                            if rendered_output not in seen:
                                seen.add(rendered_output)
                                shared.setdefault("tool_output", []).append(rendered_output)
                        continue
                    if event_type != "tool_status":
                        continue
                    output = str(event.get("output_preview") or "").strip()
                    if output and output not in seen:
                        seen.add(output)
                        shared.setdefault("tool_output", []).append(output)
            if terminal_status and not chunk and not pending:
                shared["caf_remote_job_output_finished"] = True
                shared["caf_remote_job_terminal_status"] = str(terminal_status)
                _forget_remote_job(config, job_id)
                shared.setdefault("logs", []).append(f"[CAF JOB] Remote job finished ({terminal_status}).")
                return
            # Drain an already-existing journal quickly after a page reload;
            # once caught up, return to a low-churn polling cadence.
            time.sleep(0.1 if chunk else 0.5)
    finally:
        if hasattr(env, "close"):
            env.close()


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
        # Retained for local CAF web-app use; remote jobs do not depend on
        # either an app URL or an app-managed MCP command.
        for key in ("caf_cli_app_url", "caf_cli_app_server_command"):
            if not str(config.get(key) or "").strip():
                config[key] = self.default_config()[key]
        if config.get("execution_target") == "ssh":
            config["caf_cli_transport"] = "job"
        elif config.get("caf_cli_transport") not in {"cli", "api"}:
            config["caf_cli_transport"] = "cli"
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
                help="Run remote command steps and CAF job workers as root via the configured SSH user's password.",
            )
            st.text_input("CAF directory", key="caf_cli_directory")
            st.text_input("CLI command", key="caf_cli_command", help="Normally ./start_cli.sh; validation prompts invoke one-shot run.")
            if target == "ssh":
                st.session_state["caf_cli_transport"] = "job"
                st.caption("Remote transport: durable CAF job runner (SSH/SFTP event journal; no CAF web server required).")
            else:
                st.selectbox(
                    "CAF transport", ["cli", "api"], key="caf_cli_transport",
                    format_func=lambda value: "CLI" if value == "cli" else "CAF App API (local)",
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
        remote_job_config = {**config, "active_project_id": project["id"]}
        tracked_remote_job = (
            _tracked_remote_job(remote_job_config)
            if config.get("execution_target") == "ssh" else None
        )
        if tracked_remote_job:
            shared = st.session_state.get("caf_cli_exec_shared")
            if not isinstance(shared, dict) or not shared.get("caf_remote_job_id"):
                job_id = str(tracked_remote_job["job_id"])
                shared = {
                    "cancel_requested": False,
                    "logs": [],
                    "tool_output": [],
                    "completed": False,
                    "telemetry": {},
                    "caf_remote_job_id": job_id,
                    "caf_remote_job_recovery_pending": True,
                }
                st.session_state["caf_cli_exec_shared"] = shared
                st.session_state["caf_cli_exec_logs"] = shared["logs"]
            elif not shared.get("caf_remote_job_id"):
                shared["caf_remote_job_id"] = str(tracked_remote_job["job_id"])
        current_shared = st.session_state.get("caf_cli_exec_shared", {})
        active_remote_job = bool(
            tracked_remote_job
            and not current_shared.get("completed")
            and not current_shared.get("caf_remote_job_output_finished")
        )
        active_session_conflict = bool(st.session_state.get("caf_cli_active_session_conflict"))
        app_restart_required = bool(st.session_state.get("caf_cli_app_restart_required"))
        session_start_timeout = bool(st.session_state.get("caf_cli_session_start_timeout"))
        stop_active_and_retry = False
        restart_app_and_retry = False
        retry_start = False

        if target == "ssh":
            jobs_cache_key = f"caf_cli_remote_jobs_{project['id']}"
            cached_jobs = st.session_state.setdefault(
                jobs_cache_key,
                {
                    "jobs": [], "error": "", "updated_at": 0.0, "refreshing": False,
                    "purging": False, "generation": 0, "rendered_generation": 0,
                },
            )
            if not cached_jobs.get("updated_at") and not cached_jobs.get("refreshing"):
                _request_remote_caf_job_refresh(remote_job_config, cached_jobs)
            _render_remote_caf_jobs_panel(st, project["id"], remote_job_config)

            @st.fragment(run_every="4s")
            def refresh_remote_jobs_timer() -> None:
                """Refresh data invisibly; only full reruns replace the visible panel."""
                cache = st.session_state.get(jobs_cache_key, cached_jobs)
                now = time.time()
                if not cache.get("refreshing") and now - float(cache.get("updated_at") or 0) >= 4:
                    _request_remote_caf_job_refresh(remote_job_config, cache)
                if int(cache.get("generation") or 0) != int(cache.get("rendered_generation") or 0):
                    cache["rendered_generation"] = int(cache.get("generation") or 0)
                    st.rerun(scope="app")

            refresh_remote_jobs_timer()
        col_run, col_cancel, col_clear = st.columns([3, 1, 1])
        with col_run:
            run = st.button(
                "▶  Execute", key="btn_caf_cli_exec_run", type="primary", use_container_width=True,
                disabled=not ready or run_in_progress or active_remote_job or active_session_conflict or app_restart_required or session_start_timeout,
            )
        with col_cancel:
            if st.button("⏹  Stop", key="btn_caf_cli_exec_cancel", use_container_width=True,
                         disabled=not (run_in_progress or active_remote_job)
                         or bool(st.session_state.get("caf_cli_exec_shared", {}).get("caf_job_stop_requested"))):
                st.session_state["cancel_requested"] = True
                shared = st.session_state.get("caf_cli_exec_shared", {})
                if active_remote_job and not shared.get("caf_remote_job_id") and tracked_remote_job:
                    shared["caf_remote_job_id"] = str(tracked_remote_job["job_id"])
                shared["cancel_requested"] = True
                cancel_ref = shared.get("cancel_ref")
                if isinstance(cancel_ref, list) and cancel_ref:
                    cancel_ref[0] = True
                if (
                    config.get("execution_target") == "ssh"
                    and shared.get("caf_remote_job_id")
                    and not shared.get("caf_job_stop_requested")
                ):
                    shared["caf_job_stop_requested"] = True
                    shared.setdefault("logs", []).append("[CAF JOB] Sending immediate cancel request to the remote job …")
                    threading.Thread(
                        target=_stop_caf_remote_job_background,
                        args=(dict(remote_job_config), shared),
                        daemon=True,
                    ).start()
                elif (
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
                st.session_state.pop("caf_cli_session_start_timeout", None)
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

        if session_start_timeout and not run_in_progress:
            st.warning(
                "CAF did not acknowledge starting the session in time. It may have started remotely; "
                "retrying checks CAF first and will not stop an active session automatically."
            )
            retry_col, keep_col, _ = st.columns([2, 2, 1])
            with retry_col:
                retry_start = st.button(
                    "Retry CAF request", key="btn_caf_cli_retry_start", type="primary", use_container_width=True,
                )
            with keep_col:
                if st.button("Keep current CAF session", key="btn_caf_cli_keep_start_timeout", use_container_width=True):
                    st.session_state.pop("caf_cli_session_start_timeout", None)
                    st.rerun()

        def render_logs() -> None:
            shared = st.session_state.get("caf_cli_exec_shared", {})
            execution_status, status_style = _caf_execution_status(shared)
            status_text = f"**Execution Status:** {execution_status}"
            if status_style == "success":
                st.success(status_text)
            elif status_style == "error":
                st.error(status_text)
            elif status_style == "warning":
                st.warning(status_text)
            else:
                st.info(status_text)
            log_col, output_col = st.columns(2)
            log_col.markdown("**Execution Log**")
            output_col.markdown("**Tool Output**")
            log_box = log_col.empty()
            tool_output_box = output_col.empty()
            if not shared.get("caf_remote_job_id"):
                for entry in shared.get("logs", []):
                    match = re.match(r"\[CAF JOB\] Started durable remote job ([A-Za-z0-9_-]+)", str(entry))
                    if match:
                        shared["caf_remote_job_id"] = match.group(1)
                        break
            if (
                config.get("execution_target") == "ssh"
                and shared.get("caf_remote_job_id")
                and shared.get("caf_remote_job_output_monitor_version") != 4
                and not shared.get("caf_remote_job_output_finished")
            ):
                shared["caf_remote_job_output_monitor_started"] = True
                shared["caf_remote_job_output_monitor_version"] = 4
                threading.Thread(
                    target=_collect_remote_job_output,
                    args=(dict(remote_job_config), shared, str(shared["caf_remote_job_id"])),
                    daemon=True,
                ).start()
            entries = [
                {"text": _clean_execution_log_text(entry)}
                for entry in shared.get("logs", [])[-200:]
            ]
            render_terminal(log_box, entries, lambda _line: "", height=360, follow_newest=True)
            output_entries = [
                {"text": _clean_execution_log_text(entry)}
                for entry in shared.get("tool_output", [])
            ]
            execution_active = bool(
                not shared.get("completed")
                and (
                    run_in_progress
                    or active_remote_job
                    or (
                        shared.get("caf_remote_job_id")
                        and not shared.get("caf_remote_job_output_finished")
                    )
                )
            )
            render_terminal(
                tool_output_box, output_entries, lambda _line: "", empty_msg="Awaiting tool output…",
                height=360, follow_newest=execution_active,
            )

        if (run or stop_active_and_retry or restart_app_and_retry or retry_start) and not (run_in_progress or active_remote_job):
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
            st.session_state.pop("caf_cli_session_start_timeout", None)
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
            thread = st.session_state.get("caf_cli_exec_thread")
            # A remote Stop is authoritative once its job journal reports a
            # terminal state.  Do not keep the controls disabled while an old
            # foreground event read finishes timing out in the background.
            if shared.get("cancel_requested") and shared.get("caf_remote_job_output_finished"):
                st.session_state["_run_in_progress"] = False
                st.session_state.pop("caf_cli_exec_thread", None)
                st.rerun()
            if thread and thread.is_alive():
                if st.session_state.get("cancel_requested"):
                    shared["cancel_requested"] = True
                    cancel_ref = shared.get("cancel_ref")
                    if isinstance(cancel_ref, list) and cancel_ref:
                        cancel_ref[0] = True
                if shared.get("cancel_requested"):
                    st.caption("Stop requested — waiting for the active CAF command to return.")
                return

            if active_remote_job and not shared.get("caf_remote_job_output_finished"):
                if shared.get("cancel_requested"):
                    st.caption("Stop requested — waiting for the remote CAF job to stop.")
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
                if telemetry.get("caf_session_start_timeout"):
                    st.session_state["caf_cli_session_start_timeout"] = True
                from config.defaults import MAX_RUN_HISTORY
                history_key = f"run_history_{project['id']}"
                history = [*st.session_state.get(history_key, []), telemetry]
                st.session_state[history_key] = history[-MAX_RUN_HISTORY:]
            st.rerun()

        # Only the live execution area reruns while CAF is active.  A full
        # app rerun on every incoming journal event makes unrelated Execute
        # controls and expanders visibly flash.
        shared_for_output = st.session_state.get("caf_cli_exec_shared", {})
        output_refresh = "1s" if (
            run_in_progress
            or active_remote_job
            or (
                shared_for_output.get("caf_remote_job_id")
                and not shared_for_output.get("caf_remote_job_output_finished")
            )
        ) else None

        @st.fragment(run_every=output_refresh)
        def _render_live_caf_execution() -> None:
            render_logs()

        _render_live_caf_execution()

        if run_in_progress or active_remote_job:
            _poll_caf_execution()


    def run_evaluation(self, env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
        run_env = _environment_for_config(config)
        try:
            if config.get("execution_target") == "ssh":
                return run_caf_remote_job(run_env, config, on_log)
            if config.get("caf_cli_transport") == "api":
                return run_caf_api_run(run_env, config, on_log)
            return run_caf_cli_run(run_env, config, on_log)
        finally:
            if hasattr(run_env, "close"):
                run_env.close()
