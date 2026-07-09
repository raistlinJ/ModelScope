"""
Unit tests for core.evaluator.run_bash_evaluation and the bash-mode dispatch
in run_evaluation.

The environment is always a MagicMock so no shell processes run; we verify
command construction, cancel behaviour, sudo prefixing, step-format parsing,
and validation wiring.
"""
import time
import pytest
import requests
from unittest.mock import MagicMock, call, patch
from core.evaluator import execute_helper_prompt, run_bash_evaluation, run_evaluation
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

    def test_validation_set_passes_when_any_expected_check_matches(self):
        env = _env(stdout="ready: 204", exit_code=0)
        result = run_bash_evaluation(
            env,
            _cfg(validation_sets=[{
                "name": "multi",
                "steps": [{
                    "commands": [{
                        "type": "command",
                        "command": "check",
                        "checks": [
                            {"expected_output_type": "Exact String", "expected_output": "ready"},
                            {"expected_output_type": "Regex", "expected_output": r"ready: \d+"},
                        ],
                    }]
                }],
            }]),
            _log(),
        )

        assert result["validation_passed"] is True
        cmd_result = result["validation_sets_results"][0]["steps"][0]
        assert cmd_result["matched_check"]["expected_output_type"] == "Regex"
        assert len(cmd_result["checks"]) == 2

    def test_validation_set_with_empty_checks_ignores_output(self):
        env = _env(stdout="wrong", exit_code=0)
        result = run_bash_evaluation(
            env,
            _cfg(validation_sets=[{
                "name": "empty-checks",
                "steps": [{
                    "commands": [{
                        "type": "command",
                        "command": "check",
                        "checks": [],
                        "expected_output_type": "Exact String",
                        "expected_output": "stale legacy value",
                    }]
                }],
            }]),
            _log(),
        )

        assert result["validation_passed"] is True
        cmd_result = result["validation_sets_results"][0]["steps"][0]
        assert cmd_result["checks"] == []
        assert cmd_result["matched_check"]["expected_output_type"] == "Ignore"

    def test_validation_set_fails_when_no_expected_checks_match(self):
        env = _env(stdout="wrong", exit_code=0)
        result = run_bash_evaluation(
            env,
            _cfg(validation_sets=[{
                "name": "multi",
                "steps": [{
                    "commands": [{
                        "type": "command",
                        "command": "check",
                        "checks": [
                            {"expected_output_type": "Exact String", "expected_output": "ok"},
                            {"expected_output_type": "Regex", "expected_output": r"ready: \d+"},
                        ],
                    }]
                }],
            }]),
            _log(),
        )

        assert result["validation_passed"] is False
        assert "No expected output checks matched" in result["validation_sets_results"][0]["steps"][0]["reason"]


# ── LLM Judge ("LLM Helper") prompt step failures ─────────────────────────────

def _prompt_step(user_prompt="hi"):
    """A Startup/Completion step containing a single LLM-Judge prompt command."""
    return [{"delay_seconds": 0, "commands": [
        {"type": "prompt", "enabled": True, "system_prompt": "", "user_prompt": user_prompt}
    ]}]


class TestExecuteHelperPrompt:
    @staticmethod
    def _openai_response(text="ok"):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {"choices": [{"message": {"content": text}}]}
        return resp

    @patch("core.evaluator.requests.post")
    def test_openai_helper_normalizes_v1_url(self, mock_post):
        mock_post.return_value = self._openai_response("done")

        result = execute_helper_prompt(
            {"type": "prompt", "user_prompt": "hello", "preserve_context": False},
            {
                "llm_helper_backend": "OpenAI-Compatible",
                "llm_helper_openai_url": "http://judge.local:8000/v1",
                "llm_helper_model": "selected-vllm-model",
            },
            [],
            lambda *args: None,
        )

        assert result["exit_code"] == 0
        assert mock_post.call_args.args[0] == "http://judge.local:8000/v1/chat/completions"
        assert mock_post.call_args.kwargs["json"]["model"] == "selected-vllm-model"

    @patch("core.evaluator.requests.post")
    def test_openai_helper_errors_when_model_blank(self, mock_post):
        result = execute_helper_prompt(
            {"type": "prompt", "user_prompt": "hello", "preserve_context": False},
            {
                "llm_helper_backend": "OpenAI-Compatible",
                "llm_helper_openai_url": "http://judge.local:8000",
                "llm_helper_model": "",
                "llm_helper_openai_models": [{"name": "cached-vllm-model"}],
            },
            [],
            lambda *args: None,
        )

        assert result["exit_code"] == 1
        assert "No model selected for LLM Helper" in result["stderr"]
        mock_post.assert_not_called()

    @patch("core.evaluator.requests.post")
    def test_openai_helper_http_error_includes_response_body(self, mock_post):
        resp = MagicMock()
        resp.text = '{"error":"model is required"}'
        err = requests.exceptions.HTTPError("400 Client Error")
        err.response = resp
        resp.raise_for_status.side_effect = err
        mock_post.return_value = resp

        result = execute_helper_prompt(
            {"type": "prompt", "user_prompt": "hello", "preserve_context": False},
            {
                "llm_helper_backend": "OpenAI-Compatible",
                "llm_helper_openai_url": "http://judge.local:8000",
                "llm_helper_model": "bad-model",
            },
            [],
            lambda *args: None,
        )

        assert result["exit_code"] == 1
        assert "model is required" in result["stderr"]

    @patch("core.evaluator.requests.post")
    def test_openai_helper_chat_template_error_is_actionable(self, mock_post):
        resp = MagicMock()
        resp.text = (
            '{"error":{"message":"As of transformers v4.44, default chat template '
            'is no longer allowed, so you must provide a chat template if the tokenizer '
            'does not define one.","type":"BadRequestError","code":400}}'
        )
        err = requests.exceptions.HTTPError("400 Client Error")
        err.response = resp
        resp.raise_for_status.side_effect = err
        mock_post.return_value = resp

        result = execute_helper_prompt(
            {"type": "prompt", "user_prompt": "hello", "preserve_context": False},
            {
                "llm_helper_backend": "OpenAI-Compatible",
                "llm_helper_openai_url": "http://judge.local:8000",
                "llm_helper_model": "base-model-without-template",
            },
            [],
            lambda *args: None,
        )

        assert result["exit_code"] == 1
        assert "vLLM could not render chat messages" in result["stderr"]
        assert "--chat-template" in result["stderr"]
        assert "response body:" in result["stderr"]


class TestRunBashEvaluationPromptStepFailure:
    def test_unreachable_prompt_fails_the_run(self):
        """A prompt step that can't connect (no URL configured) must mark the
        whole run as failed, not just log an error and continue."""
        env = _env()
        result = run_bash_evaluation(
            env,
            _cfg(
                llm_helper_enabled=True, llm_helper_backend="OpenAI-Compatible",
                llm_helper_openai_url="",
                startup_commands=_prompt_step("hello"),
            ),
            _log(),
        )
        assert result["prompt_call_failed"] is True
        assert result["validation_passed"] is False

    def test_disabled_llm_judge_is_skipped_not_failed(self):
        env = _env()
        result = run_bash_evaluation(
            env,
            _cfg(llm_helper_enabled=False, startup_commands=_prompt_step("hello")),
            _log(),
        )
        assert result["prompt_call_failed"] is False
        assert result["validation_passed"] is None

    def test_completion_prompt_failure_overrides_passed_validation(self):
        """Startup/Completion prompt steps aren't validation commands, so
        _run_validation_sets never sees them — this must still flip an
        otherwise-passing validation result to failed."""
        env = _env(exit_code=0)
        result = run_bash_evaluation(
            env,
            _cfg(
                validation_commands=["check"], fail_patterns=[],
                llm_helper_enabled=True, llm_helper_backend="OpenAI-Compatible",
                llm_helper_openai_url="",
                completion_commands=_prompt_step("cleanup prompt"),
            ),
            _log(),
        )
        assert result["validation_passed"] is False


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
