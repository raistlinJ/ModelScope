"""
Unit tests for core.evaluator.run_bash_evaluation and the bash-mode dispatch
in run_evaluation.

The environment is always a MagicMock so no shell processes run; we verify
command construction, cancel behaviour, sudo prefixing, step-format parsing,
and validation wiring.
"""
import time
import pytest
from unittest.mock import MagicMock, call
from core.evaluator import run_bash_evaluation, run_evaluation
from core.environment import LocalEnvironment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(stdout="", stderr="", exit_code=0):
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = False
    return env


def _log():
    return lambda msg: None


def _cfg(**overrides):
    base = {"startup_commands": [], "cancel_requested_ref": [False]}
    base.update(overrides)
    return base


# ── Legacy string-command format ──────────────────────────────────────────────

class TestRunBashEvaluationLegacyCommands:
    def test_string_commands_produce_tool_calls(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(startup_commands=["echo hello", "ls /tmp"]), _log())
        assert len(result["tool_calls"]) == 2
        assert all(tc["tool"] == "bash" for tc in result["tool_calls"])

    def test_tool_calls_record_command(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(startup_commands=["whoami"]), _log())
        assert result["tool_calls"][0]["args"]["command"] == "whoami"

    def test_empty_string_commands_are_skipped(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(startup_commands=["", "   ", "echo x"]), _log())
        assert len(result["tool_calls"]) == 1

    def test_no_commands_gives_empty_tool_calls(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(), _log())
        assert result["tool_calls"] == []

    def test_run_bot_type_is_bash_bot(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(), _log())
        assert result["run_bot_type"] == "bash_bot"

    def test_run_aborted_false_on_normal_completion(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(startup_commands=["echo hi"]), _log())
        assert result["run_aborted"] is False

    def test_total_latency_is_positive(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(), _log())
        assert result["total_latency"] >= 0


# ── Step-format dict commands ─────────────────────────────────────────────────

class TestRunBashEvaluationStepFormat:
    def _step(self, *cmds, delay=0):
        return {
            "delay_seconds": delay,
            "commands": [{"command": c, "enabled": True} for c in cmds],
        }

    def test_step_format_produces_tool_calls(self):
        env = _env()
        result = run_bash_evaluation(
            env, _cfg(startup_commands=[self._step("uname")]), _log()
        )
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["args"]["command"] == "uname"

    def test_disabled_commands_are_skipped(self):
        env = _env()
        step = {"delay_seconds": 0, "commands": [
            {"command": "echo a", "enabled": True},
            {"command": "echo b", "enabled": False},
        ]}
        result = run_bash_evaluation(env, _cfg(startup_commands=[step]), _log())
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["args"]["command"] == "echo a"

    def test_delay_is_applied(self):
        env = _env()
        t0 = time.time()
        run_bash_evaluation(
            env, _cfg(startup_commands=[self._step("echo x", delay=0.05)]), _log()
        )
        assert time.time() - t0 >= 0.04

    def test_long_running_flag_uses_3600_timeout(self):
        env = _env()
        step = {"delay_seconds": 0, "commands": [
            {"command": "long_cmd", "enabled": True, "long_running": True}
        ]}
        run_bash_evaluation(env, _cfg(startup_commands=[step]), _log())
        env.execute.assert_called_once_with("long_cmd", timeout=3600)

    def test_per_command_timeout_overrides_global(self):
        env = _env()
        step = {"delay_seconds": 0, "commands": [
            {"command": "fast_cmd", "enabled": True, "timeout_seconds": 5}
        ]}
        run_bash_evaluation(env, _cfg(startup_commands=[step], bash_timeout=60), _log())
        env.execute.assert_called_once_with("fast_cmd", timeout=5)

    def test_multiple_commands_in_one_step(self):
        env = _env()
        step = self._step("cmd1", "cmd2", "cmd3")
        result = run_bash_evaluation(env, _cfg(startup_commands=[step]), _log())
        assert len(result["tool_calls"]) == 3


# ── Sudo prefix ───────────────────────────────────────────────────────────────

class TestRunBashEvaluationSudo:
    def test_sudo_prefix_applied_to_legacy_commands(self):
        env = _env()
        run_bash_evaluation(
            env, _cfg(startup_commands=["whoami"], sudo=True), _log()
        )
        # First call is the sudo-auth preflight check, second is the actual command.
        env.execute.assert_called_with("sudo whoami", timeout=60)
        assert env.execute.call_args_list == [
            call("sudo -k; sudo -n -v", timeout=10),
            call("sudo whoami", timeout=60),
        ]

    def test_sudo_prefix_applied_to_step_format(self):
        env = _env()
        step = {"delay_seconds": 0, "commands": [{"command": "id", "enabled": True}]}
        run_bash_evaluation(
            env, _cfg(startup_commands=[step], sudo=True), _log()
        )
        env.execute.assert_called_with("sudo id", timeout=60)
        assert env.execute.call_args_list == [
            call("sudo -k; sudo -n -v", timeout=10),
            call("sudo id", timeout=60),
        ]

    def test_no_sudo_by_default(self):
        env = _env()
        run_bash_evaluation(env, _cfg(startup_commands=["whoami"]), _log())
        env.execute.assert_called_once_with("whoami", timeout=60)


# ── Cancel ────────────────────────────────────────────────────────────────────

class TestRunBashEvaluationCancel:
    def test_cancel_before_first_step_sets_aborted(self):
        env = _env()
        result = run_bash_evaluation(
            env, _cfg(startup_commands=["echo a", "echo b"],
                      cancel_requested_ref=[True]), _log()
        )
        assert result["run_aborted"] is True
        env.execute.assert_not_called()

    def test_cancel_mid_step_format_stops_remaining_commands(self):
        env = _env()
        cancel_ref = [False]

        executed = []
        def side_effect(cmd, **kwargs):
            executed.append(cmd)
            cancel_ref[0] = True
            return {"stdout": "", "stderr": "", "exit_code": 0}

        env.execute.side_effect = side_effect
        step = {"delay_seconds": 0, "commands": [
            {"command": "cmd1", "enabled": True},
            {"command": "cmd2", "enabled": True},
        ]}
        result = run_bash_evaluation(
            env, _cfg(startup_commands=[step], cancel_requested_ref=cancel_ref), _log()
        )
        assert result["run_aborted"] is True
        assert "cmd1" in executed[0]
        assert len(executed) == 1  # cmd2 was suppressed

    def test_cancel_skips_completion_commands(self):
        env = _env()
        result = run_bash_evaluation(
            env,
            _cfg(startup_commands=[], completion_commands=["echo cleanup"],
                 cancel_requested_ref=[True]),
            _log(),
        )
        env.execute.assert_not_called()


# ── Validation commands ────────────────────────────────────────────────────────

class TestRunBashEvaluationValidation:
    def test_validation_passes_on_zero_exit(self):
        env = _env(exit_code=0)
        result = run_bash_evaluation(
            env, _cfg(validation_commands=["check_cmd"], fail_patterns=[]), _log()
        )
        assert result["validation_passed"] is True

    def test_validation_fails_on_nonzero_exit(self):
        env = _env(exit_code=1)
        result = run_bash_evaluation(
            env, _cfg(validation_commands=["check_cmd"], fail_patterns=[]), _log()
        )
        assert result["validation_passed"] is False

    def test_multiple_validation_commands_all_must_pass(self):
        env = MagicMock(spec=LocalEnvironment)
        env.is_remote_caf = False
        env.execute.side_effect = [
            {"stdout": "ok",  "stderr": "", "exit_code": 0},
            {"stdout": "bad", "stderr": "", "exit_code": 1},
        ]
        result = run_bash_evaluation(
            env, _cfg(validation_commands=["v1", "v2"], fail_patterns=[]), _log()
        )
        assert result["validation_passed"] is False

    def test_no_validation_commands_leaves_passed_none(self):
        env = _env()
        result = run_bash_evaluation(env, _cfg(), _log())
        assert result["validation_passed"] is None

    def test_fail_pattern_match_fails_validation(self):
        env = _env(stdout="ERROR: operation failed", exit_code=0)
        result = run_bash_evaluation(
            env,
            _cfg(validation_commands=["check"], fail_patterns=["error: operation"]),
            _log(),
        )
        assert result["validation_passed"] is False

    def test_validation_stdout_accumulated(self):
        env = _env(stdout="line1\n", exit_code=0)
        result = run_bash_evaluation(
            env, _cfg(validation_commands=["c1", "c2"], fail_patterns=[]), _log()
        )
        assert result["validation_stdout"] == "line1\nline1\n"


# ── Completion commands ────────────────────────────────────────────────────────

class TestRunBashEvaluationCompletion:
    def test_completion_commands_run_after_startup(self):
        called = []
        env = MagicMock(spec=LocalEnvironment)
        env.is_remote_caf = False
        env.execute.side_effect = lambda cmd, **kw: (
            called.append(cmd) or {"stdout": "", "stderr": "", "exit_code": 0}
        )
        run_bash_evaluation(
            env,
            _cfg(startup_commands=["echo start"], completion_commands=["echo done"]),
            _log(),
        )
        assert called[0] == "echo start"
        assert called[1] == "echo done"

    def test_metrics_matrix_passed_through(self):
        env = _env()
        matrix = [{"id": "M-001", "name": "Success Rate"}]
        result = run_bash_evaluation(env, _cfg(metrics_matrix=matrix), _log())
        assert result["metrics_matrix"] == matrix


# ── run_evaluation bash-mode dispatch ─────────────────────────────────────────

class TestRunEvaluationBashDispatch:
    def test_execution_mode_bash_returns_bash_telemetry(self):
        env = _env()
        result = run_evaluation(
            env,
            _cfg(execution_mode="bash", startup_commands=["echo test"]),
            _log(),
        )
        assert result["run_bot_type"] == "bash_bot"

    def test_bash_mode_result_has_tool_calls_key(self):
        env = _env()
        result = run_evaluation(
            env,
            _cfg(execution_mode="bash", startup_commands=["echo hi"]),
            _log(),
        )
        assert "tool_calls" in result

    def test_bash_mode_does_not_call_llm(self):
        env = _env()
        result = run_evaluation(env, _cfg(execution_mode="bash"), _log())
        assert result.get("llm_rounds") is None  # bash telemetry has no llm_rounds key
