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
})

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
