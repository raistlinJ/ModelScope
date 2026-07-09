"""CLI ↔ GUI parity for `modelscope project`.

The GUI assembles its run config in ui/execute_tab.py; `_cmd_project` must
mirror that assembly so a project file runs identically from the CLI.
"""
import json

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
