"""
Unit tests for MCP manager: probe_mcp_server and the evaluator MCP probe
integration (mcp_running is set correctly based on probe result).
"""
import pytest
from unittest.mock import MagicMock, patch
from core.mcp_manager import probe_mcp_server
from core.evaluator import run_llama_cli_evaluation
from core.environment import LocalEnvironment


# ── probe_mcp_server ──────────────────────────────────────────────────────────

class TestProbeMcpServer:
    def test_returns_true_when_rpc_session_succeeds(self):
        with patch("core.mcp_manager._mcp_rpc_session", return_value=(True, {})):
            assert probe_mcp_server("http://127.0.0.1:9191") is True

    def test_returns_false_when_rpc_session_fails(self):
        with patch("core.mcp_manager._mcp_rpc_session", return_value=(False, {})):
            assert probe_mcp_server("http://127.0.0.1:9191") is False

    def test_uses_default_base_url(self):
        """probe_mcp_server must forward the base_url to _mcp_rpc_session."""
        with patch("core.mcp_manager._mcp_rpc_session", return_value=(True, {})) as mock_session:
            probe_mcp_server()
        mock_session.assert_called_once()
        call_url = mock_session.call_args[0][0]
        # Default URL should point to localhost
        assert "127.0.0.1" in call_url or "localhost" in call_url

    def test_custom_url_forwarded(self):
        custom_url = "http://192.168.1.100:9191"
        with patch("core.mcp_manager._mcp_rpc_session", return_value=(False, {})) as mock_session:
            probe_mcp_server(custom_url)
        call_url = mock_session.call_args[0][0]
        assert call_url == custom_url


# ── evaluator MCP probe ───────────────────────────────────────────────────────

def _env():
    env = MagicMock(spec=LocalEnvironment)
    env.execute.return_value = {"stdout": "ok", "stderr": "", "exit_code": 0}
    env.is_remote_caf = False
    return env


def _log():
    return lambda msg: None


def _cfg_with_mcp(**overrides):
    """Config with a minimal MCP server list so tools are loaded."""
    base = {
        "backend": "llama.cpp",
        "model_dir": "/models",
        "model_name": "llama3.gguf",
        "prompts": ["hello"],
        "commands": [],
        "cancel_requested_ref": [False],
        "mcp_servers": [{"name": "custom", "command": "node", "args": [], "enabled": True}],
        "mcp_server_url": "http://127.0.0.1:9191",
    }
    base.update(overrides)
    return base


class TestEvaluatorMcpProbe:
    def test_mcp_running_set_true_when_probe_succeeds(self):
        """When probe_mcp_server returns True, tool calls are forwarded to MCP."""
        log_msgs = []
        env = _env()

        with patch("core.evaluator._load_tool_schemas", return_value=[
            {"type": "function", "function": {"name": "file_creator",
                                               "description": "create",
                                               "parameters": {}}}
        ]):
            with patch("core.evaluator.probe_mcp_server", return_value=True):
                with patch("core.evaluator.call_mcp_tool") as mock_call:
                    mock_call.return_value = {"status": "success"}
                    run_llama_cli_evaluation(
                        env, _cfg_with_mcp(),
                        lambda m: log_msgs.append(m),
                    )

        # Log must confirm MCP is active
        assert any("[MCP] Broker detected" in m for m in log_msgs), (
            f"Expected broker-detected log, got: {log_msgs}"
        )

    def test_mcp_running_false_when_probe_fails(self):
        """When probe_mcp_server returns False, a warning is logged."""
        log_msgs = []
        env = _env()

        with patch("core.evaluator._load_tool_schemas", return_value=[
            {"type": "function", "function": {"name": "file_creator",
                                               "description": "create",
                                               "parameters": {}}}
        ]):
            with patch("core.evaluator.probe_mcp_server", return_value=False):
                run_llama_cli_evaluation(
                    env, _cfg_with_mcp(),
                    lambda m: log_msgs.append(m),
                )

        assert any("WARN" in m and "MCP broker" in m for m in log_msgs), (
            f"Expected MCP warning log, got: {log_msgs}"
        )

    def test_no_probe_when_no_tools_loaded(self):
        """probe_mcp_server is never called when mcp_servers is empty."""
        env = _env()
        with patch("core.evaluator.probe_mcp_server") as mock_probe:
            run_llama_cli_evaluation(
                env,
                {
                    "backend": "llama.cpp",
                    "model_dir": "/models",
                    "model_name": "m.gguf",
                    "prompts": ["hi"],
                    "commands": [],
                    "cancel_requested_ref": [False],
                    # No mcp_servers key → tools = [] → probe should not run
                },
                lambda m: None,
            )
        mock_probe.assert_not_called()


class TestCliSystemPromptSize:
    """CLI system prompt includes arg names but not verbose parameter descriptions."""

    def test_sys_prompt_includes_arg_names_but_not_descriptions(self):
        """Arg names must appear so the model can construct valid calls.
        Verbose per-arg descriptions must be omitted to keep the prompt compact."""
        captured_cmds = []

        env = MagicMock(spec=LocalEnvironment)
        env.execute.side_effect = lambda cmd, **kw: (
            captured_cmds.append(cmd) or {"stdout": "r", "stderr": "", "exit_code": 0}
        )
        env.is_remote_caf = False

        tool_with_params = {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write content to a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"description": "Absolute path to destination"},
                        "content": {"description": "File content to write"},
                    },
                },
            },
        }

        with patch("core.evaluator._load_tool_schemas", return_value=[tool_with_params]):
            with patch("core.evaluator.probe_mcp_server", return_value=False):
                run_llama_cli_evaluation(
                    env,
                    _cfg_with_mcp(model_dir="/models", model_name="m.gguf"),
                    lambda m: None,
                )

        assert captured_cmds, "env.execute was never called"
        cmd = captured_cmds[0]
        # The tool name must appear
        assert "write_file" in cmd
        # Argument names must appear so the model can fill them in
        assert "path" in cmd
        assert "content" in cmd
        # But verbose per-argument descriptions must NOT be embedded
        assert "Absolute path to destination" not in cmd
        assert "File content to write" not in cmd
