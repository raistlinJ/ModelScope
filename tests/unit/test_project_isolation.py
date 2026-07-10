"""Regression test: switching projects must not leak per-project state."""
from unittest.mock import patch
import streamlit as st
import core.state as state


def _make_llama_project(pid, cfg):
    return {"id": pid, "type": "llama_cli_bot", "config": cfg}


def _make_llama_server_project(pid, cfg):
    return {"id": pid, "type": "llama_server_bot", "config": cfg}


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


def test_llm_judge_settings_are_per_project():
    proj_a = _make_llama_project("A", {
        "llm_helper_enabled": True,
        "llm_helper_backend": "Ollama",
        "llm_helper_model": "llama3:70b",
        "llm_helper_openai_apikey": "sk-project-a",
    })
    proj_b = _make_llama_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["llama_cli_llm_helper_enabled"] is True
    assert st.session_state["llama_cli_llm_helper_model"] == "llama3:70b"

    state.sync_project("B")
    # B omitted the LLM Judge config — A's values must reset to defaults,
    # not survive the switch.
    assert st.session_state["llama_cli_llm_helper_enabled"] is False, \
        "llm_helper_enabled leaked from project A into project B"
    assert st.session_state["llama_cli_llm_helper_backend"] == "OpenAI-Compatible", \
        "llm_helper_backend leaked from project A into project B"
    assert st.session_state["llama_cli_llm_helper_model"] == "", \
        "llm_helper_model leaked from project A into project B"
    assert st.session_state["llama_cli_llm_helper_openai_apikey"] == "", \
        "llm_helper_openai_apikey leaked from project A into project B"

    # Switching back must restore A's LLM Judge settings from its config.
    state.sync_project("A")
    assert st.session_state["llama_cli_llm_helper_enabled"] is True
    assert st.session_state["llama_cli_llm_helper_model"] == "llama3:70b"
    assert st.session_state["llama_cli_llm_helper_openai_apikey"] == "sk-project-a"


def test_target_and_mcp_fields_do_not_leak():
    proj_a = _make_llama_project("A", {
        "pct_vmid": "101",
        "sudo_password": "hunter2",
        "mcp_enabled": True,
    })
    proj_b = _make_llama_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["llama_cli_pct_vmid"] == "101"
    assert st.session_state["llama_cli_sudo_password"] == "hunter2"
    assert st.session_state["llama_cli_mcp_enabled"] is True

    state.sync_project("B")
    assert st.session_state["llama_cli_pct_vmid"] == "", \
        "pct_vmid leaked from project A into project B"
    assert st.session_state["llama_cli_sudo_password"] == "", \
        "sudo_password leaked from project A into project B"
    assert st.session_state["llama_cli_mcp_enabled"] is False, \
        "mcp_enabled leaked from project A into project B"


def test_bash_sudo_password_and_vmid_do_not_leak():
    proj_a = {"id": "A", "type": "bash_bot",
              "config": {"pct_vmid": "202", "sudo_password": "hunter2"}}
    proj_b = {"id": "B", "type": "bash_bot", "config": {}}

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["bash_pct_vmid"] == "202"
    assert st.session_state["bash_sudo_password"] == "hunter2"

    state.sync_project("B")
    assert st.session_state["bash_pct_vmid"] == "", \
        "pct_vmid leaked from bash project A into project B"
    assert st.session_state["bash_sudo_password"] == "", \
        "sudo_password leaked from bash project A into project B"


def test_llama_server_advanced_options_do_not_leak_between_projects():
    proj_a = _make_llama_server_project("A", {
        "en_temp": True, "temperature": 0.55,
        "en_gpu_layers": True, "gpu_layers": 20,
        "flash_attn": True,
        "en_seed": True, "seed": 42,
    })
    proj_b = _make_llama_server_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["llama_server_en_temp"] is True
    assert st.session_state["llama_server_temperature"] == 0.55
    assert st.session_state["llama_server_gpu_layers"] == 20
    assert st.session_state["llama_server_flash_attn"] is True
    assert st.session_state["llama_server_seed"] == 42

    state.sync_project("B")
    assert st.session_state["llama_server_en_temp"] is False, \
        "en_temp leaked from project A into project B"
    assert st.session_state["llama_server_temperature"] == 0.8, \
        "temperature leaked from project A into project B"
    assert st.session_state["llama_server_gpu_layers"] == 99, \
        "gpu_layers leaked from project A into project B"
    assert st.session_state["llama_server_flash_attn"] is False, \
        "flash_attn leaked from project A into project B"
    assert st.session_state["llama_server_seed"] == -1, \
        "seed leaked from project A into project B"

    # Switching back must restore A's advanced options from its config.
    state.sync_project("A")
    assert st.session_state["llama_server_temperature"] == 0.55
    assert st.session_state["llama_server_gpu_layers"] == 20


def test_llama_server_ready_timeout_does_not_leak_between_projects():
    proj_a = _make_llama_server_project("A", {"server_ready_timeout": 600})
    proj_b = _make_llama_server_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["llama_server_ready_timeout"] == 600

    state.sync_project("B")
    assert st.session_state["llama_server_ready_timeout"] == 300, \
        "server_ready_timeout leaked from project A into project B"

    state.sync_project("A")
    assert st.session_state["llama_server_ready_timeout"] == 600


def test_llama_server_execution_target_does_not_leak_between_projects():
    """execution_target/ssh_*/sudo were wired into the state_key_map early on
    but had no UI and were hardcoded to "local" on flush — so this hydration
    path went untested until the Execution Target panel was actually added."""
    proj_a = _make_llama_server_project("A", {
        "execution_target": "ssh",
        "ssh_host": "10.0.0.5",
        "ssh_port": 2222,
        "ssh_user": "admin",
        "sudo": True,
        "sudo_password": "hunter2",
    })
    proj_b = _make_llama_server_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    state.sync_project("A")
    assert st.session_state["llama_server_execution_target"] == "ssh"
    assert st.session_state["llama_server_ssh_host"] == "10.0.0.5"
    assert st.session_state["llama_server_ssh_port"] == 2222
    assert st.session_state["llama_server_sudo"] is True
    assert st.session_state["llama_server_sudo_password"] == "hunter2"

    state.sync_project("B")
    assert st.session_state["llama_server_execution_target"] == "local", \
        "execution_target leaked from project A into project B"
    assert st.session_state["llama_server_ssh_host"] == "", \
        "ssh_host leaked from project A into project B"
    assert st.session_state["llama_server_sudo"] is False, \
        "sudo leaked from project A into project B"
    assert st.session_state["llama_server_sudo_password"] == "", \
        "sudo_password leaked from project A into project B"


def test_ephemeral_widget_keys_are_purged_on_switch():
    proj_a = _make_llama_project("A", {})
    proj_b = _make_llama_project("B", {})

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]
    state.sync_project("A")

    # Simulate ephemera created while working in project A
    st.session_state["bash_exec_vset_0_selected"] = True
    st.session_state["llama_exec_vset_2_step_0_cmd_1_selected"] = True
    st.session_state["bash_local_test_result"] = {"status": "success"}
    st.session_state["llama_cli_ssh_test_result"] = {"status": "success"}
    st.session_state["testing_bash_ssh"] = True
    st.session_state["llama_cli_is_fetching_openai_models"] = True

    state.sync_project("B")
    for key in (
        "bash_exec_vset_0_selected",
        "llama_exec_vset_2_step_0_cmd_1_selected",
        "bash_local_test_result",
        "llama_cli_ssh_test_result",
        "testing_bash_ssh",
        "llama_cli_is_fetching_openai_models",
    ):
        assert key not in st.session_state, f"{key} survived the project switch"