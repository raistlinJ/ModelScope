from unittest.mock import MagicMock, patch

from core.environment import SSHEnvironment
from core.evaluator import run_llama_cli_evaluation


def _prompt_set(text: str) -> list[dict]:
    return [{
        "name": "Prompt", "enabled": True,
        "steps": [{"delay_seconds": 0, "commands": [{
            "type": "prompt", "enabled": True, "system_prompt": "", "user_prompt": text,
        }]}],
    }]


def test_ssh_llama_bot_uses_tunnelled_remote_mcp_and_stops_it():
    env = MagicMock(spec=SSHEnvironment)
    env.host = "remote.example"
    remote_mcp = MagicMock()
    remote_mcp.local_port = 43123
    config = {
        "type": "llama_cli_bot",
        "backend": "openai",
        "execution_target": "ssh",
        "openai_base_url": "http://model.example/v1",
        "model_name": "model.gguf",
        "mcp_enabled": True,
        "mcp_servers": [{"name": "built-in", "enabled": True}],
        "validation_sets": _prompt_set("hello"),
        "cancel_requested_ref": [False],
    }
    with patch("core.remote_server.start_remote_managed_mcp_server", return_value=remote_mcp) as start_mcp, \
         patch("core.evaluator.probe_mcp_server", return_value=True) as probe, \
         patch("core.evaluator.stream_llama_cpp", return_value={"message": {"content": "done"}, "usage": {}}):
        result = run_llama_cli_evaluation(env, config, lambda *args: None)

    start_mcp.assert_called_once()
    probe.assert_called_once_with("http://127.0.0.1:43123")
    remote_mcp.terminate.assert_called_once()
    assert result["validation_passed"] is True


def test_ssh_llama_bot_forwards_selected_builtin_tool_groups():
    env = MagicMock(spec=SSHEnvironment)
    env.host = "remote.example"
    remote_mcp = MagicMock()
    remote_mcp.local_port = 43123
    config = {
        "type": "llama_cli_bot",
        "backend": "openai",
        "execution_target": "ssh",
        "openai_base_url": "http://model.example/v1",
        "model_name": "model.gguf",
        "mcp_enabled": True,
        "mcp_servers": [
            {"name": "Filesystem · read_file", "server": "filesystem", "tool_name": "read_file", "enabled": True},
            {"name": "Terminal · terminal_execute", "server": "terminal", "tool_name": "terminal_execute", "enabled": False},
        ],
        "validation_sets": _prompt_set("hello"),
        "cancel_requested_ref": [False],
    }
    with patch("core.remote_server.start_remote_managed_mcp_server", return_value=remote_mcp) as start_mcp, \
         patch("core.evaluator.probe_mcp_server", return_value=True), \
         patch("core.evaluator.stream_llama_cpp", return_value={"message": {"content": "done"}, "usage": {}}):
        run_llama_cli_evaluation(env, config, lambda *args: None)

    assert start_mcp.call_args.kwargs["tool_names"] == ["read_file"]
