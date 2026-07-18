"""Execution environments — the abstraction over *where* commands run.

ModelScope drives an evaluation against a ``BaseEnvironment`` so the rest of the
system (evaluator, preflight, batch runner) never cares whether it is talking to
the local machine or a remote Kali VM over SSH. Two concretions exist:

* :class:`LocalEnvironment` — runs commands as local subprocesses.
* :class:`SSHEnvironment`   — runs commands on a remote host via paramiko; it
  advertises ``is_remote_caf = True`` so the orchestrator delegates the whole
  run to the remote CyberAgentFlow CLI instead of the local LLM loop.

Use :func:`create_environment` rather than instantiating the classes directly —
it is the single place that knows how to map "local vs SSH + connection
details" to the right object (factory pattern).
"""
import shlex
import subprocess
import pathlib
import os
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, Optional


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
        remote_cwd: str = "~/modelscope",
    ) -> None:
        self._host           = host
        self._port           = int(port)
        self._username       = username
        self._password       = password or None
        self._key_path       = key_path or None
        self._remote_cwd     = remote_cwd or "~/modelscope"  # user-supplied, never mutated
        self._resolved_cwd: Optional[str] = None  # set on first connect after tilde/cwd resolution
        self._client: Any    = None
        self._sftp:   Any    = None
        self._active_channel: Any = None  # set during execute_streaming; used by cancel()

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
        resolved = self._remote_cwd
        if resolved.startswith("~"):
            _stdin, _stdout, _stderr = client.exec_command("echo $HOME")
            _home = _stdout.read().decode().strip()
            if _home:
                resolved = _home + resolved[1:]  # ~/foo → /home/user/foo
        try:
            sftp.chdir(resolved)
            resolved = sftp.getcwd() or resolved
        except IOError:
            pass  # Directory may not exist yet; execute() will cd there anyway
        self._resolved_cwd = resolved
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

    def get_client(self):
        """Return the underlying paramiko SSHClient, connecting first if needed.

        For callers that need raw transport access beyond execute()/read_file()/
        write_file() — e.g. opening a "direct-tcpip" port-forward channel to
        supervise a remote managed llama-server (see core.remote_server).
        Reuses this environment's authenticated connection rather than opening
        a second one.
        """
        self.connect()
        return self._client

    @property
    def remote_cwd(self) -> str:
        return self._resolved_cwd if self._resolved_cwd is not None else self._remote_cwd

    @property
    def host(self) -> str:
        return self._host

    @property
    def username(self) -> str:
        return self._username

    def _command_in_cwd(self, command: str) -> str:
        return f"cd {shlex.quote(self.remote_cwd)} && {command}"

    def execute(self, command: str, timeout: int = 15, env_vars: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        try:
            self.connect()
            # Always export TERM so ncurses/apt/dpkg don't emit "TERM not set" stderr noise.
            # Caller-supplied env_vars take precedence if they override TERM.
            _effective_env = {"TERM": "xterm", **(env_vars or {})}
            exports = " ".join(
                f"export {k}={shlex.quote(str(v))};" for k, v in _effective_env.items()
            )
            full_cmd = self._command_in_cwd(f"{exports + ' ' if exports else ''}{command}")
            _, stdout, stderr = self._client.exec_command(full_cmd, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            return {
                "stdout":    stdout.read().decode("utf-8", errors="replace"),
                "stderr":    stderr.read().decode("utf-8", errors="replace"),
                "exit_code": exit_code,
            }
        except Exception as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1}

    def execute_streaming(
        self,
        command: str,
        timeout: int = 600,
        on_chunk: Optional[Callable[[str], None]] = None,
        input_queue: Any = None,
        cancel_ref: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Execute command with a PTY, streaming output via on_chunk (designed for background threads).

        Uses a paramiko channel with PTY so CAF's interactive prompts ([approval],
        [decision], [timeout]) appear in the stream. When CAF reaches an input()
        call, bytes from input_queue are sent as stdin.

        NOTE: PTY output may contain ANSI colour codes and carriage-return-based
        progress bars. The caller is responsible for ANSI stripping.

        Returns the same dict shape as execute().
        """
        try:
            self.connect()
            full_cmd = self._command_in_cwd(command)
            transport = self._client.get_transport()
            channel = transport.open_session()
            channel.get_pty()
            channel.exec_command(full_cmd)
            self._active_channel = channel

            stdout_buf = ""
            deadline = time.time() + timeout

            try:
                while time.time() < deadline:
                    if cancel_ref and cancel_ref[0]:
                        channel.close()
                        break

                    # Inject stdin from the UI input queue
                    if input_queue is not None:
                        try:
                            user_text = input_queue.get_nowait()
                            channel.sendall((user_text + "\n").encode("utf-8"))
                            if on_chunk:
                                on_chunk(f"\n>>> {user_text}\n")
                        except Exception:
                            pass

                    if channel.recv_ready():
                        data = channel.recv(4096)
                        if data:
                            text = data.decode("utf-8", errors="replace")
                            stdout_buf += text
                            if on_chunk:
                                on_chunk(text)

                    if channel.exit_status_ready() and not channel.recv_ready():
                        # Drain final buffered bytes before exiting
                        while channel.recv_ready():
                            data = channel.recv(4096)
                            if data:
                                text = data.decode("utf-8", errors="replace")
                                stdout_buf += text
                                if on_chunk:
                                    on_chunk(text)
                        break

                    time.sleep(0.05)
            finally:
                self._active_channel = None

            exit_code = channel.recv_exit_status() if not channel.closed else -1
            try:
                channel.close()
            except Exception:
                pass

            return {"stdout": stdout_buf, "stderr": "", "exit_code": exit_code}

        except Exception as exc:
            self._active_channel = None
            return {"stdout": "", "stderr": str(exc), "exit_code": -1}

    def cancel(self) -> None:
        """Close the active streaming channel from another thread (cancellation)."""
        ch = self._active_channel
        if ch is not None:
            try:
                ch.close()
            except Exception:
                pass

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

class PCTEnvironment(BaseEnvironment):
    """Execution environment for Proxmox LXC containers (wraps a base environment)."""

    def __init__(self, vmid: str, base_env: BaseEnvironment) -> None:
        self.vmid = str(vmid)
        self.base_env = base_env
        self.is_remote_caf = base_env.is_remote_caf

    def execute(self, command: str, timeout: int = 15) -> Dict[str, Any]:
        wrapped = f"pct exec {self.vmid} -- bash -c {shlex.quote(command)}"
        return self.base_env.execute(wrapped, timeout=timeout)

    def read_file(self, path: str) -> str:
        res = self.execute(f"cat {shlex.quote(path)}")
        if res["exit_code"] != 0:
            raise FileNotFoundError(f"Could not read {path}: {res['stderr']}")
        return res["stdout"]

    def write_file(self, path: str, content: str) -> Dict[str, Any]:
        import base64
        b64_content = base64.b64encode(content.encode()).decode()
        res = self.execute(f"echo {b64_content} | base64 -d > {shlex.quote(path)}")
        if res["exit_code"] != 0:
            return {"error": res["stderr"]}
        return {"status": "success", "bytes_written": len(content)}

    def delete_file(self, path: str) -> bool:
        res = self.execute(f"rm -f {shlex.quote(path)}")
        return res["exit_code"] == 0

    def exists(self, path: str) -> bool:
        res = self.execute(f"test -e {shlex.quote(path)}")
        return res["exit_code"] == 0


# ── Factory ──────────────────────────────────────────────────────────────────

# Default install location of CyberAgentFlow on a remote Kali target. Centralised
# here so the UI, CLI and tests all agree on one fallback value.
DEFAULT_REMOTE_CWD = "~/modelscope"


def create_environment(
    *,
    ssh: bool = False,
    host: str = "",
    port: int = 22,
    username: str = "root",
    password: Optional[str] = None,
    key_path: Optional[str] = None,
    remote_cwd: Optional[str] = None,
    pct_vmid: Optional[str] = None,
    project_id: Optional[str] = None,
) -> BaseEnvironment:
    """Return the right :class:`BaseEnvironment` for the requested target.

    This is the single construction point for environments. Callers (the CLI,
    the Execute tab, the CAF tab) each extract connection details from their own
    source — argparse, ``st.session_state`` keys, thread-local vars — and pass
    them as keyword arguments, so we never duplicate the ``SSHEnvironment(...)``
    kwarg block or the local/remote branch across call sites.

    Args:
        ssh: When True, build an :class:`SSHEnvironment`; otherwise local.
        host/port/username/password/key_path/remote_cwd: SSH connection details
            (ignored for the local environment).
        pct_vmid: If provided, wraps the environment in a PCTEnvironment for LXC.
    """
    _cwd = remote_cwd or (f"~/modelscope/{project_id}" if project_id else DEFAULT_REMOTE_CWD)
    
    if ssh:
        env = SSHEnvironment(
            host=host,
            port=int(port or 22),
            username=username or "root",
            password=password or None,
            key_path=key_path or None,
            remote_cwd=_cwd,
        )
    else:
        env = LocalEnvironment()
        
    if pct_vmid:
        return PCTEnvironment(pct_vmid, env)
    return env
