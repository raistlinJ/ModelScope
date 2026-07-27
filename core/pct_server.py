"""Managed llama-server lifecycle *inside* a Proxmox LXC container.

The SSH equivalent (``core.remote_server``) tunnels a loopback port back to
ModelScope. That is not available here: ``pct exec`` is a one-shot command
channel with no transport to forward over. Instead the server binds all
interfaces inside the container and ModelScope talks to the container's own
address, which the Proxmox host it runs on can route to directly.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from typing import Any, Callable

import requests

from core.llama_metrics import strip_user_metrics_flag


# Binding the container's loopback would make the server unreachable from the
# Proxmox host, so a container launch always binds every interface.
CONTAINER_BIND_HOST = "0.0.0.0"

_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def container_ipv4(env: Any, timeout: int = 15) -> str:
    """First globally-scoped IPv4 address of the container behind ``env``.

    ``hostname`` is missing from some minimal images, so fall back to iproute2
    before giving up.
    """
    result = env.execute(
        "hostname -I 2>/dev/null || ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}'",
        timeout=timeout,
    )
    for token in (result.get("stdout") or "").split():
        address = token.split("/")[0]
        if _IPV4.match(address) and not address.startswith("127."):
            return address
    raise RuntimeError(
        "Could not determine the container's IP address. Give it a network "
        "interface, or install iproute2 inside it."
    )


class PCTManagedServer:
    """Handle for llama-server running inside an LXC container.

    Mirrors the parts of :class:`subprocess.Popen` the evaluator's teardown
    path uses (``poll``/``terminate``/``wait``/``kill``) so a container-hosted
    server is torn down exactly like a local one.
    """

    def __init__(self, env: Any, vmid: str, pid: str, log_path: str, base_url: str):
        self._env = env
        self._vmid = str(vmid)
        self._pid = str(pid)
        self._log_path = log_path
        self._base_url = base_url
        self._known_dead = False

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def vmid(self) -> str:
        return self._vmid

    def poll(self):
        """None while the container process is alive, else a dead sentinel.

        ``kill -0`` cannot recover an exit code, so — as with the SSH handle —
        the non-None value only means "not running".
        """
        if self._known_dead:
            return 1
        result = self._env.execute(
            f"kill -0 {self._pid} 2>/dev/null && echo ALIVE || echo DEAD", timeout=10,
        )
        if "ALIVE" in (result.get("stdout") or ""):
            return None
        self._known_dead = True
        return 1

    def read_log_tail(self, chars: int = 2000) -> str:
        result = self._env.execute(
            f"tail -c {chars} {shlex.quote(self._log_path)} 2>&1 || cat {shlex.quote(self._log_path)} 2>&1", timeout=10,
        )
        return result.get("stdout", "")

    def terminate(self) -> None:
        try:
            self._env.execute(f"kill {self._pid} 2>/dev/null", timeout=10)
        except Exception:
            pass

    def kill(self) -> None:
        try:
            self._env.execute(f"kill -9 {self._pid} 2>/dev/null", timeout=10)
        except Exception:
            pass
        self._known_dead = True

    def wait(self, timeout: float | None = None) -> int:
        deadline = time.time() + timeout if timeout is not None else None
        while True:
            if self.poll() is not None:
                return 0
            if deadline is not None and time.time() >= deadline:
                raise subprocess.TimeoutExpired(cmd="llama-server (pct)", timeout=timeout)
            time.sleep(0.5)


def _container_path(path: str) -> str:
    """Quote a path for the container's shell, still letting ~/ expand."""
    if path.startswith("~/"):
        return f'"$HOME/"{shlex.quote(path[2:])}'
    return shlex.quote(path)


def resolve_container_binary(env: Any, binary: str) -> str:
    """Expand a directory into the llama-server inside it, container-side.

    The path names a location in the container, so the host filesystem cannot
    answer whether it is a directory.
    """
    binary = (binary or "").strip()
    if not binary:
        return "llama-server"
    probe = env.execute(f"test -d {_container_path(binary)}", timeout=10)
    if probe.get("exit_code", 1) == 0:
        return f"{binary.rstrip('/')}/llama-server"
    if binary.rsplit("/", 1)[-1] in ("llama-cli", "llama-cli.exe"):
        return binary.rsplit("/", 1)[0] + "/llama-server" if "/" in binary else "llama-server"
    return binary


def start_pct_managed_llama_server(
    env: Any,
    vmid: str,
    binary: str,
    model_path: str,
    context_size: int,
    port: int,
    on_log: Callable[[str], None],
    custom_flags: str = "",
    advanced_flags: str = "",
    ready_timeout: float = 300.0,
) -> PCTManagedServer:
    """Launch llama-server inside the container behind ``env``.

    Returns a handle once the server answers ``/health`` at the container's
    address, otherwise raises RuntimeError with the container-side log tail.
    """
    address = container_ipv4(env)
    binary = resolve_container_binary(env, binary)

    command_parts = [
        _container_path(binary),
        "-m", _container_path(model_path),
        "-c", str(context_size),
        "--port", str(port),
        "--host", CONTAINER_BIND_HOST,
    ]
    if advanced_flags.strip():
        command_parts.append(advanced_flags.strip())
    sanitized_custom_flags = strip_user_metrics_flag(custom_flags)
    if sanitized_custom_flags != custom_flags.strip():
        on_log("[METRICS] Ignoring custom --metrics; ModelScope enables it automatically")
    if sanitized_custom_flags:
        command_parts.append(sanitized_custom_flags)
    server_command = " ".join(command_parts)

    log_path = f"/tmp/modelscope_llama_server_{port}.log"
    pid_path = f"/tmp/modelscope_llama_server_{port}.pid"
    
    # Proxmox 8's pct exec (lxc-attach) aggressively destroys the temporary cgroup
    # when it exits, instantly SIGKILLing all background processes (even setsid).
    # We use systemd-run to launch it in a persistent system service cgroup.
    launch_sysd = (
        f"systemd-run --unit=modelscope_llama_server_{port} --property=Type=simple "
        f"/bin/bash -c 'echo $$ > {pid_path} && exec {server_command} > {shlex.quote(log_path)} 2>&1'"
    )
    on_log(f"[SERVER] Starting inside LXC {vmid} ({address}): {server_command}")
    
    result = env.execute(launch_sysd, timeout=20)
    if result.get("exit_code") == 0:
        time.sleep(0.5)
        pid_res = env.execute(f"cat {pid_path} 2>/dev/null", timeout=5)
        pid = pid_res.get("stdout", "").strip()
    else:
        # Fallback for alpine/non-systemd containers
        launch_fallback = f"setsid nohup {server_command} </dev/null > {shlex.quote(log_path)} 2>&1 & echo $!"
        result = env.execute(launch_fallback, timeout=20)
        stdout = (result.get("stdout") or "").strip()
        pid = stdout.splitlines()[-1].strip() if stdout else ""

    if not pid or not pid.isdigit():
        raise RuntimeError(
            f"Failed to start llama-server in LXC {vmid}: "
            f"{result.get('stderr') or result.get('stdout') or 'unknown error'}"
        )

    base_url = f"http://{address}:{port}"
    handle = PCTManagedServer(env, vmid, pid, log_path, base_url)

    deadline = time.time() + ready_timeout
    while time.time() < deadline:
        if handle.poll() is not None:
            tail = handle.read_log_tail()
            if not tail.strip():
                tail = (
                    f"Launch stdout: {result.get('stdout', '')}\nLaunch stderr: {result.get('stderr', '')}\n\n"
                    f"Hint: If stdout/stderr are empty and no log was produced, the llama-server binary "
                    f"might be crashing instantly (e.g. SIGILL due to unsupported CPU instructions like AVX2), "
                    f"or it might not be fully downloaded."
                )
            raise RuntimeError(
                f"llama-server exited immediately in LXC {vmid}"
                + (f": {tail[-800:]}" if tail else "")
            )
        try:
            response = requests.get(f"{base_url}/health", timeout=1.0)
            if response.status_code == 200:
                on_log(f"[SERVER] Ready in LXC {vmid} at {base_url}")
                return handle
        except requests.exceptions.RequestException:
            pass
        time.sleep(1)

    # Don't orphan a container process holding the port and the model in RAM.
    handle.terminate()
    raise RuntimeError(
        f"llama-server in LXC {vmid} did not become ready after {int(ready_timeout)}s at {base_url}"
    )
