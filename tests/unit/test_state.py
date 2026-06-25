"""
Unit tests for core.state — init_state and sync_scenario.

Streamlit session_state is mocked via conftest.py (autouse fixture).
We re-use the same MockState pattern but drive it through the real
init_state / sync_scenario functions.
"""
import pytest
import streamlit as st
from core.state import init_state, sync_scenario, sync_project, _DEFAULTS


# ── init_state ────────────────────────────────────────────────────────────────

class TestInitState:
    def test_all_defaults_keys_seeded(self, mock_streamlit_state):
        init_state()
        for key in _DEFAULTS:
            assert key in st.session_state, f"Key not seeded: {key}"

    def test_existing_value_not_overwritten(self, mock_streamlit_state):
        st.session_state["backend_type"] = "ollama"
        init_state()
        assert st.session_state["backend_type"] == "ollama"

    def test_missing_key_gets_default(self, mock_streamlit_state):
        # Remove a key that would normally be in defaults
        if "caf_scope" in st.session_state:
            del st.session_state["caf_scope"]
        init_state()
        assert "caf_scope" in st.session_state

    def test_run_history_default_is_list(self, mock_streamlit_state):
        init_state()
        assert isinstance(st.session_state["run_history"], list)

    def test_cancel_requested_default_false(self, mock_streamlit_state):
        init_state()
        assert st.session_state["cancel_requested"] is False

    def test_prompts_user_edited_default_false(self, mock_streamlit_state):
        init_state()
        assert st.session_state["_prompts_user_edited"] is False

    def test_caf_scope_default_is_narrow(self, mock_streamlit_state):
        if "caf_scope" in st.session_state:
            del st.session_state["caf_scope"]
        init_state()
        assert st.session_state["caf_scope"] == "Narrow"

    def test_idempotent_double_call(self, mock_streamlit_state):
        init_state()
        st.session_state["custom_flag"] = 99
        init_state()
        # Custom key not in _DEFAULTS is untouched
        assert st.session_state["custom_flag"] == 99


# ── sync_scenario ─────────────────────────────────────────────────────────────

class TestSyncScenario:
    def setup_method(self):
        """Seed state before each test."""
        pass

    def test_updates_validation_command(self, mock_streamlit_state):
        init_state()
        sync_scenario("Scenario 1 – File Creation")
        assert st.session_state["validation_command"] == "cat /tmp/test"

    def test_updates_metrics_matrix(self, mock_streamlit_state):
        init_state()
        sync_scenario("Scenario 1 – File Creation")
        assert len(st.session_state["metrics_matrix"]) > 0

    def test_updates_fail_patterns(self, mock_streamlit_state):
        init_state()
        sync_scenario("Scenario 1 – File Creation")
        assert "no such file" in st.session_state["fail_patterns"]

    def test_last_exec_scenario_updated(self, mock_streamlit_state):
        init_state()
        sync_scenario("Scenario 2 – Network Scan")
        assert st.session_state["_last_exec_scenario"] == "Scenario 2 – Network Scan"

    def test_prompts_updated_when_not_user_edited(self, mock_streamlit_state):
        init_state()
        st.session_state["_prompts_user_edited"] = False
        st.session_state["sys_prompt"] = "old_sys"
        st.session_state["user_prompt"] = "old_usr"
        sync_scenario("Scenario 1 – File Creation")
        assert st.session_state["sys_prompt"] != "old_sys"
        assert st.session_state["user_prompt"] != "old_usr"

    def test_prompts_not_overwritten_when_user_edited(self, mock_streamlit_state):
        init_state()
        st.session_state["_prompts_user_edited"] = True
        st.session_state["sys_prompt"] = "MY CUSTOM SYS"
        st.session_state["user_prompt"] = "MY CUSTOM USR"
        sync_scenario("Scenario 1 – File Creation")
        assert st.session_state["sys_prompt"] == "MY CUSTOM SYS"
        assert st.session_state["user_prompt"] == "MY CUSTOM USR"

    def test_prompts_user_edited_flag_not_reset(self, mock_streamlit_state):
        """DEF-001 regression guard: sync_scenario must never clear _prompts_user_edited."""
        init_state()
        st.session_state["_prompts_user_edited"] = True
        sync_scenario("Scenario 1 – File Creation")
        assert st.session_state["_prompts_user_edited"] is True

    def test_caf_fields_updated_for_caf_scenario(self, mock_streamlit_state):
        init_state()
        sync_scenario("CAF – Guardrail Test")
        assert st.session_state["caf_scope"] in ("Narrow", "Broad")
        assert st.session_state["caf_urgency"] in ("Stealth", "Speed")

    def test_caf_fields_not_set_for_non_caf_scenario(self, mock_streamlit_state):
        """Scenario 1 has no caf_scope key → caf_ fields must not be overwritten."""
        init_state()
        original_scope = st.session_state.get("caf_scope", "Narrow")
        sync_scenario("Scenario 1 – File Creation")
        # Scenario 1 has no caf_scope key — state must be unchanged
        assert st.session_state.get("caf_scope", "Narrow") == original_scope

    def test_unknown_scenario_falls_back_to_default(self, mock_streamlit_state):
        """Passing a non-existent key should use the default scenario, not crash."""
        init_state()
        sync_scenario("NonExistent Scenario XYZ")
        # Should not raise, and metrics_matrix should be populated (from DEFAULT_SCENARIO)
        assert "validation_command" in st.session_state


# ── sync_project ──────────────────────────────────────────────────────────────

class TestSyncProject:
    def _bash_project(self, project_id="proj-1", **cfg_overrides):
        cfg = {
            "startup_commands": ["echo hello"],
            "bash_timeout": 90,
            "completion_commands": ["echo done"],
            "validation_commands": ["check_cmd"],
            "execution_target": "local",
            "ssh_host": "10.0.0.9",
            "ssh_port": 22,
            "ssh_user": "kali",
            "ssh_password": "secret",
            "ssh_key_path": "",
            "fail_patterns": ["error"],
            "metrics_matrix": [{"id": "M-001"}],
            "sudo": False,
        }
        cfg.update(cfg_overrides)
        return {"id": project_id, "type": "bash_bot", "config": cfg}

    def _llama_project(self, project_id="proj-2", **cfg_overrides):
        cfg = {
            "backend": "llama.cpp",
            "binary_path": "/usr/bin/llama-cli",
            "model_dir": "/models",
            "model_name": "llama3.gguf",
            "prompts": ["Hello, world"],
            "commands": ["uname"],
            "timeout": 120,
            "execution_target": "local",
            "ssh_host": "",
            "ssh_port": 22,
            "ssh_user": "root",
            "ssh_password": "",
            "ssh_key_path": "",
            "sudo": False,
            "validation_commands": ["check"],
            "fail_patterns": [],
            "metrics_matrix": [],
            "openai_api_key": "",
        }
        cfg.update(cfg_overrides)
        return {"id": project_id, "type": "llama_cli_bot", "config": cfg}

    def test_sync_bash_bot_updates_startup_commands(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")
        assert st.session_state["bash_startup_commands"] == ["echo hello"]

    def test_sync_bash_bot_updates_timeout(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")
        assert st.session_state["bash_timeout"] == 90

    def test_sync_bash_bot_updates_ssh_host(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")
        assert st.session_state["bash_ssh_host"] == "10.0.0.9"

    def test_sync_bash_bot_updates_metrics_matrix(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")
        assert st.session_state["bash_metrics_matrix"] == [{"id": "M-001"}]

    def test_sync_bash_bot_records_last_active_project_id(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")
        assert st.session_state["_last_active_project_id"] == "proj-1"

    def test_sync_llama_cli_bot_updates_backend(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._llama_project()]
        sync_project("proj-2")
        assert st.session_state["llama_cli_backend"] == "llama.cpp"

    def test_sync_llama_cli_bot_updates_prompts(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._llama_project()]
        sync_project("proj-2")
        assert st.session_state["llama_cli_prompts"] == ["Hello, world"]

    def test_sync_llama_cli_bot_updates_model_name(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._llama_project()]
        sync_project("proj-2")
        assert st.session_state["llama_cli_model_name"] == "llama3.gguf"

    def test_sync_llama_cli_bot_updates_timeout(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._llama_project()]
        sync_project("proj-2")
        assert st.session_state["llama_cli_timeout"] == 120

    def test_unknown_project_id_is_noop(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._bash_project()]
        st.session_state["bash_timeout"] = 60
        sync_project("nonexistent-id")
        assert st.session_state["bash_timeout"] == 60  # unchanged

    def test_partial_config_resets_missing_keys_to_defaults(self, mock_streamlit_state):
        """Isolation fix: a key absent from the project config must reset to default,
        not inherit the previously active project's value (the regression case)."""
        init_state()
        project = {
            "id": "proj-partial",
            "type": "bash_bot",
            "config": {"startup_commands": ["echo partial"]},
            # bash_timeout deliberately absent — must reset to default (60), not keep 42
        }
        st.session_state["projects"] = [project]
        st.session_state["bash_timeout"] = 42  # simulate previous project's value
        sync_project("proj-partial")
        assert st.session_state["bash_startup_commands"] == ["echo partial"]
        # bash_timeout is absent from cfg → must fall back to default (60), not leak 42
        assert st.session_state["bash_timeout"] == 60

    def test_project_switch_isolates_llama_cli_keys(self, mock_streamlit_state):
        """Switching from one llama_cli_bot project to another resets all working keys
        so that the second project never sees the first project's configuration."""
        init_state()
        proj_a = self._llama_project(
            project_id="proj-a",
            binary_path="/old/llama-cli",
            model_dir="/old/models",
            model_name="old.gguf",
            openai_base_url="http://grain.utep.edu:11434",
        )
        proj_b = self._llama_project(
            project_id="proj-b",
            binary_path="",
            model_dir="",
            model_name="",
            openai_base_url="",
        )
        st.session_state["projects"] = [proj_a, proj_b]

        # Activate project A, then switch to B
        sync_project("proj-a")
        assert st.session_state["llama_cli_binary_path"] == "/old/llama-cli"

        sync_project("proj-b")
        # B has empty values — must NOT inherit A's values
        assert st.session_state["llama_cli_binary_path"] == ""
        assert st.session_state["llama_cli_model_dir"]   == ""
        assert st.session_state["llama_cli_openai_base_url"] == ""

    def test_project_switch_resets_inactive_bot_keys(self, mock_streamlit_state):
        init_state()
        st.session_state["llama_cli_binary_path"] = "/old/llama-cli"
        st.session_state["llama_cli_backend"] = "ollama"
        st.session_state["bash_startup_commands"] = ["echo hey"]
        st.session_state["bash_ssh_host"] = "10.0.0.1"

        st.session_state["projects"] = [
            self._bash_project(),
            self._llama_project(project_id="proj-b"),
        ]

        sync_project("proj-1")
        assert st.session_state["llama_cli_binary_path"] == ""
        assert st.session_state["llama_cli_backend"] == "llama.cpp"

        sync_project("proj-b")
        assert st.session_state["bash_startup_commands"] == []
        assert st.session_state["bash_ssh_host"] == ""

    def test_project_switch_persists_and_restores_run_state(self, mock_streamlit_state):
        """Switching projects saves the outgoing run-state under a per-project key
        and restores the incoming project's previously saved state.  A first visit
        to a project starts with empty state; returning to a project restores the
        logs that were visible when the user left."""
        init_state()
        proj_a = self._bash_project()                          # id="proj-1"
        proj_b = self._bash_project()
        proj_b["id"] = "proj-2"
        st.session_state["projects"] = [proj_a, proj_b]

        # Start on proj-1 with some run state
        st.session_state["_last_active_project_id"] = "proj-1"
        st.session_state["run_logs"]      = [{"text": "proj-1 log"}]
        st.session_state["run_completed"] = True
        st.session_state["telemetry"]     = {"latency": 1.23}

        # Switch to proj-2 (first visit — should start empty)
        sync_project("proj-2")
        assert st.session_state["run_logs"]      == [], "first visit must start with empty logs"
        assert st.session_state["run_completed"] is False
        assert st.session_state["telemetry"]     == {}

        # proj-1's state must have been saved
        assert st.session_state["run_logs_proj-1"]      == [{"text": "proj-1 log"}]
        assert st.session_state["run_completed_proj-1"] is True
        assert st.session_state["telemetry_proj-1"]     == {"latency": 1.23}

        # Now switch back to proj-1 — run state must be restored
        sync_project("proj-1")
        assert st.session_state["run_logs"]      == [{"text": "proj-1 log"}]
        assert st.session_state["run_completed"] is True
        assert st.session_state["telemetry"]     == {"latency": 1.23}

    def test_project_switch_clears_llama_transient_caches(self, mock_streamlit_state):
        """Discovery caches and transient widget keys must be cleared on switch."""
        init_state()
        st.session_state["projects"] = [self._llama_project()]
        # Simulate caches left by a previous project render
        st.session_state["llama_cli_discovered_models"]  = [{"name": "old.gguf"}]
        st.session_state["llama_cli_openai_models"]      = [{"name": "gpt-old"}]
        st.session_state["_llama_svc_result"]            = ("ok", "was running", "")
        st.session_state["_llama_openai_url_widget"]     = "http://old-server"
        sync_project("proj-2")
        assert "llama_cli_discovered_models" not in st.session_state
        assert "llama_cli_openai_models"     not in st.session_state
        assert "_llama_svc_result"           not in st.session_state
        assert "_llama_openai_url_widget"    not in st.session_state

    def test_empty_projects_list_is_noop(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = []
        sync_project("proj-1")
        # Should not raise

    def test_sync_llama_cli_bot_updates_openai_api_key(self, mock_streamlit_state):
        init_state()
        st.session_state["projects"] = [self._llama_project(openai_api_key="sk-test-key")]
        sync_project("proj-2")
        assert st.session_state["llama_cli_openai_api_key"] == "sk-test-key"

    def test_project_switch_clears_dynamic_step_widget_keys(self, mock_streamlit_state):
        """Dynamic step widget keys (_us_*, _sc_*, llama_mcp_en_*) from the previous
        project must be deleted on switch so that Streamlit re-seeds them from the
        freshly loaded step data, preventing step-content bleed across projects."""
        init_state()
        # Seed dynamic keys as if a previous project render left them in state
        st.session_state["_us_llama_3_content"] = "grain's prompt"
        st.session_state["_us_llama_3_type"]    = "Prompt"
        st.session_state["_us_llama_4_content"] = "grain's second step"
        st.session_state["_sc_bash_5_cmd"]      = "rm -rf /"
        st.session_state["_sc_bash_5_en"]       = False
        st.session_state["llama_mcp_en_0"]      = True
        st.session_state["llama_mcp_en_1"]      = False

        st.session_state["projects"] = [self._llama_project()]
        sync_project("proj-2")

        # All dynamic keys must be gone
        assert "_us_llama_3_content" not in st.session_state
        assert "_us_llama_3_type"    not in st.session_state
        assert "_us_llama_4_content" not in st.session_state
        assert "_sc_bash_5_cmd"      not in st.session_state
        assert "_sc_bash_5_en"       not in st.session_state
        assert "llama_mcp_en_0"      not in st.session_state
        assert "llama_mcp_en_1"      not in st.session_state

    def test_project_switch_clears_dynamic_keys_for_bash_bot_too(self, mock_streamlit_state):
        """Dynamic key clearing applies to bash_bot projects as well, since a previous
        llama_cli_bot project's _us_* keys could survive into a bash_bot render."""
        init_state()
        st.session_state["_us_llama_3_content"] = "leftover llama content"
        st.session_state["llama_mcp_en_0"]      = True
        st.session_state["_sc_bash_2_cmd"]      = "old bash cmd"

        st.session_state["projects"] = [self._bash_project()]
        sync_project("proj-1")

        assert "_us_llama_3_content" not in st.session_state
        assert "llama_mcp_en_0"      not in st.session_state
        assert "_sc_bash_2_cmd"      not in st.session_state

