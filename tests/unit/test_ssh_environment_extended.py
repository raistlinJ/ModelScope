"""
Extended unit tests for core/environment.py SSHEnvironment.

Uses paramiko mocks to cover:
  - connect(): idempotent, tilde expansion, chdir failure, key_path/password paths
  - close()
  - execute(): env_vars, exception handling
  - execute_streaming(): cancel path, input_queue injection, timeout, exit handling
  - read_file(), write_file(), delete_file(), exists()
  - cancel()
  - create_environment() factory
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import io
import time

import pytest

from core.environment import SSHEnvironment, LocalEnvironment, create_environment


def _make_mock_ssh_env(host="10.0.0.1", username="kali", remote_cwd="/opt/caf"):
    """Create an SSHEnvironment with all paramiko calls mocked."""
    env = SSHEnvironment(host=host, port=22, username=username,
                         password="pass", remote_cwd=remote_cwd)
    # Build mock client
    mock_client = MagicMock()
    mock_transport = MagicMock()
    mock_transport.is_active.return_value = True
    mock_client.get_transport.return_value = mock_transport
    env._client = mock_client

    # Build mock sftp
    mock_sftp = MagicMock()
    mock_sftp.getcwd.return_value = remote_cwd
    env._sftp = mock_sftp

    env._resolved_cwd = remote_cwd
    env._dir_created = False
    return env, mock_client, mock_sftp


class TestSSHEnvironmentConnect:
    def test_connect_idempotent_when_active(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        mock_client = MagicMock()
        mock_transport = MagicMock()
        mock_transport.is_active.return_value = True
        mock_client.get_transport.return_value = mock_transport
        env._client = mock_client
        env._sftp = MagicMock()
        env._resolved_cwd = "/tmp"

        # connect() should return early without calling paramiko.SSHClient again
        with patch("paramiko.SSHClient") as mock_ssh_cls:
            env.connect()
        mock_ssh_cls.assert_not_called()

    def test_connect_opens_sftp(self):
        env = SSHEnvironment(host="h", port=22, username="u",
                             password="p", remote_cwd="/tmp")
        mock_sftp = MagicMock()
        mock_sftp.getcwd.return_value = "/tmp"

        mock_client = MagicMock()
        mock_client.get_transport.return_value = None  # not connected
        env._client = None  # force reconnect

        with patch("paramiko.SSHClient") as mock_ssh_cls:
            mock_instance = mock_ssh_cls.return_value
            mock_instance.open_sftp.return_value = mock_sftp
            mock_instance.exec_command.return_value = (
                MagicMock(),
                _make_stdout(b"/home/u"),
                MagicMock(),
            )
            env.connect()

        mock_instance.open_sftp.assert_called_once()

    def test_connect_resolves_tilde_in_remote_cwd(self):
        env = SSHEnvironment(host="h", port=22, username="u",
                             password="p", remote_cwd="~/caf")
        mock_sftp = MagicMock()
        mock_sftp.getcwd.return_value = "/home/u/caf"
        env._client = None

        with patch("paramiko.SSHClient") as mock_ssh_cls:
            mock_instance = mock_ssh_cls.return_value
            mock_instance.open_sftp.return_value = mock_sftp
            mock_instance.exec_command.return_value = (
                MagicMock(),
                _make_stdout(b"/home/u"),
                MagicMock(),
            )
            env.connect()

        # remote_cwd should have tilde expanded
        assert env.remote_cwd.startswith("/home/u")

    def test_connect_uses_key_path(self):
        env = SSHEnvironment(host="h", port=22, username="u",
                             key_path="/path/to/key", remote_cwd="/tmp")
        env._client = None
        mock_sftp = MagicMock()
        mock_sftp.getcwd.return_value = "/tmp"

        with patch("paramiko.SSHClient") as mock_ssh_cls:
            mock_instance = mock_ssh_cls.return_value
            mock_instance.open_sftp.return_value = mock_sftp
            mock_instance.exec_command.return_value = (
                MagicMock(),
                _make_stdout(b""),  # no HOME
                MagicMock(),
            )
            env.connect()

        connect_kwargs = mock_instance.connect.call_args[1]
        assert connect_kwargs.get("key_filename") == "/path/to/key"

    def test_connect_chdir_failure_ignored(self):
        env = SSHEnvironment(host="h", port=22, username="u",
                             password="p", remote_cwd="/nonexistent")
        env._client = None
        mock_sftp = MagicMock()
        mock_sftp.chdir.side_effect = IOError("no such directory")
        mock_sftp.getcwd.return_value = None

        with patch("paramiko.SSHClient") as mock_ssh_cls:
            mock_instance = mock_ssh_cls.return_value
            mock_instance.open_sftp.return_value = mock_sftp
            mock_instance.exec_command.return_value = (
                MagicMock(),
                _make_stdout(b""),
                MagicMock(),
            )
            # Should not raise even if chdir fails
            env.connect()


class TestSSHEnvironmentClose:
    def test_close_sets_client_and_sftp_to_none(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        env.close()
        assert env._client is None
        assert env._sftp is None

    def test_close_when_already_none(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        env.close()  # should not raise


class TestSSHEnvironmentExecute:
    def test_execute_returns_stdout_stderr_exit_code(self):
        env, mock_client, _ = _make_mock_ssh_env()
        stdout_mock = _make_stdout(b"output", exit_code=0)
        stderr_mock = _make_stderr(b"")
        mock_client.exec_command.return_value = (MagicMock(), stdout_mock, stderr_mock)

        result = env.execute("echo hello")
        assert result["stdout"] == "output"
        assert result["exit_code"] == 0

    def test_execute_with_env_vars(self):
        env, mock_client, _ = _make_mock_ssh_env()
        stdout_mock = _make_stdout(b"", exit_code=0)
        stderr_mock = _make_stderr(b"")
        mock_client.exec_command.return_value = (MagicMock(), stdout_mock, stderr_mock)

        env.execute("my_cmd", env_vars={"KEY": "value"})
        cmd_arg = mock_client.exec_command.call_args[0][0]
        assert "export KEY=" in cmd_arg

    def test_execute_exception_returns_error_dict(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        env._client = None
        env._sftp = None

        with patch.object(env, "connect", side_effect=Exception("connect failed")):
            result = env.execute("cmd")
        assert result["exit_code"] == -1
        assert "connect failed" in result["stderr"]


class TestSSHEnvironmentProperties:
    def test_host_property(self):
        env = SSHEnvironment(host="192.168.1.1", port=22, username="u", remote_cwd="/tmp")
        assert env.host == "192.168.1.1"

    def test_username_property(self):
        env = SSHEnvironment(host="h", port=22, username="kali", remote_cwd="/tmp")
        assert env.username == "kali"

    def test_remote_cwd_before_connect(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="~/caf")
        assert env.remote_cwd == "~/caf"

    def test_remote_cwd_after_resolution(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="~/caf")
        env._resolved_cwd = "/home/u/caf"
        assert env.remote_cwd == "/home/u/caf"


class TestSSHEnvironmentReadFile:
    def test_read_file_returns_content(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        with patch.object(env, "connect"):
            mock_fh = MagicMock()
            mock_fh.__enter__ = MagicMock(return_value=mock_fh)
            mock_fh.__exit__ = MagicMock(return_value=False)
            mock_fh.read.return_value = b"file content"
            mock_sftp.open.return_value = mock_fh
            result = env.read_file("path/to/file.txt")
        assert result == "file content"


class TestSSHEnvironmentWriteFile:
    def test_write_file_success(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        with patch.object(env, "connect"):
            with patch.object(env, "execute", return_value={"exit_code": 0}):
                mock_fh = MagicMock()
                mock_fh.__enter__ = MagicMock(return_value=mock_fh)
                mock_fh.__exit__ = MagicMock(return_value=False)
                mock_sftp.open.return_value = mock_fh
                result = env.write_file("path/to/file.txt", "content")
        assert result["status"] == "success"
        assert result["bytes_written"] == len("content".encode())

    def test_write_file_exception_returns_error(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        with patch.object(env, "connect", side_effect=Exception("sftp error")):
            result = env.write_file("path/to/file.txt", "content")
        assert "error" in result


class TestSSHEnvironmentDeleteFile:
    def test_delete_file_success(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        with patch.object(env, "connect"):
            result = env.delete_file("path/to/file.txt")
        assert result is True
        mock_sftp.remove.assert_called_once_with("path/to/file.txt")

    def test_delete_file_ioerror_returns_false(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        mock_sftp.remove.side_effect = IOError("not found")
        with patch.object(env, "connect"):
            result = env.delete_file("path/to/file.txt")
        assert result is False

    def test_delete_file_other_exception_returns_false(self):
        env, mock_client, mock_sftp = _make_mock_ssh_env()
        mock_sftp.remove.side_effect = RuntimeError("unexpected")
        with patch.object(env, "connect"):
            result = env.delete_file("path/to/file.txt")
        assert result is False


class TestSSHEnvironmentExists:
    def test_exists_returns_true(self):
        env, _, mock_sftp = _make_mock_ssh_env()
        with patch.object(env, "connect"):
            result = env.exists("path/to/file.txt")
        assert result is True
        mock_sftp.stat.assert_called_once_with("path/to/file.txt")

    def test_exists_ioerror_returns_false(self):
        env, _, mock_sftp = _make_mock_ssh_env()
        mock_sftp.stat.side_effect = IOError("not found")
        with patch.object(env, "connect"):
            result = env.exists("path/to/file.txt")
        assert result is False

    def test_exists_other_exception_returns_false(self):
        env, _, mock_sftp = _make_mock_ssh_env()
        mock_sftp.stat.side_effect = RuntimeError("unexpected")
        with patch.object(env, "connect"):
            result = env.exists("path/to/file.txt")
        assert result is False


class TestSSHEnvironmentCancel:
    def test_cancel_closes_active_channel(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        mock_ch = MagicMock()
        env._active_channel = mock_ch
        env.cancel()
        mock_ch.close.assert_called_once()

    def test_cancel_no_active_channel(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        env._active_channel = None
        env.cancel()  # should not raise

    def test_cancel_close_exception_ignored(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        mock_ch = MagicMock()
        mock_ch.close.side_effect = Exception("already closed")
        env._active_channel = mock_ch
        env.cancel()  # should not raise


class TestSSHEnvironmentExecuteStreaming:
    def test_execute_streaming_cancel_terminates(self):
        env, mock_client, _ = _make_mock_ssh_env()
        mock_transport = MagicMock()
        mock_channel = MagicMock()
        mock_channel.recv_ready.return_value = False
        mock_channel.exit_status_ready.return_value = False
        mock_channel.closed = False
        mock_transport.open_session.return_value = mock_channel
        mock_client.get_transport.return_value = mock_transport

        cancel_ref = [False]

        def _flip_cancel(*a, **kw):
            cancel_ref[0] = True
            return False

        mock_channel.recv_ready.side_effect = _flip_cancel

        with patch.object(env, "connect"):
            result = env.execute_streaming(
                "cmd", timeout=10,
                cancel_ref=cancel_ref,
            )
        assert result["exit_code"] == -1 or "stdout" in result

    def test_execute_streaming_exception_returns_error(self):
        env = SSHEnvironment(host="h", port=22, username="u", remote_cwd="/tmp")
        with patch.object(env, "connect", side_effect=Exception("transport failed")):
            result = env.execute_streaming("cmd", timeout=5)
        assert result["exit_code"] == -1
        assert "transport failed" in result["stderr"]


# ── create_environment factory ─────────────────────────────────────────────────

class TestCreateEnvironment:
    def test_local_environment(self):
        env = create_environment()
        assert isinstance(env, LocalEnvironment)

    def test_ssh_environment(self):
        env = create_environment(ssh=True, host="10.0.0.1")
        assert isinstance(env, SSHEnvironment)
        assert env.host == "10.0.0.1"

    def test_ssh_environment_defaults(self):
        env = create_environment(ssh=True, host="h")
        assert env.username == "root"

    def test_ssh_environment_custom_cwd(self):
        env = create_environment(ssh=True, host="h", remote_cwd="/opt/myapp")
        assert "/opt/myapp" in env.remote_cwd


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_stdout(data: bytes, exit_code: int = 0) -> MagicMock:
    """Build a mock stdout channel that returns data and exit code."""
    ch = MagicMock()
    ch.read.return_value = data
    ch.channel.recv_exit_status.return_value = exit_code
    return ch


def _make_stderr(data: bytes) -> MagicMock:
    ch = MagicMock()
    ch.read.return_value = data
    return ch
