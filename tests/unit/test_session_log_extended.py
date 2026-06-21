"""
Extended tests for core/session_log.py covering the SessionRepository class
and uncovered paths in SessionLog.
"""
from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest

from core.session_log import (
    SessionLog,
    SessionRepository,
    default_sessions_dir,
)


class TestSessionLogIndexParam:
    def test_save_telemetry_with_index(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        tel = {"run_model": "m", "total_latency": 1.0}
        sl.save_telemetry(tel, index=0)
        assert (sl.session_dir / "telemetry_0.json").exists()

    def test_save_telemetry_index_none_uses_default(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        tel = {"run_model": "m"}
        sl.save_telemetry(tel)
        assert (sl.session_dir / "telemetry.json").exists()

    def test_save_telemetry_index_1(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.save_telemetry({"run_model": "m"}, index=1)
        assert (sl.session_dir / "telemetry_1.json").exists()

    def test_save_telemetry_strips_caf_credentials(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        tel = {
            "caf_config": {
                "scope": "Narrow",
                "urgency": "Speed",
                "target_credentials": ["user:password"],
            }
        }
        sl.save_telemetry(tel)
        data = json.loads((sl.session_dir / "telemetry.json").read_text())
        assert "target_credentials" not in data.get("caf_config", {})
        assert data["caf_config"]["scope"] == "Narrow"

    def test_save_config_strips_sensitive(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        cfg = {
            "backend_type": "llama.cpp",
            "target_ssh_password": "secret",
            "ssh_key_path": "/path/to/key",
            "judge_api_key": "sk-xxx",
        }
        sl.save_config(cfg)
        data = json.loads((sl.session_dir / "config.json").read_text())
        assert "target_ssh_password" not in data
        assert "ssh_key_path" not in data
        assert "judge_api_key" not in data
        assert data["backend_type"] == "llama.cpp"

    def test_lazy_dir_creation(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        # Directory should not exist before any write
        assert not sl.session_dir.exists()
        sl.log("first message")
        assert sl.session_dir.exists()

    def test_session_dir_property(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        assert isinstance(sl.session_dir, pathlib.Path)

    def test_close_is_noop(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.close()  # should not raise


class TestDefaultSessionsDir:
    def test_returns_path(self):
        result = default_sessions_dir()
        assert isinstance(result, pathlib.Path)
        assert "sessions" in str(result)


class TestSessionRepository:
    def test_list_sessions_empty_dir(self, tmp_path):
        repo = SessionRepository(base_dir=tmp_path)
        assert repo.list_sessions() == []

    def test_list_sessions_missing_dir(self, tmp_path):
        repo = SessionRepository(base_dir=tmp_path / "nonexistent")
        assert repo.list_sessions() == []

    def test_list_sessions_returns_dirs_newest_first(self, tmp_path):
        for name in ["2026-01-01_a", "2026-06-01_b", "2025-12-01_c"]:
            (tmp_path / name).mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        sessions = repo.list_sessions()
        names = [s.name for s in sessions]
        assert names[0] > names[-1]  # newest first

    def test_list_sessions_with_limit(self, tmp_path):
        for i in range(5):
            (tmp_path / f"2026-0{i+1}-01_x").mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        sessions = repo.list_sessions(limit=3)
        assert len(sessions) == 3

    def test_find_session_exact_match(self, tmp_path):
        d = tmp_path / "2026-06-18_12-00-00_abc12345"
        d.mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.find_session("2026-06-18_12-00-00_abc12345")
        assert result == d

    def test_find_session_by_suffix(self, tmp_path):
        d = tmp_path / "2026-06-18_12-00-00_abc12345"
        d.mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.find_session("abc12345")
        assert result == d

    def test_find_session_not_found(self, tmp_path):
        (tmp_path / "2026-06-18_12-00-00_xyz99999").mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.find_session("abc12345")
        assert result is None

    def test_find_session_missing_base_dir(self, tmp_path):
        repo = SessionRepository(base_dir=tmp_path / "noexist")
        result = repo.find_session("abc12345")
        assert result is None

    def test_read_telemetry_telemetry_json(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        tel = {"run_model": "m"}
        (d / "telemetry.json").write_text(json.dumps(tel))
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.read_telemetry(d)
        assert result["run_model"] == "m"

    def test_read_telemetry_fallback_to_0(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        tel = {"run_model": "caf"}
        (d / "telemetry_0.json").write_text(json.dumps(tel))
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.read_telemetry(d)
        assert result["run_model"] == "caf"

    def test_read_telemetry_empty_on_missing(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.read_telemetry(d)
        assert result == {}

    def test_read_telemetry_empty_on_bad_json(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        (d / "telemetry.json").write_text("NOT JSON")
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.read_telemetry(d)
        assert result == {}

    def test_telemetry_files_single(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        (d / "telemetry.json").touch()
        repo = SessionRepository(base_dir=tmp_path)
        files = repo.telemetry_files(d)
        assert len(files) == 1
        assert files[0].name == "telemetry.json"

    def test_telemetry_files_multi_prompt(self, tmp_path):
        d = tmp_path / "session"
        d.mkdir()
        for i in range(3):
            (d / f"telemetry_{i}.json").touch()
        repo = SessionRepository(base_dir=tmp_path)
        files = repo.telemetry_files(d)
        assert len(files) == 3

    def test_get_session_not_found(self, tmp_path):
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.get_session("nonexistent")
        assert result is None

    def test_get_session_found(self, tmp_path):
        d = tmp_path / "2026-06-18_12-00-00_abc12345"
        d.mkdir()
        tel = {"run_model": "test"}
        (d / "telemetry.json").write_text(json.dumps(tel))
        repo = SessionRepository(base_dir=tmp_path)
        result = repo.get_session("abc12345")
        assert result is not None
        assert result["telemetry"]["run_model"] == "test"
        assert result["dir"] == d

    def test_base_dir_property(self, tmp_path):
        repo = SessionRepository(base_dir=tmp_path)
        assert repo.base_dir == tmp_path

    def test_default_base_dir(self):
        repo = SessionRepository()
        assert isinstance(repo.base_dir, pathlib.Path)
