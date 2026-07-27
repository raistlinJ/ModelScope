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

    def test_an_empty_path_is_not_probed(self):
        env = _fake_env()
        assert resolve_container_binary(env, "  ") == ""
        env.execute.assert_not_called()


# ── Launch ────────────────────────────────────────────────────────────────────

class TestStartPctManagedLlamaServer:
    def test_launch_binds_all_interfaces_and_targets_the_container_address(self):
        env = _fake_env(
            _result("10.0.0.8"),      # container_ipv4
            _result(exit_code=1),     # test -d on the binary
            _result("2451"),          # nohup launch → PID
            _result("ALIVE"),         # poll before the health check
        )
        logs: list[str] = []
        with patch("core.pct_server.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            handle = start_pct_managed_llama_server(
                env, "101", "/usr/bin/llama-server", "/models/m.gguf", 4096, 8080, logs.append,
            )

        assert isinstance(handle, PCTManagedServer)
        assert handle.base_url == "http://10.0.0.8:8080"
        assert handle.vmid == "101"
        launch_cmd = env.execute.call_args_list[2].args[0]
        assert launch_cmd.startswith("nohup ")
        assert "-m /models/m.gguf" in launch_cmd
        assert f"--host {CONTAINER_BIND_HOST}" in launch_cmd
        assert "--port 8080" in launch_cmd
        assert "echo $!" in launch_cmd
        mock_get.assert_called_with("http://10.0.0.8:8080/health", timeout=1.0)
        assert any("LXC 101" in line for line in logs)

    def test_metrics_flag_is_forced_and_a_user_copy_is_dropped(self):
        env = _fake_env(
            _result("10.0.0.8"), _result(exit_code=1), _result("7"), _result("ALIVE"),
        )
        logs: list[str] = []
        with patch("core.pct_server.requests.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/m.gguf", 4096, 8080, logs.append,
                custom_flags="--metrics --parallel 2",
                advanced_flags="--metrics --temp 0.4",
            )

        launch_cmd = env.execute.call_args_list[2].args[0]
        assert "--temp 0.4" in launch_cmd
        assert "--parallel 2" in launch_cmd
        assert any("Ignoring custom --metrics" in line for line in logs)

    def test_a_failed_launch_reports_the_container_error(self):
        env = _fake_env(
            _result("10.0.0.8"),
            _result(exit_code=1),
            _result("", "pct: permission denied", 1),
        )
        with pytest.raises(RuntimeError, match="permission denied"):
            start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
            )

    def test_a_server_that_dies_immediately_surfaces_its_log_tail(self):
        env = _fake_env(
            _result("10.0.0.8"),
            _result(exit_code=1),
            _result("2451"),
            _result("DEAD"),                      # poll
            _result("error loading model"),       # log tail
        )
        with pytest.raises(RuntimeError, match="error loading model"):
            start_pct_managed_llama_server(
                env, "101", "llama-server", "/models/missing.gguf", 4096, 8080, lambda _m: None,
            )

    def test_a_readiness_timeout_stops_the_container_process(self):
        env = MagicMock()
        env.execute.side_effect = lambda cmd, timeout=15: (
            _result("10.0.0.8") if "hostname" in cmd
            else _result(exit_code=1) if cmd.startswith("test -d")
            else _result("2451") if cmd.startswith("nohup")
            else _result("ALIVE")
        )
        refused = requests.exceptions.ConnectionError("refused")
        with patch("core.pct_server.requests.get", side_effect=refused), \
             patch("core.pct_server.time.sleep"):
            with pytest.raises(RuntimeError, match="did not become ready"):
                start_pct_managed_llama_server(
                    env, "101", "llama-server", "/models/m.gguf", 4096, 8080, lambda _m: None,
                    ready_timeout=0.2,
                )
        assert any(
            call.args[0].startswith("kill 2451")
            for call in env.execute.call_args_list
        )


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
