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

    # Metrics / scenario
    "tool_focus",
    "active_scenario",

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
_SENSITIVE_KEYS: frozenset[str] = frozenset({
    "target_ssh_password",
    "target_ssh_key_path",
    "judge_api_key",
    "bash_ssh_password",
    "llama_cli_ssh_password",
    "llama_cli_openai_api_key",
})

# Sensitive keys nested inside project config dicts.
NESTED_SENSITIVE: frozenset[str] = frozenset({
    "ssh_password",
    "openai_api_key",
    "ssh_key_path",
})


def _sanitize_projects(projects: list) -> list:
    """Return a deep copy of *projects* with sensitive config keys cleared."""
    import copy
    clean = []
    for proj in projects:
        p = copy.deepcopy(proj)
        cfg = p.get("config", {})
        for k in NESTED_SENSITIVE:
            if k in cfg:
                cfg[k] = ""
        p["config"] = cfg
        clean.append(p)
    return clean

_SETTINGS_PATH: Path = Path.home() / ".modelscope" / "settings.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_settings(session_state: Any) -> None:
    """Write PERSIST_KEYS values from *session_state* to the settings file.

    Sensitive keys are always stripped.  Any I/O or serialisation error is
    swallowed silently so that a save failure never crashes the UI.
    """
    try:
        data: dict[str, Any] = {}
        for key in PERSIST_KEYS:
            if key in _SENSITIVE_KEYS:
                continue
            try:
                value = session_state[key]
                # Verify JSON-serialisability (avoids storing un-serialisable objects)
                json.dumps(value)
                data[key] = value
            except (KeyError, TypeError):
                pass

        # Sanitize sensitive fields nested inside project configs before writing.
        if "projects" in data and isinstance(data["projects"], list):
            data["projects"] = _sanitize_projects(data["projects"])

        _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _SETTINGS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
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
        return {
            k: v for k, v in data.items()
            if k in PERSIST_KEYS and k not in _SENSITIVE_KEYS
        }
    except Exception:
        return {}
