"""
Unit tests for core.evaluator.run_llama_cli_evaluation.

Prompt execution is driven by the Startup / Validation / Completion step model:
a "prompt"-type command inside a validation set's steps is routed through
_exec_llama_prompt (the main LLM) when config["type"] == "llama_cli_bot"; a
"command"-type step runs via env.execute. All external I/O is mocked:
env.execute via MagicMock, stream_llama_cpp via unittest.mock.patch so no real
HTTP or subprocess calls happen.
"""
import pytest
from unittest.mock import MagicMock, call, patch
from core.evaluator import run_llama_cli_evaluation
from core.environment import LocalEnvironment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(stdout="response text", stderr="", exit_code=0):
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = False
    return env


def _log():
    return lambda msg, tag=None: None


def _prompt_set(*user_prompts, name="Prompt Set"):
    """A validation set with one LLM-Judge prompt step per prompt given."""
    return {
        "name": name, "enabled": True,
        "steps": [
            {"delay_seconds": 0, "commands": [
                {"type": "prompt", "enabled": True, "system_prompt": "", "user_prompt": p}
            ]}
            for p in user_prompts
        ],
    }


def _command_set(cmd="check", **cmd_overrides):
    """A validation set with one plain-command step."""
    cmd_obj = {"type": "command", "enabled": True, "command": cmd, "expected_output_type": "Ignore"}
    cmd_obj.update(cmd_overrides)
    return {"name": "Validation", "enabled": True, "steps": [{"delay_seconds": 0, "commands": [cmd_obj]}]}


def _cfg(**overrides):
    base = {
        "type": "llama_cli_bot",
        "backend": "llama.cpp",
        "model_dir": "/models",
        "model_name": "llama3.gguf",
        "cancel_requested_ref": [False],
    }
    base.update(overrides)
    return base


# ── OpenAI HTTP backend (main LLM, via validation-set prompt step) ────────────

class TestRunLlamaCLIOpenAIBackend:
    def _stream_result(self, content="AI response"):
        return {
            "message": {"content": content},
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    def test_openai_backend_calls_stream_llama_cpp(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.return_value = self._stream_result()
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("Hello")]),
                _log(),
            )
        mock_stream.assert_called()
        assert len(result["prompt_responses"]) == 1
        assert result["prompt_responses"][0]["response"] == "AI response"

    def test_openai_backend_no_base_url_fails_validation(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(backend="openai", openai_base_url="",
                 validation_sets=[_prompt_set("Hello")]),
            _log(),
        )
        assert result["validation_passed"] is False

    def test_openai_backend_http_error_fails_validation(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.side_effect = Exception("connection refused")
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("Hello")]),
                _log(),
            )
        assert result["validation_passed"] is False

    def test_openai_backend_cancel_stops_after_first_prompt(self):
        env = _env()
        cancel_ref = [False]

        def side_effect(**kwargs):
            cancel_ref[0] = True
            return self._stream_result()

        with patch("core.evaluator.stream_llama_cpp", side_effect=side_effect):
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("p1", "p2", "p3")],
                     cancel_requested_ref=cancel_ref),
                _log(),
            )

        assert len(result["prompt_responses"]) == 1

    def test_openai_backend_response_appended_to_prompt_responses(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.return_value = self._stream_result("The sky is blue.")
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("What colour is the sky?")]),
                _log(),
            )
        assert result["prompt_responses"][0]["response"] == "The sky is blue."

    def test_openai_backend_dispatch_with_real_selectbox_value(self):
        """Production path: selectbox stores 'OpenAI-compatible HTTP', not bare 'openai'.
        Old code: == 'openai' would FAIL here.  New code: .startswith('openai') passes.
        """
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.return_value = self._stream_result()
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="OpenAI-compatible HTTP",
                     openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("Hello")]),
                _log(),
            )
        mock_stream.assert_called()
        assert len(result["prompt_responses"]) == 1


# ── Local binary backend ───────────────────────────────────────────────────────

class TestRunLlamaCLILocalBinary:
    def test_binary_dir_expands_to_llama_cli(self, tmp_path):
        env = _env()
        run_llama_cli_evaluation(
            env,
            _cfg(binary_path=str(tmp_path), validation_sets=[_prompt_set("hello")]),
            _log(),
        )
        called_cmd = env.execute.call_args[0][0]
        assert f"{tmp_path}/llama-cli" in called_cmd

    def test_model_path_constructed_from_dir_and_name(self):
        env = _env()
        run_llama_cli_evaluation(env, _cfg(validation_sets=[_prompt_set("hi")]), _log())
        called_cmd = env.execute.call_args[0][0]
        assert "/models/llama3.gguf" in called_cmd

    def test_prompt_response_populated_from_stdout(self):
        env = _env(stdout="The answer is 42")
        result = run_llama_cli_evaluation(
            env, _cfg(validation_sets=[_prompt_set("what is 6*7?")]), _log()
        )
        assert result["prompt_responses"][0]["response"] == "The answer is 42"

    def test_multiple_prompts_produce_multiple_responses(self):
        env = _env(stdout="r")
        result = run_llama_cli_evaluation(
            env, _cfg(validation_sets=[_prompt_set("p1", "p2", "p3")]), _log()
        )
        assert len(result["prompt_responses"]) == 3

    def test_cancel_stops_after_first_prompt(self):
        env = _env()
        cancel_ref = [False]

        def side_effect(cmd, **kw):
            cancel_ref[0] = True
            return {"stdout": "r", "stderr": "", "exit_code": 0}

        env.execute.side_effect = side_effect
        result = run_llama_cli_evaluation(
            env,
            _cfg(validation_sets=[_prompt_set("p1", "p2")], cancel_requested_ref=cancel_ref),
            _log(),
        )
        assert len(result["prompt_responses"]) == 1

    def test_tool_call_uses_llama_cli_label(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(validation_sets=[_prompt_set("hi")]), _log())
        assert result["tool_calls"][0]["tool"] == "llama-cli"

    def test_run_bot_type_is_llama_cli_bot(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(), _log())
        assert result["run_bot_type"] == "llama_cli_bot"


# ── Binary path auto-correction ───────────────────────────────────────────────

class TestRunLlamaCLIBinaryAutoCorrect:
    """llama-server → llama-cli auto-correction must fire for the llama.cpp CLI
    backend only.  The managed-server backend legitimately uses llama-server."""

    def test_server_binary_swapped_for_cli_in_llamacpp_backend(self, tmp_path):
        """binary_path pointing at llama-server is corrected to llama-cli."""
        server_bin = str(tmp_path / "llama-server")
        env = _env()
        run_llama_cli_evaluation(
            env,
            _cfg(backend="llama.cpp", binary_path=server_bin,
                 validation_sets=[_prompt_set("hello")]),
            _log(),
        )
        called_cmd = env.execute.call_args[0][0]
        expected_cli = str(tmp_path / "llama-cli")
        assert expected_cli in called_cmd, (
            f"Expected llama-cli in command, got: {called_cmd}"
        )
        assert "llama-server" not in called_cmd, (
            f"llama-server should have been removed from command, got: {called_cmd}"
        )

    def test_server_binary_not_swapped_for_managed_backend(self):
        """llama-server path is left untouched for the managed-server backend,
        which legitimately needs it.  (The managed path calls _start_managed_llama_server.)
        """
        fake_server_bin = "/fake/path/llama-server"
        env = _env()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = None   # pretend startup failed gracefully
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(backend="llama-server (managed)", binary_path=fake_server_bin,
                         validation_sets=[_prompt_set("hello")]),
                    _log(),
                )
        # _start_managed_llama_server should have received the original llama-server path
        if mock_start.called:
            call_args = mock_start.call_args[0]
            # First positional arg to _start_managed_llama_server is the binary path
            assert call_args[0] == fake_server_bin, (
                f"Managed backend should use llama-server, got: {call_args[0]}"
            )


# ── Startup/Completion shell command steps ────────────────────────────────────

class TestRunLlamaCLIShellCommands:
    def test_command_step_runs_and_tagged_as_bash(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(startup_commands=[
                {"delay_seconds": 0, "commands": [
                    {"type": "command", "command": "echo done", "enabled": True}
                ]},
            ]),
            _log(),
        )
        env.execute.assert_called_once()
        assert result["tool_calls"][0]["tool"] == "bash"

    def test_sudo_prefix_on_shell_commands(self):
        env = _env()
        run_llama_cli_evaluation(
            env,
            _cfg(sudo=True, startup_commands=[
                {"delay_seconds": 0, "commands": [
                    {"type": "command", "command": "whoami", "enabled": True}
                ]},
            ]),
            _log(),
        )
        # First call is the sudo-auth preflight check, second is the actual command.
        assert env.execute.call_args_list == [
            call("sudo -k; sudo -n -v", timeout=10),
            call("sudo whoami", timeout=120),
        ]

    def test_cancel_stops_shell_commands(self):
        env = _env()
        cancel_ref = [False]

        def side_effect(cmd, **kw):
            cancel_ref[0] = True
            return {"stdout": "", "stderr": "", "exit_code": 0}

        env.execute.side_effect = side_effect
        result = run_llama_cli_evaluation(
            env,
            _cfg(startup_commands=[
                {"delay_seconds": 0, "commands": [{"type": "command", "command": "cmd1", "enabled": True}]},
                {"delay_seconds": 0, "commands": [{"type": "command", "command": "cmd2", "enabled": True}]},
            ], cancel_requested_ref=cancel_ref),
            _log(),
        )
        assert result["run_aborted"] is True
        assert env.execute.call_count == 1


# ── Validation ────────────────────────────────────────────────────────────────

class TestRunLlamaCLIValidation:
    def test_validation_passes_on_zero_exit(self):
        env = _env(exit_code=0)
        result = run_llama_cli_evaluation(
            env, _cfg(validation_sets=[_command_set("check")]), _log()
        )
        assert result["validation_passed"] is True

    def test_validation_fails_on_nonzero_exit(self):
        env = _env(exit_code=1)
        result = run_llama_cli_evaluation(
            env, _cfg(validation_sets=[_command_set("check")]), _log()
        )
        assert result["validation_passed"] is False

    def test_no_validation_sets_leaves_validation_passed_none(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(), _log())
        assert result["validation_passed"] is None

    def test_total_latency_is_set(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(), _log())
        assert result["total_latency"] >= 0
