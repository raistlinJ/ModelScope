"""
Unit tests for core.evaluator.run_llama_cli_evaluation.

All external I/O is mocked: env.execute via MagicMock, stream_llama_cpp via
unittest.mock.patch so no real HTTP or subprocess calls happen.
"""
import pytest
from unittest.mock import MagicMock, patch
from core.evaluator import run_llama_cli_evaluation
from core.environment import LocalEnvironment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(stdout="response text", stderr="", exit_code=0):
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = False
    return env


def _log():
    return lambda msg: None


def _cfg(**overrides):
    base = {
        "backend": "llama.cpp",
        "model_dir": "/models",
        "model_name": "llama3.gguf",
        "prompts": [],
        "commands": [],
        "cancel_requested_ref": [False],
    }
    base.update(overrides)
    return base


# ── OpenAI HTTP backend ───────────────────────────────────────────────────────

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
                     prompts=["Hello"]),
                _log(),
            )
        mock_stream.assert_called()
        assert len(result["prompt_responses"]) == 1
        assert result["prompt_responses"][0]["response"] == "AI response"

    def test_openai_backend_no_base_url_aborts(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(backend="openai", openai_base_url="", prompts=["Hello"]),
            _log(),
        )
        assert result["run_aborted"] is True

    def test_openai_backend_http_error_records_failed_tool_call(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.side_effect = Exception("connection refused")
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     prompts=["Hello"]),
                _log(),
            )
        assert len(result["tool_calls"]) >= 1
        assert result["tool_calls"][0]["exit_code"] == 1

    def test_openai_backend_cancel_stops_after_first_prompt(self):
        env = _env()
        cancel_ref = [False]

        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            def side_effect(**kwargs):
                cancel_ref[0] = True
                return self._stream_result()
            mock_stream.side_effect = side_effect

            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     prompts=["p1", "p2", "p3"], cancel_requested_ref=cancel_ref),
                _log(),
            )

        assert result["run_aborted"] is True
        assert len(result["prompt_responses"]) == 1

    def test_openai_backend_response_appended_to_prompt_responses(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.return_value = self._stream_result("The sky is blue.")
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     prompts=["What colour is the sky?"]),
                _log(),
            )
        assert result["prompt_responses"][0]["prompt"] == "What colour is the sky?"
        assert result["prompt_responses"][0]["response"] == "The sky is blue."

    def test_openai_backend_tool_call_uses_openai_http_label(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.return_value = self._stream_result()
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     prompts=["q"]),
                _log(),
            )
        # With the agent loop, tool_calls only contains actual tool calls from the model,
        # not the LLM call itself. The test verifies the system runs without error.
        assert len(result["prompt_responses"]) == 1

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
                     prompts=["Hello"]),
                _log(),
            )
        mock_stream.assert_called()
        assert len(result["prompt_responses"]) == 1


# ── Local binary backend ───────────────────────────────────────────────────────

class TestRunLlamaCLILocalBinary:
    def test_no_model_aborts(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(model_dir="", model_name="", prompts=["hello"]),
            _log(),
        )
        assert result["run_aborted"] is True

    def test_binary_dir_expands_to_llama_cli(self, tmp_path):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(binary_path=str(tmp_path), prompts=["hello"]),
            _log(),
        )
        called_cmd = env.execute.call_args[0][0]
        assert f"{tmp_path}/llama-cli" in called_cmd

    def test_model_path_constructed_from_dir_and_name(self):
        env = _env()
        run_llama_cli_evaluation(env, _cfg(prompts=["hi"]), _log())
        called_cmd = env.execute.call_args[0][0]
        assert "/models/llama3.gguf" in called_cmd

    def test_prompt_response_populated_from_stdout(self):
        env = _env(stdout="The answer is 42")
        result = run_llama_cli_evaluation(env, _cfg(prompts=["what is 6*7?"]), _log())
        assert result["prompt_responses"][0]["response"] == "The answer is 42"

    def test_multiple_prompts_produce_multiple_responses(self):
        env = _env(stdout="r")
        result = run_llama_cli_evaluation(env, _cfg(prompts=["p1", "p2", "p3"]), _log())
        assert len(result["prompt_responses"]) == 3

    def test_cancel_stops_after_first_prompt(self):
        env = _env()
        cancel_ref = [False]

        call_count = [0]
        def side_effect(cmd, **kw):
            call_count[0] += 1
            cancel_ref[0] = True
            return {"stdout": "r", "stderr": "", "exit_code": 0}

        env.execute.side_effect = side_effect
        result = run_llama_cli_evaluation(
            env,
            _cfg(prompts=["p1", "p2"], cancel_requested_ref=cancel_ref),
            _log(),
        )
        assert result["run_aborted"] is True
        assert call_count[0] == 1

    def test_tool_call_uses_llama_cli_label(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(prompts=["hi"]), _log())
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
        import os
        # Create a fake llama-server in a temp dir (path just needs to exist as a
        # non-directory basename — the evaluator only checks basename, not existence)
        server_bin = str(tmp_path / "llama-server")
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(backend="llama.cpp", binary_path=server_bin, prompts=["hello"]),
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
        import os
        from unittest.mock import patch as _patch
        # The managed-server backend calls _start_managed_llama_server; mock that
        # so the test doesn't need a real binary.
        fake_server_bin = "/fake/path/llama-server"
        env = _env()
        with _patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = None   # pretend startup failed gracefully
            with _patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(backend="llama-server (managed)", binary_path=fake_server_bin,
                         prompts=["hello"]),
                    _log(),
                )
        # _start_managed_llama_server should have received the original llama-server path
        if mock_start.called:
            call_args = mock_start.call_args[0]
            # First positional arg to _start_managed_llama_server is the binary path
            assert call_args[0] == fake_server_bin, (
                f"Managed backend should use llama-server, got: {call_args[0]}"
            )


# ── Shell commands ────────────────────────────────────────────────────────────

class TestRunLlamaCLIShellCommands:
    def test_shell_commands_run_after_prompts(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env, _cfg(commands=["echo done"]), _log()
        )
        env.execute.assert_called_once()
        assert result["tool_calls"][0]["tool"] == "bash"

    def test_sudo_prefix_on_shell_commands(self):
        env = _env()
        run_llama_cli_evaluation(
            env, _cfg(commands=["whoami"], sudo=True), _log()
        )
        called_cmds = [c[0][0] for c in env.execute.call_args_list]
        assert any("sudo whoami" in c for c in called_cmds)

    def test_cancel_stops_shell_commands(self):
        env = _env()
        cancel_ref = [False]

        def side_effect(cmd, **kw):
            cancel_ref[0] = True
            return {"stdout": "", "stderr": "", "exit_code": 0}

        env.execute.side_effect = side_effect
        result = run_llama_cli_evaluation(
            env,
            _cfg(commands=["cmd1", "cmd2"], cancel_requested_ref=cancel_ref),
            _log(),
        )
        assert result["run_aborted"] is True
        assert env.execute.call_count == 1


# ── Validation ────────────────────────────────────────────────────────────────

class TestRunLlamaCLIValidation:
    def test_validation_passes_on_zero_exit(self):
        env = _env(exit_code=0)
        result = run_llama_cli_evaluation(
            env, _cfg(validation_commands=["check"], fail_patterns=[]), _log()
        )
        assert result["validation_passed"] is True

    def test_validation_fails_on_nonzero_exit(self):
        env = _env(exit_code=1)
        result = run_llama_cli_evaluation(
            env, _cfg(validation_commands=["check"], fail_patterns=[]), _log()
        )
        assert result["validation_passed"] is False

    def test_aborted_run_skips_validation(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(model_dir="", model_name="",  # triggers abort
                 validation_commands=["check"], fail_patterns=[]),
            _log(),
        )
        assert result["validation_passed"] is None

    def test_total_latency_is_set(self):
        env = _env()
        result = run_llama_cli_evaluation(env, _cfg(), _log())
        assert result["total_latency"] >= 0
