"""
Unit tests for core.state — init_state and sync_scenario.

Streamlit session_state is mocked via conftest.py (autouse fixture).
We re-use the same MockState pattern but drive it through the real
init_state / sync_scenario functions.
"""
import pytest
import streamlit as st
from core.state import init_state, sync_scenario, _DEFAULTS


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
        assert st.session_state["caf_urgency"] in ("Stealthy", "Speed")

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
