"""
Unit tests for core.environment.SSHEnvironment.

These exercise the command-construction and capability-flag logic without a
real SSH server: the paramiko client is replaced with a fake that records the
command string it was asked to run. The load-bearing behaviour under test is
the `cd {remote_cwd} && {exports} {command}` wrapping and env-var quoting.
"""
import pytest
from core.environment import SSHEnvironment, LocalEnvironment, BaseEnvironment


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
    """Records the last command passed to exec_command."""
    def __init__(self):
        self.last_command = None

    def exec_command(self, command, timeout=None):
        self.last_command = command
        return (_FakeStream(), _FakeStream(b"ok"), _FakeStream(b""))


@pytest.fixture
def ssh(monkeypatch):
    env = SSHEnvironment(host="10.0.0.9", username="kali",
                         remote_cwd="/home/kali/cyber-agent-flow")
    fake = _FakeClient()
    env._client = fake
    # connect() is idempotent and would try a real socket; stub it out.
    monkeypatch.setattr(env, "connect", lambda: None)
    return env, fake


# ── Capability flag ─────────────────────────────────────────────────────────

def test_ssh_is_remote_caf():
    assert SSHEnvironment.is_remote_caf is True


def test_local_is_not_remote_caf():
    assert LocalEnvironment.is_remote_caf is False


def test_base_default_flag():
    assert BaseEnvironment.is_remote_caf is False


# ── Command construction ──────────────────────────────────────────────────────

class TestExecuteCommandConstruction:
    def test_prefixes_cd_to_remote_cwd(self, ssh):
        env, fake = ssh
        env.execute("ls")
        assert fake.last_command.startswith("cd /home/kali/cyber-agent-flow && ")

    def test_quotes_remote_cwd_with_spaces(self, monkeypatch):
        env = SSHEnvironment(host="10.0.0.9", username="kali",
                             remote_cwd="/home/kali/caf lab")
        fake = _FakeClient()
        env._client = fake
        monkeypatch.setattr(env, "connect", lambda: None)

        env.execute("ls")

        assert fake.last_command.startswith("cd '/home/kali/caf lab' && ")

    def test_appends_command(self, ssh):
        env, fake = ssh
        env.execute("nmap -F 127.0.0.1")
        assert fake.last_command.endswith("nmap -F 127.0.0.1")

    def test_env_vars_are_exported_and_quoted(self, ssh):
        env, fake = ssh
        env.execute("run", env_vars={"API_KEY": "a b'c"})
        assert "export API_KEY=" in fake.last_command
        # shlex.quote wraps values containing spaces / quotes
        assert "'a b'\"'\"'c'" in fake.last_command

    def test_no_exports_when_no_env_vars(self, ssh):
        env, fake = ssh
        env.execute("whoami")
        assert "export" not in fake.last_command

    def test_returns_decoded_streams(self, ssh):
        env, _ = ssh
        result = env.execute("ls")
        assert result == {"stdout": "ok", "stderr": "", "exit_code": 0}

    def test_exception_returns_error_dict(self, monkeypatch):
        env = SSHEnvironment(host="x", remote_cwd="/tmp")
        monkeypatch.setattr(env, "connect",
                            lambda: (_ for _ in ()).throw(OSError("boom")))
        result = env.execute("ls")
        assert result["exit_code"] == -1
        assert "boom" in result["stderr"]


# ── Properties ────────────────────────────────────────────────────────────────

def test_public_properties():
    env = SSHEnvironment(host="1.2.3.4", username="root", remote_cwd="/opt/caf")
    assert env.host == "1.2.3.4"
    assert env.username == "root"
    assert env.remote_cwd == "/opt/caf"
