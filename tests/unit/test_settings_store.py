"""
Unit tests for core/settings_store.py.

Covers:
  - save_settings: persists correct keys, strips sensitive keys, handles non-serializable values
  - load_settings: reads and filters, returns empty on errors
  - PERSIST_KEYS / _SENSITIVE_KEYS definitions
"""
from __future__ import annotations

import json
import pathlib

import pytest

from core.settings_store import save_settings, load_settings, PERSIST_KEYS, _SENSITIVE_KEYS


class TestSaveSettings:
    def test_saves_known_key(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {"backend_type": "ollama", "llm_url": "http://localhost:11434"}
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["backend_type"] == "ollama"

    def test_strips_sensitive_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {
            "backend_type": "llama.cpp",
            "target_ssh_password": "supersecret",
            "judge_api_key": "sk-abc123",
            "target_ssh_key_path": "/path/to/key",
        }
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert "target_ssh_password" not in data
        assert "judge_api_key" not in data
        assert "target_ssh_key_path" not in data

    def test_ignores_unknown_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {"unknown_key_xyz": "value", "backend_type": "ollama"}
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert "unknown_key_xyz" not in data

    def test_ignores_non_serializable_values(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {"backend_type": "ollama", "mcp_url": object()}
        # Should not raise; non-serializable value silently skipped
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert "mcp_url" not in data

    def test_creates_parent_directory(self, tmp_path, monkeypatch):
        nested = tmp_path / "a" / "b" / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", nested)
        save_settings({"backend_type": "ollama"})
        assert nested.exists()

    def test_swallows_io_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        # Make parent non-writable? Instead, patch Path.write_text to raise.
        def _crash(*a, **kw):
            raise PermissionError("denied")
        monkeypatch.setattr(pathlib.Path, "write_text", _crash)
        # Should not raise
        save_settings({"backend_type": "ollama"})

    def test_saves_multiple_persist_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {
            "backend_type": "ollama",
            "llm_url": "http://localhost:11434",
            "context_size": 4096,
            "tool_focus": "file_creator",
        }
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert data["context_size"] == 4096
        assert data["tool_focus"] == "file_creator"


class TestSaveSettingsProjectMerge:
    """A stale/parallel Streamlit session must not silently delete a project
    another session created or is still holding, but a project a session
    deliberately deleted must not get resurrected.

    Regression coverage for a real incident: driving the app with several
    independent browser sessions caused one session's save to overwrite the
    on-disk projects list and delete a project the other session had.
    """

    def test_session_unaware_of_a_disk_project_does_not_delete_it(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        # Disk already has two projects (as if another session created "b").
        path.write_text(json.dumps({
            "projects": [
                {"id": "a", "name": "A", "type": "bash_bot", "config": {}},
                {"id": "b", "name": "B", "type": "bash_bot", "config": {}},
            ],
        }))
        # This session only ever knew about "a" — it loaded before "b" existed.
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
                 "_known_project_ids_at_load": ["a"]}
        save_settings(state)
        data = json.loads(path.read_text())
        ids = {p["id"] for p in data["projects"]}
        assert ids == {"a", "b"}, "a project unknown to this session was deleted on save"

    def test_session_that_deliberately_deleted_a_project_is_respected(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        path.write_text(json.dumps({
            "projects": [
                {"id": "a", "name": "A", "type": "bash_bot", "config": {}},
                {"id": "b", "name": "B", "type": "bash_bot", "config": {}},
            ],
        }))
        # This session saw both "a" and "b" at load, then the user deleted "b".
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
                 "_known_project_ids_at_load": ["a", "b"]}
        save_settings(state)
        data = json.loads(path.read_text())
        ids = {p["id"] for p in data["projects"]}
        assert ids == {"a"}, "a project this session explicitly deleted was resurrected"

    def test_edited_project_uses_this_sessions_version(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        path.write_text(json.dumps({
            "projects": [{"id": "a", "name": "Old Name", "type": "bash_bot", "config": {}}],
        }))
        state = {"projects": [{"id": "a", "name": "New Name", "type": "bash_bot", "config": {}}],
                 "_known_project_ids_at_load": ["a"]}
        save_settings(state)
        data = json.loads(path.read_text())
        assert data["projects"][0]["name"] == "New Name"

    def test_no_known_ids_baseline_falls_back_to_current_disk_state(self, tmp_path, monkeypatch):
        """No _known_project_ids_at_load recorded (e.g. a non-Streamlit
        caller) must not spuriously resurrect anything — the safe fallback
        assumes the session saw whatever is currently on disk."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        path.write_text(json.dumps({
            "projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
        }))
        state = {"projects": []}  # no baseline key at all
        save_settings(state)
        data = json.loads(path.read_text())
        assert data["projects"] == []

    def test_first_ever_save_with_no_disk_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}]}
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert [p["id"] for p in data["projects"]] == ["a"]

    def test_one_non_serializable_project_does_not_drop_the_others(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        state = {
            "projects": [
                {"id": "a", "name": "A", "type": "bash_bot", "config": {}},
                {"id": "bad", "name": "Bad", "type": "bash_bot", "config": {"oops": object()}},
                {"id": "c", "name": "C", "type": "bash_bot", "config": {}},
            ],
            "_known_project_ids_at_load": ["a", "bad", "c"],
        }
        save_settings(state)
        data = json.loads(path.read_text())
        ids = {p["id"] for p in data["projects"]}
        assert ids == {"a", "c"}, "a single bad project must not wipe out the rest"


class TestLoadSettings:
    def test_returns_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "nonexistent.json")
        result = load_settings()
        assert result == {}

    def test_returns_correct_data(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"backend_type": "ollama", "context_size": 8192}))
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        result = load_settings()
        assert result["backend_type"] == "ollama"
        assert result["context_size"] == 8192

    def test_filters_unknown_keys(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({"backend_type": "ollama", "evil_key": "hacked"}))
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        result = load_settings()
        assert "evil_key" not in result

    def test_strips_sensitive_keys_from_file(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps({
            "backend_type": "ollama",
            "target_ssh_password": "secret",
        }))
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        result = load_settings()
        assert "target_ssh_password" not in result

    def test_returns_empty_on_invalid_json(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text("NOT JSON {{{")
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        result = load_settings()
        assert result == {}

    def test_returns_empty_on_non_dict_json(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        path.write_text(json.dumps([1, 2, 3]))
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        result = load_settings()
        assert result == {}

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH",
                            tmp_path / "settings.json")
        state = {"backend_type": "llama.cpp", "context_size": 2048, "mcp_url": ""}
        save_settings(state)
        result = load_settings()
        assert result["backend_type"] == "llama.cpp"
        assert result["context_size"] == 2048


class TestPersistKeysDefinition:
    def test_persist_keys_is_frozenset(self):
        assert isinstance(PERSIST_KEYS, frozenset)

    def test_sensitive_keys_not_in_persist_keys(self):
        # Sensitive keys must never be in PERSIST_KEYS
        for k in _SENSITIVE_KEYS:
            assert k not in PERSIST_KEYS, f"{k} must not be in PERSIST_KEYS"

    def test_backend_type_in_persist_keys(self):
        assert "backend_type" in PERSIST_KEYS

    def test_ssh_password_in_sensitive(self):
        assert "target_ssh_password" in _SENSITIVE_KEYS
