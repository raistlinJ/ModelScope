"""Unit tests for core.session_log.SessionLog."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from core.session_log import SessionLog, _SENSITIVE_KEYS


class TestSessionLogBasics:
    """Core creation and attribute behaviour."""

    def test_session_dir_is_under_base_dir(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        assert str(sl.session_dir).startswith(str(tmp_path))

    def test_session_dir_not_created_on_init(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        assert not sl.session_dir.exists()

    def test_default_base_dir_is_home(self):
        """Check the default base dir resolves to ~/.modelscope/sessions.

        We instantiate but do NOT write, so no actual directory is created.
        """
        sl = SessionLog()
        assert sl.session_dir.parts[-3] == ".modelscope"
        assert sl.session_dir.parts[-2] == "sessions"

    def test_close_is_noop(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.close()  # must not raise


class TestSessionLogWrite:
    """log(), save_telemetry(), save_config() correctness."""

    def test_log_creates_directory(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.log("hello")
        assert sl.session_dir.exists()

    def test_log_writes_to_run_log(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.log("test message")
        log_file = sl.session_dir / "run.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "test message" in content

    def test_log_appends_multiple_lines(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.log("line one")
        sl.log("line two")
        content = (sl.session_dir / "run.log").read_text()
        assert "line one" in content
        assert "line two" in content

    def test_log_timestamps_each_line(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.log("timestamped")
        content = (sl.session_dir / "run.log").read_text()
        # Timestamps look like [2024-01-15 10:30:00.123]
        assert "[" in content and "]" in content

    def test_save_telemetry_default_filename(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.save_telemetry({"validation_passed": True, "total_tokens": 100})
        dest = sl.session_dir / "telemetry.json"
        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["validation_passed"] is True
        assert data["total_tokens"] == 100

    def test_save_telemetry_with_index(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.save_telemetry({"prompt": "first"}, index=0)
        sl.save_telemetry({"prompt": "second"}, index=1)
        assert (sl.session_dir / "telemetry_0.json").exists()
        assert (sl.session_dir / "telemetry_1.json").exists()
        data0 = json.loads((sl.session_dir / "telemetry_0.json").read_text())
        assert data0["prompt"] == "first"

    def test_save_telemetry_creates_directory(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        sl.save_telemetry({"x": 1})
        assert sl.session_dir.exists()

    def test_save_config_creates_config_json(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        cfg = {"model": "llama", "llm_url": "http://localhost:8080"}
        sl.save_config(cfg)
        dest = sl.session_dir / "config.json"
        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["model"] == "llama"
        assert data["llm_url"] == "http://localhost:8080"

    def test_save_config_strips_sensitive_keys(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        cfg = {
            "model": "llama",
            "target_ssh_password": "s3cr3t",
            "target_ssh_key_path": "/home/user/.ssh/id_rsa",
            "ssh_password": "also_secret",
            "ssh_key_path": "/tmp/key",
        }
        sl.save_config(cfg)
        data = json.loads((sl.session_dir / "config.json").read_text())
        assert "model" in data
        for key in _SENSITIVE_KEYS:
            assert key not in data

    def test_save_config_does_not_modify_original_dict(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        cfg = {"model": "llama", "target_ssh_password": "s3cr3t"}
        sl.save_config(cfg)
        assert "target_ssh_password" in cfg  # original untouched

    def test_telemetry_uses_default_str_for_unserializable(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        # cancel_requested_ref is a list in real configs — trivially serializable
        # but objects that are not JSON-native should fall back to str().
        class Opaque:
            def __repr__(self):
                return "<Opaque>"
        sl.save_telemetry({"obj": Opaque()})
        dest = sl.session_dir / "telemetry.json"
        data = json.loads(dest.read_text())
        assert "<Opaque>" in data["obj"]


class TestSessionLogSafety:
    """Exception-safety: logging must never break a run."""

    def test_log_swallows_write_errors(self, tmp_path, monkeypatch):
        """If the write fails, log() must not propagate the exception."""
        sl = SessionLog(base_dir=tmp_path)

        def _bad_open(*a, **kw):
            raise OSError("disk full")

        monkeypatch.setattr("builtins.open", _bad_open)
        # Should not raise
        sl.log("this will fail silently")

    def test_save_telemetry_swallows_errors(self, tmp_path, monkeypatch):
        sl = SessionLog(base_dir=tmp_path)

        def _bad_write(data, encoding=None):
            raise OSError("no space")

        sl._ensure_dir()  # create dir first so Path.write_text is the target
        monkeypatch.setattr(Path, "write_text", _bad_write)
        sl.save_telemetry({"x": 1})  # must not raise

    def test_save_config_swallows_errors(self, tmp_path, monkeypatch):
        sl = SessionLog(base_dir=tmp_path)
        sl._ensure_dir()
        monkeypatch.setattr(Path, "write_text", lambda *a, **kw: (_ for _ in ()).throw(OSError("err")))
        sl.save_config({"k": "v"})  # must not raise


class TestSessionLogThreadSafety:
    """Concurrent log() calls should not interleave or corrupt run.log."""

    def test_concurrent_log_calls_produce_all_lines(self, tmp_path):
        sl = SessionLog(base_dir=tmp_path)
        n_threads = 10
        n_writes  = 20
        errors    = []

        def _writer(thread_id: int) -> None:
            for i in range(n_writes):
                try:
                    sl.log(f"thread={thread_id} i={i}")
                except Exception as exc:
                    errors.append(exc)

        threads = [threading.Thread(target=_writer, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected exceptions: {errors}"
        content = (sl.session_dir / "run.log").read_text()
        line_count = content.count("\n")
        assert line_count == n_threads * n_writes


class TestSessionLogDirectoryNaming:
    """The session directory name should be unique across back-to-back calls."""

    def test_two_instances_have_different_session_dirs(self, tmp_path):
        sl1 = SessionLog(base_dir=tmp_path)
        # Sleep 1 ms so the timestamp-based name can differ.
        time.sleep(0.001)
        sl2 = SessionLog(base_dir=tmp_path)
        # Even if timestamps collide, the UUID suffix prevents collision.
        assert sl1.session_dir != sl2.session_dir
