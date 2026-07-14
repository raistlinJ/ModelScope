import pytest
import json
import streamlit as st

from core.bot_types import get_bot_plugin
from core.project_import import prepare_imported_project
from ui.config_tab import _export_project_json


def test_import_creates_a_new_id_and_keeps_exported_config():
    project = prepare_imported_project(
        {
            "id": "old-id",
            "name": "My Server",
            "type": "llama_server_bot",
            "config": {"model_name": "model.gguf", "tokens": 8192},
        },
        ["Other Project"],
    )

    assert project["id"] != "old-id"
    assert project["type"] == "llama_server_bot"
    assert project["config"]["model_name"] == "model.gguf"
    assert project["config"]["tokens"] == 8192
    # Plugin defaults fill in fields an older export may not have carried.
    assert project["config"]["server_port"] == 8080


def test_import_makes_duplicate_name_unique():
    project = prepare_imported_project(
        {"name": "My Project", "type": "bash_bot", "config": {}},
        ["my project", "My Project (imported)"],
    )

    assert project["name"] == "My Project (imported 2)"


@pytest.mark.parametrize("payload", [None, [], {"type": "unknown_bot"}, {"type": "bash_bot", "config": []}])
def test_import_rejects_invalid_project_documents(payload):
    with pytest.raises(ValueError):
        prepare_imported_project(payload, [])


def test_export_flushes_llama_server_runtime_fields_before_serializing(tmp_path, monkeypatch):
    """Export must include widget changes not yet written to project config."""
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
    plugin = get_bot_plugin("llama_server_bot")
    project = plugin.make_project("server", "Server")
    st.session_state.clear()
    st.session_state.update({
        "llama_server_binary_path": "/opt/llama-server",
        "llama_server_model_dir": "/models",
        "llama_server_model_name": "qwen.gguf",
        "llama_server_tokens": 16384,
        "llama_server_server_host": "0.0.0.0",
        "llama_server_server_port": 9090,
        "llama_server_temperature": 0.42,
        "llama_server_en_temp": True,
    })

    exported = json.loads(_export_project_json(project))
    config = exported["config"]
    assert config["binary_path"] == "/opt/llama-server"
    assert config["model_dir"] == "/models"
    assert config["model_name"] == "qwen.gguf"
    assert config["tokens"] == 16384
    assert config["server_host"] == "0.0.0.0"
    assert config["server_port"] == 9090
    assert config["temperature"] == 0.42
    assert config["en_temp"] is True


def test_export_flushes_llama_cli_runtime_fields_before_serializing(tmp_path, monkeypatch):
    """Llama CLI exports the live runtime and validation configuration."""
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
    plugin = get_bot_plugin("llama_cli_bot")
    project = plugin.make_project("cli", "CLI")
    st.session_state.clear()
    st.session_state.update({
        "llama_cli_binary_path": "/opt/llama-cli",
        "llama_cli_model_dir": "/models",
        "llama_cli_model_name": "qwen.gguf",
        "llama_cli_tokens": 16384,
        "llama_cli_server_port": 9090,
        "llama_cli_validation_commands": [{"command": "test -f /tmp/result"}],
        "llama_cli_fail_patterns": ["fatal error"],
    })

    config = json.loads(_export_project_json(project))["config"]
    assert config["binary_path"] == "/opt/llama-cli"
    assert config["model_dir"] == "/models"
    assert config["model_name"] == "qwen.gguf"
    assert config["tokens"] == 16384
    assert config["server_port"] == 9090
    assert config["validation_commands"] == [{"command": "test -f /tmp/result"}]
    assert config["fail_patterns"] == ["fatal error"]


def test_export_flushes_bash_runtime_fields_before_serializing(tmp_path, monkeypatch):
    """Bash Bot export includes the current target, commands, and checks."""
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
    plugin = get_bot_plugin("bash_bot")
    project = plugin.make_project("bash", "Bash")
    st.session_state.clear()
    st.session_state.update({
        "bash_execution_target": "ssh",
        "bash_ssh_host": "runner.internal",
        "bash_ssh_port": 2222,
        "bash_ssh_user": "operator",
        "bash_timeout": 90,
        "bash_validation_commands": [{"command": "test -f /tmp/result"}],
        "bash_fail_patterns": ["fatal error"],
        "bash_metrics_matrix": [{"name": "duration"}],
        "bash_startup_commands": [{"commands": [{"command": "mkdir -p /tmp/work"}]}],
    })

    config = json.loads(_export_project_json(project))["config"]
    assert config["execution_target"] == "ssh"
    assert config["ssh_host"] == "runner.internal"
    assert config["ssh_port"] == 2222
    assert config["ssh_user"] == "operator"
    assert config["bash_timeout"] == 90
    assert config["validation_commands"] == [{"command": "test -f /tmp/result"}]
    assert config["fail_patterns"] == ["fatal error"]
    assert config["metrics_matrix"] == [{"name": "duration"}]
    assert config["startup_commands"] == [{"commands": [{"command": "mkdir -p /tmp/work"}]}]
