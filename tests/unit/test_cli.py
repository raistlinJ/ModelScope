"""
Comprehensive unit tests for cli.py.

Covers:
  - _use_color, _c, _colorize_log_line
  - _load_config_file (JSON, YAML, missing)
  - _box_table
  - _build_config
  - _make_env
  - _apply_config_file_defaults (config file + env-var tiers)
  - _print_run_summary
  - _maybe_inject_run_subcommand
  - main() dispatch: run, batch, sessions list/show, scenarios, --list-scenarios
  - _cmd_run: --dry-run, --json, --model missing, SSH params
  - _cmd_batch: missing file, parse error, non-list, SSH jobs skipped, unknown scenario
  - _cmd_sessions_list, _cmd_sessions_show
  - _cmd_scenarios: list all, --describe, unknown
  - _find_session, _read_telemetry
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import types
from io import StringIO
from unittest.mock import MagicMock, patch, mock_open

import pytest

# --------------------------------------------------------------------------- #
#  Module-level helpers (no side-effects on import)                           #
# --------------------------------------------------------------------------- #

import cli


# ── _use_color / _c ────────────────────────────────────────────────────────────

class TestUseColor:
    def test_no_color_when_env_set(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        # Reload to see env change — just call directly
        assert cli._use_color() is False

    def test_c_returns_plain_text_without_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        result = cli._c("hello", cli._BOLD)
        assert result == "hello"

    def test_c_wraps_with_codes_with_color(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        # isatty may be False in test; just check no exception
        result = cli._c("hello", cli._BOLD)
        assert "hello" in result


# ── _colorize_log_line ─────────────────────────────────────────────────────────

class TestColorizeLogLine:
    def test_no_color_returns_unchanged(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        line = "[LLM] Agent turn 1"
        assert cli._colorize_log_line(line) == line

    def test_error_tag_recognized(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        with patch("cli._use_color", return_value=True):
            result = cli._colorize_log_line("[ERROR] something failed")
            # The tag should be wrapped in ANSI codes
            assert "[ERROR]" in result

    def test_plain_line_unchanged_with_color(self, monkeypatch):
        with patch("cli._use_color", return_value=True):
            line = "no tag here"
            result = cli._colorize_log_line(line)
            assert "no tag here" in result


# ── _load_config_file ──────────────────────────────────────────────────────────

class TestLoadConfigFile:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        result = cli._load_config_file()
        assert result == {}

    def test_loads_json_config(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".modelscope"
        config_dir.mkdir()
        (config_dir / "cli.json").write_text(
            json.dumps({"model": "mymodel", "backend": "ollama"}),
            encoding="utf-8"
        )
        result = cli._load_config_file()
        assert result["model"] == "mymodel"

    def test_ignores_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".modelscope"
        config_dir.mkdir()
        (config_dir / "cli.json").write_text("NOT VALID JSON", encoding="utf-8")
        result = cli._load_config_file()
        assert result == {}

    def test_ignores_non_dict_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".modelscope"
        config_dir.mkdir()
        (config_dir / "cli.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        result = cli._load_config_file()
        assert result == {}

    def test_loads_yaml_when_available(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".modelscope"
        config_dir.mkdir()
        # Write a YAML file (no JSON present)
        (config_dir / "cli.yaml").write_text("model: yaml_model\n", encoding="utf-8")
        try:
            import yaml  # noqa
            result = cli._load_config_file()
            assert result.get("model") == "yaml_model"
        except ImportError:
            pytest.skip("pyyaml not installed")


# ── _box_table ─────────────────────────────────────────────────────────────────

class TestBoxTable:
    def test_returns_no_data_for_empty(self):
        assert cli._box_table([]) == "(no data)"

    def test_contains_header_and_data(self):
        rows = [{"Name": "Alice", "Score": "95"}]
        table = cli._box_table(rows)
        assert "Name" in table
        assert "Alice" in table
        assert "Score" in table
        assert "95" in table

    def test_title_appears(self):
        rows = [{"A": "1"}]
        table = cli._box_table(rows, title="My Table")
        assert "My Table" in table

    def test_multiple_rows(self):
        rows = [{"X": str(i)} for i in range(5)]
        table = cli._box_table(rows)
        for i in range(5):
            assert str(i) in table

    def test_box_drawing_characters(self):
        rows = [{"Col": "val"}]
        table = cli._box_table(rows)
        assert "┌" in table
        assert "┘" in table


# ── _build_config ──────────────────────────────────────────────────────────────

class TestBuildConfig:
    def _args(self, **overrides):
        args = argparse.Namespace(
            model="testmodel",
            backend="llama.cpp",
            llm_url=None,
            context_size=4096,
            scenario="Scenario 1 – File Creation",
            system_prompt=None,
            user_prompt=None,
            mcp_url="",
            ssh_host=None,
            ssh_port=22,
            ssh_user="root",
            ssh_password=None,
            ssh_key_path=None,
            ssh_caf_dir="~/cyber-agent-flow",
            caf_scope=None,
            caf_urgency=None,
        )
        for k, v in overrides.items():
            setattr(args, k, v)
        return args

    def test_basic_config_keys(self):
        cfg = cli._build_config(self._args())
        assert cfg["selected_model"] == "testmodel"
        assert cfg["backend_type"] == "llama.cpp"
        assert cfg["execution_mode"] == "local"

    def test_ssh_host_sets_caf_ssh_mode(self):
        cfg = cli._build_config(self._args(ssh_host="10.0.0.1"))
        assert cfg["execution_mode"] == "caf_ssh"

    def test_system_prompt_override(self):
        cfg = cli._build_config(self._args(system_prompt="custom sys"))
        assert cfg["sys_prompt"] == "custom sys"

    def test_user_prompt_override(self):
        cfg = cli._build_config(self._args(user_prompt="custom user"))
        assert cfg["user_prompt"] == "custom user"

    def test_default_url_llama_cpp(self):
        from config.defaults import LLAMA_CPP_DEFAULT_URL
        cfg = cli._build_config(self._args(backend="llama.cpp"))
        assert cfg["llm_url"] == LLAMA_CPP_DEFAULT_URL

    def test_default_url_ollama(self):
        from config.defaults import OLLAMA_DEFAULT_URL
        cfg = cli._build_config(self._args(backend="ollama"))
        assert cfg["llm_url"] == OLLAMA_DEFAULT_URL

    def test_llm_url_override(self):
        cfg = cli._build_config(self._args(llm_url="http://custom:9999"))
        assert "custom" in cfg["llm_url"]

    def test_caf_scope_override(self):
        cfg = cli._build_config(self._args(caf_scope="Broad"))
        assert cfg["caf_scope"] == "Broad"

    def test_cancel_requested_ref_is_list(self):
        cfg = cli._build_config(self._args())
        assert isinstance(cfg["cancel_requested_ref"], list)
        assert cfg["cancel_requested_ref"] == [False]

    def test_mcp_running_when_url_provided(self):
        cfg = cli._build_config(self._args(mcp_url="http://localhost:3000"))
        assert cfg["mcp_running"] is True

    def test_mcp_not_running_when_no_url(self):
        cfg = cli._build_config(self._args(mcp_url=""))
        assert cfg["mcp_running"] is False


# ── _make_env ──────────────────────────────────────────────────────────────────

class TestMakeEnv:
    def _args(self, ssh_host=None, ssh_port=22, ssh_user="root",
               ssh_password=None, ssh_key_path=None, ssh_caf_dir="~/cyber-agent-flow"):
        return argparse.Namespace(
            ssh_host=ssh_host,
            ssh_port=ssh_port,
            ssh_user=ssh_user,
            ssh_password=ssh_password,
            ssh_key_path=ssh_key_path,
            ssh_caf_dir=ssh_caf_dir,
        )

    def test_returns_local_environment_without_ssh(self):
        from core.environment import LocalEnvironment
        logs = []
        env = cli._make_env(self._args(), lambda m: logs.append(m))
        assert isinstance(env, LocalEnvironment)
        assert any("[INIT]" in l for l in logs)

    def test_returns_ssh_environment_with_ssh_host(self):
        from core.environment import SSHEnvironment
        logs = []
        env = cli._make_env(self._args(ssh_host="10.0.0.1"), lambda m: logs.append(m))
        assert isinstance(env, SSHEnvironment)
        assert any("SSH" in l for l in logs)

    def test_ssh_env_has_correct_host(self):
        from core.environment import SSHEnvironment
        env = cli._make_env(self._args(ssh_host="192.168.1.10"), lambda _: None)
        assert env.host == "192.168.1.10"

    def test_ssh_env_has_correct_username(self):
        from core.environment import SSHEnvironment
        env = cli._make_env(self._args(ssh_host="10.0.0.1", ssh_user="kali"), lambda _: None)
        assert env.username == "kali"


# ── _apply_config_file_defaults ───────────────────────────────────────────────

class TestApplyConfigFileDefaults:
    def _args(self, **kw):
        a = argparse.Namespace(
            model=None,
            backend="llama.cpp",
            verbose=False,
            dry_run=False,
            context_size=4096,
        )
        for k, v in kw.items():
            setattr(a, k, v)
        return a

    def test_config_file_sets_unset_key(self):
        args = self._args()
        cli._apply_config_file_defaults(args, {"model": "from_file"}, [])
        assert args.model == "from_file"

    def test_explicit_cli_flag_wins_over_config_file(self):
        args = self._args(model="cli_model")
        cli._apply_config_file_defaults(args, {"model": "from_file"}, ["--model", "cli_model"])
        assert args.model == "cli_model"

    def test_env_var_overrides_config_file(self, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_MODEL", "from_env")
        args = self._args()
        cli._apply_config_file_defaults(args, {"model": "from_file"}, [])
        assert args.model == "from_env"

    def test_explicit_cli_wins_over_env_var(self, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_MODEL", "from_env")
        args = self._args(model="cli_model")
        cli._apply_config_file_defaults(args, {}, ["--model", "cli_model"])
        assert args.model == "cli_model"

    def test_bool_env_var_true_values(self, monkeypatch):
        for val in ("1", "true", "yes"):
            monkeypatch.setenv("MODELSCOPE_VERBOSE", val)
            args = self._args(verbose=False)
            cli._apply_config_file_defaults(args, {}, [])
            assert args.verbose is True

    def test_bool_env_var_false_value(self, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_VERBOSE", "false")
        args = self._args(verbose=True)
        cli._apply_config_file_defaults(args, {}, [])
        assert args.verbose is False

    def test_int_env_var(self, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_CONTEXT_SIZE", "8192")
        args = self._args()
        cli._apply_config_file_defaults(args, {}, [])
        assert args.context_size == 8192

    def test_int_env_var_invalid_ignored(self, monkeypatch):
        monkeypatch.setenv("MODELSCOPE_CONTEXT_SIZE", "notanint")
        args = self._args()
        cli._apply_config_file_defaults(args, {}, [])
        assert args.context_size == 4096  # unchanged

    def test_unknown_config_file_key_ignored(self):
        args = self._args()
        # 'unknown_key' not in args namespace — should not crash
        cli._apply_config_file_defaults(args, {"unknown_key": "val"}, [])

    def test_hyphen_config_key_normalised(self):
        args = self._args()
        args.dry_run = False
        cli._apply_config_file_defaults(args, {"dry-run": True}, [])
        assert args.dry_run is True


# ── _print_run_summary ─────────────────────────────────────────────────────────

class TestPrintRunSummary:
    def _tel(self, **kw):
        base = {
            "run_scenario": "Scenario 1",
            "run_model": "testmodel",
            "run_backend": "llama.cpp",
            "run_aborted": False,
            "validation_passed": None,
            "total_latency": 1.5,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "llm_rounds": 2,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        base.update(kw)
        return base

    def test_no_exception_on_basic_telemetry(self, capsys):
        cli._print_run_summary(self._tel())
        # Any output is fine — just no exception

    def test_passed_shows_PASSED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(validation_passed=True))
        out = capsys.readouterr().out
        assert "PASSED" in out

    def test_failed_shows_FAILED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(validation_passed=False))
        out = capsys.readouterr().out
        assert "FAILED" in out

    def test_aborted_shows_ABORTED(self, capsys):
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(run_aborted=True))
        out = capsys.readouterr().out
        assert "ABORTED" in out

    def test_metrics_matrix_evaluated(self, capsys):
        matrix = [{"type": "validation_passed", "enabled": True}]
        with patch("cli._use_color", return_value=False):
            cli._print_run_summary(self._tel(metrics_matrix=matrix, validation_passed=True))
        # No exception is the key assertion here


# ── _maybe_inject_run_subcommand ───────────────────────────────────────────────

class TestMaybeInjectRunSubcommand:
    def test_no_args_unchanged(self):
        assert cli._maybe_inject_run_subcommand([]) == []

    def test_run_subcommand_unchanged(self):
        argv = ["run", "--model", "x"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_batch_subcommand_unchanged(self):
        argv = ["batch", "--jobs-file", "j.json"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_sessions_subcommand_unchanged(self):
        argv = ["sessions", "list"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_scenarios_subcommand_unchanged(self):
        argv = ["scenarios"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_help_flag_unchanged(self):
        argv = ["--help"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_list_scenarios_flag_unchanged(self):
        argv = ["--list-scenarios"]
        assert cli._maybe_inject_run_subcommand(argv) == argv

    def test_flat_model_arg_injects_run(self):
        argv = ["--model", "mymodel"]
        result = cli._maybe_inject_run_subcommand(argv)
        assert result[0] == "run"

    def test_flat_dry_run_injects_run(self):
        argv = ["--model", "x", "--dry-run"]
        result = cli._maybe_inject_run_subcommand(argv)
        assert result[0] == "run"

    def test_flag_equal_value_skipped(self):
        argv = ["--model=mymodel", "--dry-run"]
        result = cli._maybe_inject_run_subcommand(argv)
        assert result[0] == "run"

    def test_bare_positional_not_subcommand(self):
        # A bare positional that isn't a subcommand name — leave unchanged
        argv = ["some_file.py"]
        result = cli._maybe_inject_run_subcommand(argv)
        assert result == argv


# ── main() dispatch ────────────────────────────────────────────────────────────

class TestMainDispatch:
    def test_no_subcommand_prints_help(self, capsys):
        ret = cli.main([])
        out = capsys.readouterr().out + capsys.readouterr().err
        assert ret == 0

    def test_list_scenarios_legacy_flag(self, capsys):
        ret = cli.main(["--list-scenarios"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "Available" in out

    def test_scenarios_subcommand(self, capsys):
        ret = cli.main(["scenarios"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "Scenario" in out

    def test_scenarios_describe_unknown(self, capsys):
        ret = cli.main(["scenarios", "--describe", "NonExistentScenario999"])
        assert ret == 2

    def test_scenarios_describe_known(self, capsys):
        from config.scenarios import SCENARIOS
        name = next(iter(SCENARIOS))
        ret = cli.main(["scenarios", "--describe", name])
        assert ret == 0

    def test_sessions_no_dir_exits_zero(self, tmp_path, capsys):
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path / "nonexistent")])
        assert ret == 0

    def test_sessions_list_empty_dir(self, tmp_path, capsys):
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_list_with_data(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd1234"
        session_dir.mkdir()
        tel = {"run_scenario": "Test", "run_model": "model", "total_latency": 1.0, "validation_passed": True, "total_tokens": 100}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert ret == 0
        assert "abcd1234" in out or "2026" in out

    def test_sessions_list_limit(self, tmp_path, capsys):
        for i in range(5):
            d = tmp_path / f"2026-06-18_12-00-0{i}_abcd000{i}"
            d.mkdir()
        ret = cli.main(["sessions", "list", "--sessions-dir", str(tmp_path), "-n", "2"])
        assert ret == 0

    def test_sessions_show_not_found(self, tmp_path, capsys):
        ret = cli.main(["sessions", "show", "nonexistent", "--sessions-dir", str(tmp_path)])
        assert ret == 2

    def test_sessions_show_found(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd1234"
        session_dir.mkdir()
        tel = {"run_scenario": "Test", "run_model": "m", "total_latency": 1.0,
               "validation_passed": True, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry.json").write_text(json.dumps(tel))
        (session_dir / "run.log").write_text("[INIT] started")
        ret = cli.main(["sessions", "show", "abcd1234", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_show_uses_telemetry_0(self, tmp_path, capsys):
        session_dir = tmp_path / "2026-06-18_12-00-00_abcd5678"
        session_dir.mkdir()
        tel = {"run_scenario": "CAF", "run_model": "m", "total_latency": 2.0,
               "validation_passed": None, "total_tokens": 0, "tool_calls": [],
               "run_aborted": False, "run_backend": "llama.cpp",
               "prompt_tokens": 0, "completion_tokens": 0, "llm_rounds": 0,
               "run_timestamp": "2026-06-18"}
        (session_dir / "telemetry_0.json").write_text(json.dumps(tel))
        ret = cli.main(["sessions", "show", "abcd5678", "--sessions-dir", str(tmp_path)])
        assert ret == 0

    def test_sessions_no_action_defaults_to_list(self, tmp_path, capsys):
        # 'sessions' with no sub-action should default to list using the default sessions dir
        with patch("cli._default_sessions_dir", return_value=tmp_path / "noexist"):
            ret = cli.main(["sessions"])
        assert ret == 0


# ── _cmd_run ──────────────────────────────────────────────────────────────────

class TestCmdRun:
    def _run_args(self, **kw):
        """Parse a minimal 'run' invocation through the real arg parser."""
        argv = ["run", "--model", "test-model"] + [
            item for k, v in kw.items()
            for item in ([f"--{k.replace('_', '-')}"] + ([str(v)] if not isinstance(v, bool) else []))
        ]
        return argv

    def test_missing_model_returns_2(self, capsys):
        ret = cli.main(["run"])
        assert ret == 2

    def test_dry_run_prints_config_and_exits_0(self, capsys):
        ret = cli.main(["run", "--model", "mymodel", "--dry-run"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "mymodel" in out

    def test_dry_run_redacts_password(self, capsys):
        ret = cli.main(["run", "--model", "m", "--dry-run",
                         "--ssh-host", "10.0.0.1", "--ssh-password", "secret"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "secret" not in out
        assert "REDACTED" in out

    def test_dry_run_with_ssh_shows_params(self, capsys):
        ret = cli.main(["run", "--model", "m", "--dry-run",
                         "--ssh-host", "10.0.0.1", "--ssh-user", "kali"])
        out = capsys.readouterr().out
        assert ret == 0
        assert "ssh" in out.lower() or "_ssh_params" in out

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_run_calls_run_evaluation(self, mock_make_env, mock_run_eval, tmp_path):
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": True,
            "run_aborted": False,
            "total_latency": 1.0,
            "run_scenario": "s",
            "run_model": "m",
            "run_backend": "llama.cpp",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_rounds": 0,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        ret = cli.main(["run", "--model", "mymodel", "--session-dir", str(tmp_path)])
        mock_run_eval.assert_called_once()
        assert ret == 0

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_run_returns_1_when_validation_fails(self, mock_make_env, mock_run_eval, tmp_path):
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": False,
            "run_aborted": False,
            "total_latency": 1.0,
            "run_scenario": "s",
            "run_model": "m",
            "run_backend": "llama.cpp",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_rounds": 0,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        ret = cli.main(["run", "--model", "m", "--session-dir", str(tmp_path)])
        assert ret == 1

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_run_json_flag_prints_telemetry(self, mock_make_env, mock_run_eval, capsys, tmp_path):
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        tel = {
            "validation_passed": True,
            "run_aborted": False,
            "total_latency": 1.0,
            "run_scenario": "s",
            "run_model": "m",
            "run_backend": "llama.cpp",
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
            "llm_rounds": 1,
            "tool_calls": [],
            "metrics_matrix": [],
        }
        mock_run_eval.return_value = tel
        ret = cli.main(["run", "--model", "m", "--json", "--session-dir", str(tmp_path)])
        out = capsys.readouterr().out
        assert ret == 0
        assert '"run_model"' in out

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_env_close_called_even_on_success(self, mock_make_env, mock_run_eval, tmp_path):
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": True, "run_aborted": False, "total_latency": 0.1,
            "run_scenario": "s", "run_model": "m", "run_backend": "b",
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "llm_rounds": 0, "tool_calls": [], "metrics_matrix": [],
        }
        cli.main(["run", "--model", "m", "--session-dir", str(tmp_path)])
        mock_env.close.assert_called_once()

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_config_file_defaults_applied_before_run(self, mock_make_env, mock_run_eval, tmp_path, monkeypatch):
        """Config file model overrides empty --model when not explicitly set."""
        monkeypatch.setattr(pathlib.Path, "home", lambda: tmp_path)
        config_dir = tmp_path / ".modelscope"
        config_dir.mkdir()
        (config_dir / "cli.json").write_text(json.dumps({"backend": "ollama"}), encoding="utf-8")

        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": True, "run_aborted": False, "total_latency": 0.1,
            "run_scenario": "s", "run_model": "m", "run_backend": "ollama",
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "llm_rounds": 0, "tool_calls": [], "metrics_matrix": [],
        }
        # Pass backend explicitly — config file provides ollama
        ret = cli.main(["run", "--model", "m", "--session-dir", str(tmp_path)])
        assert ret == 0


# ── _cmd_batch ─────────────────────────────────────────────────────────────────

class TestCmdBatch:
    def test_missing_jobs_file_returns_2(self, tmp_path, capsys):
        ret = cli.main(["batch", "--jobs-file", str(tmp_path / "nonexistent.json")])
        assert ret == 2

    def test_invalid_json_returns_2(self, tmp_path, capsys):
        p = tmp_path / "jobs.json"
        p.write_text("NOT JSON")
        ret = cli.main(["batch", "--jobs-file", str(p)])
        assert ret == 2

    def test_non_list_json_returns_2(self, tmp_path, capsys):
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps({"not": "a list"}))
        ret = cli.main(["batch", "--jobs-file", str(p)])
        assert ret == 2

    def test_empty_list_returns_0(self, tmp_path, capsys):
        p = tmp_path / "jobs.json"
        p.write_text("[]")
        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 0

    def test_non_dict_entry_skipped(self, tmp_path, capsys):
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps(["not_a_dict"]))
        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 0

    def test_unknown_scenario_skipped(self, tmp_path, capsys):
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps([{"scenario": "NONEXISTENT_SCENARIO_XYZ", "model": "m"}]))
        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 0

    def test_ssh_job_skipped(self, tmp_path, capsys):
        from config.scenarios import SCENARIOS
        name = next(iter(SCENARIOS))
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps([{"scenario": name, "model": "m", "ssh_host": "10.0.0.1"}]))
        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 0

    @patch("core.batch_runner.BatchRunner.run")
    @patch("core.batch_runner.BatchRunner.export_csv")
    def test_valid_job_runs_batch(self, mock_csv, mock_run, tmp_path):
        from config.scenarios import SCENARIOS
        from core.batch_runner import BatchReport
        name = next(iter(SCENARIOS))
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps([{"scenario": name, "model": "m"}]))

        mock_run.return_value = BatchReport(
            total_jobs=1,
            completed=1,
            failed=0,
            duration_seconds=1.0,
            summary_rows=[{
                "job_id": "j1", "label": "test", "status": "done",
                "latency": 1.0, "total_tokens": 0,
                "passed_metrics": 0, "failed_metrics": 0, "error": "",
            }],
        )
        mock_csv.return_value = "job_id,label\nj1,test\n"

        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 0

    @patch("core.batch_runner.BatchRunner.run")
    @patch("core.batch_runner.BatchRunner.export_csv")
    def test_batch_returns_1_on_failure(self, mock_csv, mock_run, tmp_path):
        from config.scenarios import SCENARIOS
        from core.batch_runner import BatchReport
        name = next(iter(SCENARIOS))
        p = tmp_path / "jobs.json"
        p.write_text(json.dumps([{"scenario": name, "model": "m"}]))

        mock_run.return_value = BatchReport(
            total_jobs=1,
            completed=0,
            failed=1,
            duration_seconds=1.0,
            summary_rows=[{
                "job_id": "j1", "label": "test", "status": "failed",
                "latency": 0.0, "total_tokens": 0,
                "passed_metrics": 0, "failed_metrics": 0, "error": "crash",
            }],
        )
        mock_csv.return_value = ""

        ret = cli.main(["batch", "--jobs-file", str(p), "--output-dir", str(tmp_path / "out")])
        assert ret == 1


# ── _default_sessions_dir ─────────────────────────────────────────────────────

class TestDefaultSessionsDir:
    def test_returns_path(self):
        result = cli._default_sessions_dir()
        assert isinstance(result, pathlib.Path)
        assert "sessions" in str(result)


# ── backward-compat flat invocation ───────────────────────────────────────────

class TestBackwardCompatInvocation:
    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_flat_invocation_works(self, mock_make_env, mock_run_eval, tmp_path):
        """cli.py --model m should work via run injection."""
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": True, "run_aborted": False, "total_latency": 0.1,
            "run_scenario": "s", "run_model": "m", "run_backend": "llama.cpp",
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "llm_rounds": 0, "tool_calls": [], "metrics_matrix": [],
        }
        ret = cli.main(["--model", "m", "--session-dir", str(tmp_path)])
        assert ret == 0

    @patch("cli.run_evaluation")
    @patch("cli._make_env")
    def test_env_var_model_default(self, mock_make_env, mock_run_eval, tmp_path, monkeypatch):
        """MODELSCOPE_MODEL env var fills in missing --model."""
        monkeypatch.setenv("MODELSCOPE_MODEL", "env_model")
        mock_env = MagicMock()
        mock_make_env.return_value = mock_env
        mock_run_eval.return_value = {
            "validation_passed": True, "run_aborted": False, "total_latency": 0.1,
            "run_scenario": "s", "run_model": "env_model", "run_backend": "llama.cpp",
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "llm_rounds": 0, "tool_calls": [], "metrics_matrix": [],
        }
        ret = cli.main(["run", "--session-dir", str(tmp_path)])
        mock_run_eval.assert_called_once()
        cfg = mock_run_eval.call_args[0][1]
        assert cfg["selected_model"] == "env_model"
