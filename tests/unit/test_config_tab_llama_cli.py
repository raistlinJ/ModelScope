"""
Unit tests for the Llama-CLI-Bot "Test Run" piece of ui.config_tab:
  - _test_llama_cli_run must error when required input (binary path) is
    missing, rather than silently assuming llama-cli is on PATH.
"""
import streamlit as st
from unittest.mock import MagicMock, patch

from ui.config_tab import _test_llama_cli_run


def _project():
    return {"id": "p1", "name": "Test", "type": "llama_cli_bot", "config": {}}


def _set_session(**overrides):
    defaults = {
        "llama_cli_execution_target": "local",
        "llama_cli_model_dir": "/models",
        "llama_cli_model_name": "llama3.gguf",
        "llama_cli_binary_path": "llama-cli",
        "llama_cli_discovered_models": [],
        "llama_cli_sudo": False,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        st.session_state[key] = value


class TestTestLlamaCliRun:
    def test_no_model_selected_reports_error(self):
        _set_session(llama_cli_model_name="", llama_cli_model_dir="")
        _test_llama_cli_run(_project())
        level, msg, _ = st.session_state["_llama_svc_result"]
        assert level == "error"
        assert "No model selected" in msg

    def test_no_binary_path_reports_error_instead_of_defaulting(self):
        """binary_path is required input — leaving it blank must not silently
        assume 'llama-cli' is on PATH."""
        _set_session(llama_cli_binary_path="")
        with patch("core.environment.LocalEnvironment") as mock_env_cls:
            mock_env_cls.return_value = MagicMock()
            _test_llama_cli_run(_project())
        level, msg, _ = st.session_state["_llama_svc_result"]
        assert level == "error"
        assert "No binary path configured" in msg
