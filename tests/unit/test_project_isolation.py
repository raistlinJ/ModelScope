"""Regression test: switching projects must not leak per-project state."""
from unittest.mock import patch
import streamlit as st
import core.state as state


def _make_llama_project(pid, cfg):
    return {"id": pid, "type": "llama_cli_bot", "config": cfg}


def test_llama_cli_fields_do_not_leak_between_projects():
    # Two llama-cli projects: A sets prompt/fail/validation; B leaves them blank.
    proj_a = _make_llama_project("A", {
        "system_prompt": "PROMPT_FROM_A",
        "fail_patterns": ["ERR_A"],
        "validation_commands": ["cmd_a"],
    })
    proj_b = _make_llama_project("B", {})  # B omits all three

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    st.session_state["active_project_id"] = "A"
    state.sync_project("A")
    assert st.session_state["llama_cli_system_prompt"] == "PROMPT_FROM_A"
    assert st.session_state["llama_cli_fail_patterns"] == ["ERR_A"]
    assert st.session_state["llama_cli_validation_commands"] == ["cmd_a"]

    st.session_state["active_project_id"] = "B"
    state.sync_project("B")
    # B omitted these, so they MUST be reset to defaults, not A's values.
    assert st.session_state["llama_cli_system_prompt"] == "", \
        "system_prompt leaked from project A into project B"
    assert st.session_state["llama_cli_fail_patterns"] == [], \
        "fail_patterns leaked from project A into project B"
    assert st.session_state["llama_cli_validation_commands"] == [], \
        "validation_commands leaked from project A into project B"