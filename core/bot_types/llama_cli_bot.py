"""Llama-CLI-Bot plugin."""

from __future__ import annotations

from typing import Any, Mapping

from core.bot_types.base import (
    COMMON_RUNTIME_DEFAULTS,
    LLM_HELPER_DEFAULTS,
    StatusItem,
    merged_defaults,
)
from core.bot_types.bashbot import BashBotPlugin
from core.environment import BaseEnvironment


LLAMA_CLI_STATE_KEY_MAP: dict[str, str] = {
    "llama_cli_execution_target": "execution_target",
    "llama_cli_ssh_host": "ssh_host",
    "llama_cli_ssh_port": "ssh_port",
    "llama_cli_ssh_user": "ssh_user",
    "llama_cli_ssh_password": "ssh_password",
    "llama_cli_ssh_key_path": "ssh_key_path",
    "llama_cli_sudo": "sudo",
    "llama_cli_sudo_password": "sudo_password",
    "llama_cli_pct_vmid": "pct_vmid",
    "llama_cli_backend": "backend",
    "llama_cli_binary_path": "binary_path",
    "llama_cli_model_dir": "model_dir",
    "llama_cli_model_name": "model_name",
    "llama_cli_tokens": "tokens",
    "llama_cli_en_temp": "en_temp",
    "llama_cli_temperature": "temperature",
    "llama_cli_en_gpu_layers": "en_gpu_layers",
    "llama_cli_gpu_layers": "gpu_layers",
    "llama_cli_en_threads": "en_threads",
    "llama_cli_threads": "threads",
    "llama_cli_flash_attn": "flash_attn",
    "llama_cli_en_top_k": "en_top_k",
    "llama_cli_top_k": "top_k",
    "llama_cli_en_top_p": "en_top_p",
    "llama_cli_top_p": "top_p",
    "llama_cli_en_min_p": "en_min_p",
    "llama_cli_min_p": "min_p",
    "llama_cli_en_repeat_penalty": "en_repeat_penalty",
    "llama_cli_repeat_penalty": "repeat_penalty",
    "llama_cli_en_freq_penalty": "en_freq_penalty",
    "llama_cli_freq_penalty": "freq_penalty",
    "llama_cli_en_predict": "en_predict",
    "llama_cli_predict": "predict",
    "llama_cli_en_rope_freq_base": "en_rope_freq_base",
    "llama_cli_rope_freq_base": "rope_freq_base",
    "llama_cli_en_rope_freq_scale": "en_rope_freq_scale",
    "llama_cli_rope_freq_scale": "rope_freq_scale",
    "llama_cli_en_seed": "en_seed",
    "llama_cli_seed": "seed",
    "llama_cli_custom_flags": "custom_flags",
    "llama_cli_server_port": "server_port",
    "llama_cli_openai_base_url": "openai_base_url",
    "llama_cli_openai_verify_ssl": "openai_verify_ssl",
    "llama_cli_openai_api_key": "openai_api_key",
    "llama_cli_mcp_enabled": "mcp_enabled",
    "llama_cli_mcp_config_path": "mcp_config_path",
    "llama_cli_mcp_servers": "mcp_servers",
    "llama_cli_prompts": "prompts",
    "llama_cli_commands": "commands",
    "llama_cli_steps": "steps",
    "llama_cli_startup_commands": "startup_commands",
    "llama_cli_completion_commands": "completion_commands",
    "llama_cli_timeout": "timeout",
    "llama_cli_validation_commands": "validation_commands",
    "llama_cli_fail_patterns": "fail_patterns",
    "llama_cli_metrics_matrix": "metrics_matrix",
    "llama_cli_validation_sets": "validation_sets",
    "llama_cli_system_prompt": "system_prompt",
    "llama_cli_llm_helper_backend": "llm_helper_backend",
    "llama_cli_llm_helper_openai_url": "llm_helper_openai_url",
    "llama_cli_llm_helper_openai_apikey": "llm_helper_openai_apikey",
    "llama_cli_llm_helper_openai_verify_ssl": "llm_helper_openai_verify_ssl",
    "llama_cli_llm_helper_ollama_url": "llm_helper_ollama_url",
    "llama_cli_llm_helper_model": "llm_helper_model",
    "llama_cli_llm_helper_enabled": "llm_helper_enabled",
    "llama_cli_llm_helper_openai_models": "llm_helper_openai_models",
    "llama_cli_llm_helper_ollama_models": "llm_helper_ollama_models",
}


# Working-copy session-state defaults, reset on every project switch unless
# the key is also listed in global_keys (see core.state.sync_project).
LLAMA_CLI_SESSION_DEFAULTS: dict[str, Any] = {
    "llama_cli_execution_target":    "local",
    "llama_cli_ssh_host":            "",
    "llama_cli_ssh_port":            22,
    "llama_cli_ssh_user":            "root",
    "llama_cli_ssh_password":        "",
    "llama_cli_ssh_key_path":        "",
    "llama_cli_sudo":                False,
    "llama_cli_sudo_password":       "",
    "llama_cli_pct_vmid":            "",
    "llama_cli_backend":             "llama.cpp",
    "llama_cli_binary_path":         "",
    "llama_cli_model_dir":           "",
    "llama_cli_model_name":          "",
    "llama_cli_tokens":              32768,
    "llama_cli_en_temp":             False,
    "llama_cli_temperature":         0.8,
    "llama_cli_en_gpu_layers":       False,
    "llama_cli_gpu_layers":          99,
    "llama_cli_en_threads":          False,
    "llama_cli_threads":             4,
    "llama_cli_flash_attn":          False,
    "llama_cli_en_top_k":            False,
    "llama_cli_top_k":               40,
    "llama_cli_en_top_p":            False,
    "llama_cli_top_p":               0.9,
    "llama_cli_en_min_p":            False,
    "llama_cli_min_p":               0.1,
    "llama_cli_en_repeat_penalty":   False,
    "llama_cli_repeat_penalty":      1.1,
    "llama_cli_en_freq_penalty":     False,
    "llama_cli_freq_penalty":        0.0,
    "llama_cli_en_predict":          False,
    "llama_cli_predict":             512,
    "llama_cli_en_rope_freq_base":   False,
    "llama_cli_rope_freq_base":      10000.0,
    "llama_cli_en_rope_freq_scale":  False,
    "llama_cli_rope_freq_scale":     1.0,
    "llama_cli_en_seed":             False,
    "llama_cli_seed":                -1,
    "llama_cli_custom_flags":        "",
    "llama_cli_server_port":         8080,
    "llama_cli_openai_base_url":     "",
    "llama_cli_openai_verify_ssl":   True,
    "llama_cli_openai_api_key":      "",
    "llama_cli_mcp_enabled":         False,
    "llama_cli_mcp_config_path":     "",
    "llama_cli_mcp_servers":         [],
    "llama_cli_prompts":             [],
    "llama_cli_commands":            [],
    "llama_cli_steps":               [],  # unified step editor (type: prompt|command)
    "llama_cli_startup_commands":    [],
    "llama_cli_completion_commands": [],
    "llama_cli_timeout":             120,
    "llama_cli_validation_sets":     [],
    "llama_cli_metrics_matrix":      [],
    "llama_cli_validation_commands": [],
    "llama_cli_fail_patterns":       [],
    "llama_cli_system_prompt":       "",
    "llama_exec_config_expanded":    True,
    "_llama_cli_testing":            False,
    # Dialog nonce / index — per-project but not part of state_key_map (must
    # still be reset on switch; see core.state.sync_project docstring).
    "llama_cli_val_editor_nonce":    0,
    "llama_cli_val_active_set_idx":  0,
    **{f"llama_cli_{k}": v for k, v in LLM_HELPER_DEFAULTS.items()},
}


class LlamaCliBotPlugin(BashBotPlugin):
    type_id = "llama_cli_bot"
    label = "Llama-CLI-Bot"
    icon = "🦙"
    default_project_name = "Llama-CLI Project"
    state_key_map = LLAMA_CLI_STATE_KEY_MAP
    session_defaults = LLAMA_CLI_SESSION_DEFAULTS
    owned_prefixes = (
        "llama_cli_val_",       # llama-cli validation set widgets
        "_llama_cli_val_",      # llama-cli validation dialog steps
        "llama_cli_llm_helper_",  # llama-cli LLM helper widgets
        "llama_exec_vset_",     # execute-tab validation-set selection (index-keyed)
        "llama_cli_is_fetching_",  # transient LLM-helper fetch flags
        "_llama_openai_",       # OpenAI-compatible widget keys
        "_llama_preset_sel",    # preset selector
        "llama_mcp_en_",        # MCP server enable toggles (positional checkbox)
    )
    templates = ()
    cache_keys = (
        "llama_cli_discovered_models",
        "llama_cli_openai_models",
        "_llama_svc_result",
        "_llama_svc_cmd",
    )

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        return merged_defaults(
            COMMON_RUNTIME_DEFAULTS,
            {
                "backend": "llama.cpp",
                "binary_path": "",
                "model_dir": "",
                "model_name": "",
                "tokens": 2048,
                "openai_base_url": "",
                "openai_verify_ssl": True,
                "openai_api_key": "",
                "mcp_enabled": False,
                "mcp_config_path": "",
                "mcp_servers": [],
                "prompts": [],
                "commands": [],
                "steps": [],
                "timeout": 60,
                "system_prompt": "",
            },
            LLM_HELPER_DEFAULTS,
        )

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        backend = session_state.get("llama_cli_backend", "llama.cpp")
        model = session_state.get("llama_cli_model_name") or "not chosen"
        target = session_state.get("llama_cli_execution_target", "local")
        items = [
            StatusItem(f"Model: {model}", "up" if model != "not chosen" else "wait"),
            StatusItem(f"Backend: {backend}", "up"),
            StatusItem(f"Target: {str(target).upper()}", "up"),
        ]
        if project:
            items.append(StatusItem(f"Project: {project.get('name', 'Unnamed')}", "up"))
        return items

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config.setdefault("type", self.type_id)
        config.setdefault("backend_type", config.get("backend", "llama.cpp"))
        config.setdefault("selected_model", config.get("model_name", ""))
        config.setdefault("context_size", config.get("tokens", 2048))
        config.setdefault("llm_url", config.get("openai_base_url", ""))
        config.setdefault("mcp_server_url", "http://127.0.0.1:9191")
        config["mcp_servers"] = [
            server for server in config.get("mcp_servers", []) if server.get("enabled")
        ]
        return config

    def render_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._render_llama_cli_bot_config(project)

    def render_execute(self, project: dict[str, Any]) -> None:
        from ui import execute_tab

        execute_tab._render_llama_cli_execute(project)

    def flush_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._flush_llama_cli_config(project)

    def run_evaluation(self, env: BaseEnvironment, config: dict[str, Any], on_log) -> dict[str, Any]:
        from core.evaluator import run_llama_cli_evaluation

        return run_llama_cli_evaluation(env, config, on_log)
