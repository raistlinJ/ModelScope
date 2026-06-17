"""
Security regression tests for _execute_tool_in_env (nmap tool).

These guard the allowlist-based injection fix: targets containing
shell metacharacters, whitespace, or newlines must be rejected with
an error dict rather than passed to the shell.

Also covers: file_creator via LocalEnvironment, SSHEnvironment write_file
path quoting.
"""
import pytest
from unittest.mock import MagicMock, patch, call
from core.evaluator import _execute_tool_in_env
from core.environment import LocalEnvironment


# ── nmap target validation ────────────────────────────────────────────────────

class TestNmapTargetValidation:
    def _env(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "", "stderr": "", "exit_code": 0}
        return env

    def test_valid_ip_passes(self):
        env = self._env()
        _execute_tool_in_env(env, "run_nmap_scan", {"target": "127.0.0.1", "arguments": "-F"})
        assert env.execute.called

    def test_valid_hostname_passes(self):
        env = self._env()
        _execute_tool_in_env(env, "run_nmap_scan", {"target": "example.com", "arguments": "-F"})
        assert env.execute.called

    def test_valid_cidr_passes(self):
        env = self._env()
        _execute_tool_in_env(env, "run_nmap_scan", {"target": "192.168.1.0/24", "arguments": "-F"})
        assert env.execute.called

    def test_newline_in_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "127.0.0.1\ncat /etc/passwd", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_semicolon_in_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "127.0.0.1;rm -rf /", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_space_in_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "127.0.0.1 malicious", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_pipe_in_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "127.0.0.1|id", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_backtick_in_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "`whoami`", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_dollar_substitution_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "$(id)", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_empty_target_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "", "arguments": "-F"})
        assert "error" in result
        assert not env.execute.called

    def test_malformed_arguments_rejected(self):
        env = self._env()
        result = _execute_tool_in_env(env, "run_nmap_scan",
                                      {"target": "127.0.0.1", "arguments": "-F '"})
        assert "error" in result
        assert not env.execute.called

    def test_valid_arguments_quoted_safely(self):
        """Safe args are tokenised and re-quoted — verify nmap receives them."""
        env = self._env()
        _execute_tool_in_env(env, "run_nmap_scan",
                             {"target": "127.0.0.1", "arguments": "-T4 -sV"})
        assert env.execute.called
        cmd = env.execute.call_args[0][0]
        assert "nmap" in cmd
        assert "127.0.0.1" in cmd


# ── file_creator via LocalEnvironment ────────────────────────────────────────

class TestFileCreatorLocal:
    def test_writes_file(self, tmp_path):
        env = LocalEnvironment()
        dest = str(tmp_path / "out.txt")
        result = _execute_tool_in_env(
            env, "file_creator", {"path": dest, "content": "hello world"}
        )
        assert result.get("status") == "success"
        assert (tmp_path / "out.txt").read_text() == "hello world"

    def test_creates_parent_dirs(self, tmp_path):
        env = LocalEnvironment()
        dest = str(tmp_path / "a" / "b" / "c.txt")
        result = _execute_tool_in_env(
            env, "file_creator", {"path": dest, "content": "nested"}
        )
        assert result.get("status") == "success"

    def test_bytes_written_reported(self, tmp_path):
        env = LocalEnvironment()
        dest = str(tmp_path / "f.txt")
        content = "abc"
        result = _execute_tool_in_env(
            env, "file_creator", {"path": dest, "content": content}
        )
        assert result["bytes_written"] == len(content.encode())


# ── unknown tool returns error ─────────────────────────────────────────────────

def test_unknown_tool_returns_error():
    env = MagicMock()
    result = _execute_tool_in_env(env, "totally_unknown_tool", {})
    assert "error" in result
    assert "Unknown tool" in result["error"]


# ── SSHEnvironment write_file uses shlex.quote for mkdir path ────────────────

class TestSshWriteFilePathQuoting:
    def test_mkdir_path_is_quoted(self):
        """write_file must call mkdir -p with shlex.quote(parent) so a path
        containing spaces or metacharacters cannot inject a second command."""
        from core.environment import SSHEnvironment
        import shlex

        env = SSHEnvironment(host="10.0.0.1", remote_cwd="/opt/caf")

        # Stub connect and SFTP open to avoid network
        env._client = MagicMock()
        env._sftp = MagicMock()
        env._sftp.open.return_value.__enter__ = MagicMock(return_value=MagicMock())
        env._sftp.open.return_value.__exit__ = MagicMock(return_value=False)

        # Capture execute calls
        captured = []
        original_execute = env.execute

        def fake_execute(cmd, timeout=10):
            captured.append(cmd)
            return {"stdout": "", "stderr": "", "exit_code": 0}

        env.execute = fake_execute
        env.connect = lambda: None

        # A parent directory with a space and semicolon in the name
        tricky_path = "/opt/caf/my dir;evil/file.txt"
        env.write_file(tricky_path, "content")

        # The mkdir call must have been made (captured)
        mkdir_calls = [c for c in captured if "mkdir" in c]
        assert len(mkdir_calls) == 1

        # The path in the mkdir call must be shlex-quoted so ';evil' can't run
        mkdir_cmd = mkdir_calls[0]
        # shlex.quote wraps the path in single quotes, preventing injection
        quoted_parent = shlex.quote("/opt/caf/my dir;evil")
        assert quoted_parent in mkdir_cmd
