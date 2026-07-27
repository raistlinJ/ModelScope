"""Unit tests for the CAF + managed llama.cpp bot plugin."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock

import pytest
import streamlit as st

from core.bot_types import get_bot_plugin, refresh_bot_plugins
from core.state import _effective_defaults
from core.state import sync_project
from plugins.bot_types.caf_cli_run import (
    CAF_CLI_RUN_SESSION_DEFAULTS,
    CafActiveSessionError,
    CafAppRestartRequiredError,
    CafCliRunPlugin,
    CafSessionStartTimeoutError,
)
from plugins.bot_types.caf_llama_bot import (
    CAF_LLAMA_DEFAULT_MODEL_DIRECTORY,
    CafLlamaBotPlugin,
    _derive_local_url,
    _resolve_binary_and_model,
    scan_caf_llama_models,
)


def test_registry_discovers_caf_llama_bot():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_llama_bot")
    assert plugin is not None
    assert plugin.type_id == "caf_llama_bot"
    assert plugin.label == "CAF + llama.cpp"


def test_plugin_subclasses_caf_cli_run_plugin():
    assert issubclass(CafLlamaBotPlugin, CafCliRunPlugin)
    assert isinstance(CafLlamaBotPlugin(), CafCliRunPlugin)


def test_both_caf_plugins_use_the_same_tool_discovery_method():
    assert CafLlamaBotPlugin._fetch_tools is CafCliRunPlugin._fetch_tools


class TestPresentationHooks:
    """CafCliRunPlugin's defaults must preserve caf_cli_run_bot's original UI;
    only CafLlamaBotPlugin should override them to describe the shared
    execution target and rename the unified backend section."""

    def test_base_plugin_defaults_are_unchanged(self):
        plugin = CafCliRunPlugin()
        assert plugin._execution_target_intro() is None
        assert plugin._backend_section_title() == "CyberAgentFlow CLI"

    def test_llama_bot_overrides_both_hooks(self):
        plugin = CafLlamaBotPlugin()
        intro = plugin._execution_target_intro()
        assert intro and "llama-server" in intro
        assert plugin._backend_section_title() == "CyberAgentFlow + llama.cpp"


def test_plugin_does_not_override_shared_caf_session_fallbacks():
    """Plugin defaults are globally aggregated, so shared CAF keys must retain
    CafCliRunPlugin's values until the active project hydrates or renders."""
    refresh_bot_plugins()
    effective = _effective_defaults()
    for key in ("caf_cli_provider", "caf_cli_url", "caf_cli_api_key", "caf_cli_model"):
        assert CafLlamaBotPlugin.session_defaults[key] == CAF_CLI_RUN_SESSION_DEFAULTS[key]
        assert effective[key] == CAF_CLI_RUN_SESSION_DEFAULTS[key]


def test_derive_local_url_normalizes_wildcard_hosts():
    assert _derive_local_url({"server_host": "0.0.0.0", "server_port": 8090}) == "http://127.0.0.1:8090"
    assert _derive_local_url({"server_host": "::", "server_port": 8090}) == "http://127.0.0.1:8090"
    assert _derive_local_url({"server_host": "[::]", "server_port": 8090}) == "http://127.0.0.1:8090"
    assert _derive_local_url({"server_host": "192.168.1.5", "server_port": 8090}) == "http://192.168.1.5:8090"
    assert _derive_local_url({}) == "http://127.0.0.1:8080"


class TestConfigNormalization:
    def test_fresh_config_uses_shared_caf_and_hugging_face_defaults(self):
        config = CafLlamaBotPlugin().default_config()
        assert config["ssh_port"] == 22
        assert config["caf_cli_directory"] == "~/cyber-agent-flow"
        assert config["model_dir"] == CAF_LLAMA_DEFAULT_MODEL_DIRECTORY

    def test_blank_defaults_are_repaired_without_overwriting_explicit_values(self):
        plugin = CafLlamaBotPlugin()
        repaired = plugin.normalize_project_config({
            "ssh_port": "", "caf_cli_directory": " ", "model_dir": "",
        })
        assert repaired["ssh_port"] == 22
        assert repaired["caf_cli_directory"] == "~/cyber-agent-flow"
        assert repaired["model_dir"] == CAF_LLAMA_DEFAULT_MODEL_DIRECTORY

        explicit = plugin.normalize_project_config({
            "ssh_port": 2222, "caf_cli_directory": "/opt/caf", "model_dir": "/models",
        })
        assert explicit["ssh_port"] == 2222
        assert explicit["caf_cli_directory"] == "/opt/caf"
        assert explicit["model_dir"] == "/models"

    def test_project_switch_hydrates_corrected_defaults_for_blank_fields(self):
        st.session_state.clear()
        st.session_state.update({
            "projects": [{
                "id": "caf-blank",
                "type": "caf_llama_bot",
                "config": {
                    "ssh_port": "",
                    "caf_cli_directory": " ",
                    "model_dir": None,
                },
            }],
            "active_project_id": "caf-blank",
        })

        sync_project("caf-blank")

        assert st.session_state["caf_cli_ssh_port"] == 22
        assert st.session_state["caf_cli_directory"] == "~/cyber-agent-flow"
        assert st.session_state["caf_llama_srv_model_dir"] == CAF_LLAMA_DEFAULT_MODEL_DIRECTORY

    def test_locks_connection_fields(self):
        plugin = CafLlamaBotPlugin()
        config = {
            "model_name": "llama3.gguf",
            "server_host": "0.0.0.0",
            "server_port": 9999,
            "caf_cli_provider": "ollama_direct",
            "caf_cli_api_key": "secret",
        }
        normalized = plugin.normalize_project_config(config)
        assert normalized["caf_cli_provider"] == "openai"
        assert normalized["caf_cli_api_key"] == ""
        assert normalized["selected_model"] == "llama3.gguf"
        assert normalized["caf_cli_url"] == "http://127.0.0.1:9999"

    def test_strips_and_clears_selected_model_when_model_name_blank(self):
        plugin = CafLlamaBotPlugin()
        config = plugin.normalize_project_config({"model_name": "   "})
        assert config["model_name"] == ""
        assert config["selected_model"] == ""

    def test_flush_config_recomputes_url_from_just_flushed_values(self):
        plugin = CafLlamaBotPlugin()
        st.session_state.clear()
        st.session_state.update({
            "caf_llama_srv_server_host": "0.0.0.0",
            "caf_llama_srv_server_port": 9100,
            "caf_llama_srv_model_name": "llama3.gguf",
        })
        project = {"config": {"caf_cli_url": "http://stale-value:1234", "server_host": "stale", "server_port": 1234}}
        plugin.flush_config(project)
        assert project["config"]["server_host"] == "0.0.0.0"
        assert project["config"]["server_port"] == 9100
        assert project["config"]["caf_cli_url"] == "http://127.0.0.1:9100"
        assert project["config"]["selected_model"] == "llama3.gguf"

    def test_threshold_state_exports_through_own_key(self):
        plugin = CafLlamaBotPlugin()
        assert plugin.state_key_map.get("caf_llama_bot_metric_thresholds") == "metric_thresholds"
        assert "caf_cli_run_bot_metric_thresholds" not in plugin.state_key_map
        assert "caf_llama_bot_metric_thresholds" in plugin.session_defaults


class TestResolveBinaryAndModel:
    def test_local_directory_binary_and_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        config = {
            "execution_target": "local",
            "binary_path": str(bin_dir),
            "model_dir": "~/models",
            "model_name": "llama3.gguf",
        }
        binary, model_path = _resolve_binary_and_model(config)
        assert binary == str(bin_dir / "llama-server")
        assert model_path == os.path.abspath(os.path.join(str(tmp_path), "models", "llama3.gguf"))

    def test_ssh_leaves_tilde_paths_unresolved_and_never_stats_them(self, monkeypatch):
        isdir_calls = []
        monkeypatch.setattr(os.path, "isdir", lambda p: (isdir_calls.append(p), False)[1])
        config = {
            "execution_target": "ssh",
            "binary_path": "~/bin/llama-server",
            "model_dir": "~/models",
            "model_name": "llama3.gguf",
        }
        binary, model_path = _resolve_binary_and_model(config)
        assert binary == "~/bin/llama-server"
        assert model_path == "~/models/llama3.gguf"
        assert isdir_calls == []


def _base_config(**overrides):
    config = {
        "model_name": "llama3.gguf",
        "execution_target": "local",
        "server_host": "127.0.0.1",
        "server_port": 8090,
        "tokens": 4096,
    }
    config.update(overrides)
    return config


class TestRunEvaluation:
    def test_empty_model_name_aborts_without_starting_server(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        started = MagicMock()
        monkeypatch.setattr("core.evaluator._start_managed_llama_server", started)
        logs = []

        telemetry = plugin.run_evaluation(MagicMock(), {"model_name": "  "}, logs.append)

        assert telemetry["run_aborted"] is True
        assert telemetry["run_bot_type"] == "caf_llama_bot"
        assert isinstance(telemetry["run_timestamp"], str) and telemetry["run_timestamp"]
        started.assert_not_called()
        assert any("No model selected" in line for line in logs)

    def test_managed_server_start_failure_returns_aborted_without_calling_super(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        monkeypatch.setattr(
            "core.evaluator._start_managed_llama_server",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        super_run = MagicMock()
        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", super_run)
        logs = []

        telemetry = plugin.run_evaluation(MagicMock(), _base_config(), logs.append)

        assert telemetry["run_aborted"] is True
        assert telemetry["run_bot_type"] == "caf_llama_bot"
        assert telemetry["run_backend"]
        assert telemetry["run_model"] == "llama3.gguf"
        assert "boom" in telemetry["error"]
        super_run.assert_not_called()
        assert any("Failed to start managed llama-server" in line for line in logs)

    def test_generic_exception_from_super_caught_with_partial_metrics(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        fake_proc = MagicMock()
        monkeypatch.setattr("core.evaluator._start_managed_llama_server", MagicMock(return_value=fake_proc))
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot.fetch_llama_server_metrics",
            MagicMock(side_effect=[
                {"available": True, "prompt_tokens": 1.0},
                {"available": True, "prompt_tokens": 5.0},
            ]),
        )
        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", MagicMock(side_effect=RuntimeError("caf blew up")))

        telemetry = plugin.run_evaluation(MagicMock(), _base_config(), lambda msg: None)

        assert telemetry["run_aborted"] is True
        assert telemetry["error"] == "caf blew up"
        assert telemetry["llama_server_metrics"]["available"] is True
        fake_proc.terminate.assert_called_once()

    @pytest.mark.parametrize(
        "exc_cls", [CafActiveSessionError, CafAppRestartRequiredError, CafSessionStartTimeoutError]
    )
    def test_caf_specific_exceptions_propagate_unchanged(self, monkeypatch, exc_cls):
        plugin = CafLlamaBotPlugin()
        fake_proc = MagicMock()
        monkeypatch.setattr("core.evaluator._start_managed_llama_server", MagicMock(return_value=fake_proc))
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot.fetch_llama_server_metrics",
            MagicMock(return_value={"available": True}),
        )
        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", MagicMock(side_effect=exc_cls("busy")))

        with pytest.raises(exc_cls):
            plugin.run_evaluation(MagicMock(), _base_config(), lambda msg: None)

        # Teardown must still run even though the exception propagates.
        fake_proc.terminate.assert_called_once()

    def test_teardown_survives_terminate_failure_and_still_kills(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        fake_proc = MagicMock()
        fake_proc.terminate.side_effect = RuntimeError("terminate failed")
        fake_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="llama-server", timeout=5)
        monkeypatch.setattr("core.evaluator._start_managed_llama_server", MagicMock(return_value=fake_proc))
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot.fetch_llama_server_metrics",
            MagicMock(return_value={"available": True}),
        )
        good_telemetry = {"run_backend": "CAF"}
        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", MagicMock(return_value=good_telemetry))
        logs = []

        telemetry = plugin.run_evaluation(MagicMock(), _base_config(), logs.append)

        # A cleanup failure must not replace a successful result.
        assert telemetry is good_telemetry
        assert telemetry["run_bot_type"] == "caf_llama_bot"
        fake_proc.terminate.assert_called_once()
        fake_proc.wait.assert_called_once()
        fake_proc.kill.assert_called_once()
        assert any("Failed to terminate" in line for line in logs)

    def test_teardown_failure_does_not_mask_caf_specific_exception(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        fake_proc = MagicMock()
        fake_proc.terminate.side_effect = RuntimeError("terminate failed")
        fake_proc.wait.side_effect = RuntimeError("wait failed too")
        fake_proc.kill.side_effect = RuntimeError("kill also failed")
        monkeypatch.setattr("core.evaluator._start_managed_llama_server", MagicMock(return_value=fake_proc))
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot.fetch_llama_server_metrics",
            MagicMock(return_value={"available": True}),
        )
        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", MagicMock(side_effect=CafActiveSessionError("busy")))

        with pytest.raises(CafActiveSessionError):
            plugin.run_evaluation(MagicMock(), _base_config(), lambda msg: None)

    def test_ssh_path_closes_llama_env_and_uses_native_url_for_caf(self, monkeypatch):
        plugin = CafLlamaBotPlugin()
        fake_env = MagicMock()
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )
        fake_server = MagicMock()
        fake_server.local_port = 54321
        monkeypatch.setattr(
            "core.remote_server.start_remote_managed_llama_server", MagicMock(return_value=fake_server)
        )
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot.fetch_llama_server_metrics",
            MagicMock(return_value={"available": True}),
        )

        seen_config = {}

        def fake_super_run(self, env, config, on_log):
            seen_config.update(config)
            return {"run_backend": "CAF"}

        monkeypatch.setattr(CafCliRunPlugin, "run_evaluation", fake_super_run)

        config = _base_config(execution_target="ssh", ssh_host="10.0.0.5")
        telemetry = plugin.run_evaluation(MagicMock(), config, lambda msg: None)

        assert telemetry["run_bot_type"] == "caf_llama_bot"
        # CAF's own process runs ON the remote host, so it must reach the
        # server at its own bind address, not through the SSH tunnel.
        assert seen_config["caf_cli_url"] == "http://127.0.0.1:8090"
        fake_server.terminate.assert_called_once()
        fake_env.close.assert_called_once()


class TestPortDefaults:
    """New/missing configs get 8080 (matching every other llama bot type);
    an explicitly saved port — including a pre-existing 8090 — must survive."""

    def test_default_config_uses_8080(self):
        plugin = CafLlamaBotPlugin()
        assert plugin.default_config()["server_port"] == 8080

    def test_normalize_defaults_missing_port_to_8080(self):
        plugin = CafLlamaBotPlugin()
        config = plugin.normalize_project_config({"model_name": "llama3.gguf"})
        assert config["server_port"] == 8080

    def test_normalize_preserves_explicit_legacy_port(self):
        plugin = CafLlamaBotPlugin()
        config = plugin.normalize_project_config({"model_name": "llama3.gguf", "server_port": 8090})
        assert config["server_port"] == 8090
        assert config["caf_cli_url"] == "http://127.0.0.1:8090"

    def test_ssh_port_defaults_to_22_when_missing(self):
        plugin = CafLlamaBotPlugin()
        config = plugin.normalize_project_config({"model_name": "llama3.gguf"})
        assert config["ssh_port"] == 22

    def test_ssh_port_preserves_explicit_value(self):
        plugin = CafLlamaBotPlugin()
        config = plugin.normalize_project_config({"model_name": "llama3.gguf", "ssh_port": 2222})
        assert config["ssh_port"] == 2222


class TestScanCafLlamaModels:
    def test_no_model_dir_reports_error(self):
        models, error = scan_caf_llama_models({"execution_target": "local"})
        assert models == []
        assert "Model Directory" in error

    def test_local_missing_directory_reports_error(self, tmp_path):
        missing = tmp_path / "nope"
        models, error = scan_caf_llama_models({"execution_target": "local", "model_dir": str(missing)})
        assert models == []
        assert "not found" in error

    def test_local_empty_directory_is_not_an_error(self, tmp_path):
        models, error = scan_caf_llama_models({"execution_target": "local", "model_dir": str(tmp_path)})
        assert models == []
        assert error == ""

    def test_local_scan_excludes_vocab_and_keeps_relative_nested_paths(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "model.gguf").touch()
        (tmp_path / "top.gguf").touch()
        (tmp_path / "ggml-vocab-llama.gguf").touch()

        models, error = scan_caf_llama_models({"execution_target": "local", "model_dir": str(tmp_path)})

        assert error == ""
        names = sorted(m["name"] for m in models)
        assert names == [os.path.join("sub", "model.gguf"), "top.gguf"]
        assert sorted(m["path"] for m in models) == names

    def test_local_scan_finds_deep_hugging_face_snapshot_only_gguf(self, tmp_path):
        snapshot = (
            tmp_path / "models--owner--repository" / "snapshots" / "a1b2c3"
            / "quantized" / "release"
        )
        snapshot.mkdir(parents=True)
        (snapshot / "model-q4.gguf").touch()
        (snapshot / "model.safetensors").touch()
        (snapshot / "ggml-vocab-test.gguf").touch()
        (tmp_path / "unrelated.txt").touch()

        models, error = scan_caf_llama_models({
            "execution_target": "local", "model_dir": str(tmp_path),
        })

        expected = os.path.join(
            "models--owner--repository", "snapshots", "a1b2c3",
            "quantized", "release", "model-q4.gguf",
        )
        assert error == ""
        assert models == [{
            "name": expected, "path": expected, "size_gb": 0.0,
        }]

    def test_ssh_uses_shared_environment_and_closes_it(self, monkeypatch):
        fake_env = MagicMock()
        fake_env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -d
            {"stdout": "/models/sub/model.gguf\n/models/top.gguf\n", "stderr": "", "exit_code": 0},  # find
        ]
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )

        config = {"execution_target": "ssh", "model_dir": "/models", "ssh_host": "10.0.0.5"}
        models, error = scan_caf_llama_models(config)

        assert error == ""
        names = sorted(m["name"] for m in models)
        assert names == ["sub/model.gguf", "top.gguf"]
        fake_env.close.assert_called_once()

    def test_ssh_expands_tilde_model_dir_relative_to_home(self, monkeypatch):
        fake_env = MagicMock()
        fake_env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -d
            {"stdout": "/home/user/models/model.gguf\n", "stderr": "", "exit_code": 0},  # find
            {"stdout": "/home/user\n", "stderr": "", "exit_code": 0},  # echo $HOME
        ]
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )

        config = {"execution_target": "ssh", "model_dir": "~/models"}
        models, error = scan_caf_llama_models(config)

        assert error == ""
        assert models == [{"name": "model.gguf", "path": "model.gguf"}]

    def test_ssh_missing_directory_reports_distinct_error(self, monkeypatch):
        fake_env = MagicMock()
        fake_env.execute.return_value = {"stdout": "", "stderr": "", "exit_code": 1}
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )

        models, error = scan_caf_llama_models({"execution_target": "ssh", "model_dir": "/nope"})

        assert models == []
        assert "not found on the SSH target" in error
        fake_env.close.assert_called_once()

    def test_ssh_connection_failure_reports_distinct_error(self, monkeypatch):
        fake_env = MagicMock()
        fake_env.execute.return_value = {"stdout": "", "stderr": "connection refused", "exit_code": -1}
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )

        models, error = scan_caf_llama_models({"execution_target": "ssh", "model_dir": "/models"})

        assert models == []
        assert "SSH connection failed" in error
        fake_env.close.assert_called_once()

    def test_ssh_scan_failure_reports_error(self, monkeypatch):
        fake_env = MagicMock()
        fake_env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -d
            {"stdout": "", "stderr": "find: permission denied", "exit_code": 1},  # find
        ]
        monkeypatch.setattr(
            "plugins.bot_types.caf_llama_bot._environment_for_config", MagicMock(return_value=fake_env)
        )

        models, error = scan_caf_llama_models({"execution_target": "ssh", "model_dir": "/models"})

        assert models == []
        assert "permission denied" in error
        fake_env.close.assert_called_once()
