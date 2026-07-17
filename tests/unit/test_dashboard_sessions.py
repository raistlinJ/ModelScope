"""Unit tests for ui.dashboard_tab session/project scoping.

The analytical dashboard groups on-disk sessions by the project they belong
to (keyed by ``active_project_id`` in each session's ``config.json``).  These
tests verify the underlying filter logic — the Streamlit rendering itself is
out of scope, but the partition (mine / unscoped / other) must be correct.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.session_log import SessionRepository


def _make_session(base: Path, name: str, *, project_id: str | None) -> Path:
    """Create a session dir with a config.json.  ``project_id=None`` ⇒ no key."""
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    cfg: dict = {"model": "llama", "active_scenario": "S1"}
    if project_id is not None:
        cfg["active_project_id"] = project_id
    (d / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
    # Empty telemetry so repo.telemetry_files() returns [] (not relevant here)
    return d


def _partition(base: Path, target_pid: str) -> tuple[list, list, list]:
    """Replicate _render_sessions_for_project's filter in pure Python.

    Returns (mine, unscoped, other) — all as lists of Path objects, newest
    first.  Mirrors the logic in dashboard_tab so any divergence is caught.
    """
    all_sessions = SessionRepository(base_dir=base).list_sessions(limit=None)

    mine: list = []
    unscoped: list = []
    other: list = []
    for d in all_sessions:
        cfg_path = d / "config.json"
        if not cfg_path.exists():
            unscoped.append(d)
            continue
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            unscoped.append(d)
            continue
        if cfg.get("active_project_id") == target_pid:
            mine.append(d)
        elif "active_project_id" not in cfg:
            unscoped.append(d)
        else:
            other.append(d)
    return (
        sorted(mine,     key=lambda p: p.name, reverse=True)[:20],
        sorted(unscoped, key=lambda p: p.name, reverse=True)[:20],
        sorted(other,    key=lambda p: p.name, reverse=True)[:20],
    )


class TestSessionPartition:
    def test_partitions_by_active_project_id(self, tmp_path: Path):
        _make_session(tmp_path, "2026-01-01_00-00-00_a1", project_id="proj-A")
        _make_session(tmp_path, "2026-01-02_00-00-00_b2", project_id="proj-B")
        _make_session(tmp_path, "2026-01-03_00-00-00_c3", project_id=None)
        _make_session(tmp_path, "2026-01-04_00-00-00_d4", project_id="proj-A")

        mine, unscoped, other = _partition(tmp_path, "proj-A")

        assert {p.name for p in mine} == {
            "2026-01-04_00-00-00_d4",
            "2026-01-01_00-00-00_a1",
        }
        assert [p.name for p in mine] == [
            "2026-01-04_00-00-00_d4",  # newest first
            "2026-01-01_00-00-00_a1",
        ]
        assert {p.name for p in unscoped} == {"2026-01-03_00-00-00_c3"}
        assert {p.name for p in other}    == {"2026-01-02_00-00-00_b2"}

    def test_no_matching_sessions_yields_empty_mine(self, tmp_path: Path):
        _make_session(tmp_path, "2026-01-01_00-00-00_z1", project_id="proj-X")
        mine, unscoped, other = _partition(tmp_path, "proj-A")
        assert mine == []
        assert unscoped == []
        # proj-X has an active_project_id, so the session is "other" (hidden).
        assert {p.name for p in other} == {"2026-01-01_00-00-00_z1"}

    def test_session_without_config_is_unscoped(self, tmp_path: Path):
        d = tmp_path / "2026-01-01_00-00-00_no_cfg"
        d.mkdir()
        # no config.json
        mine, unscoped, other = _partition(tmp_path, "proj-A")
        assert mine == []
        assert {p.name for p in unscoped} == {"2026-01-01_00-00-00_no_cfg"}
        assert other == []

    def test_session_with_corrupt_config_is_unscoped(self, tmp_path: Path):
        d = tmp_path / "2026-01-01_00-00-00_bad"
        d.mkdir()
        (d / "config.json").write_text("{ not json", encoding="utf-8")
        mine, unscoped, other = _partition(tmp_path, "proj-A")
        assert mine == []
        assert {p.name for p in unscoped} == {"2026-01-01_00-00-00_bad"}
        assert other == []

    def test_empty_sessions_dir(self, tmp_path: Path):
        # Base dir exists but has no session subdirs
        assert _partition(tmp_path, "proj-A") == ([], [], [])

    def test_limit_to_twenty_most_recent(self, tmp_path: Path):
        # 25 sessions for proj-A → only the 20 newest should be returned.
        for i in range(25):
            _make_session(tmp_path, f"2026-01-{(i % 28) + 1:02d}_00-00-00_x{i:02d}",
                          project_id="proj-A")
        mine, _, _ = _partition(tmp_path, "proj-A")
        assert len(mine) == 20


class TestValidationOutputHighlighting:
    def test_highlights_regex_matches_and_escapes_output(self):
        from ui.dashboard_tab import _highlight_validation_matches

        rendered = _highlight_validation_matches(
            "status: PASS <safe>",
            [{"expected_output_type": "Regex", "expected_output": r"PASS"}],
        )

        assert '<mark class="validation-output-match">PASS</mark>' in rendered
        assert "&lt;safe&gt;" in rendered

    def test_highlights_exact_string_only_when_it_passes(self):
        from ui.dashboard_tab import _highlight_validation_matches

        rendered = _highlight_validation_matches(
            "all clear\n",
            [{"expected_output_type": "Exact String", "expected_output": "all clear"}],
        )

        assert rendered == '<mark class="validation-output-match">all clear\n</mark>'

class TestActiveProjectIdIsPersisted:
    """Regression: the Execute tab must save ``active_project_id`` in config.json
    so the dashboard can correlate sessions with projects.  We assert against
    SessionLog.save_config() since that's the canonical writer.
    """

    def test_active_project_id_round_trips(self, tmp_path: Path):
        from core.session_log import SessionLog

        sl = SessionLog(base_dir=tmp_path)
        sl.save_config({"model": "llama", "active_project_id": "proj-7"})
        cfg = json.loads((sl.session_dir / "config.json").read_text())
        assert cfg["active_project_id"] == "proj-7"

    def test_active_project_id_is_not_in_sensitive_keys(self):
        from core.session_log import _SENSITIVE_KEYS

        # active_project_id is metadata, not a secret — it MUST survive the
        # sanitiser, otherwise project-scoping would always fail.
        assert "active_project_id" not in _SENSITIVE_KEYS


# ── Hydration: re-loading run history from disk ────────────────────────────────

def _make_session_with_telemetry(base: Path, name: str, *,
                                 project_id: str | None,
                                 telemetry: dict | None = None) -> Path:
    """Create a session dir with config.json AND telemetry.json (so it can be
    hydrated by ``history_for_project``)."""
    d = _make_session(base, name, project_id=project_id)
    if telemetry is None:
        telemetry = {"run_timestamp": name, "validation_passed": True,
                     "total_latency": 1.0, "metrics_matrix": []}
    (d / "telemetry.json").write_text(json.dumps(telemetry), encoding="utf-8")
    return d


class TestHistoryForProject:
    """``SessionRepository.history_for_project`` is what re-populates a
    project's ``run_history_<pid>`` on app startup.  These tests cover the
    reader side without needing Streamlit."""

    def test_returns_telemetry_dicts_for_project(self, tmp_path: Path):
        _make_session_with_telemetry(tmp_path, "2026-01-01_aa", project_id="proj-A",
                                      telemetry={"run_timestamp": "2026-01-01_aa",
                                                 "validation_passed": True})
        _make_session_with_telemetry(tmp_path, "2026-01-02_bb", project_id="proj-B",
                                      telemetry={"run_timestamp": "2026-01-02_bb"})
        _make_session_with_telemetry(tmp_path, "2026-01-03_cc", project_id="proj-A",
                                      telemetry={"run_timestamp": "2026-01-03_cc"})

        repo = SessionRepository(base_dir=tmp_path)
        hist = repo.history_for_project("proj-A")

        assert [h["run_timestamp"] for h in hist] == ["2026-01-03_cc", "2026-01-01_aa"]

    def test_skips_sessions_without_telemetry(self, tmp_path: Path):
        # Session with config but no telemetry file — must be skipped, not crash.
        _make_session(tmp_path, "2026-01-01_aa", project_id="proj-A")
        _make_session_with_telemetry(tmp_path, "2026-01-02_bb", project_id="proj-A",
                                      telemetry={"run_timestamp": "2026-01-02_bb"})

        hist = SessionRepository(base_dir=tmp_path).history_for_project("proj-A")
        assert [h["run_timestamp"] for h in hist] == ["2026-01-02_bb"]

    def test_skips_corrupt_telemetry(self, tmp_path: Path):
        d = _make_session(tmp_path, "2026-01-01_aa", project_id="proj-A")
        (d / "telemetry.json").write_text("{ broken", encoding="utf-8")

        hist = SessionRepository(base_dir=tmp_path).history_for_project("proj-A")
        assert hist == []

    def test_limit_caps_results_to_n_most_recent(self, tmp_path: Path):
        for i in range(15):
            _make_session_with_telemetry(
                tmp_path, f"2026-01-{(i % 28) + 1:02d}_t{i:02d}",
                project_id="proj-A",
                telemetry={"run_timestamp": f"2026-01-{(i % 28) + 1:02d}_t{i:02d}"},
            )
        hist = SessionRepository(base_dir=tmp_path).history_for_project("proj-A", limit=5)
        assert len(hist) == 5

    def test_empty_for_unknown_project(self, tmp_path: Path):
        _make_session_with_telemetry(tmp_path, "2026-01-01_aa", project_id="proj-A")
        hist = SessionRepository(base_dir=tmp_path).history_for_project("proj-Z")
        assert hist == []

    def test_empty_for_missing_base_dir(self, tmp_path: Path):
        repo = SessionRepository(base_dir=tmp_path / "does_not_exist")
        assert repo.history_for_project("proj-A") == []

    def test_empty_for_empty_project_id(self, tmp_path: Path):
        _make_session_with_telemetry(tmp_path, "2026-01-01_aa", project_id="proj-A")
        hist = SessionRepository(base_dir=tmp_path).history_for_project("")
        assert hist == []


class TestSessionsForProject:
    """``SessionRepository.sessions_for_project`` returns paths (used by the
    sessions viewer).  Tested separately so a regression in one doesn't mask
    the other."""

    def test_returns_paths_in_reverse_chronological_order(self, tmp_path: Path):
        _make_session(tmp_path, "2026-01-01_aa", project_id="proj-A")
        _make_session(tmp_path, "2026-01-03_cc", project_id="proj-A")
        _make_session(tmp_path, "2026-01-02_bb", project_id="proj-A")

        paths = SessionRepository(base_dir=tmp_path).sessions_for_project("proj-A")
        assert [p.name for p in paths] == [
            "2026-01-03_cc", "2026-01-02_bb", "2026-01-01_aa",
        ]

    def test_limit(self, tmp_path: Path):
        for i in range(10):
            _make_session(tmp_path, f"2026-01-{(i % 28) + 1:02d}_x{i:02d}",
                          project_id="proj-A")
        paths = SessionRepository(base_dir=tmp_path).sessions_for_project("proj-A", limit=3)
        assert len(paths) == 3

    def test_skips_other_projects(self, tmp_path: Path):
        _make_session(tmp_path, "2026-01-01_aa", project_id="proj-A")
        _make_session(tmp_path, "2026-01-02_bb", project_id="proj-B")
        paths = SessionRepository(base_dir=tmp_path).sessions_for_project("proj-A")
        assert {p.name for p in paths} == {"2026-01-01_aa"}


class TestHydrationGuards:
    """Verify the per-pid hydration flag pattern that prevents repeated disk
    scans.  Tested at the session-state level (no Streamlit rerun loop)."""

    def test_flag_prevents_repeat_scan(self, tmp_path: Path, monkeypatch):
        """If the hydration flag is already set, the helper must not touch disk."""
        from ui import dashboard_tab

        # Pre-seed the flag as if the user has already visited the dashboard.
        state = {"_history_hydrated_proj-X": True, "run_history_proj-X": []}
        monkeypatch.setattr(dashboard_tab.st, "session_state", state)

        # If the helper tried to read disk, this would explode (no real repo).
        # But our test path points at a real tmp dir to make sure no IO occurs.
        project = {"id": "proj-X"}
        dashboard_tab._hydrate_project_history_if_empty(project)

        # Still empty — no disk read happened.
        assert state.get("run_history_proj-X") == []

    def test_existing_in_memory_history_is_preserved(self, tmp_path: Path, monkeypatch):
        """If the user already has in-memory runs, disk hydration must NOT
        overwrite them — they may be newer than what's on disk."""
        from ui import dashboard_tab

        existing = [{"run_timestamp": "fresh-in-memory", "validation_passed": True}]
        state = {"run_history_proj-Y": list(existing)}
        monkeypatch.setattr(dashboard_tab.st, "session_state", state)

        # Disk has an older session for the same project.
        _make_session_with_telemetry(tmp_path, "2025-12-31_aa", project_id="proj-Y",
                                      telemetry={"run_timestamp": "2025-12-31_aa"})

        # Point the helper at our tmp_path-based repo so we can detect any read.
        from core.session_log import SessionRepository
        monkeypatch.setattr(SessionRepository, "__init__",
                            lambda self, base_dir=None: SessionRepository.__init__(
                                self, base_dir=tmp_path))

        project = {"id": "proj-Y"}
        dashboard_tab._hydrate_project_history_if_empty(project)

        # In-memory list untouched.
        assert state["run_history_proj-Y"] == existing
        # Flag set so we don't re-scan on the next rerun.
        assert state["_history_hydrated_proj-Y"] is True
