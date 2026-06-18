"""Persistent session logging for ModelScope evaluation runs.

Each call to ``run_evaluation()`` (or ``run_caf_ssh_evaluation()``) can be
wrapped with a ``SessionLog`` instance.  The instance creates a timestamped
directory under ``base_dir`` (default ``~/.modelscope/sessions``) and writes:

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
import threading
import uuid
from pathlib import Path
from typing import Any

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
                  Defaults to ``~/.modelscope/sessions``.
    """

    def __init__(self, base_dir: str | os.PathLike | None = None) -> None:
        if base_dir is None:
            base_dir = Path.home() / ".modelscope" / "sessions"
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
        """
        try:
            filename = f"telemetry_{index}.json" if index is not None else "telemetry.json"
            with self._lock:
                self._ensure_dir()
                dest = self._session_dir / filename
                dest.write_text(
                    json.dumps(telemetry, indent=2, default=str),
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
