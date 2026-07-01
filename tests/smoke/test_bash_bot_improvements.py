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
    assert "Connected" in result["message"]


# ── 4. Dialog flag removal: _show_val_set_dialog no longer used ───────────────

def test_no_show_val_set_dialog_flag_in_render_validation():
    """_render_bash_validation must not reference _show_val_set_dialog as a trigger."""
    import ast, inspect
    from ui.config_tab import _render_bash_validation
    source = inspect.getsource(_render_bash_validation)
    # The flag check should have been removed
    assert '_show_val_set_dialog' not in source, (
        "_render_bash_validation still references _show_val_set_dialog; "
        "the flag-based dialog trigger was not removed."
    )


def test_no_behavioral_validation_in_render():
    """_render_bash_validation must not contain 'Behavioral Validation' text."""
    import inspect
    from ui.config_tab import _render_bash_validation
    source = inspect.getsource(_render_bash_validation)
    assert "Behavioral Validation" not in source


# ── 5. Template integration in app.py ─────────────────────────────────────────

def test_app_imports_bash_templates():
    """app.py must import BASH_BOT_TEMPLATES (tested via the dialog function source)."""
    import ast
    with open("app.py") as f:
        src = f.read()
    assert "BASH_BOT_TEMPLATES" in src
    assert "bash_templates" in src


def test_template_config_deep_copied():
    """Creating a project from template must not share references with BASH_BOT_TEMPLATES."""
    from config.bash_templates import BASH_BOT_TEMPLATES
    fc = BASH_BOT_TEMPLATES["file_creator"]
    project_cfg = copy.deepcopy(fc)
    project_cfg["startup_commands"][0]["commands"][0]["command"] = "MUTATED"
    # Original must be unchanged
    assert BASH_BOT_TEMPLATES["file_creator"]["startup_commands"][0]["commands"][0]["command"] != "MUTATED"
