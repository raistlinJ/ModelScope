"""
Unit tests for the Llama-Server-Bot pieces of ui.config_tab:
  - _state_prefix_from_test_result_key (generalised sudo-prefix recovery)
  - _flush_llama_server_config (execution_target must reflect the UI, not be
    hardcoded to "local")
  - _test_llama_server_run (Check Status now actually instantiates the
    managed server instead of only probing an existing one)

All process/network calls are mocked — no real llama-server binary or port
is touched.
"""
import streamlit as st
from unittest.mock import MagicMock, patch

from ui.config_tab import (
    _state_prefix_from_test_result_key,
    _flush_llama_server_config,
    _test_llama_server_run,
)


def _project():
    return {"id": "p1", "name": "Test", "type": "llama_server_bot", "config": {}}


def _set_session(**overrides):
    """_test_llama_server_run() calls _flush_llama_server_config() first, which
    overwrites project["config"] FROM st.session_state — so tests must seed
    session_state, not project["config"] directly, or the flush clobbers it."""
    defaults = {
        "llama_server_model_dir": "/models",
        "llama_server_model_name": "server.gguf",
        "llama_server_binary_path": "",
        "llama_server_tokens": 32768,
        "llama_server_custom_flags": "",
        "llama_server_server_host": "127.0.0.1",
        "llama_server_server_port": 18080,
        "llama_server_openai_verify_ssl": True,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        st.session_state[key] = value


# ── _state_prefix_from_test_result_key ────────────────────────────────────────

class TestStatePrefixFromTestResultKey:
    def test_bash_local(self):
        assert _state_prefix_from_test_result_key("bash_local_test_result") == "bash"

    def test_bash_pct(self):
        assert _state_prefix_from_test_result_key("bash_pct_test_result") == "bash"

    def test_llama_cli_local(self):
        assert _state_prefix_from_test_result_key("llama_cli_local_test_result") == "llama_cli"

    def test_llama_cli_pct(self):
        assert _state_prefix_from_test_result_key("llama_cli_pct_test_result") == "llama_cli"

    def test_llama_server_local(self):
        """Regression: naive split('_')[0] would wrongly return 'llama' here."""
        assert _state_prefix_from_test_result_key("llama_server_local_test_result") == "llama_server"

    def test_llama_server_pct(self):
        assert _state_prefix_from_test_result_key("llama_server_pct_test_result") == "llama_server"


# ── _flush_llama_server_config ────────────────────────────────────────────────

class TestFlushLlamaServerExecutionTarget:
    def test_execution_target_reflects_session_state(self):
        """Regression: this used to be hardcoded to "local" regardless of the
        UI selection, silently discarding ssh/pct configuration on every flush."""
        project = _project()
        st.session_state["llama_server_execution_target"] = "ssh"
        st.session_state["llama_server_ssh_host"] = "10.0.0.5"
        _flush_llama_server_config(project)
        assert project["config"]["execution_target"] == "ssh"
        assert project["config"]["ssh_host"] == "10.0.0.5"

    def test_defaults_to_local_when_unset(self):
        project = _project()
        _flush_llama_server_config(project)
        assert project["config"]["execution_target"] == "local"

    def test_pct_target_flushed(self):
        project = _project()
        st.session_state["llama_server_execution_target"] = "pct"
        st.session_state["llama_server_pct_vmid"] = "101"
        _flush_llama_server_config(project)
        assert project["config"]["execution_target"] == "pct"
        assert project["config"]["pct_vmid"] == "101"


# ── _test_llama_server_run ────────────────────────────────────────────────────

class TestTestLlamaServerRun:
    def test_no_model_selected_reports_error(self):
        _set_session(llama_server_model_name="")
        project = _project()
        _test_llama_server_run(project)
        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "error"
        assert "No model selected" in msg

    @patch("core.llama_server.get_server_info")
    @patch("core.llama_server.port_open", return_value=True)
    @patch("core.evaluator._start_managed_llama_server")
    def test_already_running_reports_live_status_without_starting(
        self, mock_start, mock_port_open, mock_info
    ):
        mock_info.return_value = {"model_path": "/models/server.gguf", "n_ctx": 4096}
        _set_session()
        project = _project()
        _test_llama_server_run(project)

        mock_start.assert_not_called()
        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "ok"
        assert "already running" in msg.lower()
        assert "server.gguf" in msg

    @patch("core.llama_server.get_server_info", return_value=None)
    @patch("core.llama_server.port_open", return_value=True)
    @patch("core.evaluator._start_managed_llama_server")
    def test_already_running_but_unresponsive_is_an_error(
        self, mock_start, mock_port_open, mock_info
    ):
        _set_session()
        project = _project()
        _test_llama_server_run(project)

        mock_start.assert_not_called()
        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "error"
        assert "already listening" in msg.lower()

    @patch("core.llama_server.get_server_info")
    @patch("core.llama_server.port_open", return_value=False)
    @patch("core.evaluator._managed_llama_server_advanced_flags", return_value="-ngl 20")
    @patch("core.evaluator._start_managed_llama_server")
    def test_successful_launch_starts_verifies_and_terminates(
        self, mock_start, mock_advanced_flags, mock_port_open, mock_info
    ):
        proc = MagicMock()
        mock_start.return_value = proc
        mock_info.return_value = {"model_path": "/models/server.gguf", "n_ctx": 8192}

        _set_session(
            llama_server_binary_path="/opt/llama.cpp/llama-server",
            llama_server_tokens=8192,
            llama_server_custom_flags="--jinja",
            llama_server_server_host="127.0.0.1",
            llama_server_server_port=18080,
        )
        project = _project()
        _test_llama_server_run(project)

        mock_start.assert_called_once()
        args = mock_start.call_args.args
        assert args[0] == "/opt/llama.cpp/llama-server"
        assert args[1] == "/models/server.gguf"
        assert args[2] == 8192
        assert args[3] == 18080
        assert args[4] == "127.0.0.1"
        assert mock_start.call_args.kwargs["custom_flags"] == "--jinja"
        assert mock_start.call_args.kwargs["advanced_flags"] == "-ngl 20"

        proc.terminate.assert_called_once()

        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "ok"
        assert "Test successful" in msg
        assert "server.gguf" in msg

    @patch("core.llama_server.port_open", return_value=False)
    @patch("core.evaluator._managed_llama_server_advanced_flags", return_value="")
    @patch("core.evaluator._start_managed_llama_server", side_effect=RuntimeError("boom"))
    def test_launch_failure_reports_error(self, mock_start, mock_advanced_flags, mock_port_open):
        _set_session()
        project = _project()
        _test_llama_server_run(project)

        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "error"
        assert "boom" in msg

    @patch("core.llama_server.get_server_info", return_value=None)
    @patch("core.llama_server.port_open", return_value=False)
    @patch("core.evaluator._managed_llama_server_advanced_flags", return_value="")
    @patch("core.evaluator._start_managed_llama_server")
    def test_started_but_unresponsive_still_terminates(
        self, mock_start, mock_advanced_flags, mock_port_open, mock_info
    ):
        proc = MagicMock()
        mock_start.return_value = proc
        _set_session()
        project = _project()
        _test_llama_server_run(project)

        proc.terminate.assert_called_once()
        level, msg, _ = st.session_state["_llama_server_svc_result"]
        assert level == "error"
        assert "didn't return model info" in msg
