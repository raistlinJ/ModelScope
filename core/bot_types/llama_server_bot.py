"""Llama-Server-Bot plugin."""

from __future__ import annotations

from typing import Any, Mapping

from core.bot_types.base import (
    COMMON_RUNTIME_DEFAULTS,
    LLM_HELPER_DEFAULTS,
    StatusItem,
    merged_defaults,
)
from core.bot_types.llama_cli_bot import LlamaCliBotPlugin
from config.defaults import MCP_CONFIG_PATH


LLAMA_SERVER_STATE_KEY_MAP: dict[str, str] = {
    "llama_server_execution_target": "execution_target",
    "llama_server_ssh_host": "ssh_host",
    "llama_server_ssh_port": "ssh_port",
    "llama_server_ssh_user": "ssh_user",
    "llama_server_ssh_password": "ssh_password",
    "llama_server_ssh_key_path": "ssh_key_path",
    "llama_server_sudo": "sudo",
    "llama_server_sudo_password": "sudo_password",
    "llama_server_pct_vmid": "pct_vmid",
    "llama_server_backend": "backend",
    "llama_server_binary_path": "binary_path",
    "llama_server_model_dir": "model_dir",
    "llama_server_model_name": "model_name",
    "llama_server_tokens": "tokens",
    "llama_server_ready_timeout": "server_ready_timeout",
    "llama_server_en_temp": "en_temp",
    "llama_server_temperature": "temperature",
    "llama_server_en_gpu_layers": "en_gpu_layers",
    "llama_server_gpu_layers": "gpu_layers",
    "llama_server_en_threads": "en_threads",
    "llama_server_threads": "threads",
    "llama_server_flash_attn": "flash_attn",
    "llama_server_en_top_k": "en_top_k",
    "llama_server_top_k": "top_k",
    "llama_server_en_top_p": "en_top_p",
    "llama_server_top_p": "top_p",
    "llama_server_en_min_p": "en_min_p",
    "llama_server_min_p": "min_p",
    "llama_server_en_repeat_penalty": "en_repeat_penalty",
    "llama_server_repeat_penalty": "repeat_penalty",
    "llama_server_en_freq_penalty": "en_freq_penalty",
    "llama_server_freq_penalty": "freq_penalty",
    "llama_server_en_predict": "en_predict",
    "llama_server_predict": "predict",
    "llama_server_en_rope_freq_base": "en_rope_freq_base",
    "llama_server_rope_freq_base": "rope_freq_base",
    "llama_server_en_rope_freq_scale": "en_rope_freq_scale",
    "llama_server_rope_freq_scale": "rope_freq_scale",
    "llama_server_en_seed": "en_seed",
    "llama_server_seed": "seed",
    "llama_server_custom_flags": "custom_flags",
    "llama_server_server_host": "server_host",
    "llama_server_server_port": "server_port",
    "llama_server_openai_base_url": "openai_base_url",
    "llama_server_openai_verify_ssl": "openai_verify_ssl",
    "llama_server_openai_api_key": "openai_api_key",
    "llama_server_mcp_enabled": "mcp_enabled",
    "llama_server_mcp_config_path": "mcp_config_path",
    "llama_server_mcp_servers": "mcp_servers",
    "llama_server_prompts": "prompts",
    "llama_server_commands": "commands",
    "llama_server_steps": "steps",
    "llama_server_startup_commands": "startup_commands",
    "llama_server_completion_commands": "completion_commands",
    "llama_server_timeout": "timeout",
    "llama_server_validation_commands": "validation_commands",
    "llama_server_fail_patterns": "fail_patterns",
    "llama_server_metrics_matrix": "metrics_matrix",
    "llama_server_validation_sets": "validation_sets",
    "llama_server_system_prompt": "system_prompt",
    "llama_server_llm_helper_backend": "llm_helper_backend",
    "llama_server_llm_helper_openai_url": "llm_helper_openai_url",
    "llama_server_llm_helper_openai_apikey": "llm_helper_openai_apikey",
    "llama_server_llm_helper_openai_verify_ssl": "llm_helper_openai_verify_ssl",
    "llama_server_llm_helper_ollama_url": "llm_helper_ollama_url",
    "llama_server_llm_helper_model": "llm_helper_model",
    "llama_server_llm_helper_enabled": "llm_helper_enabled",
    "llama_server_llm_helper_openai_models": "llm_helper_openai_models",
    "llama_server_llm_helper_ollama_models": "llm_helper_ollama_models",
}


# Working-copy session-state defaults, reset on every project switch unless
# the key is also listed in global_keys (see core.state.sync_project).
# NOTE: "llama_server_bin"/"llama_server_running" look like they belong here
# but don't — they're the legacy global sidebar's managed-server process
# tracking (core/llama_server.py's start()/stop()), unrelated to this bot
# type's own per-project managed-server config. They stay in core.state's
# own bot-agnostic defaults.
LLAMA_SERVER_SESSION_DEFAULTS: dict[str, Any] = {
    "llama_server_execution_target":    "local",
    "llama_server_ssh_host":            "",
    "llama_server_ssh_port":            22,
    "llama_server_ssh_user":            "root",
    "llama_server_ssh_password":        "",
    "llama_server_ssh_key_path":        "",
    "llama_server_sudo":                False,
    "llama_server_sudo_password":       "",
    "llama_server_pct_vmid":            "",
    "llama_server_backend":             "llama-server (managed)",
    "llama_server_binary_path":         "",
    "llama_server_model_dir":           "",
    "llama_server_model_name":          "",
    "llama_server_tokens":              32768,
    "llama_server_ready_timeout":       300,
    "llama_server_en_temp":             False,
    "llama_server_temperature":         0.8,
    "llama_server_en_gpu_layers":       False,
    "llama_server_gpu_layers":          99,
    "llama_server_en_threads":          False,
    "llama_server_threads":             4,
    "llama_server_flash_attn":          False,
    "llama_server_en_top_k":            False,
    "llama_server_top_k":               40,
    "llama_server_en_top_p":            False,
    "llama_server_top_p":               0.9,
    "llama_server_en_min_p":            False,
    "llama_server_min_p":               0.1,
    "llama_server_en_repeat_penalty":   False,
    "llama_server_repeat_penalty":      1.1,
    "llama_server_en_freq_penalty":     False,
    "llama_server_freq_penalty":        0.0,
    "llama_server_en_predict":          False,
    "llama_server_predict":             512,
    "llama_server_en_rope_freq_base":   False,
    "llama_server_rope_freq_base":      10000.0,
    "llama_server_en_rope_freq_scale":  False,
    "llama_server_rope_freq_scale":     1.0,
    "llama_server_en_seed":             False,
    "llama_server_seed":                -1,
    "llama_server_custom_flags":        "--jinja --parallel 1",
    "llama_server_server_host":         "127.0.0.1",
    "llama_server_server_port":         8080,
    "llama_server_openai_base_url":     "http://127.0.0.1:8080",
    "llama_server_openai_verify_ssl":   True,
    "llama_server_openai_api_key":      "",
    "llama_server_mcp_enabled":         False,
    "llama_server_mcp_config_path":     MCP_CONFIG_PATH,
    "llama_server_mcp_servers":         [],
    "llama_server_prompts":             [],
    "llama_server_commands":            [],
    "llama_server_steps":               [],
    "llama_server_startup_commands":    [],
    "llama_server_completion_commands": [],
    "llama_server_timeout":             120,
    "llama_server_validation_sets":     [],
    "llama_server_metrics_matrix":      [],
    "llama_server_validation_commands": [],
    "llama_server_fail_patterns":       [],
    "llama_server_system_prompt":       "",
    "llama_server_exec_config_expanded": True,
    "_llama_server_testing":            False,
    "llama_server_val_editor_nonce":    0,
    "llama_server_val_active_set_idx":  0,
    **{f"llama_server_{k}": v for k, v in LLM_HELPER_DEFAULTS.items()},
}


def _server_base_url(config: dict[str, Any]) -> str:
    host = (config.get("server_host") or "127.0.0.1").strip()
    port = int(config.get("server_port") or 8080)
    client_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
    return f"http://{client_host}:{port}"


class LlamaServerBotPlugin(LlamaCliBotPlugin):
    type_id = "llama_server_bot"
    label = "Llama-Server-Bot"
    icon = "🦙"
    default_project_name = "Llama-Server Project"
    state_key_map = LLAMA_SERVER_STATE_KEY_MAP
    session_defaults = LLAMA_SERVER_SESSION_DEFAULTS
    owned_prefixes = (
        "llama_server_val_",       # llama-server validation set widgets
        "_llama_server_val_",      # llama-server validation dialog steps
        "llama_server_llm_helper_",  # llama-server LLM helper widgets
        "llama_server_exec_vset_",  # execute-tab validation-set selection (index-keyed)
        "llama_server_is_fetching_",  # transient LLM-helper fetch flags
        "_llama_server_svc_",     # llama-server test-run/status result
        "llama_server_mcp_en_",   # MCP server enable toggles
    )
    cache_keys = (
        "llama_server_discovered_models",
        "_llama_server_svc_result",
        "_llama_server_svc_cmd",
    )

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        return merged_defaults(
            COMMON_RUNTIME_DEFAULTS,
            {
                "backend": "llama-server (managed)",
                "binary_path": "",
                "model_dir": "",
                "model_name": "",
                "tokens": 32768,
                "server_ready_timeout": 300,
                "en_temp": False,
                "temperature": 0.8,
                "en_gpu_layers": False,
                "gpu_layers": 99,
                "en_threads": False,
                "threads": 4,
                "flash_attn": False,
                "en_top_k": False,
                "top_k": 40,
                "en_top_p": False,
                "top_p": 0.9,
                "en_min_p": False,
                "min_p": 0.1,
                "en_repeat_penalty": False,
                "repeat_penalty": 1.1,
                "en_freq_penalty": False,
                "freq_penalty": 0.0,
                "en_predict": False,
                "predict": 512,
                "en_rope_freq_base": False,
                "rope_freq_base": 10000.0,
                "en_rope_freq_scale": False,
                "rope_freq_scale": 1.0,
                "en_seed": False,
                "seed": -1,
                "custom_flags": "--jinja --parallel 1",
                "server_host": "127.0.0.1",
                "server_port": 8080,
                "openai_base_url": "http://127.0.0.1:8080",
                "openai_verify_ssl": True,
                "openai_api_key": "",
                "mcp_enabled": False,
                "mcp_config_path": MCP_CONFIG_PATH,
                "mcp_servers": [],
                "prompts": [],
                "commands": [],
                "steps": [],
                "timeout": 120,
                "system_prompt": "",
            },
            LLM_HELPER_DEFAULTS,
        )

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        model = session_state.get("llama_server_model_name") or "not chosen"
        host = session_state.get("llama_server_server_host") or "127.0.0.1"
        port = session_state.get("llama_server_server_port") or 8080
        items = [
            StatusItem(f"Model: {model}", "up" if model != "not chosen" else "wait"),
            StatusItem(f"Listen: {host}:{port}", "up"),
            StatusItem("Backend: managed llama-server", "up"),
        ]
        if project:
            items.append(StatusItem(f"Project: {project.get('name', 'Unnamed')}", "up"))
        return items

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config.setdefault("type", self.type_id)
        config.setdefault("backend", "llama-server (managed)")
        config.setdefault("backend_type", config.get("backend", "llama-server (managed)"))
        config.setdefault("selected_model", config.get("model_name", ""))
        config.setdefault("context_size", config.get("tokens", 32768))
        config.setdefault("server_host", "127.0.0.1")
        config.setdefault("server_port", 8080)
        config["openai_base_url"] = _server_base_url(config)
        config.setdefault("llm_url", config["openai_base_url"])
        config.setdefault("mcp_server_url", "http://127.0.0.1:9191")
        config["mcp_servers"] = [
            server for server in config.get("mcp_servers", []) if server.get("enabled")
        ]
        return config

    def render_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._render_llama_server_bot_config(project)

    def render_execute(self, project: dict[str, Any]) -> None:
        from ui import execute_tab

        execute_tab._render_llama_server_execute(project)

    def flush_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._flush_llama_server_config(project)
