import shlex
import subprocess
import pathlib
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseEnvironment(ABC):
    """Abstract base class for all target execution environments."""

    #: Capability flag: when True, the evaluator delegates the entire run to a
    #: remote CyberAgentFlow CLI instead of driving the local LLM/tool loop.
    #: Environments advertise their capabilities here so the orchestrator never
    #: needs to branch on a concrete subclass (open/closed principle).
    is_remote_caf: bool = False

    @abstractmethod
    def execute(self, command: str, timeout: int = 15) -> Dict[str, Any]:
        """Execute a shell command and return stdout, stderr, exit_code."""
        pass

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read content from a file in the environment."""
        pass

    @abstractmethod
    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        """Write content to a file in the environment."""
        pass

    @abstractmethod
    def delete_file(self, path: str) -> bool:
        """Delete a file in the environment."""
        pass

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Check if a path exists in the environment."""
        pass


class LocalEnvironment(BaseEnvironment):
    """Execution environment for the local machine."""

    def execute(self, command: str, timeout: int = 15) -> Dict[str, Any]:
        try:
            res = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return {
                "stdout": res.stdout,
                "stderr": res.stderr,
                "exit_code": res.returncode,
            }
        except subprocess.TimeoutExpired:
            return {"stdout": "", "stderr": "Timed out", "exit_code": -1}
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def read_file(self, path: str) -> str:
        return pathlib.Path(path).read_text(encoding="utf-8")

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        try:
            p = pathlib.Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"status": "success", "bytes_written": len(content.encode())}
        except Exception as e:
            return {"error": str(e)}

    def delete_file(self, path: str) -> bool:
        try:
            p = pathlib.Path(path)
            if p.exists():
                p.unlink()
                return True
            return False
        except Exception:
            return False

    def exists(self, path: str) -> bool:
        return os.path.exists(path)


class SSHEnvironment(BaseEnvironment):
    """Execution environment for a remote Kali Linux machine via SSH."""

    #: SSH targets host the CyberAgentFlow CLI; the evaluator delegates to it.
    is_remote_caf: bool = True

    def __init__(
        self,
        host: str,
        port: int = 22,
        username: str = "root",
        password: Optional[str] = None,
        key_path: Optional[str] = None,
        remote_cwd: str = "~/cyber-agent-flow",
    ) -> None:
        self._host       = host
        self._port       = int(port)
        self._username   = username
        self._password   = password or None
        self._key_path   = key_path or None
        self._remote_cwd = remote_cwd or "~/cyber-agent-flow"
        self._client: Any = None
        self._sftp:   Any = None

    def connect(self) -> None:
        """Open SSH connection (idempotent)."""
        import paramiko
        if (
            self._client is not None
            and self._client.get_transport() is not None
            and self._client.get_transport().is_active()
        ):
            return
        client = paramiko.SSHClient()
        # SECURITY: AutoAddPolicy trusts unknown host keys on first contact,
        # so this connection is NOT protected against MITM. It is acceptable
        # only for the trusted lab/VM network this tool targets. Do NOT
        # "upgrade" to a stricter policy without also giving operators a way to
        # manage known_hosts, or remote evaluations will silently break.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname": self._host,
            "port":     self._port,
            "username": self._username,
            "timeout":  10,
        }
        if self._key_path:
            kwargs["key_filename"] = self._key_path
        if self._password:
            kwargs["password"] = self._password
        client.connect(**kwargs)
        self._client = client
        sftp = client.open_sftp()
        # Expand ~ — SFTP protocol does not resolve shell home shortcuts, so
        # sftp.chdir("~/foo") raises [Errno 2] No such file.  Ask the remote
        # shell for $HOME once and substitute it before entering the directory.
        if self._remote_cwd.startswith("~"):
            _stdin, _stdout, _stderr = client.exec_command("echo $HOME")
            _home = _stdout.read().decode().strip()
            if _home:
                self._remote_cwd = _home + self._remote_cwd[1:]  # ~/foo → /home/user/foo
        try:
            sftp.chdir(self._remote_cwd)
            self._remote_cwd = sftp.getcwd() or self._remote_cwd
        except IOError:
            pass  # Directory may not exist yet; execute() will cd there anyway
        self._sftp = sftp

    def close(self) -> None:
        """Close SSH and SFTP connections."""
        for conn in (self._sftp, self._client):
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        self._sftp   = None
        self._client = None

    @property
    def remote_cwd(self) -> str:
        return self._remote_cwd

    @property
    def host(self) -> str:
        return self._host

    @property
    def username(self) -> str:
        return self._username

    def execute(self, command: str, timeout: int = 15, env_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            self.connect()
            exports = " ".join(
                f"export {k}={shlex.quote(str(v))};" for k, v in (env_vars or {}).items()
            )
            full_cmd = f"cd {self._remote_cwd} && {exports + ' ' if exports else ''}{command}"
            _, stdout, stderr = self._client.exec_command(full_cmd, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            return {
                "stdout":    stdout.read().decode("utf-8", errors="replace"),
                "stderr":    stderr.read().decode("utf-8", errors="replace"),
                "exit_code": exit_code,
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def read_file(self, path: str) -> str:
        self.connect()
        with self._sftp.open(path, "r") as fh:
            return fh.read().decode("utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        try:
            self.connect()
            parent = "/".join(path.rstrip("/").split("/")[:-1])
            if parent:
                # Quote the path so a remote directory name can never inject a
                # second shell command into the mkdir invocation.
                self.execute(f"mkdir -p {shlex.quote(parent)}", timeout=10)
            encoded = content.encode("utf-8")
            with self._sftp.open(path, "wb") as fh:
                fh.write(encoded)
            return {"status": "success", "bytes_written": len(encoded)}
        except Exception as e:
            return {"error": str(e)}

    def delete_file(self, path: str) -> bool:
        try:
            self.connect()
            self._sftp.remove(path)
            return True
        except IOError:
            return False
        except Exception:
            return False

    def exists(self, path: str) -> bool:
        try:
            self.connect()
            self._sftp.stat(path)
            return True
        except IOError:
            return False
        except Exception:
            return False
