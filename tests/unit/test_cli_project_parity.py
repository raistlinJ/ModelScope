"""CLI ↔ GUI parity for `modelscope project`.

The GUI assembles its run config in ui/execute_tab.py; `_cmd_project` must
mirror that assembly so a project file runs identically from the CLI.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

import cli


def _write_project(tmp_path, bot_type, config, name="Parity Test"):
    path = tmp_path / "project.json"
    path.write_text(json.dumps({"name": name, "type": bot_type, "config": config}))
    return path


def _dry_run(capsys, path, extra_args=()):
    """Run `project --dry-run` and return (parsed config, raw stdout)."""
    ret = cli.main(["project", "-f", str(path), "--dry-run", *extra_args])
    assert ret == 0
    out = capsys.readouterr().out
    # Config JSON starts at the first "{" after the header line
    return json.loads(out[out.index("{"):]), out


def _dry_run_config(capsys, path, extra_args=()):
    return _dry_run(capsys, path, extra_args)[0]


class TestLlamaProjectNormalization:
    def test_alias_keys_derived_like_gui(self, tmp_path, capsys):
        path = _write_project(tmp_path, "llama_cli_bot", {
            "backend": "ollama",
            "model_name": "llama3:8b",
            "tokens": 4096,
            "openai_base_url": "https://llm.example:8443",
        })
        cfg = _dry_run_config(capsys, path)
        assert cfg["backend_type"] == "ollama"
        assert cfg["selected_model"] == "llama3:8b"
        assert cfg["context_size"] == 4096
        assert cfg["llm_url"] == "https://llm.example:8443"
        assert cfg["type"] == "llama_cli_bot"
        assert cfg["mcp_server_url"]  # GUI default injected

    def test_disabled_mcp_servers_filtered(self, tmp_path, capsys):
        path = _write_project(tmp_path, "llama_cli_bot", {
            "mcp_servers": [
                {"name": "on", "enabled": True},
                {"name": "off", "enabled": False},
                {"name": "unset"},
            ],
        })
        cfg = _dry_run_config(capsys, path)
        assert [s["name"] for s in cfg["mcp_servers"]] == ["on"]

    def test_bash_project_not_normalized(self, tmp_path, capsys):
        path = _write_project(tmp_path, "bash_bot", {"bash_timeout": 30})
        cfg = _dry_run_config(capsys, path)
        assert "backend_type" not in cfg
        assert "selected_model" not in cfg


class TestProjectSessionArtifacts:
    @patch("core.session_log.SessionLog")
    @patch("core.environment.create_environment")
    @patch("core.bot_types.get_bot_plugin")
    def test_project_saves_effective_config_with_telemetry(
        self, mock_plugin_lookup, mock_create_environment, mock_session_log,
        tmp_path,
    ):
        """Headless bot runs must retain both result data and launch settings."""
        project = _write_project(tmp_path, "llama_cli_bot", {
            "backend": "llama.cpp",
            "model_name": "model.gguf",
        })
        plugin = MagicMock()
        telemetry = {
            "validation_passed": True,
            "run_scenario": "",
            "run_model": "model.gguf",
            "run_backend": "llama.cpp",
            "total_latency": 0.1,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_rounds": 0,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        plugin.run_evaluation.return_value = telemetry
        mock_plugin_lookup.return_value = plugin
        mock_create_environment.return_value = MagicMock()
        session = mock_session_log.return_value

        assert cli.main(["project", "--file", str(project)]) == 0

        session.save_telemetry.assert_called_once_with(telemetry)
        session.save_config.assert_called_once()
        saved_config = session.save_config.call_args.args[0]
        assert saved_config["model_name"] == "model.gguf"
        session.close.assert_called_once()


class TestProxBatchProjectRun:
    """`modelscope project` must batch a ProxBatch project the way the Execute
    tab does — one evaluation per selected container, not one run total."""

    def _project(self, tmp_path):
        return _write_project(tmp_path, "llama_server_proxbatch_bot", {
            "pct_vmids": ["100", "101"],
            "pct_vmid_names": {"100": "kali-one", "101": "kali-two"},
            "binary_path": "/usr/local/bin/llama-server",
            "model_dir": "/opt/models",
            "model_name": "demo.gguf",
            "startup_commands": [{"commands": [{"command": "echo start"}]}],
            "validation_sets": [{"name": "smoke", "steps": [{"commands": [{"command": "true"}]}]}],
        })

    @patch("core.session_log.SessionLog")
    @patch("core.evaluator.run_llama_cli_evaluation")
    def test_each_container_is_evaluated_in_its_own_environment(
        self, mock_evaluation, mock_session_log, tmp_path, capsys,
    ):
        seen = []

        def fake_evaluation(env, config, on_log):
            seen.append((env.vmid, config["server_in_container"]))
            on_log("[STARTUP] echo start", "shell")
            on_log("[VALIDATE CMD] Running: true", "shell")
            return {"validation_passed": True, "total_latency": 1.5}

        mock_evaluation.side_effect = fake_evaluation

        assert cli.main(["project", "-f", str(self._project(tmp_path))]) == 0

        assert seen == [("100", True), ("101", True)]
        telemetry = mock_session_log.return_value.save_telemetry.call_args.args[0]
        assert telemetry["run_bot_type"] == "llama_server_proxbatch_bot"
        assert telemetry["pct_vmids"] == ["100", "101"]
        assert telemetry["total_latency"] == 3.0
        # Log-derived progress must be recorded headlessly too, so a CLI run's
        # session log renders in the GUI exactly like one started there.
        assert [item["units_started"] for item in telemetry["batch_containers"]] == [2, 2]
        assert "Containers" in capsys.readouterr().out

    @patch("core.session_log.SessionLog")
    @patch("core.evaluator.run_llama_cli_evaluation")
    def test_the_summary_breaks_the_batch_down_per_container(
        self, mock_evaluation, mock_session_log, tmp_path, capsys,
    ):
        mock_evaluation.side_effect = lambda env, config, on_log: {
            "validation_passed": config["pct_vmid"] == "100", "total_latency": 1.0,
        }

        cli.main(["project", "-f", str(self._project(tmp_path))])

        out = capsys.readouterr().out
        assert "kali-one" in out
        assert "kali-two" in out
        assert "PASSED" in out
        assert "FAILED" in out


class TestLlmHelperApiKeyResolution:
    def test_flag_injects_helper_api_key(self, tmp_path, capsys):
        path = _write_project(tmp_path, "llama_cli_bot", {"llm_helper_enabled": True})
        cfg = _dry_run_config(capsys, path, ("--llm-helper-api-key", "sk-flag"))
        # Redacted in dry-run output, but present → resolution worked
        assert cfg["llm_helper_openai_apikey"] == "***REDACTED***"

    def test_env_injects_helper_api_key(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_LLM_HELPER_API_KEY", "sk-env")
        path = _write_project(tmp_path, "llama_cli_bot", {"llm_helper_enabled": True})
        cfg = _dry_run_config(capsys, path)
        assert cfg["llm_helper_openai_apikey"] == "***REDACTED***"

    def test_project_file_key_used_when_no_override(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv("MODELSCOPE_LLM_HELPER_API_KEY", raising=False)
        path = _write_project(tmp_path, "llama_cli_bot", {
            "llm_helper_enabled": True,
            "llm_helper_openai_apikey": "sk-from-file",
        })
        cfg, raw = _dry_run(capsys, path)
        assert cfg["llm_helper_openai_apikey"] == "***REDACTED***"
        assert "sk-from-file" not in raw


class TestDryRunRedaction:
    def test_api_keys_and_passwords_redacted(self, tmp_path, capsys):
        path = _write_project(tmp_path, "llama_cli_bot", {
            "openai_api_key": "sk-secret",
            "llm_helper_openai_apikey": "sk-helper",
            "sudo_password": "hunter2",
        })
        out_cfg, raw = _dry_run(capsys, path, ("--llm-helper-api-key", "sk-override"))
        for secret in ("sk-secret", "hunter2", "sk-override"):
            assert secret not in raw
        assert out_cfg["openai_api_key"] == "***REDACTED***"
        assert out_cfg["llm_helper_openai_apikey"] == "***REDACTED***"
        assert out_cfg["sudo_password"] == "***REDACTED***"
