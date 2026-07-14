import copy
import json
from unittest.mock import MagicMock

import streamlit as st

from core.bot_types import get_bot_plugin, iter_bot_plugins, refresh_bot_plugins
from core.bot_types.bashbot import BashBotPlugin
from core.settings_store import NESTED_SENSITIVE
from ui.config_tab import _export_project_json


def test_registry_exposes_builtin_bot_types_in_order():
    refresh_bot_plugins()
    assert [plugin.type_id for plugin in iter_bot_plugins()] == [
        "bash_bot",
        "llama_cli_bot",
        "llama_server_bot",
    ]


def test_llama_cli_plugin_extends_bash_base():
    refresh_bot_plugins()
    plugin = get_bot_plugin("llama_cli_bot")
    assert isinstance(plugin, BashBotPlugin)
    assert plugin.type_id == "llama_cli_bot"


def test_llama_server_plugin_extends_llama_cli_base():
    refresh_bot_plugins()
    plugin = get_bot_plugin("llama_server_bot")
    assert isinstance(plugin, BashBotPlugin)
    assert plugin.type_id == "llama_server_bot"

    config = plugin.default_config()
    assert config["backend"] == "llama-server (managed)"
    assert config["server_host"] == "127.0.0.1"
    assert config["server_port"] == 8080


def test_llama_server_plugin_has_advanced_options_parity_with_llama_cli():
    """The Advanced Options (sampling/perf) cfg keys must hydrate for both bot
    types, since llama-server's panel mirrors llama-cli's — a key present in
    one plugin's state_key_map but not the other silently drops a setting on
    project-switch hydration (see core.state.sync_project)."""
    refresh_bot_plugins()
    cli_plugin = get_bot_plugin("llama_cli_bot")
    server_plugin = get_bot_plugin("llama_server_bot")

    advanced_keys = {
        "en_temp", "temperature",
        "en_gpu_layers", "gpu_layers",
        "en_threads", "threads",
        "flash_attn",
        "en_top_k", "top_k",
        "en_top_p", "top_p",
        "en_min_p", "min_p",
        "en_repeat_penalty", "repeat_penalty",
        "en_freq_penalty", "freq_penalty",
        "en_predict", "predict",
        "en_seed", "seed",
        "en_rope_freq_base", "rope_freq_base",
        "en_rope_freq_scale", "rope_freq_scale",
    }

    cli_state_keys = set(cli_plugin.state_key_map.values())
    server_state_keys = set(server_plugin.state_key_map.values())
    assert advanced_keys <= cli_state_keys, "test fixture out of sync with llama_cli_bot"
    missing = advanced_keys - server_state_keys
    assert not missing, f"llama_server_bot state_key_map missing: {missing}"


def test_llama_cli_plugin_normalizes_cli_project_config():
    refresh_bot_plugins()
    plugin = get_bot_plugin("llama_cli_bot")
    config = {
        "backend": "ollama",
        "model_name": "llama3",
        "tokens": 4096,
        "openai_base_url": "http://localhost:11434",
        "mcp_servers": [
            {"name": "enabled", "enabled": True},
            {"name": "disabled", "enabled": False},
            {"name": "missing"},
        ],
    }

    plugin.normalize_project_config(config)

    assert config["type"] == "llama_cli_bot"
    assert config["backend_type"] == "ollama"
    assert config["selected_model"] == "llama3"
    assert config["context_size"] == 4096
    assert config["llm_url"] == "http://localhost:11434"
    assert [server["name"] for server in config["mcp_servers"]] == ["enabled"]


def test_llama_server_plugin_normalizes_managed_project_config():
    refresh_bot_plugins()
    plugin = get_bot_plugin("llama_server_bot")
    config = {
        "model_name": "llama3.gguf",
        "tokens": 8192,
        "server_host": "0.0.0.0",
        "server_port": 19191,
        "mcp_servers": [
            {"name": "enabled", "enabled": True},
            {"name": "disabled", "enabled": False},
        ],
    }

    plugin.normalize_project_config(config)

    assert config["type"] == "llama_server_bot"
    assert config["backend_type"] == "llama-server (managed)"
    assert config["selected_model"] == "llama3.gguf"
    assert config["context_size"] == 8192
    assert config["openai_base_url"] == "http://127.0.0.1:19191"
    assert config["llm_url"] == "http://127.0.0.1:19191"
    assert [server["name"] for server in config["mcp_servers"]] == ["enabled"]


def test_plugin_run_dispatch_delegates_to_evaluator(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("bash_bot")
    env = MagicMock()
    expected = {"run_bot_type": "bash_bot"}

    def fake_run_bash_evaluation(run_env, config, on_log):
        assert run_env is env
        assert config == {"x": 1}
        return expected

    monkeypatch.setattr("core.evaluator.run_bash_evaluation", fake_run_bash_evaluation)

    assert plugin.run_evaluation(env, {"x": 1}, lambda msg: None) is expected


def test_registry_discovers_external_plugin_file(tmp_path, monkeypatch):
    plugin_file = tmp_path / "custom_bot.py"
    plugin_file.write_text(
        """
from core.bot_types.base import BotTypePlugin


class CustomBotPlugin(BotTypePlugin):
    type_id = "custom_bot"
    label = "Custom Bot"
    icon = "C"

    def default_config(self, template_key="blank"):
        return {"custom": True}

    def render_config(self, project):
        pass

    def render_execute(self, project):
        pass

    def flush_config(self, project):
        pass

    def run_evaluation(self, env, config, on_log):
        return {"run_bot_type": self.type_id}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODELSCOPE_BOT_PLUGIN_PATH", str(tmp_path))

    try:
        refresh_bot_plugins()
        plugin = get_bot_plugin("custom_bot")

        assert plugin is not None
        assert plugin.label == "Custom Bot"
        assert plugin.default_config() == {"custom": True}
    finally:
        monkeypatch.delenv("MODELSCOPE_BOT_PLUGIN_PATH", raising=False)
        refresh_bot_plugins()


def test_base_plugin_flush_exports_declared_state_mapping():
    """New plugins get complete export support by declaring a state map."""
    from core.bot_types.base import BotTypePlugin

    class MinimalPlugin(BotTypePlugin):
        state_key_map = {"minimal_model": "model_name"}

    st.session_state.clear()
    st.session_state["minimal_model"] = "model.gguf"
    project = {"config": {}}
    MinimalPlugin().flush_config(project)

    assert project["config"] == {"model_name": "model.gguf"}


def test_every_registered_plugin_exports_all_declared_non_secret_settings(tmp_path, monkeypatch):
    """Prevent future plugin additions from silently dropping mapped fields."""
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
    refresh_bot_plugins()

    for plugin in iter_bot_plugins():
        st.session_state.clear()
        st.session_state.update(copy.deepcopy(plugin.session_defaults))
        project = plugin.make_project(plugin.type_id, plugin.label)
        config = json.loads(_export_project_json(project))["config"]
        expected = set(plugin.state_key_map.values()) - NESTED_SENSITIVE
        missing = expected - set(config)
        assert not missing, f"{plugin.type_id} export omits mapped fields: {sorted(missing)}"
