"""
Persistent settings store for ModelScope.

Saves and loads user configuration to/from ~/.modelscope/settings.json so that
preferences survive app restarts.  Only non-sensitive, non-transient session-state
keys are persisted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

# ---------------------------------------------------------------------------
# Keys to persist
# ---------------------------------------------------------------------------

PERSIST_KEYS: frozenset[str] = frozenset({
    # Model / backend
    "backend_type",
    "llm_url",
    "model_dir",
    "context_size",
    "model_source_mode",
    "external_llm_url",

    # Active project
    "active_project",

    # Target environment
    "target_env_type",
    "target_ssh_host",
    "target_ssh_port",
    "target_ssh_user",
    "target_ssh_caf_dir",

    # CAF 4-Pillar configuration
    "caf_scope",
    "caf_urgency",
    "caf_allowed_subnets",
    "caf_target_credentials",

    # MCP
    "mcp_url",
    "mcp_server_url",

     # Metrics
     "tool_focus",

    # Prompt / validation
    "sys_prompt",
    "user_prompt",
    "validation_command",
    "fail_patterns",

    # Projects (new project management system)
    "projects",
    "active_project_id",

    # Bash-bot working copy keys
    "bash_startup_commands",
    "bash_timeout",
    "bash_completion_commands",
    "bash_validation_commands",
    "bash_execution_target",
    "bash_ssh_host",
    "bash_ssh_port",
    "bash_ssh_user",
    "bash_ssh_key_path",
    "bash_fail_patterns",
    "bash_metrics_matrix",
    "bash_validation_sets",
    "bash_sudo",

    # Llama-CLI-bot working copy keys
    "llama_cli_execution_target",
    "llama_cli_ssh_host",
    "llama_cli_ssh_port",
    "llama_cli_ssh_user",
    "llama_cli_ssh_key_path",
    "llama_cli_sudo",
    "llama_cli_backend",
    "llama_cli_binary_path",
    "llama_cli_model_dir",
    "llama_cli_model_name",
    "llama_cli_tokens",
    "llama_cli_en_temp",
    "llama_cli_temperature",
    "llama_cli_en_gpu_layers",
    "llama_cli_gpu_layers",
    "llama_cli_en_threads",
    "llama_cli_threads",
    "llama_cli_flash_attn",
    "llama_cli_en_top_k",
    "llama_cli_top_k",
    "llama_cli_en_top_p",
    "llama_cli_top_p",
    "llama_cli_en_min_p",
    "llama_cli_min_p",
    "llama_cli_en_repeat_penalty",
    "llama_cli_repeat_penalty",
    "llama_cli_en_freq_penalty",
    "llama_cli_freq_penalty",
    "llama_cli_en_predict",
    "llama_cli_predict",
    "llama_cli_en_rope_freq_base",
    "llama_cli_rope_freq_base",
    "llama_cli_en_rope_freq_scale",
    "llama_cli_rope_freq_scale",
    "llama_cli_en_seed",
    "llama_cli_seed",
    "llama_cli_custom_flags",
    "llama_cli_openai_base_url",
    "llama_cli_openai_verify_ssl",
    "llama_cli_mcp_config_path",
    "llama_cli_mcp_servers",
    "llama_cli_prompts",
    "llama_cli_commands",
    "llama_cli_timeout",
    "llama_cli_validation_commands",
    "llama_cli_fail_patterns",
    "llama_cli_metrics_matrix",

    # GGUF compile pipeline
    "compile_source_path",
    "compile_output_dir",
    "compile_quantization",
})

# Keys that must never be written even if they accidentally appear in PERSIST_KEYS.
# Also stripped from per-run config.json by core.session_log.
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "target_ssh_password",
    "target_ssh_key_path",
    "judge_api_key",   # legacy frontier-judge key — may exist in old exports
    "bash_ssh_password",
    "llama_cli_ssh_password",
    "llama_cli_openai_api_key",
    "llm_helper_openai_apikey",
})

# Sensitive keys nested inside project config dicts that are never written to
# disk at all (cleared to "" on every save).
NESTED_SENSITIVE: frozenset[str] = frozenset({
    "openai_api_key",
    "ssh_key_path",
})

# Nested keys that ARE persisted, but lightly obfuscated (base64, not real
# encryption) so a save/reload round-trip doesn't force re-entering the SSH
# login password every time — mirrors the plaintext persistence the user
# already chose for llm_helper_openai_apikey, but for a value sensitive
# enough (grants remote shell access) to warrant not sitting in plain text.
NESTED_OBSCURED: frozenset[str] = frozenset({
    "ssh_password",
    # For ssh/pct targets this can hold a copy of ssh_password.
    "sudo_password",
})

_OBSCURED_PREFIX = "b64:"


def _obscure(value: str) -> str:
    if not value:
        return ""
    import base64
    return _OBSCURED_PREFIX + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _unobscure(value: str) -> str:
    if not value or not value.startswith(_OBSCURED_PREFIX):
        return value or ""
    import base64
    try:
        return base64.b64decode(value[len(_OBSCURED_PREFIX):].encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def _sanitize_projects(projects: list) -> list:
    """Return a deep copy of *projects* with sensitive config keys cleared and
    obscured keys base64-encoded, ready to write to disk."""
    import copy
    clean = []
    for proj in projects:
        p = copy.deepcopy(proj)
        cfg = p.get("config", {})
        for k in NESTED_SENSITIVE:
            if k in cfg:
                cfg[k] = ""
        for k in NESTED_OBSCURED:
            if k in cfg:
                cfg[k] = _obscure(cfg[k])
        p["config"] = cfg
        clean.append(p)
    return clean


def _deobscure_projects(projects: list) -> list:
    """Reverse of _sanitize_projects's obscuring step, applied after loading
    from disk so in-memory session state holds plaintext SSH passwords."""
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        cfg = proj.get("config")
        if not isinstance(cfg, dict):
            continue
        for k in NESTED_OBSCURED:
            if k in cfg:
                cfg[k] = _unobscure(cfg[k])
    return projects

_SETTINGS_PATH: Path = Path.home() / ".modelscope" / "settings.json"


def _backup_path() -> Path:
    """Return the last-known-good companion file for the settings document."""
    return _SETTINGS_PATH.with_name(f"{_SETTINGS_PATH.name}.bak")


def _project_journal_path() -> Path:
    """Return an independent durable snapshot of the project collection.

    Older running app instances may still write ``settings.json`` directly
    during a source reload. Keeping projects separately lets a current
    instance restore them even if such a stale process overwrites settings.
    """
    return _SETTINGS_PATH.with_name("projects.json")


def _read_settings_document(path: Path) -> dict[str, Any]:
    """Read one settings file, rejecting malformed/non-object JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("settings document must be a JSON object")
    return data


def _read_project_journal() -> list:
    """Return valid journal projects, or no entries when it is unavailable."""
    try:
        projects = _read_settings_document(_project_journal_path()).get("projects", [])
        return projects if isinstance(projects, list) else []
    except Exception:
        return []


def _write_json_atomically(path: Path, data: dict[str, Any]) -> None:
    """Replace *path* only after a complete JSON document is on disk.

    A Streamlit reload in another browser session must never observe a partial
    settings file and bootstrap an empty project list over the user's data.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _merge_with_disk_projects(
    session_state: Any,
    session_projects: list,
    unserializable_project_ids: set[str] | None = None,
) -> list:
    """Reconcile this session's project list with whatever is currently on disk.

    save_settings() used to overwrite the "projects" key outright with
    whatever a single session's memory held. Streamlit runs one independent
    session per browser tab/connection — if a second tab/session is opened
    (or reconnects after a hiccup) before it has loaded a project another,
    more current session just created, that second session's very next save
    would silently delete the project it never knew about. This reconciles
    instead of overwriting:

    - A project id this session still has: its (possibly edited) version wins.
    - A project id present only on disk is preserved unless explicitly
      deleted, including after a browser reconnect or source reload.
    - A project is removed only when this session explicitly records its id in
      ``_deleted_project_ids``. A missing project list can also be caused by
      a Streamlit code reload, and must never imply deletion.
    """
    try:
        disk_data = _read_settings_document(_SETTINGS_PATH)
        disk_projects = disk_data.get("projects", [])
        if not isinstance(disk_projects, list):
            disk_projects = []
    except Exception:
        disk_projects = []

    unserializable_project_ids = unserializable_project_ids or set()
    deleted_project_ids = set(session_state.get("_deleted_project_ids", []) or [])
    session_ids = {p.get("id") for p in session_projects if isinstance(p, dict)}
    merged = list(session_projects)
    for stored_projects in (disk_projects, _read_project_journal()):
        for proj in stored_projects:
            pid = proj.get("id") if isinstance(proj, dict) else None
            if pid is None or pid in session_ids:
                continue
            if pid in unserializable_project_ids:
                # Never turn a transient/non-persistable widget value into a
                # project deletion. Preserve the last complete version instead.
                import copy
                merged.append(_deobscure_projects([copy.deepcopy(proj)])[0])
                session_ids.add(pid)
                continue
            if pid not in deleted_project_ids:
                # Its absence may be a freshly-reset Streamlit session rather
                # than a user request. De-obscure before it re-enters
                # _sanitize_projects(), which would otherwise double-encode it.
                import copy
                merged.append(_deobscure_projects([copy.deepcopy(proj)])[0])
                session_ids.add(pid)
    return merged


def reconcile_projects(session_state: Any) -> list:
    """Restore persisted projects that a transient UI session forgot.

    This runs before the project sidebar is rendered, so a hot reload cannot
    temporarily present a default project as though the user's projects were
    gone and then autosave that loss.
    """
    projects = session_state.get("projects") if hasattr(session_state, "get") else None
    if not isinstance(projects, list):
        projects = []
    merged = _merge_with_disk_projects(session_state, projects)
    if hasattr(session_state, "__setitem__"):
        session_state["projects"] = merged
    return merged


def save_settings(session_state: Any) -> None:
    """Write PERSIST_KEYS values from *session_state* to the settings file.

    Sensitive keys are always stripped.  Any I/O or serialisation error is
    swallowed silently so that a save failure never crashes the UI.
    """
    try:
        data: dict[str, Any] = {}
        for key in PERSIST_KEYS:
            if key in _SENSITIVE_KEYS or key == "projects":
                continue  # "projects" is validated/merged separately below
            try:
                value = session_state[key]
                # Verify JSON-serialisability (avoids storing un-serialisable objects)
                json.dumps(value)
                data[key] = value
            except (KeyError, TypeError):
                pass

        # "projects" is validated per-project (not as one all-or-nothing
        # blob) so a single non-serialisable value can't silently drop every
        # other project from the saved file, then merged with on-disk state
        # so a stale/parallel session can't silently delete another
        # session's projects (see _merge_with_disk_projects).
        session_projects = session_state.get("projects") \
            if hasattr(session_state, "get") else None
        if isinstance(session_projects, list):
            safe_projects = []
            unserializable_project_ids: set[str] = set()
            for proj in session_projects:
                try:
                    json.dumps(proj)
                    safe_projects.append(proj)
                except TypeError:
                    if isinstance(proj, dict) and isinstance(proj.get("id"), str):
                        unserializable_project_ids.add(proj["id"])
            merged = _merge_with_disk_projects(
                session_state,
                safe_projects,
                unserializable_project_ids,
            )
            # Keep the in-memory UI list in sync too. Without this, a hot
            # reload could save a recovered project yet continue displaying
            # only the temporary default until another rerun.
            session_state["projects"] = merged
            data["projects"] = _sanitize_projects(merged)

            # Write projects independently before the aggregate settings
            # document. A stale app process that lacks this journal can still
            # overwrite settings, but it cannot erase this recovery source.
            _write_json_atomically(_project_journal_path(), {"projects": data["projects"]})

        # Keep one complete, readable prior version. Combined with atomic
        # replacement this makes a code reload or concurrent reader unable to
        # observe partial JSON and replace a project list with a default one.
        try:
            previous = _read_settings_document(_SETTINGS_PATH)
        except Exception:
            previous = None
        if previous is not None:
            _write_json_atomically(_backup_path(), previous)
        _write_json_atomically(_SETTINGS_PATH, data)

    except Exception:
        pass


def load_settings() -> dict[str, Any]:
    """Read and return the persisted settings dict.

    Returns an empty dict on any error (missing file, bad JSON, permission
    denied, etc.) so that callers can safely iterate over the result.
    """
    try:
        try:
            data = _read_settings_document(_SETTINGS_PATH)
        except Exception:
            # If an older release was interrupted while writing settings,
            # recover the last known good file rather than bootstrapping an
            # empty project list and persisting that loss.
            data = _read_settings_document(_backup_path())
        # Only return keys that belong to PERSIST_KEYS and are not sensitive
        result = {
            k: v for k, v in data.items()
            if k in PERSIST_KEYS and k not in _SENSITIVE_KEYS
        }
        if isinstance(result.get("projects"), list):
            projects = list(result["projects"])
            known_ids = {project.get("id") for project in projects if isinstance(project, dict)}
            for project in _read_project_journal():
                project_id = project.get("id") if isinstance(project, dict) else None
                if project_id and project_id not in known_ids:
                    projects.append(project)
                    known_ids.add(project_id)
            result["projects"] = _deobscure_projects(projects)
        return result
    except Exception:
        return {}
