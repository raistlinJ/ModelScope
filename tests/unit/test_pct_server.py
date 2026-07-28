"""Unit tests for core.pct_server — llama-server inside a Proxmox LXC.

No container is touched: PCTEnvironment is a MagicMock whose execute() returns
scripted results, and requests.get is patched so health checks are simulated.
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.pct_server import (
    CONTAINER_BIND_HOST,
    PCTManagedServer,
    container_ipv4,
    resolve_container_binary,
    start_pct_managed_llama_server,
)


def _result(stdout="", stderr="", exit_code=0):
    return {"stdout": stdout, "stderr": stderr, "exit_code": exit_code}


def _fake_env(*results):
    env = MagicMock()
    env.execute.side_effect = list(results)
    return env


# ── Address discovery ─────────────────────────────────────────────────────────

class TestContainerIpv4:
    def test_takes_the_first_routable_address_from_hostname(self):
        assert container_ipv4(_fake_env(_result("10.0.0.8 172.17.0.1 \n"))) == "10.0.0.8"

    def test_accepts_the_iproute2_fallback_format(self):
        assert container_ipv4(_fake_env(_result("192.168.1.50/24\n"))) == "192.168.1.50"

    def test_ignores_loopback_and_non_addresses(self):
        with pytest.raises(RuntimeError, match="IP address"):
            container_ipv4(_fake_env(_result("127.0.0.1 fe80::1\n")))

    def test_reports_a_container_with_no_network(self):
        with pytest.raises(RuntimeError, match="IP address"):
            container_ipv4(_fake_env(_result("", "hostname: not found", 127)))


# ── Binary resolution ─────────────────────────────────────────────────────────

class TestResolveContainerBinary:
    def test_a_directory_in_the_container_gains_the_server_name(self):
        env = _fake_env(_result(exit_code=0))  # test -d succeeds
        assert resolve_container_binary(env, "/opt/llama.cpp/build/bin") == "/opt/llama.cpp/build/bin/llama-server"

    def test_a_file_path_is_left_alone(self):
        env = _fake_env(_result(exit_code=1))
        assert resolve_container_binary(env, "/usr/local/bin/llama-server") == "/usr/local/bin/llama-server"

    def test_a_llama_cli_path_is_corrected(self):
        env = _fake_env(_result(exit_code=1))
        assert resolve_container_binary(env, "/usr/local/bin/llama-cli") == "/usr/local/bin/llama-server"

    def test_an_empty_path_falls_back_to_the_container_s_PATH(self):
        env = _fake_env()
        # Leaving Binary Path blank is documented as "use llama-server from
        # PATH", so there is nothing to probe for.
        assert resolve_container_binary(env, "  ") == "llama-server"
        env.execute.assert_not_called()


# ── Launch ────────────────────────────────────────────────────────────────────

def _launch_env(pid="2451", *, systemd_ok=True, launch_stderr="", alive=True, log_tail=""):
    """Container double that answers by command rather than by call order.

    The launch sequence (address → binary probe → port cleanup → systemd-run →
    MainPID poll, with a setsid fallback for non-systemd containers) has been
    reordered and extended before, so scripting a positional list of results
    makes these tests fail for reasons that have nothing to do with intent.
    """
    def run(cmd, timeout=None):
        if "hostname" in cmd:
            return _result("10.0.0.8")
        if cmd.startswith("test -d"):
            return _result(exit_code=1)
        if cmd.startswith("pkill"):
            return _result()
        if cmd.startswith("systemd-run"):
            return _result("", launch_stderr, 0 if systemd_ok else 1)
        if "systemctl show -p MainPID" in cmd:
            return _result(f"MainPID={pid}")
        if cmd.startswith("setsid nohup"):
            return _result(pid, launch_stderr, 0 if pid else 1)
        if cmd.startswith("tail -c") or cmd.startswith("cat "):
            return _result(log_tail)
        if cmd.startswith("kill -0"):
            return _result("ALIVE" if alive else "DEAD")
        return _result()

    env = MagicMock()
    env.execute.side_effect = run
    return env


def _launch_commands(env):
    return [call.args[0] for call in env.execute.call_args_list]


class TestStartPctManagedLlamaServer:
    def test_launch_binds_all_interfaces_and_targets_the_container_address(self):
        env = _launch_env()
        logs: list[str] = []
        with patch("core.pct_server.requests.get") as mock_get, \
             patch("core.pct_server.time.sleep"):
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_pct_managed_llama_server(
                env, "101", "/usr/bin/llama-server", "/models/m.gguf", 4096, 8080, logs.append,
            )

        assert isinstance(handle, PCTManagedServer)
        assert handle.base_url == "http://10.0.0.8:8080"
        assert handle.vmid == "101"
        launch_cmd = next(c for c in _launch_commands(env) if c.startswith("systemd-run"))
        assert "-m /models/m.gguf" in launch_cmd
        assert f"--host {CONTAINER_BIND_HOST}" in launch_cmd
        assert "--port 8080" in launch_cmd
        mock_get.assert_called_with("http://10.0.0.8:8080/health", timeout=1.0)
        assert any("LXC 101" in line for line in logs)

    def test_the_port_is_cleared_of_orphans_before_launching(self):
        """A cancelled run can leave a server holding the port and the model."""
        env = _launch_env()
        with patch("core.pct_server.requests.get") as mock_get, \
             patch("core.pct_server.time.sleep"):
            mock_get.return_value = MagicMock(status_code=200)
            start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
            )

        commands = _launch_commands(env)
        cleanup = next(i for i, c in enumerate(commands) if c.startswith("pkill"))
        launch = next(i for i, c in enumerate(commands) if c.startswith("systemd-run"))
        assert cleanup < launch

    def test_a_non_systemd_container_falls_back_to_setsid(self):
        env = _launch_env(systemd_ok=False)
        with patch("core.pct_server.requests.get") as mock_get, \
             patch("core.pct_server.time.sleep"):
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
            )

        assert handle.base_url == "http://10.0.0.8:8080"
        assert any(c.startswith("setsid nohup") for c in _launch_commands(env))

    def test_metrics_flag_is_forced_and_a_user_copy_is_dropped(self):
        env = _launch_env(pid="7")
        logs: list[str] = []
        with patch("core.pct_server.requests.get") as mock_get, \
             patch("core.pct_server.time.sleep"):
            mock_get.return_value = MagicMock(status_code=200)
            start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/m.gguf", 4096, 8080, logs.append,
                custom_flags="--metrics --parallel 2",
                advanced_flags="--metrics --temp 0.4",
            )

        launch_cmd = next(c for c in _launch_commands(env) if c.startswith("systemd-run"))
        assert "--temp 0.4" in launch_cmd
        assert "--parallel 2" in launch_cmd
        assert any("Ignoring custom --metrics" in line for line in logs)

    def test_a_failed_launch_reports_the_container_error(self):
        """Neither systemd-run nor the fallback produced a PID."""
        env = _launch_env(pid="", systemd_ok=False, launch_stderr="pct: permission denied")
        with patch("core.pct_server.time.sleep"):
            with pytest.raises(RuntimeError, match="permission denied"):
                start_pct_managed_llama_server(
                    env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
                )

    def test_a_server_that_dies_immediately_surfaces_its_log_tail(self):
        env = _launch_env(alive=False, log_tail="error loading model")
        with patch("core.pct_server.time.sleep"):
            with pytest.raises(RuntimeError, match="error loading model"):
                start_pct_managed_llama_server(
                    env, "101", "llama-server", "/models/missing.gguf", 4096, 8080, lambda _m: None,
                )

    def test_a_readiness_timeout_stops_the_container_process(self):
        env = _launch_env()
        refused = requests.exceptions.ConnectionError("refused")
        with patch("core.pct_server.requests.get", side_effect=refused), \
             patch("core.pct_server.time.sleep"):
            with pytest.raises(RuntimeError, match="did not become ready"):
                start_pct_managed_llama_server(
                    env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
                    ready_timeout=0.2,
                )
        assert any(c.startswith("kill 2451") for c in _launch_commands(env))


# ── Handle lifecycle ──────────────────────────────────────────────────────────

class TestPctManagedServerHandle:
    def test_poll_reports_alive_then_caches_death(self):
        env = _fake_env(_result("ALIVE"), _result("DEAD"))
        handle = PCTManagedServer(env, "101", "2451", "/tmp/log", "http://10.0.0.8:8080")

        assert handle.poll() is None
        assert handle.poll() == 1
        assert handle.poll() == 1  # cached; no third round-trip
        assert env.execute.call_count == 2

    def test_terminate_survives_an_unreachable_container(self):
        env = MagicMock()
        env.execute.side_effect = RuntimeError("container is gone")

        handle = PCTManagedServer(env, "101", "2451", "/tmp/log", "http://10.0.0.8:8080")
        handle.terminate()
        handle.kill()

        assert handle.poll() == 1
