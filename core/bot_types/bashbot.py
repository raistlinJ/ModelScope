"""Built-in Bash-Bot plugin.

Bash-Bot is the base bot type: it supplies the shared command lifecycle
(startup -> validation -> completion) that richer bot types can extend.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from core.bot_types.base import (
    COMMON_RUNTIME_DEFAULTS,
    LLM_HELPER_DEFAULTS,
    BotTypePlugin,
    ProjectTemplate,
    StatusItem,
    merged_defaults,
)
from core.environment import BaseEnvironment


BASH_STATE_KEY_MAP: dict[str, str] = {
    "bash_startup_commands": "startup_commands",
    "bash_timeout": "bash_timeout",
    "bash_completion_commands": "completion_commands",
    "bash_validation_commands": "validation_commands",
    "bash_execution_target": "execution_target",
    "bash_ssh_host": "ssh_host",
    "bash_ssh_port": "ssh_port",
    "bash_ssh_user": "ssh_user",
    "bash_ssh_password": "ssh_password",
    "bash_ssh_key_path": "ssh_key_path",
    "bash_fail_patterns": "fail_patterns",
    "bash_metrics_matrix": "metrics_matrix",
    "bash_validation_sets": "validation_sets",
    "bash_sudo": "sudo",
    "bash_sudo_password": "sudo_password",
    "bash_pct_vmid": "pct_vmid",
    "bash_llm_helper_backend": "llm_helper_backend",
    "bash_llm_helper_openai_url": "llm_helper_openai_url",
    "bash_llm_helper_openai_apikey": "llm_helper_openai_apikey",
    "bash_llm_helper_openai_verify_ssl": "llm_helper_openai_verify_ssl",
    "bash_llm_helper_ollama_url": "llm_helper_ollama_url",
    "bash_llm_helper_model": "llm_helper_model",
    "bash_llm_helper_enabled": "llm_helper_enabled",
    "bash_llm_helper_openai_models": "llm_helper_openai_models",
    "bash_llm_helper_ollama_models": "llm_helper_ollama_models",
}


# Working-copy session-state defaults, reset on every project switch unless
# the key is also listed in global_keys (see core.state.sync_project).
BASH_SESSION_DEFAULTS: dict[str, Any] = {
    "bash_startup_commands":    [],
    "bash_timeout":             60,
    "bash_completion_commands": [],
    "bash_validation_commands": [],
    "bash_execution_target":    "local",
    "bash_ssh_host":            "",
    "bash_ssh_port":            22,
    "bash_ssh_user":            "root",
    "bash_ssh_password":        "",
    "bash_ssh_key_path":        "",
    "bash_fail_patterns":       [],
    "bash_metrics_matrix":      [],
    "bash_validation_sets":     [],
    "bash_sudo":                False,
    "bash_sudo_password":       "",
    "bash_pct_vmid":            "",
    "bash_exec_config_expanded": True,
    **{f"bash_{k}": v for k, v in LLM_HELPER_DEFAULTS.items()},
}


class BashBotPlugin(BotTypePlugin):
    type_id = "bash_bot"
    label = "Bash-Bot"
    icon = "💻"
    default_project_name = "Bash Project"
    state_key_map = BASH_STATE_KEY_MAP
    session_defaults = BASH_SESSION_DEFAULTS
    owned_prefixes = (
        "bash_val_",       # bash validation set widgets (name, desc, enabled, etc.)
        "_bash_val_",      # bash validation dialog steps
        "bash_llm_helper_",  # bash LLM helper widgets
        "bash_exec_vset_", # execute-tab validation-set selection (index-keyed)
        "bash_is_fetching_",  # transient LLM-helper fetch flags
    )
    templates = (
        ProjectTemplate("blank", "Blank"),
        ProjectTemplate(
            "file_creator",
            "File Creator (example)",
            "Creates `/tmp/test` with numbers 1-10, then validates content.",
        ),
        ProjectTemplate(
            "nmap_scanner",
            "Nmap Scanner (example)",
            "Runs `nmap -F 127.0.0.1`, saves output, then validates scan structure.",
        ),
    )

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        if template_key != "blank":
            from config.bash_templates import BASH_BOT_TEMPLATES

            if template_key in BASH_BOT_TEMPLATES:
                cfg = copy.deepcopy(BASH_BOT_TEMPLATES[template_key])
                for key, value in LLM_HELPER_DEFAULTS.items():
                    cfg.setdefault(key, copy.deepcopy(value))
                return cfg

        return merged_defaults(
            COMMON_RUNTIME_DEFAULTS,
            {
                "bash_timeout": 60,
            },
            LLM_HELPER_DEFAULTS,
        )

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        target = session_state.get("bash_execution_target", "local")
        ssh_ok = target == "local" or bool(str(session_state.get("bash_ssh_host", "")).strip())
        items = [StatusItem(f"Target: {target.upper()}", "up" if ssh_ok else "wait")]
        if project:
            items.append(StatusItem(f"Project: {project.get('name', 'Unnamed')}", "up"))
        return items

    def render_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._render_bash_bot_config(project)

    def render_execute(self, project: dict[str, Any]) -> None:
        from ui import execute_tab

        execute_tab._render_bash_execute(project)

    def flush_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._flush_bash_config(project)

    def run_evaluation(self, env: BaseEnvironment, config: dict[str, Any], on_log) -> dict[str, Any]:
        from core.evaluator import run_bash_evaluation

        return run_bash_evaluation(env, config, on_log)
