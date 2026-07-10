"""Remote-host lifecycle management for a managed llama-server process over SSH.

core.evaluator._start_managed_llama_server always launches llama-server as a
LOCAL subprocess — execution_target=ssh/pct only ever changed where shell
commands ran, never where the managed server itself ran. This module is what
actually makes "run the managed server on the remote host" work: it
backgrounds llama-server over an existing SSHEnvironment connection, tunnels
a local ephemeral port to it via a paramiko "direct-tcpip" channel (so the
remote port never needs to be reachable from wherever ModelScope itself
runs), and polls /health through that tunnel.

PCT targets are intentionally out of scope here: a Proxmox LXC container has
its own network namespace, so "the remote host's 127.0.0.1" is not reachable
via a port-forward to the physical host the same way an SSH target is.
"""

from __future__ import annotations

import select
import shlex
import socketserver
import subprocess
import threading
import time
from typing import Callable

import requests

from core.environment import SSHEnvironment


class _ForwardHandler(socketserver.BaseRequestHandler):
    # Bound per-instance via _make_handler(); placeholders keep this class
    # self-documenting and satisfy static analysis.
    ssh_transport = None
    remote_host = "127.0.0.1"
    remote_port = 0

    def handle(self) -> None:
        try:
            chan = self.ssh_transport.open_channel(
                "direct-tcpip",
                (self.remote_host, self.remote_port),
                self.request.getpeername(),
            )
        except Exception:
            return
        if chan is None:
            return
        try:
            while True:
                r, _, _ = select.select([self.request, chan], [], [])
                if self.request in r:
                    data = self.request.recv(4096)
                    if not data:
                        break
                    chan.sendall(data)
                if chan in r:
                    data = chan.recv(4096)
                    if not data:
                        break
                    self.request.sendall(data)
        except Exception:
            pass
        finally:
            try:
                chan.close()
            except Exception:
                pass
            try:
                self.request.close()
            except Exception:
                pass


class _ForwardServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(transport, remote_host: str, remote_port: int):
    return type(
        "_BoundForwardHandler",
        (_ForwardHandler,),
        {"ssh_transport": transport, "remote_host": remote_host, "remote_port": remote_port},
    )


class SSHPortForward:
    """Local TCP listener that tunnels every connection to remote_host:remote_port
    over an existing SSH transport.

    Binds to an OS-assigned local port (127.0.0.1:0) so multiple tunnels never
    collide; `.local_port` is what callers should connect to instead of the
    remote address directly.
    """

    def __init__(self, ssh_client, remote_host: str, remote_port: int):
        transport = ssh_client.get_transport()
        handler = _make_handler(transport, remote_host, remote_port)
        self._server = _ForwardServer(("127.0.0.1", 0), handler)
        self.local_port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass


def _quote_remote_path(path: str) -> str:
    """Quote a path for a remote shell command, letting a leading ~/ still
    expand via $HOME (shlex.quote on a literal "~" would defeat that)."""
    if path.startswith("~/"):
        return f'"$HOME/"{shlex.quote(path[2:])}'
    return shlex.quote(path)


class RemoteManagedServer:
    """Handle for a llama-server process running on a remote host, launched
    and supervised over SSH. Mirrors just enough of subprocess.Popen's
    interface (poll/terminate/wait/kill) that callers can tear it down
    identically to a local managed server.
    """

    def __init__(self, env: SSHEnvironment, remote_pid: str, log_path: str, tunnel: SSHPortForward):
        self._env = env
        self._remote_pid = remote_pid
        self._log_path = log_path
        self._tunnel = tunnel
        self._known_dead = False

    @property
    def local_port(self) -> int:
        return self._tunnel.local_port

    def poll(self):
        """None if the remote process is still alive, else a non-None sentinel.

        `kill -0` can't recover a real exit code, so unlike subprocess.Popen
        the non-None value here is not meaningful beyond "not running".
        """
        if self._known_dead:
            return 1
        res = self._env.execute(
            f"kill -0 {self._remote_pid} 2>/dev/null && echo ALIVE || echo DEAD", timeout=10
        )
        if "ALIVE" in res.get("stdout", ""):
            return None
        self._known_dead = True
        return 1

    def read_log_tail(self, chars: int = 2000) -> str:
        res = self._env.execute(f"tail -c {chars} {shlex.quote(self._log_path)} 2>/dev/null", timeout=10)
        return res.get("stdout", "")

    def terminate(self) -> None:
        try:
            self._env.execute(f"kill {self._remote_pid} 2>/dev/null", timeout=10)
        except Exception:
            pass

    def kill(self) -> None:
        try:
            self._env.execute(f"kill -9 {self._remote_pid} 2>/dev/null", timeout=10)
        except Exception:
            pass
        self._known_dead = True
        self._tunnel.close()

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.time() + timeout if timeout is not None else None
        while True:
            if self.poll() is not None:
                self._tunnel.close()
                return 0
            if deadline is not None and time.time() >= deadline:
                raise subprocess.TimeoutExpired(cmd="llama-server (remote)", timeout=timeout)
            time.sleep(0.5)


def start_remote_managed_llama_server(
    env: SSHEnvironment,
    binary: str,
    model_path: str,
    context_size: int,
    remote_port: int,
    remote_host: str,
    on_log: Callable[[str], None],
    custom_flags: str = "",
    advanced_flags: str = "",
    ready_timeout: float = 300.0,
) -> RemoteManagedServer:
    """Launch llama-server on the host behind `env` and return a handle once
    it responds ready, or raise RuntimeError.

    remote_host/remote_port only need to be reachable FROM the remote machine
    itself (typically 127.0.0.1) — ModelScope talks to the server through an
    SSH-tunnelled local port instead of the remote address directly.
    """
    cmd_parts = [
        _quote_remote_path(binary) if binary.startswith("~/") else shlex.quote(binary),
        "-m", _quote_remote_path(model_path),
        "-c", str(context_size),
        "--port", str(remote_port),
        "--host", shlex.quote(remote_host),
    ]
    if advanced_flags.strip():
        cmd_parts.append(advanced_flags.strip())
    if custom_flags.strip():
        cmd_parts.append(custom_flags.strip())
    remote_cmd = " ".join(cmd_parts)

    log_path = f"/tmp/modelscope_llama_server_{remote_port}.log"
    launch_cmd = f"nohup {remote_cmd} > {shlex.quote(log_path)} 2>&1 & echo $!"
    on_log(f"[SERVER] Starting on {env.host} via SSH: {remote_cmd}")

    res = env.execute(launch_cmd, timeout=15)
    stdout = (res.get("stdout") or "").strip()
    remote_pid = stdout.splitlines()[-1].strip() if stdout else ""
    if res.get("exit_code", -1) != 0 or not remote_pid.isdigit():
        raise RuntimeError(
            f"Failed to start remote server on {env.host}: "
            f"{res.get('stderr') or res.get('stdout') or 'unknown error'}"
        )

    client = env.get_client()
    tunnel = SSHPortForward(client, remote_host, remote_port)
    handle = RemoteManagedServer(env, remote_pid, log_path, tunnel)

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if handle.poll() is not None:
            tail = handle.read_log_tail()
            tunnel.close()
            raise RuntimeError(
                f"Server exited immediately on {env.host}:{remote_port}"
                + (f": {tail[-800:]}" if tail else "")
            )
        try:
            resp = requests.get(f"http://127.0.0.1:{tunnel.local_port}/health", timeout=1.0)
            if resp.status_code == 200:
                on_log(f"[SERVER] Ready on {env.host}:{remote_port} (tunnelled via local port {tunnel.local_port})")
                return handle
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)

    # Readiness timed out: tear down the remote process before giving up so we
    # don't orphan a llama-server holding the port/GPU on the remote host. The
    # local helper does the same via proc.terminate() (see evaluator.py).
    handle.terminate()
    tunnel.close()
    raise RuntimeError(
        f"Server did not become ready after {int(ready_timeout)}s on {env.host}:{remote_port}"
    )
