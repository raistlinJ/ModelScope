import subprocess
import pathlib
import os
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseEnvironment(ABC):
    """Abstract base class for all target execution environments."""

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


# ── SSHEnvironment — FUTURE RELEASE ──────────────────────────────────────────
# Remote execution over SSH (via Paramiko) is planned for a future release.
# This class is disabled until that support is fully implemented.
#
# class SSHEnvironment(BaseEnvironment):
#     """Execution environment for a remote machine via SSH."""
#
#     def __init__(
#         self,
#         host: str,
#         port: int = 22,
#         username: str = "root",
#         password: Optional[str] = None,
#         key_path: Optional[str] = None,
#     ):
#         self.host = host
#         self.port = port
#         self.username = username
#         self.password = password
#         self.key_path = key_path
#         self._ssh = None
#         self._sftp = None
#
#     def _get_ssh(self):
#         import paramiko
#         if self._ssh is None:
#             self._ssh = paramiko.SSHClient()
#             self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#             if self.key_path:
#                 self._ssh.connect(
#                     self.host, port=self.port, username=self.username, key_filename=self.key_path
#                 )
#             else:
#                 self._ssh.connect(
#                     self.host, port=self.port, username=self.username, password=self.password
#                 )
#         return self._ssh
#
#     def _get_sftp(self):
#         if self._sftp is None:
#             self._sftp = self._get_ssh().open_sftp()
#         return self._sftp
#
#     def execute(self, command: str, timeout: int = 15) -> Dict[str, Any]:
#         try:
#             client = self._get_ssh()
#             stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
#             return {
#                 "stdout": stdout.read().decode("utf-8"),
#                 "stderr": stderr.read().decode("utf-8"),
#                 "exit_code": stdout.channel.recv_exit_status(),
#             }
#         except Exception as e:
#             return {"stdout": "", "stderr": str(e), "exit_code": -1}
#
#     def read_file(self, path: str) -> str:
#         with self._get_sftp().open(path, "r") as f:
#             return f.read().decode("utf-8")
#
#     def write_file(self, path: str, content: str) -> Dict[str, Any]:
#         try:
#             sftp = self._get_sftp()
#             remote_dir = os.path.dirname(path)
#             if remote_dir:
#                 self.execute(f"mkdir -p {remote_dir}")
#             with sftp.open(path, "w") as f:
#                 f.write(content)
#             return {"status": "success", "bytes_written": len(content.encode())}
#         except Exception as e:
#             return {"error": str(e)}
#
#     def delete_file(self, path: str) -> bool:
#         try:
#             self._get_sftp().remove(path)
#             return True
#         except Exception:
#             return False
#
#     def exists(self, path: str) -> bool:
#         try:
#             self._get_sftp().stat(path)
#             return True
#         except Exception:
#             return False
#
#     def close(self):
#         if self._sftp:
#             self._sftp.close()
#         if self._ssh:
#             self._ssh.close()
# ─────────────────────────────────────────────────────────────────────────────
