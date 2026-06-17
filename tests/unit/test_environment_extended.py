"""
Extended tests for core.environment — LocalEnvironment and SSHEnvironment
methods not yet covered by the existing test_environment.py and
test_ssh_environment.py files.
"""
import os
import pytest
from unittest.mock import MagicMock, patch, call
from core.environment import LocalEnvironment, SSHEnvironment


# ── LocalEnvironment ──────────────────────────────────────────────────────────

class TestLocalEnvironmentExecute:
    def test_executes_and_returns_stdout(self):
        env = LocalEnvironment()
        result = env.execute("echo hello")
        assert result["stdout"].strip() == "hello"
        assert result["exit_code"] == 0

    def test_nonzero_exit_code(self):
        env = LocalEnvironment()
        result = env.execute("exit 1", timeout=5)
        assert result["exit_code"] == 1

    def test_stderr_captured(self):
        env = LocalEnvironment()
        result = env.execute("ls /nonexistent_path_xyz 2>&1")
        # either stderr or stdout will have the error; exit != 0
        assert result["exit_code"] != 0

    def test_timeout_returns_error(self):
        env = LocalEnvironment()
        result = env.execute("sleep 10", timeout=1)
        assert result["exit_code"] == -1
        assert "Timed out" in result["stderr"]


class TestLocalEnvironmentReadFile:
    def test_reads_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content here")
        env = LocalEnvironment()
        assert env.read_file(str(f)) == "content here"

    def test_missing_file_raises(self):
        env = LocalEnvironment()
        with pytest.raises(Exception):
            env.read_file("/nonexistent_path_xyz/file.txt")


class TestLocalEnvironmentWriteFile:
    def test_writes_content(self, tmp_path):
        env = LocalEnvironment()
        path = str(tmp_path / "out.txt")
        result = env.write_file(path, "hello world")
        assert result["status"] == "success"
        assert (tmp_path / "out.txt").read_text() == "hello world"

    def test_bytes_written_matches_utf8_length(self, tmp_path):
        env = LocalEnvironment()
        content = "hello"
        result = env.write_file(str(tmp_path / "f.txt"), content)
        assert result["bytes_written"] == len(content.encode())

    def test_creates_nested_directories(self, tmp_path):
        env = LocalEnvironment()
        path = str(tmp_path / "a" / "b" / "c.txt")
        result = env.write_file(path, "nested")
        assert result["status"] == "success"
        assert os.path.isfile(path)

    def test_write_failure_returns_error(self, tmp_path):
        env = LocalEnvironment()
        # Try to write to a path where parent is a file (not a dir)
        blocker = tmp_path / "blocker"
        blocker.write_text("I am a file")
        path = str(blocker / "child.txt")  # parent is a file → can't mkdir
        result = env.write_file(path, "will fail")
        assert "error" in result


class TestLocalEnvironmentDeleteFile:
    def test_deletes_existing_file(self, tmp_path):
        f = tmp_path / "delete_me.txt"
        f.write_text("bye")
        env = LocalEnvironment()
        assert env.delete_file(str(f)) is True
        assert not f.exists()

    def test_returns_false_for_nonexistent(self, tmp_path):
        env = LocalEnvironment()
        assert env.delete_file(str(tmp_path / "nonexistent.txt")) is False

    def test_delete_exception_returns_false(self, tmp_path):
        env = LocalEnvironment()
        # Passing a directory (not a file) → unlink should fail
        sub = tmp_path / "subdir"
        sub.mkdir()
        result = env.delete_file(str(sub))
        assert result is False


class TestLocalEnvironmentExists:
    def test_existing_file(self, tmp_path):
        f = tmp_path / "exists.txt"
        f.write_text("yes")
        assert LocalEnvironment().exists(str(f)) is True

    def test_nonexistent_path(self, tmp_path):
        assert LocalEnvironment().exists(str(tmp_path / "nope.txt")) is False

    def test_existing_directory(self, tmp_path):
        assert LocalEnvironment().exists(str(tmp_path)) is True


# ── SSHEnvironment ────────────────────────────────────────────────────────────

class _FakeChannel:
    def recv_exit_status(self):
        return 0


class _FakeStream:
    def __init__(self, data: bytes = b""):
        self._data = data
        self.channel = _FakeChannel()

    def read(self):
        return self._data


class _FakeClient:
    def __init__(self):
        self.last_command = None

    def exec_command(self, command, timeout=None):
        self.last_command = command
        return (_FakeStream(), _FakeStream(b"result"), _FakeStream(b""))


class _FakeSFTP:
    def __init__(self):
        self._files: dict = {}
        self._removed: list = []
        self._stat_raises = False

    def open(self, path, mode="r"):
        import io
        if mode == "r":
            data = self._files.get(path, b"")
            return io.BytesIO(data)
        elif mode == "wb":
            buf = io.BytesIO()

            class WritableBuffer:
                def __init__(self, inner_buf, inner_path, inner_parent):
                    self._buf = inner_buf
                    self._path = inner_path
                    self._parent = inner_parent

                def write(self, data):
                    self._buf.write(data)
                    self._parent._files[self._path] = data

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return WritableBuffer(buf, path, self)
        raise ValueError(f"Unknown mode: {mode}")

    def remove(self, path):
        self._removed.append(path)

    def stat(self, path):
        if self._stat_raises:
            raise IOError("no such file")
        if path not in self._files:
            raise IOError("no such file")
        return MagicMock()

    def chdir(self, path):
        pass

    def getcwd(self):
        return "/opt/caf"

    def close(self):
        pass


def _make_ssh_env(remote_cwd="/opt/caf"):
    env = SSHEnvironment(host="10.0.0.1", username="kali", remote_cwd=remote_cwd)
    env._client = _FakeClient()
    env._sftp = _FakeSFTP()
    env.connect = lambda: None
    return env


class TestSshEnvironmentReadFile:
    def test_reads_existing_file(self):
        env = _make_ssh_env()
        env._sftp._files["/opt/caf/transcript.md"] = b"# hello"
        result = env.read_file("/opt/caf/transcript.md")
        assert result == "# hello"


class TestSshEnvironmentDeleteFile:
    def test_deletes_file(self):
        env = _make_ssh_env()
        env._sftp._files["/opt/caf/runs/r1/metadata.json"] = b"{}"
        result = env.delete_file("/opt/caf/runs/r1/metadata.json")
        assert result is True
        assert "/opt/caf/runs/r1/metadata.json" in env._sftp._removed

    def test_ioerror_returns_false(self):
        env = _make_ssh_env()
        # Make remove raise IOError to simulate missing file
        env._sftp.remove = MagicMock(side_effect=IOError("no such file"))
        result = env.delete_file("/nonexistent")
        assert result is False


class TestSshEnvironmentExists:
    def test_existing_path_returns_true(self):
        env = _make_ssh_env()
        env._sftp._files["/opt/caf/file.txt"] = b""
        assert env.exists("/opt/caf/file.txt") is True

    def test_missing_path_returns_false(self):
        env = _make_ssh_env()
        env._sftp._stat_raises = True
        assert env.exists("/opt/caf/nonexistent") is False


class TestSshEnvironmentClose:
    def test_close_sets_to_none(self):
        env = SSHEnvironment(host="x", remote_cwd="/tmp")
        mock_sftp = MagicMock()
        mock_client = MagicMock()
        env._sftp = mock_sftp
        env._client = mock_client
        env.close()
        assert env._sftp is None
        assert env._client is None
        mock_sftp.close.assert_called_once()
        mock_client.close.assert_called_once()

    def test_close_idempotent_on_none(self):
        env = SSHEnvironment(host="x", remote_cwd="/tmp")
        # Should not raise when already None
        env.close()
        env.close()
