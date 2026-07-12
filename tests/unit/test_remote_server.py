"""
Unit tests for core.remote_server — launching, health-checking, and tearing
down a managed llama-server process on a remote host over SSH.

No real SSH connection or remote process is used: SSHEnvironment is a
MagicMock (its execute()/get_client() are stubbed) and requests.get is
patched so health checks are simulated. SSHPortForward's local TCP listener
IS real (a harmless loopback socket in a daemon thread — nothing ever
connects through it in these tests since requests.get is mocked), and every
test closes it.
"""
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core.remote_server import (
    RemoteManagedServer,
    SSHPortForward,
    _quote_remote_path,
    start_remote_managed_mcp_server,
    start_remote_managed_llama_server,
)


class TestQuoteRemotePath:
    def test_plain_path_needs_no_quoting(self):
        assert _quote_remote_path("/opt/models/m.gguf") == "/opt/models/m.gguf"

    def test_path_with_space_is_quoted(self):
        assert _quote_remote_path("/opt/my models/m.gguf") == "'/opt/my models/m.gguf'"

    def test_tilde_prefixed_path_lets_shell_expand_home(self):
        result = _quote_remote_path("~/models/m.gguf")
        assert result.startswith('"$HOME/"')
        assert "models/m.gguf" in result
        assert not result.startswith("~")  # a literal ~ would NOT expand once quoted


def _fake_env(execute_results, host="example.com"):
    """A MagicMock standing in for SSHEnvironment, returning results from
    execute_results in order for successive .execute() calls."""
    env = MagicMock()
    env.host = host
    env.execute.side_effect = execute_results
    client = MagicMock()
    client.get_transport.return_value = MagicMock()
    env.get_client.return_value = client
    return env


class TestStartRemoteManagedLlamaServer:
    def test_successful_launch_returns_handle_with_tunnel(self):
        env = _fake_env([
            {"stdout": "4242", "stderr": "", "exit_code": 0},   # launch, returns PID
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},  # poll before health check
        ])
        with patch("core.remote_server.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_remote_managed_llama_server(
                env, "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1",
                lambda m: None,
            )
        try:
            assert isinstance(handle, RemoteManagedServer)
            assert handle.local_port > 0
            launch_cmd = env.execute.call_args_list[0].args[0]
            assert "nohup" in launch_cmd
            assert "llama-server" in launch_cmd
            assert "-m /models/m.gguf" in launch_cmd
            assert "--port 8080" in launch_cmd
        finally:
            handle.kill()


class TestStartRemoteManagedMCPServer:
    def test_stages_starts_and_tunnels_builtin_mcp(self):
        env = _fake_env([
            {"stdout": "/home/student", "stderr": "", "exit_code": 0},  # home
            {"stdout": "", "stderr": "", "exit_code": 0},               # mkdir
            {"stdout": "", "stderr": "", "exit_code": 0},               # npm ci
            {"stdout": "39191", "stderr": "", "exit_code": 0},          # free port
            {"stdout": "4242", "stderr": "", "exit_code": 0},           # launch
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},          # poll
        ])
        env.write_file.return_value = {}
        with patch("core.remote_server.requests.post") as mock_post:
            mock_post.return_value = MagicMock(ok=True)
            handle = start_remote_managed_mcp_server(env, lambda m: None)
        try:
            assert isinstance(handle, RemoteManagedServer)
            assert handle.local_port > 0
            # Runtime files plus the selected MCP manifest are staged remotely.
            assert env.write_file.call_count == 6
            commands = [call.args[0] for call in env.execute.call_args_list]
            assert any("npm ci --omit=dev" in command for command in commands)
            assert any("MCP_PORT=39191" in command for command in commands)
            assert any("node /home/student/.modelscope/mcp-server/index.js" in command for command in commands)
        finally:
            handle.kill()

    def test_passes_selected_tool_groups_to_remote_mcp(self):
        env = _fake_env([
            {"stdout": "/home/student", "stderr": "", "exit_code": 0},
            {"stdout": "", "stderr": "", "exit_code": 0},
            {"stdout": "", "stderr": "", "exit_code": 0},
            {"stdout": "39191", "stderr": "", "exit_code": 0},
            {"stdout": "4242", "stderr": "", "exit_code": 0},
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},
        ])
        env.write_file.return_value = {}
        with patch("core.remote_server.requests.post", return_value=MagicMock(ok=True)):
            handle = start_remote_managed_mcp_server(env, lambda m: None, tool_names=["read_file"])
        try:
            launch_cmd = env.execute.call_args_list[4].args[0]
            assert "MCP_TOOL_NAMES=read_file" in launch_cmd
        finally:
            handle.kill()

    def test_launch_failure_raises_with_stderr(self):
        env = _fake_env([
            {"stdout": "", "stderr": "command not found", "exit_code": 127},
        ])
        with pytest.raises(RuntimeError, match="command not found"):
            start_remote_managed_llama_server(
                env, "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1",
                lambda m: None,
            )

    def test_process_exits_immediately_fails_fast_with_log_tail(self):
        env = _fake_env([
            {"stdout": "4242", "stderr": "", "exit_code": 0},              # launch
            {"stdout": "DEAD", "stderr": "", "exit_code": 0},              # poll -> dead
            {"stdout": "failed to load model", "stderr": "", "exit_code": 0},  # log tail
        ])
        with pytest.raises(RuntimeError, match="exited immediately.*failed to load model"):
            start_remote_managed_llama_server(
                env, "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1",
                lambda m: None,
            )

    @patch("core.remote_server.time.sleep")
    @patch("core.remote_server.time.time")
    def test_never_ready_raises_after_custom_timeout(self, mock_time, mock_sleep):
        env = _fake_env([
            {"stdout": "4242", "stderr": "", "exit_code": 0},   # launch
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},  # poll: still alive
            {"stdout": "", "stderr": "", "exit_code": 0},       # terminate on timeout
        ])
        mock_time.side_effect = [0, 1, 2, 3]
        with patch("core.remote_server.requests.get",
                   side_effect=__import__("requests").exceptions.ConnectionError):
            with pytest.raises(RuntimeError, match=r"did not become ready after 2s"):
                start_remote_managed_llama_server(
                    env, "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1",
                    lambda m: None, ready_timeout=2.0,
                )
        # On a readiness timeout the remote process must be terminated, otherwise
        # an orphaned llama-server keeps holding the port/GPU on the remote host.
        assert any(
            call.args and call.args[0].startswith("kill 4242")
            for call in env.execute.call_args_list
        ), "expected the remote PID to be killed on readiness timeout"

    def test_advanced_and_custom_flags_included_in_launch_command(self):
        env = _fake_env([
            {"stdout": "4242", "stderr": "", "exit_code": 0},
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},
        ])
        with patch("core.remote_server.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_remote_managed_llama_server(
                env, "llama-server", "/models/m.gguf", 4096, 8080, "127.0.0.1",
                lambda m: None,
                custom_flags="--jinja",
                advanced_flags="-ngl 20",
            )
        try:
            launch_cmd = env.execute.call_args_list[0].args[0]
            assert "-ngl 20" in launch_cmd
            assert "--jinja" in launch_cmd
        finally:
            handle.kill()

    def test_tilde_model_path_lets_remote_shell_expand_home(self):
        env = _fake_env([
            {"stdout": "4242", "stderr": "", "exit_code": 0},
            {"stdout": "ALIVE", "stderr": "", "exit_code": 0},
        ])
        with patch("core.remote_server.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_remote_managed_llama_server(
                env, "llama-server", "~/models/m.gguf", 4096, 8080, "127.0.0.1",
                lambda m: None,
            )
        try:
            launch_cmd = env.execute.call_args_list[0].args[0]
            assert "$HOME" in launch_cmd
        finally:
            handle.kill()


class TestRemoteManagedServerLifecycle:
    def test_poll_returns_none_while_alive(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "ALIVE", "stderr": "", "exit_code": 0}
        handle = RemoteManagedServer(env, "4242", "/tmp/log", MagicMock())
        assert handle.poll() is None

    def test_poll_returns_non_none_once_dead_and_caches_it(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "DEAD", "stderr": "", "exit_code": 0}
        handle = RemoteManagedServer(env, "4242", "/tmp/log", MagicMock())
        assert handle.poll() is not None
        # Cached — no further SSH round-trip needed once known dead.
        env.execute.reset_mock()
        assert handle.poll() is not None
        env.execute.assert_not_called()

    def test_terminate_sends_sigterm_and_leaves_tunnel_open(self):
        env = MagicMock()
        tunnel = MagicMock()
        handle = RemoteManagedServer(env, "4242", "/tmp/log", tunnel)
        handle.terminate()
        cmd = env.execute.call_args.args[0]
        assert cmd.startswith("kill 4242")
        tunnel.close.assert_not_called()

    def test_kill_sends_sigkill_and_closes_tunnel(self):
        env = MagicMock()
        tunnel = MagicMock()
        handle = RemoteManagedServer(env, "4242", "/tmp/log", tunnel)
        handle.kill()
        cmd = env.execute.call_args.args[0]
        assert cmd.startswith("kill -9 4242")
        tunnel.close.assert_called_once()

    def test_wait_returns_once_dead_and_closes_tunnel(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "DEAD", "stderr": "", "exit_code": 0}
        tunnel = MagicMock()
        handle = RemoteManagedServer(env, "4242", "/tmp/log", tunnel)
        handle.wait(timeout=5)
        tunnel.close.assert_called_once()

    @patch("core.remote_server.time.sleep")
    @patch("core.remote_server.time.time")
    def test_wait_raises_timeout_expired_if_still_alive(self, mock_time, mock_sleep):
        env = MagicMock()
        env.execute.return_value = {"stdout": "ALIVE", "stderr": "", "exit_code": 0}
        tunnel = MagicMock()
        handle = RemoteManagedServer(env, "4242", "/tmp/log", tunnel)
        mock_time.side_effect = [0, 1, 2]
        with pytest.raises(subprocess.TimeoutExpired):
            handle.wait(timeout=1)

    def test_read_log_tail_returns_stdout(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "some error output", "stderr": "", "exit_code": 0}
        handle = RemoteManagedServer(env, "4242", "/tmp/modelscope_llama_server_8080.log", MagicMock())
        assert handle.read_log_tail() == "some error output"
        cmd = env.execute.call_args.args[0]
        assert "/tmp/modelscope_llama_server_8080.log" in cmd


class TestSSHPortForward:
    def test_binds_to_ephemeral_local_port_and_closes_cleanly(self):
        client = MagicMock()
        client.get_transport.return_value = MagicMock()
        forward = SSHPortForward(client, "127.0.0.1", 8080)
        try:
            assert forward.local_port > 0
        finally:
            forward.close()

    def test_distinct_forwards_get_distinct_ports(self):
        client = MagicMock()
        client.get_transport.return_value = MagicMock()
        a = SSHPortForward(client, "127.0.0.1", 8080)
        b = SSHPortForward(client, "127.0.0.1", 18081)
        try:
            assert a.local_port != b.local_port
        finally:
            a.close()
            b.close()
