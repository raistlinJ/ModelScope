"""Persistent session logging for ModelScope evaluation runs.

Each call to ``run_evaluation()`` (or ``run_caf_ssh_evaluation()``) can be
wrapped with a ``SessionLog`` instance.  The instance creates a timestamped
directory under ``base_dir`` (default ``ModelScope/logs/sessions/``) and writes:

* ``run.log``           — all ``on_log()`` messages, timestamped, one per line
* ``telemetry.json``    — the dict returned by ``run_evaluation()``
* ``config.json``       — the config dict passed into ``run_evaluation()``
                          (sensitive fields stripped)

The session directory is created lazily (on first write) so failed runs that
produce no output do not litter the sessions folder with empty directories.

Thread safety: a single ``threading.Lock`` serialises all writes.  ``log()``
swallows exceptions so session logging can never break an evaluation run.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib as _pl
import threading
import uuid
from pathlib import Path
from typing import Any

# Derive the repo root relative to this file (core/ lives one level below root).
_REPO_ROOT = _pl.Path(__file__).resolve().parent.parent
_DEFAULT_BASE: Path = _REPO_ROOT / "logs" / "sessions"

_LOGGER = logging.getLogger("modelscope")

# Keys that must not appear in the persisted config snapshot.
_SENSITIVE_KEYS = frozenset({
    "target_ssh_password",
    "target_ssh_key_path",
    "ssh_password",
    "ssh_key_path",
    "judge_api_key",
})


class SessionLog:
    """Manages the per-run session directory and log/JSON artefacts.

    Args:
        base_dir: Root directory that contains all session sub-directories.
                  Defaults to ``ModelScope/logs/sessions/`` (repo-relative).
    """

    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        if base_dir is None:
            base_dir = _DEFAULT_BASE
        self._base_dir = Path(base_dir)

        # Generate a human-readable + unique directory name at construction
        # time, but do NOT create it on disk yet (lazy creation on first write).
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        run_id = uuid.uuid4().hex[:8]
        self._session_dir = self._base_dir / f"{ts}_{run_id}"
        self._run_log_path = self._session_dir / "run.log"

        self._lock = threading.Lock()
        self._dir_created = False

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def session_dir(self) -> Path:
        """The session directory path (may not yet exist on disk)."""
        return self._session_dir

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        """Create the session directory once, log the path to stdout."""
        if self._dir_created:
            return
        self._session_dir.mkdir(parents=True, exist_ok=True)
        self._dir_created = True
        _LOGGER.info("[SESSION] Log directory: %s", self._session_dir)

    # ── Public API ─────────────────────────────────────────────────────────────

    def log(self, msg: str) -> None:
        """Append a timestamped line to ``run.log``.

        Exceptions are swallowed — session logging must never interrupt a run.
        """
        try:
            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            line = f"[{ts}] {msg}\n"
            with self._lock:
                self._ensure_dir()
                with open(self._run_log_path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except Exception:
            pass

    def save_telemetry(self, telemetry: dict[str, Any], index: int | None = None) -> None:
        """Persist the telemetry dict returned by ``run_evaluation()``.

        Args:
            telemetry: The dict to serialise.
            index:     When provided, writes ``telemetry_<index>.json`` instead
                       of ``telemetry.json``.  Used by the CAF tab's multi-prompt
                       loop so each prompt gets its own artefact.

        ``caf_config.target_credentials`` is stripped before writing — it may
        contain plaintext credentials entered by the user.
        """
        try:
            safe = dict(telemetry)
            if "caf_config" in safe and isinstance(safe["caf_config"], dict):
                caf_cfg = dict(safe["caf_config"])
                caf_cfg.pop("target_credentials", None)
                safe["caf_config"] = caf_cfg
            filename = f"telemetry_{index}.json" if index is not None else "telemetry.json"
            with self._lock:
                self._ensure_dir()
                dest = self._session_dir / filename
                dest.write_text(
                    json.dumps(safe, indent=2, default=str),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def save_config(self, config: dict[str, Any]) -> None:
        """Persist a sanitised copy of the run config to ``config.json``.

        Sensitive keys (passwords, key paths) are stripped before writing.
        """
        try:
            safe = {k: v for k, v in config.items() if k not in _SENSITIVE_KEYS}
            with self._lock:
                self._ensure_dir()
                dest = self._session_dir / "config.json"
                dest.write_text(
                    json.dumps(safe, indent=2, default=str),
                    encoding="utf-8",
                )
        except Exception:
            pass

    def close(self) -> None:
        """No-op finaliser — provided for symmetry with resource managers."""
        pass


# ── Reading side: SessionRepository ────────────────────────────────────────────


def default_sessions_dir() -> Path:
    """The directory ``SessionLog`` writes to and readers should read from.

    Single source of truth for the sessions root: ``ModelScope/logs/sessions/``.
    Both the CLI ``sessions`` subcommand and the dashboard's "Recent Sessions"
    viewer consume this so they can never drift apart again (they previously
    pointed at two different directories, and the dashboard silently showed
    nothing because it read ``~/.modelscope/sessions`` which is never written).
    """
    return _DEFAULT_BASE


class SessionRepository:
    """Read-only data access over the session log directory.

    Returns plain data (paths, dicts) and contains no presentation logic — the
    CLI formats it as a table, the dashboard renders it with Streamlit. Keeping
    directory discovery and telemetry parsing here means there is exactly one
    implementation of "how a session is stored on disk".
    """

    #: Telemetry filenames to try, in order. CAF multi-prompt runs write indexed
    #: files (telemetry_0.json …); single runs write telemetry.json.
    _TELEMETRY_NAMES = ("telemetry.json", "telemetry_0.json")

    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir is not None else default_sessions_dir()

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def list_sessions(self, limit: int | None = None) -> list[Path]:
        """Return session directories, most recent first.

        Timestamp-prefixed names sort chronologically, so a reverse name sort is
        equivalent to newest-first without stat-ing every directory.
        """
        if not self._base_dir.exists():
            return []
        dirs = sorted(
            (d for d in self._base_dir.iterdir() if d.is_dir()),
            key=lambda d: d.name,
            reverse=True,
        )
        return dirs[:limit] if limit is not None else dirs

    def find_session(self, session_id: str) -> Path | None:
        """Resolve a session by full directory name or trailing 8-char run ID."""
        if not self._base_dir.exists():
            return None
        exact = self._base_dir / session_id
        if exact.is_dir():
            return exact
        for entry in sorted(self._base_dir.iterdir()):
            if entry.is_dir() and entry.name.endswith(session_id):
                return entry
        return None

    def read_telemetry(self, session_dir: str | os.PathLike) -> dict[str, Any]:
        """Read a session's telemetry, trying telemetry.json then telemetry_0.json."""
        session_dir = Path(session_dir)
        for name in self._TELEMETRY_NAMES:
            path = session_dir / name
            if path.exists():
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
        return {}

    def telemetry_files(self, session_dir: str | os.PathLike) -> list[Path]:
        """Return all telemetry artefacts for a session (single or multi-prompt)."""
        session_dir = Path(session_dir)
        single = session_dir / "telemetry.json"
        if single.exists():
            return [single]
        return sorted(session_dir.glob("telemetry_*.json"))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return ``{"dir", "telemetry"}`` for a session, or None if not found."""
        session_dir = self.find_session(session_id)
        if session_dir is None:
            return None
        return {"dir": session_dir, "telemetry": self.read_telemetry(session_dir)}

    def sessions_for_project(self, project_id: str, limit: int | None = None) -> list[Path]:
        """Return session dirs whose ``config.json`` has ``active_project_id == project_id``.

        Sessions with no config.json or a corrupt one are skipped.  ``limit``
        caps the result to the N most recent dirs (already sorted newest-first).
        """
        if not project_id or not self._base_dir.exists():
            return []
        out: list[Path] = []
        for d in self.list_sessions(limit=None):
            if limit is not None and len(out) >= limit:
                break
            cfg_path = d / "config.json"
            if not cfg_path.exists():
                continue
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if cfg.get("active_project_id") == project_id:
                out.append(d)
        return out

    def history_for_project(self, project_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """Return a list of telemetry dicts for *project_id*, newest first.

        Mirrors the shape of ``st.session_state[f"run_history_{pid}"]`` so
        callers can drop the result in directly.  Sessions without a
        ``telemetry.json`` (or with a corrupt one) are skipped silently.  When
        ``limit`` is set, only the N most recent sessions are loaded.
        """
        out: list[dict[str, Any]] = []
        for d in self.sessions_for_project(project_id, limit=limit):
            tel = self.read_telemetry(d)
            if tel:
                out.append(tel)
        return out
