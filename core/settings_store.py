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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _merge_with_disk_projects(session_state: Any, session_projects: list) -> list:
    """Reconcile this session's project list with whatever is currently on disk.

    save_settings() used to overwrite the "projects" key outright with
    whatever a single session's memory held. Streamlit runs one independent
    session per browser tab/connection — if a second tab/session is opened
    (or reconnects after a hiccup) before it has loaded a project another,
    more current session just created, that second session's very next save
    would silently delete the project it never knew about. This reconciles
    instead of overwriting:

    - A project id this session still has: its (possibly edited) version wins.
    - A project id on disk this session never saw at load time (recorded in
      "_known_project_ids_at_load"): assumed to belong to another, more
      current session — preserved rather than silently deleted.
    - A project id this session DID see at load but no longer has: treated as
      an intentional deletion in this session and is not resurrected.
    """
    try:
        disk_data = json.loads(_SETTINGS_PATH.read_text(encoding="utf-8"))
        disk_projects = disk_data.get("projects", [])
        if not isinstance(disk_projects, list):
            disk_projects = []
    except Exception:
        disk_projects = []

    known_at_load = session_state.get("_known_project_ids_at_load")
    if known_at_load is None:
        # No baseline recorded for this session — assume it started from
        # whatever is on disk right now, so nothing looks "unknown" to it.
        known_at_load = [p.get("id") for p in disk_projects if isinstance(p, dict)]
    known_at_load = set(known_at_load)

    session_ids = {p.get("id") for p in session_projects if isinstance(p, dict)}
    merged = list(session_projects)
    for proj in disk_projects:
        pid = proj.get("id") if isinstance(proj, dict) else None
        if pid is None or pid in session_ids:
            continue
        if pid not in known_at_load:
            # This session never loaded this project into memory, so its
            # NESTED_OBSCURED fields are still base64-encoded as read from
            # disk. De-obscure before it re-enters _sanitize_projects()
            # below, which would otherwise double-encode it.
            import copy
            merged.append(_deobscure_projects([copy.deepcopy(proj)])[0])
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
            for proj in session_projects:
                try:
                    json.dumps(proj)
                    safe_projects.append(proj)
                except TypeError:
                    continue
            merged = _merge_with_disk_projects(session_state, safe_projects)
            data["projects"] = _sanitize_projects(merged)

        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

        # After a successful save this session is authoritative for every
        # project it currently holds, so fold those ids into the load-time
        # baseline. This lets a project created/duplicated in this same
        # session be deleted permanently (its id is now "known", so
        # _merge_with_disk_projects treats its later absence as an intentional
        # deletion instead of resurrecting it). We union with *safe_projects*
        # (this session's own projects), never *merged* — claiming authority
        # over another tab's preserved project would let a later save delete
        # it, the exact cross-session data loss the merge exists to prevent.
        if isinstance(session_projects, list):
            try:
                known = set(session_state.get("_known_project_ids_at_load") or [])
                known |= {p.get("id") for p in safe_projects
                          if isinstance(p, dict) and p.get("id")}
                session_state["_known_project_ids_at_load"] = list(known)
            except Exception:
                pass
    except Exception:
        pass


def load_settings() -> dict[str, Any]:
    """Read and return the persisted settings dict.

    Returns an empty dict on any error (missing file, bad JSON, permission
    denied, etc.) so that callers can safely iterate over the result.
    """
    try:
        raw = _SETTINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        # Only return keys that belong to PERSIST_KEYS and are not sensitive
        result = {
            k: v for k, v in data.items()
            if k in PERSIST_KEYS and k not in _SENSITIVE_KEYS
        }
        if isinstance(result.get("projects"), list):
            result["projects"] = _deobscure_projects(result["projects"])
        return result
    except Exception:
        return {}
