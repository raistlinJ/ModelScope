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
        # The UI records an explicit deletion; mere absence is not enough.
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
                 "_deleted_project_ids": ["b"]}
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

    def test_missing_projects_without_an_explicit_delete_are_preserved(self, tmp_path, monkeypatch):
        """A reset/reloaded session may have an empty project list temporarily."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        path.write_text(json.dumps({
            "projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
        }))
        state = {"projects": []}  # no baseline key at all
        save_settings(state)
        data = json.loads(path.read_text())
        assert [project["id"] for project in data["projects"]] == ["a"]

    def test_first_ever_save_with_no_disk_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}]}
        save_settings(state)
        data = json.loads((tmp_path / "settings.json").read_text())
        assert [p["id"] for p in data["projects"]] == ["a"]

    def test_project_created_and_deleted_in_same_session_stays_deleted(self, tmp_path, monkeypatch):
        """Regression for the reported bug: a project created *and* deleted
        within one browser session must not come back after refresh/restart.
        Before the fix, the merge treated the just-created project as
        belonging to "another session" (its id was never in the load-time
        baseline) and resurrected it on the delete's auto-save."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        # Session starts with P1, P2 on disk and in memory.
        seed = [
            {"id": "P1", "name": "P1", "type": "bash_bot", "config": {}},
            {"id": "P2", "name": "P2", "type": "bash_bot", "config": {}},
        ]
        path.write_text(json.dumps({"projects": seed}))
        state = {"projects": [dict(p) for p in seed],
                 "_known_project_ids_at_load": ["P1", "P2"]}
        # User creates P3 -> auto-save.
        state["projects"].append({"id": "P3", "name": "P3", "type": "bash_bot", "config": {}})
        save_settings(state)
        # User deletes P3 -> auto-save on the next rerun.
        state["projects"] = [p for p in state["projects"] if p["id"] != "P3"]
        state["_deleted_project_ids"] = ["P3"]
        save_settings(state)
        # What a browser refresh / server restart would read back.
        data = json.loads(path.read_text())
        ids = {p["id"] for p in data["projects"]}
        assert ids == {"P1", "P2"}, "a project created then deleted in the same session was resurrected"

    def test_project_created_in_empty_start_session_can_be_deleted(self, tmp_path, monkeypatch):
        """Worst case: a session that starts with no settings file has an
        empty baseline, so *every* project it creates was previously
        un-deletable."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        state = {"projects": []}
        state["projects"] = [{"id": "A", "name": "A", "type": "bash_bot", "config": {}}]
        save_settings(state)   # create
        state["projects"] = []
        state["_deleted_project_ids"] = ["A"]
        save_settings(state)   # delete
        data = json.loads(path.read_text())
        assert data["projects"] == [], "project created in an empty-start session could not be deleted"

    def test_default_project_from_a_reset_session_does_not_replace_saved_llama_project(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        saved = [
            {"id": "default_bash", "name": "Bash Project 1", "type": "bash_bot", "config": {}},
            {"id": "server", "name": "Server", "type": "llama_server_bot", "config": {}},
            {"id": "cli", "name": "CLI", "type": "llama_cli_bot", "config": {}},
        ]
        path.write_text(json.dumps({"projects": saved}))
        reset_state = {"projects": [saved[0]], "_known_project_ids_at_load": [p["id"] for p in saved]}

        save_settings(reset_state)

        assert {project["id"] for project in json.loads(path.read_text())["projects"]} == {
            "default_bash", "server", "cli",
        }

    def test_project_journal_recovers_from_a_stale_settings_overwrite(self, tmp_path, monkeypatch):
        """An older running app must not be able to erase a newer Llama project."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        projects = [
            {"id": "default_bash", "name": "Bash", "type": "bash_bot", "config": {}},
            {"id": "server", "name": "Server", "type": "llama_server_bot", "config": {"model_name": "model.gguf"}},
        ]
        save_settings({"projects": projects})

        # Simulate an old app process saving only its default project.
        path.write_text(json.dumps({"projects": [projects[0]]}))

        restored = load_settings()
        assert {project["id"] for project in restored["projects"]} == {"default_bash", "server"}

    def test_local_saves_do_not_claim_authority_over_another_sessions_project(self, tmp_path, monkeypatch):
        """The fix must fold only *this session's own* project ids into the
        baseline, never the merged disk set. Otherwise this session would
        claim authority over a parallel tab's project and delete it on a
        later save — the exact cross-session data loss the merge prevents."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        # Disk has "b", created by another tab this session never saw.
        path.write_text(json.dumps({"projects": [
            {"id": "a", "name": "A", "type": "bash_bot", "config": {}},
            {"id": "b", "name": "B", "type": "bash_bot", "config": {}},
        ]}))
        state = {"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}],
                 "_known_project_ids_at_load": ["a"]}
        save_settings(state)
        assert "b" not in set(state["_known_project_ids_at_load"]), \
            "this session wrongly claimed authority over another session's project"
        # A second save must still preserve "b".
        save_settings(state)
        data = json.loads(path.read_text())
        ids = {p["id"] for p in data["projects"]}
        assert ids == {"a", "b"}, "another session's project was deleted after two local saves"

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

    def test_non_serializable_project_preserves_its_last_saved_version(self, tmp_path, monkeypatch):
        """A bad transient value must not delete an existing Llama project."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        saved = [
            {"id": "bash", "name": "Bash", "type": "bash_bot", "config": {}},
            {"id": "llama", "name": "Llama", "type": "llama_server_bot", "config": {"model_name": "kept.gguf"}},
        ]
        path.write_text(json.dumps({"projects": saved}))
        state = {
            "projects": [
                saved[0],
                {"id": "llama", "name": "Llama", "type": "llama_server_bot", "config": {"widget_value": object()}},
            ],
            "_known_project_ids_at_load": ["bash", "llama"],
        }

        save_settings(state)

        by_id = {project["id"]: project for project in json.loads(path.read_text())["projects"]}
        assert by_id["llama"]["config"]["model_name"] == "kept.gguf"

    def test_load_recovers_previous_snapshot_when_primary_is_corrupt(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        save_settings({"projects": [{"id": "a", "name": "A", "type": "bash_bot", "config": {}}]})
        # The second successful save rotates the first one into .bak.
        save_settings({"projects": [{"id": "b", "name": "B", "type": "llama_cli_bot", "config": {}}]})
        path.write_text("incomplete settings document")

        recovered = load_settings()
        assert {project["id"] for project in recovered["projects"]} == {"a", "b"}


class TestNestedProjectSecretHandling:
    """ssh_password/sudo_password are persisted (base64-obscured, not real
    encryption) so the user doesn't have to re-enter the SSH login password
    every session — mirroring the plaintext persistence already chosen for
    llm_helper_openai_apikey, but obscured since this value grants remote
    shell access. openai_api_key/ssh_key_path remain fully stripped."""

    def test_ssh_password_is_obscured_not_plaintext_not_stripped(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        state = {
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"ssh_password": "hunter2"},
            }],
        }
        save_settings(state)
        data = json.loads(path.read_text())
        on_disk = data["projects"][0]["config"]["ssh_password"]
        assert on_disk not in ("", "hunter2"), "must be neither stripped nor plaintext"

    def test_sudo_password_is_obscured(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        state = {
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"sudo_password": "hunter2"},
            }],
        }
        save_settings(state)
        data = json.loads(path.read_text())
        on_disk = data["projects"][0]["config"]["sudo_password"]
        assert on_disk not in ("", "hunter2")

    def test_openai_api_key_and_ssh_key_path_still_stripped(self, tmp_path, monkeypatch):
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        state = {
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"openai_api_key": "sk-abc", "ssh_key_path": "/home/u/.ssh/id_rsa"},
            }],
        }
        save_settings(state)
        data = json.loads(path.read_text())
        cfg = data["projects"][0]["config"]
        assert cfg["openai_api_key"] == ""
        assert cfg["ssh_key_path"] == ""

    def test_round_trip_recovers_plaintext_ssh_password(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
        state = {
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"ssh_password": "hunter2"},
            }],
        }
        save_settings(state)
        result = load_settings()
        assert result["projects"][0]["config"]["ssh_password"] == "hunter2"

    def test_empty_ssh_password_round_trips_as_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", tmp_path / "settings.json")
        state = {
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"ssh_password": ""},
            }],
        }
        save_settings(state)
        result = load_settings()
        assert result["projects"][0]["config"]["ssh_password"] == ""

    def test_disk_only_project_merged_in_is_not_double_encoded(self, tmp_path, monkeypatch):
        """Regression: a project this session never loaded (belongs to
        another, more current session) gets pulled in from disk still
        base64-encoded — it must be de-obscured before re-entering
        _sanitize_projects(), or a second save doubly encodes it and a
        later load_settings() would recover garbage instead of the
        original password."""
        path = tmp_path / "settings.json"
        monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
        # First session saves project "a" with a real ssh_password.
        save_settings({
            "projects": [{
                "id": "a", "name": "A", "type": "llama_server_bot",
                "config": {"ssh_password": "hunter2"},
            }],
        })
        # A second session, which never saw "a", saves its own project "b".
        state_b = {
            "projects": [{"id": "b", "name": "B", "type": "bash_bot", "config": {}}],
            "_known_project_ids_at_load": [],
        }
        save_settings(state_b)
        result = load_settings()
        by_id = {p["id"]: p for p in result["projects"]}
        assert by_id["a"]["config"]["ssh_password"] == "hunter2"


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
