"""
Functional tests for core.llama_server — start, stop, poll_ready,
crash detection, and server reuse logic.
"""
import pytest
from unittest.mock import patch, MagicMock, call
from core import llama_server
import streamlit as st


def _reset_state():
    """Clear llama_server-related session state before each test."""
    for key in list(st.session_state.keys()):
        if key.startswith("llama_server"):
            del st.session_state[key]


@pytest.fixture(autouse=True)
def clean_state():
    _reset_state()
    yield
    _reset_state()


# ── server info ───────────────────────────────────────────────────────────────

@patch("requests.get")
def test_get_server_info_falls_back_to_v1_models_for_vllm(mock_get):
    def side_effect(url, **kw):
        response = MagicMock()
        if url.endswith("/props"):
            response.ok = False
            response.status_code = 404
            return response
        if url.endswith("/v1/models"):
            response.ok = True
            response.json.return_value = {
                "object": "list",
                "data": [{"id": "Qwen/Qwen2.5-Coder-7B-Instruct", "object": "model"}],
            }
            return response
        raise AssertionError(f"unexpected URL: {url}")

    mock_get.side_effect = side_effect

    info = llama_server.get_server_info("http://localhost:8000")

    assert info == {
        "n_ctx": None,
        "model_path": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "source": "v1/models",
    }
    assert mock_get.call_args_list[0].args[0].endswith("/props")
    assert mock_get.call_args_list[1].args[0].endswith("/v1/models")


# ── start — new server ────────────────────────────────────────────────────────

@patch("subprocess.Popen")
@patch("tempfile.NamedTemporaryFile")
@patch("requests.get")
def test_start_fresh(mock_get, mock_tmp, mock_popen):
    mock_get.side_effect = Exception("not running")

    mock_log = MagicMock()
    mock_log.name = "/tmp/llama_fake.log"
    mock_tmp.return_value.__enter__ = MagicMock(return_value=mock_log)
    mock_tmp.return_value = mock_log

    mock_proc = MagicMock()
    mock_proc.pid = 1234
    mock_popen.return_value = mock_proc

    ok, msg = llama_server.start("models/m1.gguf", context_size=2048)

    assert ok is True
    assert "1234" in msg
    assert st.session_state["llama_server_process"] == mock_proc
    assert st.session_state["llama_server_crashed"] is False


@patch("subprocess.Popen")
@patch("requests.get")
def test_start_binary_not_found(mock_get, mock_popen):
    mock_get.side_effect = Exception("not running")
    mock_popen.side_effect = FileNotFoundError("llama-server not found")

    ok, msg = llama_server.start("m.gguf")

    assert ok is False
    assert "not found" in msg.lower()


# ── start — already running, reuse ───────────────────────────────────────────

@patch("requests.get")
def test_start_already_running_reuses(mock_get):
    mock_get.return_value.ok = True
    mock_get.return_value.json.return_value = {
        "default_generation_settings": {"n_ctx": 4096},
        "model_path": "models/m1.gguf",
    }

    ok, msg = llama_server.start("models/m1.gguf", context_size=4096)

    assert ok is True
    assert "Already running" in msg
    assert st.session_state["llama_server_running"] is True


@patch("subprocess.Popen")
@patch("subprocess.run")
@patch("requests.get")
def test_start_restarts_when_ctx_too_small(mock_get, mock_run, mock_popen):
    call_count = [0]

    def side_effect(url, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            # is_running check — server is up
            r = MagicMock()
            r.ok = True
            return r
        if call_count[0] == 2:
            # get_server_info /props — n_ctx too small
            r = MagicMock()
            r.ok = True
            r.json.return_value = {
                "default_generation_settings": {"n_ctx": 1024},
                "model_path": "models/m1.gguf",
            }
            return r
        raise Exception("server restarting")

    mock_get.side_effect = side_effect
    mock_proc = MagicMock()
    mock_proc.pid = 5678
    mock_popen.return_value = mock_proc

    ok, msg = llama_server.start("models/m1.gguf", context_size=4096)

    assert ok is True
    assert "5678" in msg or "Restarting" in msg


# ── stop ──────────────────────────────────────────────────────────────────────

@patch("subprocess.run")
@patch("time.sleep")
def test_stop_terminates_process(mock_sleep, mock_run):
    mock_proc = MagicMock()
    st.session_state["llama_server_process"] = mock_proc
    st.session_state["llama_server_running"] = True

    ok, msg = llama_server.stop()

    assert ok is True
    assert "stopped" in msg.lower()
    mock_proc.terminate.assert_called_once()
    assert st.session_state["llama_server_running"] is False
    assert "llama_server_process" not in st.session_state


@patch("subprocess.run")
@patch("time.sleep")
def test_stop_no_process_still_succeeds(mock_sleep, mock_run):
    # No process in session_state — should not crash
    ok, msg = llama_server.stop()
    assert ok is True
    assert st.session_state["llama_server_running"] is False


# ── poll_ready — crash detection ──────────────────────────────────────────────

def test_poll_ready_detects_crash():
    """Regression: server stuck 'Starting…' when process dies (fix: poll_ready checks proc.poll())."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 1  # process exited with code 1
    st.session_state["llama_server_process"] = mock_proc

    with patch("requests.get", side_effect=Exception("not reachable")):
        result = llama_server.poll_ready("http://localhost:8080")

    assert result is False
    assert st.session_state["llama_server_running"] is False
    assert st.session_state["llama_server_crashed"] is True
    assert st.session_state["llama_server_exit_code"] == 1


@patch("requests.get")
def test_poll_ready_sets_running_when_healthy(mock_get):
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # process still alive
    st.session_state["llama_server_process"] = mock_proc

    mock_get.return_value.ok = True

    result = llama_server.poll_ready("http://localhost:8080")

    assert result is True
    assert st.session_state["llama_server_running"] is True
    assert st.session_state.get("llama_server_crashed") is False


@patch("requests.get")
def test_poll_ready_not_running_yet(mock_get):
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None  # alive but not ready
    st.session_state["llama_server_process"] = mock_proc

    mock_get.return_value.ok = False

    result = llama_server.poll_ready("http://localhost:8080")

    assert result is False
    assert st.session_state["llama_server_running"] is False


def test_poll_ready_no_process_no_server():
    with patch("requests.get", side_effect=Exception("unreachable")):
        result = llama_server.poll_ready("http://localhost:8080")

    assert result is False
    assert st.session_state["llama_server_running"] is False
