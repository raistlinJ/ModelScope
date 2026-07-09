"""
Smoke tests for bash-bot improvements:
  - config/bash_templates.py structure
  - _get_selected_validation_sets() filtering
  - _test_bash_ssh_connection() error classification
  - Validation dialog flag removal (no _show_val_set_dialog in config_tab render path)
  - app.py template integration
"""
import copy
import pytest
from unittest.mock import MagicMock, patch


# ── 1. bash_templates structure ────────────────────────────────────────────────

def test_bash_templates_import():
    from config.bash_templates import BASH_BOT_TEMPLATES
    assert "file_creator" in BASH_BOT_TEMPLATES
    assert "nmap_scanner" in BASH_BOT_TEMPLATES


def test_file_creator_template_structure():
    from config.bash_templates import BASH_BOT_TEMPLATES
    fc = BASH_BOT_TEMPLATES["file_creator"]
    assert fc["execution_target"] == "local"
    assert len(fc["startup_commands"]) == 2
    # First step: cleanup; second step: create file
    step1_cmds = fc["startup_commands"][0]["commands"]
    assert any("rm -f /tmp/test" in c["command"] for c in step1_cmds)
    step2_cmds = fc["startup_commands"][1]["commands"]
    assert any("/tmp/test" in c["command"] for c in step2_cmds)
    # Completion step removes the file
    comp_cmds = fc["completion_commands"][0]["commands"]
    assert any("rm -f /tmp/test" in c["command"] for c in comp_cmds)
    # Validation set named FileCheck with Exact String check
    assert len(fc["validation_sets"]) == 1
    vset = fc["validation_sets"][0]
    assert vset["name"] == "FileCheck"
    cmds = vset["steps"][0]["commands"]
    assert any(c["expected_output_type"] == "Exact String" for c in cmds)


def test_nmap_scanner_template_structure():
    from config.bash_templates import BASH_BOT_TEMPLATES
    nm = BASH_BOT_TEMPLATES["nmap_scanner"]
    assert nm["bash_timeout"] == 60
    # Startup: nmap install check + nmap run
    all_cmds = [c["command"] for step in nm["startup_commands"]
                for c in step["commands"]]
    assert any("nmap" in cmd for cmd in all_cmds)
    assert any("nmap_result.txt" in cmd for cmd in all_cmds)
    # Validation uses Regex
    vset = nm["validation_sets"][0]
    assert vset["name"] == "NmapCheck"
    cmd = vset["steps"][0]["commands"][0]
    assert cmd["expected_output_type"] == "Regex"
    assert "Nmap scan report" in cmd["expected_output"]


# ── 2. _get_selected_validation_sets filtering ────────────────────────────────

def _make_mock_session(overrides: dict = {}):
    """Return a mock Streamlit session_state that behaves like a dict."""
    state = {**overrides}
    mock = MagicMock()
    mock.get = lambda k, d=None: state.get(k, d)
    return mock


def test_get_selected_validation_sets_all_selected(monkeypatch):
    """All sets selected → full list returned with enabled unchanged."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    import streamlit as st

    cfg = copy.deepcopy(BASH_BOT_TEMPLATES["file_creator"])
    monkeypatch.setattr(st, "session_state",
                        _make_mock_session({"bash_exec_vset_0_selected": True}))

    from ui.execute_tab import _get_selected_validation_sets
    result = _get_selected_validation_sets(cfg)
    assert len(result) == 1
    assert result[0]["name"] == "FileCheck"


def test_get_selected_validation_sets_set_deselected(monkeypatch):
    """Set-level deselection → set excluded from result."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    import streamlit as st

    cfg = copy.deepcopy(BASH_BOT_TEMPLATES["file_creator"])
    monkeypatch.setattr(st, "session_state",
                        _make_mock_session({"bash_exec_vset_0_selected": False}))

    from ui.execute_tab import _get_selected_validation_sets
    result = _get_selected_validation_sets(cfg)
    assert result == []


def test_get_selected_validation_sets_cmd_override(monkeypatch):
    """Command-level override: one command unchecked → enabled=False in result."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    import streamlit as st

    cfg = copy.deepcopy(BASH_BOT_TEMPLATES["file_creator"])
    # Set is selected, but command 1 (cat /tmp/test) is unchecked
    monkeypatch.setattr(st, "session_state",
                        _make_mock_session({
                            "bash_exec_vset_0_selected": True,
                            "bash_exec_vset_0_step_0_cmd_1_selected": False,
                        }))

    from ui.execute_tab import _get_selected_validation_sets
    result = _get_selected_validation_sets(cfg)
    assert len(result) == 1
    cmds = result[0]["steps"][0]["commands"]
    assert cmds[0]["enabled"] is True   # ls /tmp/test unchanged
    assert cmds[1]["enabled"] is False  # cat /tmp/test overridden to False


def test_get_selected_validation_sets_does_not_mutate_original(monkeypatch):
    """The helper returns a deep-copy — original cfg is not mutated."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    import streamlit as st

    cfg = copy.deepcopy(BASH_BOT_TEMPLATES["file_creator"])
    monkeypatch.setattr(st, "session_state",
                        _make_mock_session({
                            "bash_exec_vset_0_selected": True,
                            "bash_exec_vset_0_step_0_cmd_1_selected": False,
                        }))

    from ui.execute_tab import _get_selected_validation_sets
    _get_selected_validation_sets(cfg)
    # Original must remain unchanged
    orig_enabled = cfg["validation_sets"][0]["steps"][0]["commands"][1]["enabled"]
    assert orig_enabled is True


# ── 3. SSH error classification ────────────────────────────────────────────────

def test_ssh_connection_gaierror(monkeypatch):
    """socket.gaierror → 'Could not find server' message stored."""
    import socket
    import streamlit as st

    state = {}
    mock_ss = MagicMock()
    mock_ss.get = lambda k, d=None: state.get(k, d)

    def mock_pop(k, d=None):
        return state.pop(k, d)

    mock_ss.__setitem__ = lambda self, k, v: state.__setitem__(k, v)
    mock_ss.__getitem__ = lambda self, k: state[k]

    monkeypatch.setattr(st, "session_state", mock_ss)
    monkeypatch.setitem(state, "bash_ssh_host", "bad-host.local")
    monkeypatch.setitem(state, "bash_ssh_port", 22)
    monkeypatch.setitem(state, "bash_ssh_user", "root")
    monkeypatch.setitem(state, "bash_ssh_password", "")
    monkeypatch.setitem(state, "bash_ssh_key_path", "")

    import paramiko

    def fake_connect(**kwargs):
        raise socket.gaierror("Name or service not known")

    mock_client = MagicMock()
    mock_client.connect.side_effect = fake_connect
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_client)

    from ui.config_tab import _test_bash_ssh_connection
    _test_bash_ssh_connection()

    result = state.get("bash_ssh_test_result")
    assert result is not None
    assert result["status"] == "error"
    assert "Could not find server" in result["message"]


def test_ssh_connection_auth_failure(monkeypatch):
    """paramiko.AuthenticationException → 'Authentication failed' message stored."""
    import streamlit as st
    import paramiko

    state = {
        "bash_ssh_host": "192.168.1.1",
        "bash_ssh_port": 22,
        "bash_ssh_user": "root",
        "bash_ssh_password": "wrongpass",
        "bash_ssh_key_path": "",
    }
    mock_ss = MagicMock()
    mock_ss.get = lambda k, d=None: state.get(k, d)
    mock_ss.__setitem__ = lambda self, k, v: state.__setitem__(k, v)
    mock_ss.__getitem__ = lambda self, k: state[k]
    monkeypatch.setattr(st, "session_state", mock_ss)

    mock_client = MagicMock()
    mock_client.connect.side_effect = paramiko.AuthenticationException("Auth failed")
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_client)

    from ui.config_tab import _test_bash_ssh_connection
    _test_bash_ssh_connection()

    result = state.get("bash_ssh_test_result")
    assert result["status"] == "error"
    assert "Authentication failed" in result["message"]


def test_ssh_connection_success(monkeypatch):
    """Successful connection → status 'success'."""
    import streamlit as st
    import paramiko

    state = {
        "bash_ssh_host": "192.168.1.1",
        "bash_ssh_port": 22,
        "bash_ssh_user": "root",
        "bash_ssh_password": "pass",
        "bash_ssh_key_path": "",
    }
    mock_ss = MagicMock()
    mock_ss.get = lambda k, d=None: state.get(k, d)
    mock_ss.__setitem__ = lambda self, k, v: state.__setitem__(k, v)
    mock_ss.__getitem__ = lambda self, k: state[k]
    monkeypatch.setattr(st, "session_state", mock_ss)

    mock_stdout = MagicMock()
    mock_stdout.read.return_value = b"ok"
    mock_client = MagicMock()
    mock_client.exec_command.return_value = (None, mock_stdout, None)
    monkeypatch.setattr(paramiko, "SSHClient", lambda: mock_client)

    from ui.config_tab import _test_bash_ssh_connection
    _test_bash_ssh_connection()

    result = state.get("bash_ssh_test_result")
    assert result["status"] == "success"
    assert "Connection test succeeded" in result["message"]


# ── 4. Dialog flag removal: _show_val_set_dialog no longer used ───────────────

def test_no_show_val_set_dialog_flag_in_render_validation():
    """The validation-set renderer must not reference _show_val_set_dialog as a trigger."""
    import ast, inspect
    from ui.config_tab import _render_validation_sets_ui
    source = inspect.getsource(_render_validation_sets_ui)
    # The flag check should have been removed
    assert '_show_val_set_dialog' not in source, (
        "_render_validation_sets_ui still references _show_val_set_dialog; "
        "the flag-based dialog trigger was not removed."
    )


def test_no_behavioral_validation_in_render():
    """The validation-set renderer must not contain 'Behavioral Validation' text."""
    import inspect
    from ui.config_tab import _render_validation_sets_ui
    source = inspect.getsource(_render_validation_sets_ui)
    assert "Behavioral Validation" not in source


def test_no_metrics_matrix_in_llama_validation_tab():
    """The Llama validation tab must only render validation sets."""
    import inspect
    from ui.config_tab import _render_llama_cli_validation
    source = inspect.getsource(_render_llama_cli_validation)
    assert "Metrics Matrix" not in source
    assert "_render_metrics_matrix" not in source


def test_llm_judge_ssl_checkbox_is_editable():
    """The LLM Judge SSL checkbox should remain editable for HTTP and HTTPS URLs."""
    import inspect
    from ui.config_tab import _render_llm_prompt_helper_tab
    source = inspect.getsource(_render_llm_prompt_helper_tab)
    ssl_block = source[source.index('"Require SSL Certificate Verification"'):]
    ssl_block = ssl_block[:ssl_block.index('st.session_state[f"{pfx}_llm_helper_openai_verify_ssl"]')]
    assert "disabled=" not in ssl_block


def test_prompt_only_step_preview_is_not_empty():
    from ui.config_tab import _step_commands_preview

    preview = _step_commands_preview([
        {"type": "prompt", "system_prompt": "", "user_prompt": "Generate setup commands"}
    ])

    assert preview == "LLM Judge: Generate setup commands"


def test_step_preview_skips_blank_commands_before_prompt():
    from ui.config_tab import _step_commands_preview

    preview = _step_commands_preview([
        {"type": "command", "command": "   "},
        {"type": "prompt", "system_prompt": "Plan cleanup", "user_prompt": ""},
    ])

    assert preview == "LLM Judge: Plan cleanup"


def test_legacy_validation_check_normalizes_to_checks_list():
    from ui.config_tab import _normalize_validation_checks

    checks = _normalize_validation_checks({
        "expected_output_type": "Exact String",
        "expected_output": "ok",
    })

    assert checks == [{"expected_output_type": "Exact String", "expected_output": "ok"}]


def test_empty_validation_checks_mean_output_ignored():
    from ui.config_tab import _normalize_validation_checks, _validation_checks_button_label

    cmd = {
        "checks": [],
        "expected_output_type": "Exact String",
        "expected_output": "stale legacy value",
    }

    assert _normalize_validation_checks(cmd) == []
    assert _validation_checks_button_label(cmd) == "Output ignored"


def test_validation_checks_button_label_counts_actionable_checks():
    from ui.config_tab import _validation_checks_button_label

    assert _validation_checks_button_label({
        "checks": [
            {"expected_output_type": "Ignore", "expected_output": ""},
            {"expected_output_type": "Regex", "expected_output": "ok"},
            {"expected_output_type": "No output", "expected_output": ""},
        ],
    }) == "2 checks"


def test_multiple_validation_checks_summary_uses_or():
    from ui.config_tab import _validation_checks_summary

    summary = _validation_checks_summary({
        "checks": [
            {"expected_output_type": "Exact String", "expected_output": "ok"},
            {"expected_output_type": "Regex", "expected_output": r"ready: \\d+"},
        ]
    })

    assert "Exact String: ok" in summary
    assert " OR " in summary
    assert "Regex: ready:" in summary


def test_validation_check_type_labels_are_icons_with_hover_help():
    from ui.config_tab import (
        _validation_check_type_help,
        _validation_check_type_icon_html,
        _validation_check_type_label,
        _validation_check_type_legend_html,
        _validation_check_type_tooltip,
    )

    assert _validation_check_type_label("Exact String") == "≡"
    assert _validation_check_type_label("Exact String") != "Exact String"
    assert _validation_check_type_tooltip("Exact String").startswith("Exact String:")

    icon_html = _validation_check_type_icon_html("Exact String")
    assert 'title="Exact String:' in icon_html
    assert "≡" in icon_html

    legend_html = _validation_check_type_legend_html()
    assert legend_html.count("title=") == 4

    help_text = _validation_check_type_help()
    assert ".* Regex:" in help_text
    assert "≡ Exact String:" in help_text
    assert "∅ No output:" in help_text


def test_validation_checks_popover_does_not_force_rerun():
    import inspect
    from ui.config_tab import _render_validation_checks_control

    source = inspect.getsource(_render_validation_checks_control)

    assert "st.rerun()" not in source
    assert "on_click=_add_check" in source
    assert "on_click=_remove_check" in source


def test_validation_checks_popover_uses_compact_icon_picker():
    import inspect
    from ui.config_tab import _render_validation_checks_control

    source = inspect.getsource(_render_validation_checks_control)

    assert "format_func=_validation_check_type_label" in source
    assert "help=_validation_check_type_help()" in source
    assert "_validation_check_type_icon_html(display_type)" in source
    assert "_validation_check_type_legend_html()" in source
    assert "st.popover(_validation_checks_button_label(cmd)" in source
    assert 'label_visibility="collapsed"' in source
    assert "st.columns([0.9, 4.0, 1.0])" in source
    assert '"×"' in source


def test_validation_checks_control_does_not_render_summary_caption():
    import inspect
    from ui.config_tab import _render_validation_checks_control

    source = inspect.getsource(_render_validation_checks_control)

    assert "st.caption(summary)" not in source


def test_validation_step_action_buttons_use_compact_labels():
    import inspect
    from ui.config_tab import _render_validation_steps

    source = inspect.getsource(_render_validation_steps)

    assert '"+COMMAND/CHECK"' in source
    assert '"+PROMPT"' in source
    assert "+ Add Command/Output" not in source
    assert "+ Add Prompt" not in source


def test_clear_llm_helper_model_selection_blanks_stale_openai_model(monkeypatch):
    import streamlit as st
    from ui.config_tab import _clear_llm_helper_model_selection

    state = {
        "bash_llm_helper_model": "stale-model",
        "bash_llm_helper_openai_models": [{"name": "stale-model"}],
        "bash_llm_helper_openai_model_manual_widget": "stale-model",
        "bash_llm_helper_openai_model_sel": "stale-model",
    }
    monkeypatch.setattr(st, "session_state", state)

    _clear_llm_helper_model_selection("bash", "OpenAI-Compatible")

    assert state["bash_llm_helper_model"] == ""
    assert state["bash_llm_helper_openai_models"] == []
    assert state["bash_llm_helper_openai_model_manual_widget"] == ""
    assert "bash_llm_helper_openai_model_sel" not in state


def test_clear_llama_openai_model_selection_blanks_stale_model(monkeypatch):
    import streamlit as st
    from ui.config_tab import _clear_llama_openai_model_selection

    state = {
        "llama_cli_model_name": "stale-model",
        "llama_cli_openai_models": [{"name": "stale-model"}],
        "_llama_openai_model_manual": "stale-model",
        "_llama_openai_model_sel": "stale-model",
    }
    monkeypatch.setattr(st, "session_state", state)

    _clear_llama_openai_model_selection()

    assert state["llama_cli_model_name"] == ""
    assert state["llama_cli_openai_models"] == []
    assert state["_llama_openai_model_manual"] == ""
    assert "_llama_openai_model_sel" not in state


def test_fetch_failure_paths_clear_stale_model_selections():
    with open("ui/config_tab.py") as f:
        source = f.read()

    assert '_clear_llm_helper_model_selection(pfx, "Ollama")' in source
    assert '_clear_llm_helper_model_selection(pfx, "OpenAI-Compatible")' in source
    assert "_clear_llama_openai_model_selection()" in source


def test_llama_advanced_options_use_expected_defaults_and_keys():
    """Advanced Options must seed the real config keys with evaluator defaults."""
    with open("ui/config_tab.py") as f:
        config_src = f.read()
    with open("ui/execute_tab.py") as f:
        execute_src = f.read()
    # llama_cli_bot's session-state keys/defaults live on the plugin itself
    # (core/bot_types/llama_cli_bot.py), not in the bot-agnostic core/state.py —
    # see core/bot_types/base.py's BotTypePlugin.session_defaults.
    with open("core/bot_types/llama_cli_bot.py") as f:
        state_src = f.read()

    for expected in (
        'value_key_suffix="temperature"',
        "default_value=0.8",
        "default_value=99",
        "default_value=4",
        "default_value=40",
        "default_value=0.9",
        "default_value=0.1",
        "default_value=1.1",
        "default_value=0.0",
        "default_value=512",
        "default_value=-1",
        "default_value=10000.0",
        "default_value=1.0",
    ):
        assert expected in config_src

    for expected in (
        '"llama_cli_en_freq_penalty"',
        '"llama_cli_freq_penalty"',
        '"llama_cli_en_rope_freq_base"',
        '"llama_cli_rope_freq_base"',
        '"llama_cli_en_rope_freq_scale"',
        '"llama_cli_rope_freq_scale"',
        '"llama_cli_custom_flags"',
    ):
        assert expected in state_src

    assert '"seed":                cfg.get("seed", -1)' in execute_src
    assert '"rope_freq_base":      cfg.get("rope_freq_base", 10000.0)' in execute_src
    assert '"rope_freq_scale":     cfg.get("rope_freq_scale", 1.0)' in execute_src


# ── 5. Template integration in app.py ─────────────────────────────────────────

def test_app_imports_bash_templates():
    """app.py must import BASH_BOT_TEMPLATES (tested via the dialog function source)."""
    import ast
    with open("app.py") as f:
        src = f.read()
    assert "BASH_BOT_TEMPLATES" in src
    assert "bash_templates" in src


def test_app_does_not_render_batch_or_comparison_tabs():
    """Batch Evaluation and Model Comparison should not be top-level app tabs."""
    with open("app.py") as f:
        src = f.read()
    assert "Batch Evaluation" not in src
    assert "Model Comparison" not in src
    assert "batch_tab.render" not in src
    assert "comparison_tab.render" not in src


def test_template_config_deep_copied():
    """Creating a project from template must not share references with BASH_BOT_TEMPLATES."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    fc = BASH_BOT_TEMPLATES["file_creator"]
    project_cfg = copy.deepcopy(fc)
    project_cfg["startup_commands"][0]["commands"][0]["command"] = "MUTATED"
    # Original must be unchanged
    assert BASH_BOT_TEMPLATES["file_creator"]["startup_commands"][0]["commands"][0]["command"] != "MUTATED"
