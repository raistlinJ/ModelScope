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
from core.environment import LocalEnvironment, SSHEnvironment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _env(stdout="response text", stderr="", exit_code=0):
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = False
    return env


def _ssh_env(stdout="response text", stderr="", exit_code=0, host="arlsouth4.utep.edu"):
    """spec=SSHEnvironment so isinstance(env, SSHEnvironment) checks in
    evaluator.py (used to gate remote managed-server launch) pass."""
    env = MagicMock(spec=SSHEnvironment)
    env.execute.return_value = {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}
    env.is_remote_caf = True
    env.host = host
    return env


def _log():
    return lambda msg, tag=None: None


def _prompt_set(*user_prompts, name="Prompt Set", preserve_context=True):
    """A validation set with one LLM-Judge prompt step per prompt given."""
    return {
        "name": name, "enabled": True,
        "steps": [
            {"delay_seconds": 0, "commands": [
                {
                    "type": "prompt",
                    "enabled": True,
                    "system_prompt": "",
                    "user_prompt": p,
                    "preserve_context": preserve_context,
                }
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
        "binary_path": "llama-cli",
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

    def test_openai_backend_preserves_context_between_prompt_steps(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.side_effect = [
                self._stream_result("first response"),
                self._stream_result("second response"),
            ]
            result = run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("first prompt", "second prompt")]),
                _log(),
            )

        assert result["validation_passed"] is True
        second_messages = mock_stream.call_args_list[1].kwargs["messages"]
        assert second_messages == [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": "first response"},
            {"role": "user", "content": "second prompt"},
        ]

    def test_openai_backend_does_not_preserve_context_when_disabled(self):
        env = _env()
        with patch("core.evaluator.stream_llama_cpp") as mock_stream:
            mock_stream.side_effect = [
                self._stream_result("first response"),
                self._stream_result("second response"),
            ]
            run_llama_cli_evaluation(
                env,
                _cfg(backend="openai", openai_base_url="http://localhost:1234",
                     validation_sets=[_prompt_set("first prompt", "second prompt", preserve_context=False)]),
                _log(),
            )

        second_messages = mock_stream.call_args_list[1].kwargs["messages"]
        assert second_messages == [{"role": "user", "content": "second prompt"}]

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

    def test_llama_cli_preserves_context_between_prompt_steps(self):
        env = _env()
        env.execute.side_effect = [
            {"stdout": "first response", "stderr": "", "exit_code": 0},
            {"stdout": "second response", "stderr": "", "exit_code": 0},
        ]

        result = run_llama_cli_evaluation(
            env, _cfg(validation_sets=[_prompt_set("first prompt", "second prompt")]), _log()
        )

        assert result["validation_passed"] is True
        second_cmd = env.execute.call_args_list[1].args[0]
        assert "Conversation so far:" in second_cmd
        assert "User: first prompt" in second_cmd
        assert "Assistant: first response" in second_cmd
        assert "Current user prompt:" in second_cmd
        assert "second prompt" in second_cmd

    def test_llama_cli_does_not_preserve_context_when_disabled(self):
        env = _env()
        env.execute.side_effect = [
            {"stdout": "first response", "stderr": "", "exit_code": 0},
            {"stdout": "second response", "stderr": "", "exit_code": 0},
        ]

        run_llama_cli_evaluation(
            env,
            _cfg(validation_sets=[_prompt_set("first prompt", "second prompt", preserve_context=False)]),
            _log(),
        )

        second_cmd = env.execute.call_args_list[1].args[0]
        assert "Conversation so far:" not in second_cmd
        assert "Assistant: first response" not in second_cmd
        assert "second prompt" in second_cmd

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


# ── Missing binary path must error, not silently fall back ────────────────────

class TestRunLlamaCLIMissingBinaryPath:
    """binary_path is required input — an empty value must produce a clear
    error rather than silently assuming llama-cli/llama-server is on PATH."""

    def test_blank_binary_path_errors_instead_of_defaulting_to_llama_cli(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env, _cfg(binary_path="", validation_sets=[_prompt_set("hi")]), _log()
        )
        assert result["prompt_responses"] == []
        env.execute.assert_not_called()

    def test_blank_binary_path_errors_for_managed_llama_server(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(binary_path="", backend="llama-server (managed)"),
            _log(),
        )
        assert result.get("run_aborted") is True
        assert "binary path" in result.get("error", "").lower()


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

    def test_llama_server_bot_managed_backend_starts_server_and_uses_http(self):
        env = _env()
        proc = MagicMock()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = proc
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {
                    "message": {"content": "server response"},
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }
                result = run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        tokens=4096,
                        server_host="0.0.0.0",
                        server_port=19191,
                        openai_base_url="",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        mock_start.assert_called_once()
        args = mock_start.call_args.args
        assert args[:5] == (
            "/opt/llama.cpp/llama-server",
            "/models/server.gguf",
            4096,
            19191,
            "0.0.0.0",
        )
        assert mock_stream.call_args.kwargs["base_url"] == "http://127.0.0.1:19191"
        assert result["run_bot_type"] == "llama_server_bot"
        assert result["prompt_responses"][0]["response"] == "server response"
        proc.terminate.assert_called_once()

    def test_llama_server_bot_forwards_advanced_options_to_launch(self):
        """Advanced Options (llama-cli parity) must reach the managed
        llama-server launch command, gated by their en_* toggles."""
        env = _env()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        en_temp=True, temperature=0.55,
                        en_gpu_layers=True, gpu_layers=20,
                        en_top_k=False, top_k=999,  # disabled — must NOT appear
                        flash_attn=True,
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        advanced_flags = mock_start.call_args.kwargs["advanced_flags"]
        assert "--temp 0.55" in advanced_flags
        assert "-ngl 20" in advanced_flags
        assert "-fa on" in advanced_flags
        assert "top-k" not in advanced_flags

    def test_llama_server_bot_advanced_flags_precede_custom_flags(self):
        """Advanced Options come first so custom_flags can still override them
        (matches ordering convention: gated flags, then user-supplied ones)."""
        env = _env()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        en_temp=True, temperature=0.55,
                        custom_flags="--temp 0.1",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        kwargs = mock_start.call_args.kwargs
        assert kwargs["advanced_flags"] == "--temp 0.55"
        assert kwargs["custom_flags"] == "--temp 0.1"

    def test_llama_server_bot_forwards_configured_ready_timeout(self):
        """The UI's "Server Startup Timeout" setting must actually reach
        _start_managed_llama_server — a slow-loading model should get the
        user's configured wait, not a hardcoded default."""
        env = _env()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        server_ready_timeout=600,
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        assert mock_start.call_args.kwargs["ready_timeout"] == 600.0

    def test_llama_server_bot_defaults_ready_timeout_to_five_minutes(self):
        env = _env()
        with patch("core.evaluator._start_managed_llama_server") as mock_start:
            mock_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        assert mock_start.call_args.kwargs["ready_timeout"] == 300.0


class TestManagedLlamaServerRemoteDispatch:
    """execution_target=ssh must launch the managed server on that remote
    host (core.remote_server) instead of locally — this is what actually
    makes Execution Target=SSH do something for the managed server itself,
    not just for shell commands."""

    def test_ssh_target_dispatches_to_remote_launcher(self):
        env = _ssh_env()
        remote_proc = MagicMock()
        remote_proc.local_port = 54321
        with patch("core.remote_server.start_remote_managed_llama_server") as mock_remote_start, \
             patch("core.evaluator._start_managed_llama_server") as mock_local_start:
            mock_remote_start.return_value = remote_proc
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        execution_target="ssh",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="~/.cache/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )

        mock_remote_start.assert_called_once()
        mock_local_start.assert_not_called()
        args = mock_remote_start.call_args.args
        assert args[0] is env
        assert args[1] == "/opt/llama.cpp/llama-server"
        # Remote model path must NOT be locally abspath'd/expanduser'd — it's
        # resolved on the remote host's own filesystem, not this one's.
        assert args[2] == "~/.cache/models/server.gguf"

    def test_ssh_target_uses_tunnelled_local_port_as_base_url(self):
        env = _ssh_env()
        remote_proc = MagicMock()
        remote_proc.local_port = 54321
        with patch("core.remote_server.start_remote_managed_llama_server", return_value=remote_proc):
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": "hi"}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        execution_target="ssh",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )
        assert mock_stream.call_args.kwargs["base_url"] == "http://127.0.0.1:54321"

    def test_ssh_target_teardown_calls_remote_handle_terminate(self):
        env = _ssh_env()
        remote_proc = MagicMock()
        remote_proc.local_port = 54321
        with patch("core.remote_server.start_remote_managed_llama_server", return_value=remote_proc):
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        execution_target="ssh",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )
        remote_proc.terminate.assert_called_once()

    def test_local_target_still_dispatches_to_local_launcher(self):
        """Regression: adding the ssh branch must not change local behavior."""
        env = _env()
        with patch("core.remote_server.start_remote_managed_llama_server") as mock_remote_start, \
             patch("core.evaluator._start_managed_llama_server") as mock_local_start:
            mock_local_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        execution_target="local",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    _log(),
                )
        mock_local_start.assert_called_once()
        mock_remote_start.assert_not_called()

    def test_pct_target_falls_back_to_local_with_warning(self):
        """PCT is out of scope for remote server launch (container network
        namespace can't be port-forwarded to like an SSH host can) — it must
        keep using the existing local-launch behavior, unchanged."""
        env = MagicMock()
        env.is_remote_caf = False
        with patch("core.remote_server.start_remote_managed_llama_server") as mock_remote_start, \
             patch("core.evaluator._start_managed_llama_server") as mock_local_start:
            mock_local_start.return_value = MagicMock()
            with patch("core.evaluator.stream_llama_cpp") as mock_stream:
                mock_stream.return_value = {"message": {"content": ""}, "usage": {}}
                logs = []
                run_llama_cli_evaluation(
                    env,
                    _cfg(
                        type="llama_server_bot",
                        backend="llama-server (managed)",
                        execution_target="pct",
                        binary_path="/opt/llama.cpp/llama-server",
                        model_dir="/models",
                        model_name="server.gguf",
                        validation_sets=[_prompt_set("hello")],
                    ),
                    lambda msg, tag=None: logs.append(msg),
                )
        mock_local_start.assert_called_once()
        mock_remote_start.assert_not_called()
        assert any("does not support launching inside a PCT" in m for m in logs)


class TestManagedLlamaServerAdvancedFlags:
    """core.evaluator._managed_llama_server_advanced_flags in isolation."""

    def _base_cfg(self, **overrides):
        cfg = {
            "en_temp": False, "temperature": 0.8,
            "en_gpu_layers": False, "gpu_layers": 99,
            "en_threads": False, "threads": 4,
            "flash_attn": False,
            "en_top_k": False, "top_k": 40,
            "en_top_p": False, "top_p": 0.9,
            "en_min_p": False, "min_p": 0.1,
            "en_repeat_penalty": False, "repeat_penalty": 1.1,
            "en_freq_penalty": False, "freq_penalty": 0.0,
            "en_predict": False, "predict": 512,
            "en_seed": False, "seed": -1,
            "en_rope_freq_base": False, "rope_freq_base": 10000.0,
            "en_rope_freq_scale": False, "rope_freq_scale": 1.0,
        }
        cfg.update(overrides)
        return cfg

    def test_all_disabled_yields_empty_string(self):
        from core.evaluator import _managed_llama_server_advanced_flags
        assert _managed_llama_server_advanced_flags(self._base_cfg()) == ""

    def test_predict_never_included(self):
        """-n/--predict is llama-cli-specific (bounds a single CLI call) and
        must never leak into the managed llama-server launch flags."""
        from core.evaluator import _managed_llama_server_advanced_flags
        flags = _managed_llama_server_advanced_flags(
            self._base_cfg(en_predict=True, predict=256)
        )
        assert flags == ""

    def test_all_enabled_includes_every_flag_except_predict(self):
        from core.evaluator import _managed_llama_server_advanced_flags
        cfg = self._base_cfg(**{k: True for k in self._base_cfg() if k.startswith("en_")})
        flags = _managed_llama_server_advanced_flags(cfg)
        for expected in (
            "--temp 0.8", "-ngl 99", "-t 4", "--top-k 40", "--top-p 0.9",
            "--min-p 0.1", "--repeat-penalty 1.1", "--freq-penalty 0.0",
            "--seed -1", "--rope-freq-base 10000.0", "--rope-freq-scale 1.0",
        ):
            assert expected in flags, f"missing {expected!r} in {flags!r}"
        assert "-n " not in flags

    def test_flash_attn_independent_of_en_flags(self):
        from core.evaluator import _managed_llama_server_advanced_flags
        assert "-fa on" in _managed_llama_server_advanced_flags(self._base_cfg(flash_attn=True))
        assert "-fa" not in _managed_llama_server_advanced_flags(self._base_cfg(flash_attn=False))


class TestStartManagedLlamaServerReadiness:
    """core.evaluator._start_managed_llama_server's readiness poll.

    subprocess.Popen and requests.get are mocked so no real process or HTTP
    call happens; time.sleep is mocked so a long ready_timeout doesn't
    actually block the test suite for that long.
    """

    def _make_proc(self, exit_code=None):
        proc = MagicMock()
        proc.poll.return_value = exit_code
        proc.stderr.read.return_value = b"model failed to load: bad gguf"
        return proc

    def test_default_timeout_is_five_minutes(self):
        import inspect
        from core.evaluator import _start_managed_llama_server
        sig = inspect.signature(_start_managed_llama_server)
        assert sig.parameters["ready_timeout"].default == 300.0

    @patch("core.evaluator.time.sleep")
    @patch("core.evaluator.requests.get")
    @patch("core.evaluator.subprocess.Popen")
    def test_ready_on_first_health_check_returns_immediately(self, mock_popen, mock_get, mock_sleep):
        from core.evaluator import _start_managed_llama_server
        mock_popen.return_value = self._make_proc()
        mock_get.return_value = MagicMock(status_code=200)

        proc = _start_managed_llama_server(
            "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1", lambda m: None,
        )

        assert proc is mock_popen.return_value
        mock_sleep.assert_not_called()

    @patch("core.evaluator.time.sleep")
    @patch("core.evaluator.requests.get", side_effect=__import__("requests").exceptions.ConnectionError)
    @patch("core.evaluator.subprocess.Popen")
    def test_crashed_process_fails_fast_without_waiting_out_the_timeout(
        self, mock_popen, mock_get, mock_sleep
    ):
        """A process that exits immediately (bad model path, OOM, etc.) must
        raise right away with its stderr, not silently retry for the full
        5-minute ready_timeout."""
        from core.evaluator import _start_managed_llama_server
        mock_popen.return_value = self._make_proc(exit_code=1)

        with pytest.raises(RuntimeError, match="exited immediately.*bad gguf"):
            _start_managed_llama_server(
                "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1", lambda m: None,
            )
        # Must not have looped through the retry/sleep path at all.
        mock_sleep.assert_not_called()

    @patch("core.evaluator.time.time")
    @patch("core.evaluator.time.sleep")
    @patch("core.evaluator.requests.get", side_effect=__import__("requests").exceptions.ConnectionError)
    @patch("core.evaluator.subprocess.Popen")
    def test_never_ready_raises_after_custom_timeout(
        self, mock_popen, mock_get, mock_sleep, mock_time
    ):
        from core.evaluator import _start_managed_llama_server
        mock_popen.return_value = self._make_proc(exit_code=None)  # never crashes
        # Simulate the clock advancing past a short custom timeout.
        mock_time.side_effect = [0, 1, 2, 3]

        with pytest.raises(RuntimeError, match=r"did not become ready after 2s"):
            _start_managed_llama_server(
                "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1", lambda m: None,
                ready_timeout=2.0,
            )

    @patch("core.evaluator.time.sleep")
    @patch("core.evaluator.requests.get")
    @patch("core.evaluator.subprocess.Popen")
    def test_custom_ready_timeout_is_honored_not_hardcoded_30s(self, mock_popen, mock_get, mock_sleep):
        """Regression: the old implementation hardcoded 30 one-second
        attempts: bumping ready_timeout must actually change the poll
        window, not just the error message."""
        from core.evaluator import _start_managed_llama_server
        mock_popen.return_value = self._make_proc()
        mock_get.return_value = MagicMock(status_code=200)

        _start_managed_llama_server(
            "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1", lambda m: None,
            ready_timeout=600.0,
        )
        # Ready on the first check — proves the call accepted a >300s value
        # without raising a signature/type error, and didn't need 30 retries.
        mock_get.assert_called_once()


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


# ── LLM Judge ("LLM Helper") prompt step failures ─────────────────────────────

def _helper_prompt_step(user_prompt="hi"):
    """A Startup/Completion step containing a single LLM-Judge prompt command."""
    return [{"delay_seconds": 0, "commands": [
        {"type": "prompt", "enabled": True, "system_prompt": "", "user_prompt": user_prompt}
    ]}]


class TestRunLlamaCLIPromptStepFailure:
    def test_unreachable_prompt_fails_the_run(self):
        """A prompt step that can't connect (no URL configured) must mark the
        whole run as failed, not just log an error and continue."""
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(
                llm_helper_enabled=True, llm_helper_backend="OpenAI-Compatible",
                llm_helper_openai_url="",
                startup_commands=_helper_prompt_step("hello"),
            ),
            _log(),
        )
        assert result["prompt_call_failed"] is True
        assert result["validation_passed"] is False

    def test_disabled_llm_judge_is_skipped_not_failed(self):
        env = _env()
        result = run_llama_cli_evaluation(
            env,
            _cfg(llm_helper_enabled=False, startup_commands=_helper_prompt_step("hello")),
            _log(),
        )
        assert result["prompt_call_failed"] is False
        assert result["validation_passed"] is None

    def test_completion_prompt_failure_overrides_passed_validation(self):
        """Startup/Completion prompt steps aren't validation commands, so
        _run_validation_sets never sees them — this must still flip an
        otherwise-passing validation result to failed."""
        env = _env(exit_code=0)
        result = run_llama_cli_evaluation(
            env,
            _cfg(
                validation_sets=[_command_set("check")],
                llm_helper_enabled=True, llm_helper_backend="OpenAI-Compatible",
                llm_helper_openai_url="",
                completion_commands=_helper_prompt_step("cleanup prompt"),
            ),
            _log(),
        )
        assert result["validation_passed"] is False


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
