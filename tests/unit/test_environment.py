"""
Unit tests for core.environment.LocalEnvironment using the real filesystem
(tmp directory — no mocks for I/O operations).
"""
import os
import pathlib
import pytest
from core.environment import LocalEnvironment


@pytest.fixture
def env():
    return LocalEnvironment()


@pytest.fixture
def tmp_file(tmp_path):
    """Returns a Path inside tmp_path that does NOT exist yet."""
    return tmp_path / "testfile.txt"


# ── execute ────────────────────────────────────────────────────────────────────

class TestLocalExecute:
    def test_captures_stdout(self, env):
        result = env.execute("echo hello")
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    def test_captures_stderr(self, env):
        result = env.execute("ls /this_path_does_not_exist_xyz")
        assert result["exit_code"] != 0
        assert result["stderr"] or result["stdout"]  # ls writes to stderr

    def test_nonzero_exit_code(self, env):
        result = env.execute("false")
        assert result["exit_code"] != 0

    def test_timeout_returns_minus_one(self, env):
        result = env.execute("sleep 10", timeout=1)
        assert result["exit_code"] == -1
        assert "Timed out" in result["stderr"]

    def test_multiline_output(self, env):
        result = env.execute("printf '1\\n2\\n3\\n'")
        assert result["exit_code"] == 0
        assert result["stdout"].strip() == "1\n2\n3"

    def test_generic_exception_returns_error_dict(self, env, monkeypatch):
        import subprocess
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: (_ for _ in ()).throw(OSError("no such file or directory")))
        result = env.execute("bad_cmd")
        assert result["exit_code"] == -1
        assert "no such file or directory" in result["stderr"]
        assert result["stdout"] == ""


# ── write_file / read_file ─────────────────────────────────────────────────────

class TestLocalWriteRead:
    def test_write_creates_file(self, env, tmp_file):
        result = env.write_file(str(tmp_file), "hello world")
        assert result.get("status") == "success"
        assert tmp_file.exists()

    def test_write_returns_bytes_written(self, env, tmp_file):
        content = "hello"
        result = env.write_file(str(tmp_file), content)
        assert result["bytes_written"] == len(content.encode())

    def test_read_returns_written_content(self, env, tmp_file):
        env.write_file(str(tmp_file), "hello world")
        assert env.read_file(str(tmp_file)) == "hello world"

    def test_write_creates_parent_dirs(self, env, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "file.txt"
        result = env.write_file(str(deep), "nested")
        assert result.get("status") == "success"
        assert deep.exists()

    def test_write_unicode_content(self, env, tmp_file):
        content = "héllo wörld — 日本語"
        env.write_file(str(tmp_file), content)
        assert env.read_file(str(tmp_file)) == content

    def test_write_error_returns_error_dict(self, env):
        result = env.write_file("/root/no_permission_xyz/file.txt", "x")
        assert "error" in result


# ── delete_file ────────────────────────────────────────────────────────────────

class TestLocalDeleteFile:
    def test_deletes_existing_file(self, env, tmp_file):
        tmp_file.write_text("content")
        assert env.delete_file(str(tmp_file)) is True
        assert not tmp_file.exists()

    def test_returns_false_for_nonexistent(self, env, tmp_path):
        path = str(tmp_path / "ghost.txt")
        assert env.delete_file(path) is False


# ── exists ─────────────────────────────────────────────────────────────────────

class TestLocalExists:
    def test_existing_file(self, env, tmp_file):
        tmp_file.write_text("x")
        assert env.exists(str(tmp_file)) is True

    def test_nonexistent(self, env, tmp_path):
        assert env.exists(str(tmp_path / "ghost.txt")) is False

    def test_existing_dir(self, env, tmp_path):
        assert env.exists(str(tmp_path)) is True
