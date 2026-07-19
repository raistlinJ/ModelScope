from unittest.mock import MagicMock

import pytest

from core.bot_types import get_bot_plugin, refresh_bot_plugins
from core.environment import SSHEnvironment
from ui.config_tab import _bot_prefix_from_state_key, _validation_bot_prompt_title, _validation_bot_supports_prompts


def test_caf_cli_run_plugin_runs_chat_in_configured_environment(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    assert plugin is not None

    environment = MagicMock()
    expected = {"run_aborted": False, "validation_passed": True}
    create_environment = MagicMock(return_value=environment)
    runner = MagicMock(return_value=expected)
    monkeypatch.setattr("core.environment.create_environment", create_environment)
    monkeypatch.setitem(plugin.run_evaluation.__func__.__globals__, "run_caf_cli_run", runner)

    config = {
        "execution_target": "ssh",
        "ssh_host": "caf.lab",
        "ssh_port": 2222,
        "ssh_user": "analyst",
        "ssh_password": "secret",
        "caf_cli_directory": "/opt/cyber-agent-flow",
    }
    on_log = MagicMock()

    assert plugin.run_evaluation(None, config, on_log) is expected
    create_environment.assert_called_once_with(
        ssh=True,
        host="caf.lab",
        port=2222,
        username="analyst",
        password="secret",
        remote_cwd="/opt/cyber-agent-flow",
        project_id=None,
    )
    runner.assert_called_once()
    assert runner.call_args.args[0] is environment
    assert runner.call_args.args[1] is config
    environment.close.assert_called_once()


def test_caf_cli_run_plugin_normalizes_llm_judge_and_validation_sets():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    config = {}

    plugin.normalize_project_config(config)

    assert config["validation_sets"] == []
    assert config["llm_helper_enabled"] is False


def test_caf_cli_defaults_to_api_sse_and_fills_its_required_fields():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    config = {"caf_cli_app_url": "", "caf_cli_app_server_command": ""}

    plugin.normalize_project_config(config)

    assert config["caf_cli_transport"] == "api"
    assert config["caf_cli_app_url"] == "http://127.0.0.1:5055"
    assert config["caf_cli_app_server_command"] == "python3 mcp_kali.py"


def test_caf_validation_prompts_do_not_persist_a_system_prompt():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    config = {
        "validation_sets": [{"steps": [{"commands": [{
            "type": "prompt", "system_prompt": "legacy system prompt", "user_prompt": "Check the target",
        }]}]}],
    }

    plugin.normalize_project_config(config)

    prompt = config["validation_sets"][0]["steps"][0]["commands"][0]
    assert "system_prompt" not in prompt
    assert prompt["user_prompt"] == "Check the target"


def test_caf_cli_prompt_check_uses_one_shot_run_command():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    build_run_command = plugin.run_evaluation.__func__.__globals__["_run_command"]

    command = build_run_command({
        "execution_target": "ssh",
        "caf_cli_command": "./start_cli.sh",
        "caf_cli_provider": "openai",
        "caf_cli_url": "https://llm.example/v1",
        "selected_model": "test-model",
        "user_prompt": "Inspect the approved target",
        "caf_cli_api_key": "secret",
    })

    assert "./start_cli.sh run" in command
    assert "PYTHONUNBUFFERED=1" in command
    assert "-- 'Inspect the approved target'" in command
    assert "--api-key secret" in command


def test_caf_cli_sudo_wraps_managed_commands_with_the_ssh_user_password():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__

    wrapped = globals_["_caf_with_sudo"]("./start_ws.sh", {
        "sudo": True,
        "execution_target": "ssh",
        "ssh_password": "ssh-secret",
        "sudo_password": "ignored-old-value",
    })

    assert "sudo -S -p '' bash -c" in wrapped
    assert "./start_ws.sh" in wrapped
    assert "ssh-secret" in wrapped
    assert "ignored-old-value" not in wrapped


def test_caf_startup_prompt_uses_the_target_llm_judge_not_caf():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    run_prompt = plugin.run_evaluation.__func__.__globals__["_run_target_llm_helper_prompt"]
    env = MagicMock()
    env.execute.return_value = {
        "stdout": '{"message": {"content": "prepared"}}',
        "stderr": "",
        "exit_code": 0,
    }
    context = []

    result = run_prompt(env, {
        "type": "prompt", "system_prompt": "Prepare the environment.", "user_prompt": "List prerequisites.",
        "preserve_context": True,
    }, {
        "llm_helper_enabled": True,
        "llm_helper_backend": "Ollama",
        "llm_helper_ollama_url": "http://localhost:11434",
        "llm_helper_model": "judge-model",
    }, context, lambda _: None)

    assert result == {"exit_code": 0, "stdout": "prepared"}
    assert env.execute.call_args.args[0].startswith("curl ")
    assert "http://localhost:11434/api/chat" in env.execute.call_args.args[0]
    assert context[-1] == {"role": "assistant", "content": "prepared"}


def test_caf_model_fetch_and_cli_test_run_on_the_configured_caf_target(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    fetch_models = globals_["fetch_caf_models"]
    test_cli = globals_["test_caf_cli"]
    env = MagicMock()
    env.execute.return_value = {
        "stdout": '{"models": [{"name": "qwen3:8b"}]}',
        "stderr": "",
        "exit_code": 0,
    }
    monkeypatch.setitem(globals_, "_environment_for_config", MagicMock(return_value=env))
    config = {
        "execution_target": "ssh",
        "ssh_host": "caf.lab",
        "caf_cli_command": "./start_cli.sh",
        "caf_cli_provider": "ollama_direct",
        "caf_cli_url": "http://localhost:11434",
    }

    models, error = fetch_models(config)

    assert error == ""
    assert models == [{"name": "qwen3:8b"}]
    assert "http://localhost:11434/api/tags" in env.execute.call_args.args[0]
    env.close.assert_called_once()

    env.reset_mock()
    env.execute.return_value = {"stdout": "usage: start_cli.sh run", "stderr": "", "exit_code": 0}
    ok, message = test_cli(config)

    assert ok is True
    assert "available" in message
    assert env.execute.call_args.args[0] == "./start_cli.sh run --help"
    env.close.assert_called_once()


def test_caf_tools_are_fetched_and_filtered_on_the_configured_target(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    fetch_tools = globals_["fetch_caf_tools"]
    prepare_tools = globals_["_prepare_selected_tools"]
    env = MagicMock()
    env.execute.return_value = {
        "stdout": '{"tools": [{"name": "nmap"}, {"name": "tcpdump"}]}',
        "stderr": "",
        "exit_code": 0,
    }
    monkeypatch.setitem(globals_, "_environment_for_config", MagicMock(return_value=env))

    tools, error = fetch_tools({"execution_target": "ssh", "caf_cli_tools_config": "kali_tools.json"})

    assert error == ""
    assert [tool["name"] for tool in tools] == ["nmap", "tcpdump"]
    assert env.execute.call_args.args[0] == "cat kali_tools.json"
    env.close.assert_called_once()

    env.write_file.return_value = {}
    selected = prepare_tools(env, {
        "execution_target": "ssh",
        "caf_cli_tool_catalog": tools,
        "caf_cli_enabled_tools": ["nmap"],
    })

    assert selected["caf_cli_tools_config"] == ".modelscope_caf_tools.json"
    written = env.write_file.call_args.args
    assert written[0] == ".modelscope_caf_tools.json"
    assert '"name": "nmap"' in written[1]
    assert '"name": "tcpdump"' not in written[1]


def test_caf_environment_test_reports_ssh_and_directory_failures(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    test_cli = globals_["test_caf_cli"]
    env = MagicMock()
    monkeypatch.setitem(globals_, "_environment_for_config", MagicMock(return_value=env))

    env.connect.side_effect = RuntimeError("authentication failed")
    ok, message = test_cli({"execution_target": "ssh"})
    assert ok is False
    assert message.startswith("SSH connection failed:")

    env.reset_mock()
    env.connect.side_effect = None
    env.execute.return_value = {"stdout": "", "stderr": "cd: no such file or directory", "exit_code": 1}
    ok, message = test_cli({"execution_target": "ssh"})
    assert ok is False
    assert message.startswith("CAF directory is unavailable on the SSH target:")


def test_caf_validation_prompts_run_through_the_caf_cli(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_caf = globals_["run_caf_cli_run"]
    env = MagicMock()
    env.execute.side_effect = [
        {"stdout": "[run] Transcript: runs/validation-1/transcript.md\n", "stderr": "", "exit_code": 0},
        {"stdout": "[run] Transcript: runs/validation-2/transcript.md\n", "stderr": "", "exit_code": 0},
    ]
    env.read_file.side_effect = ["# First CAF validation response", "{}", "# Second CAF validation response", "{}"]
    def validation_sets(_env, _sets, _on_log, *, config, **_kwargs):
        assert config["type"] == "llama_cli_bot"
        assert "system_prompt" not in _sets[0]["steps"][0]["commands"][0]
        first = config["execute_prompt_callback"]("Assess the target", "VALIDATE CMD", True)
        second = config["execute_prompt_callback"]("Summarize the target", "VALIDATE CMD", True)
        assert first["stdout"] == "# First CAF validation response"
        assert second["stdout"] == "# Second CAF validation response"
        return True, []

    monkeypatch.setattr("core.evaluator._run_validation_sets", validation_sets)
    telemetry = run_caf(env, {
        "execution_target": "ssh",
        "caf_cli_command": "./start_cli.sh",
        "caf_cli_provider": "ollama_direct",
        "caf_cli_url": "http://localhost:11434",
        "selected_model": "test-model",
        "validation_sets": [{"name": "CAF prompt", "steps": [{"commands": [{"type": "prompt", "system_prompt": "Ignored"}]}]}],
    }, lambda _: None)

    validation_command = env.execute.call_args_list[1].args[0]
    assert "./start_cli.sh run" in validation_command
    assert "--continue validation-1" in validation_command
    assert "'Summarize the target'" in validation_command
    assert len(telemetry["prompt_responses"]) == 2
    assert telemetry["run_bot_type"] == "caf_cli_run_bot"
    assert "judge_scores" not in telemetry


def test_caf_validation_prompt_forwards_live_noninteractive_output(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_prompt = globals_["_run_caf_validation_prompt"]
    logs = []

    def stream_run(_env, _command, *, timeout, on_chunk, cancel_ref, completed_artifact):
        assert timeout == 600
        assert cancel_ref is None
        assert completed_artifact() is False
        on_chunk("[chat] live progress\n")
        return {"stdout": "[chat] live progress\n", "stderr": "", "exit_code": 0}

    monkeypatch.setitem(globals_, "_execute_caf_run_live", stream_run)
    result = run_prompt(MagicMock(), {"caf_cli_timeout": 600}, "Inspect target", logs.append)

    assert result["stdout"] == "[chat] live progress\n"
    assert "[VALIDATE CAF] [chat] live progress" in logs


def test_caf_validation_replaces_spinner_text_with_a_wait_counter(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_prompt = globals_["_run_caf_validation_prompt"]
    logs = []

    def stream_run(_env, _command, *, on_chunk, **_kwargs):
        on_chunk("waiting…────caf> caf> waiting…\rwaiting…\n")
        on_chunk("[status] Calling model … waiting…\n")
        on_chunk("Assistant: done\n")
        return {"stdout": "Assistant: done\n", "stderr": "", "exit_code": 0}

    monkeypatch.setitem(globals_, "_execute_caf_run_live", stream_run)
    run_prompt(MagicMock(), {}, "Inspect target", logs.append)

    assert globals_["_is_transient_caf_wait"]("waiting…────caf> caf> waiting…") is True
    assert any(message.startswith("[CAF WAIT] Waiting for CAF…") for message in logs)
    assert "[VALIDATE CAF] [status] Calling model …" in logs
    assert "[VALIDATE CAF] Assistant: done" in logs
    assert not any("caf> caf>" in message for message in logs)


def test_caf_background_updates_a_single_wait_row(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    background = globals_["_run_caf_cli_background"]
    session_logs = []

    class FakeSessionLog:
        def log(self, message):
            session_logs.append(message)

        def save_telemetry(self, _telemetry):
            pass

        def save_config(self, _config):
            pass

        def close(self):
            pass

    def run_evaluation(_project_id, _config, on_log):
        on_log("[CAF WAIT] Waiting for CAF… 0s")
        on_log("[CAF WAIT] Waiting for CAF… 1s")
        on_log("[CAF WAIT DONE] CAF resumed after 1s")
        return {}

    monkeypatch.setattr("core.session_log.SessionLog", FakeSessionLog)
    monkeypatch.setattr(plugin, "run_evaluation", run_evaluation)
    shared = {}
    background(plugin, "testchat", {}, shared)

    assert shared["logs"] == [
        "[CAF WAIT] Waiting for CAF… 1s",
        "[CAF WAIT DONE] CAF resumed after 1s",
    ]
    assert session_logs == [
        "[CAF WAIT] Waiting for CAF… 0s",
        "[CAF WAIT DONE] CAF resumed after 1s",
    ]


def test_caf_run_id_is_available_from_its_early_started_event():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    run_id = plugin.run_evaluation.__func__.__globals__["_caf_run_id"]

    assert run_id("[started] run_id=2026-07-18_00-00-00_cli tools=14") == "2026-07-18_00-00-00_cli"


def test_caf_validation_recovers_completed_transcript_after_ssh_stream_stalls(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_prompt = globals_["_run_caf_validation_prompt"]
    logs = []
    env = MagicMock()

    def read_file(path):
        if path.endswith("transcript.md"):
            return "# Completed CAF transcript"
        if path.endswith("metadata.json"):
            return '{"status": "completed"}'
        raise AssertionError(path)

    def stalled_stream(_env, _command, *, on_chunk, completed_artifact, **_kwargs):
        on_chunk("[started] run_id=recovered-run tools=1\n")
        assert completed_artifact() is True
        return {
            "stdout": "[started] run_id=recovered-run tools=1\n",
            "stderr": "",
            "exit_code": 0,
            "recovered_from_artifact": True,
        }

    env.read_file.side_effect = read_file
    monkeypatch.setitem(globals_, "_execute_caf_run_live", stalled_stream)
    result = run_prompt(env, {"execution_target": "ssh"}, "Inspect target", logs.append)

    assert result["caf_run_id"] == "recovered-run"
    assert result["stdout"] == "# Completed CAF transcript"
    assert any("recovered the completed transcript" in message for message in logs)


def test_caf_validation_recovers_a_run_id_when_ssh_loses_started_event(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_prompt = globals_["_run_caf_validation_prompt"]
    logs = []

    def stalled_stream(_env, _command, *, completed_artifact, **_kwargs):
        assert completed_artifact() is True
        return {"stdout": "", "stderr": "", "exit_code": 0, "recovered_from_artifact": True}

    monkeypatch.setitem(globals_, "_execute_caf_run_live", stalled_stream)
    monkeypatch.setitem(globals_, "_caf_run_ids", MagicMock(side_effect=[{"old-run"}, {"old-run", "lost-run"}]))
    monkeypatch.setitem(globals_, "_caf_run_completed", lambda _env, _config, run_id: run_id == "lost-run")
    monkeypatch.setitem(globals_, "_transcript", lambda *_args: ("# Recovered transcript", {}))
    result = run_prompt(MagicMock(), {"execution_target": "ssh"}, "Inspect target", logs.append)

    assert result["caf_run_id"] == "lost-run"
    assert result["stdout"] == "# Recovered transcript"
    assert any("Recovered run ID lost-run" in message for message in logs)


def test_caf_api_transport_uses_structured_prompt_callback(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_api = globals_["run_caf_api_run"]
    calls = []

    class FakeApiSession:
        def __init__(self, _env, _config, _on_log):
            self.run_id = "api-run-1"

        def run_prompt(self, prompt, *, preserve_context, cancel_ref):
            calls.append((prompt, preserve_context, cancel_ref))
            return {"stdout": "Structured API response", "stderr": "", "exit_code": 0}

        def transcript(self):
            return ""

        def stop(self):
            calls.append(("stop",))

    def validation_sets(_env, _sets, _on_log, *, config, **_kwargs):
        result = config["execute_prompt_callback"]("Inspect target", "VALIDATE CMD", True)
        assert result["stdout"] == "Structured API response"
        return True, [{"steps": [{"stdout": result["stdout"], "stderr": "", "exit_code": 0}]}]

    monkeypatch.setitem(globals_, "_CafAppSession", FakeApiSession)
    monkeypatch.setattr("core.evaluator._run_validation_sets", validation_sets)
    telemetry = run_api(MagicMock(), {
        "caf_cli_transport": "api",
        "validation_sets": [{"steps": [{"commands": [{"type": "prompt", "user_prompt": "Inspect target"}]}]}],
    }, lambda _message: None)

    assert calls[0][:2] == ("Inspect target", True)
    assert calls[-1] == ("stop",)
    assert telemetry["run_backend"] == "CyberAgentFlow API (not configured)"
    assert telemetry["caf_run_id"] == "api-run-1"
    assert telemetry["prompt_responses"] == [{"prompt": "Inspect target", "response": "Structured API response"}]


def test_caf_api_replays_durable_events_after_its_cursor():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.run_id = "api-run-1"
    session._last_event_sequence = 3
    session._durable_events_available = None
    session._events = globals_["queue"].Queue()
    session._request = MagicMock()
    session._request.return_value.json.return_value = {
        "events": [{"type": "tool_result", "sequence": 4, "tool": "nmap", "exit_code": 0}],
    }

    session._replay_durable_events()

    session._request.assert_called_once_with(
        "GET", "/api/sessions/api-run-1/events?after=3", timeout=(5, 15),
    )
    assert session._durable_events_available is True
    assert session._last_event_sequence == 4
    assert session._events.get_nowait()["type"] == "tool_result"


def test_caf_api_logs_cidr_nmap_progress_with_a_target_count():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    session_class = plugin.run_evaluation.__func__.__globals__["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.on_log = MagicMock()
    session._active_tool_progress = {}

    session._log_event({"type": "tool_call", "tool": "nmap", "args": {"args": "-T5 -p 80 -n 11.0.0.0/24"}})
    session._log_event({"type": "tool_status", "tool": "nmap", "elapsed_seconds": 2, "stdout_len": 0, "stderr_len": 0})

    assert "256 targets" in session.on_log.call_args.args[0]
    assert "running 2s" in session.on_log.call_args.args[0]
    session._log_event({"type": "tool_result", "tool": "nmap", "exit_code": 0, "duration_ms": 1})


def test_caf_api_retries_a_reset_get_on_a_fresh_closed_connection(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.base_url = "http://caf.test"
    session.http = MagicMock()
    response = MagicMock()
    session.http.request.side_effect = [
        globals_["requests"].ConnectionError("connection reset by peer"),
        response,
    ]
    monkeypatch.setattr(globals_["time"], "sleep", lambda _seconds: None)

    assert session._request("GET", "/api/session/status") is response
    assert session.http.close.call_count == 1
    assert session.http.request.call_count == 2
    assert session.http.request.call_args.kwargs["headers"]["Connection"] == "close"


def test_caf_api_uses_durable_polling_when_supported():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.base_url = "http://caf.test"
    session.http = MagicMock()
    session.on_log = MagicMock()
    session._last_event_sequence = 0
    session._durable_events_available = None
    session._payload = MagicMock(return_value={"model": "test"})
    status = MagicMock()
    status.json.return_value = {"status": "idle"}
    capabilities = MagicMock()
    capabilities.json.return_value = {"durable_event_replay": True}
    started = MagicMock()
    started.json.return_value = {"success": True, "run_id": "api-run-1", "tools": []}
    replay = MagicMock()
    replay.json.return_value = {"events": []}
    session._request = MagicMock(side_effect=[status, capabilities, started, replay])

    session.start()

    assert session.run_id == "api-run-1"
    assert session._durable_events_available is True
    assert session._request.call_args_list[-1].args == ("GET", "/api/sessions/api-run-1/events?after=0&limit=1")
    assert session._request.call_args_list[-1].kwargs == {"timeout": (5, 5)}


def test_caf_api_legacy_app_requires_restart_confirmation():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.config = {}
    session._app_status = MagicMock(return_value={"status": "idle"})
    session._app_capabilities = MagicMock(return_value=None)
    session._launch_managed_app = MagicMock()

    with pytest.raises(globals_["CafAppRestartRequiredError"]):
        session._ensure_app_running()

    session._launch_managed_app.assert_not_called()


def test_caf_api_busy_session_requires_confirmation_without_stopping_it():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.config = {}
    session.on_log = MagicMock()
    session._ensure_app_running = MagicMock(return_value={"status": "running"})
    session._request = MagicMock()
    session._session_started_by_modelscope = False
    session._stopped = False
    session._managed_app_pid = None
    session._tunnel = None

    with pytest.raises(globals_["CafActiveSessionError"]):
        session.start()

    session._request.assert_not_called()
    session.stop()
    session._request.assert_not_called()


def test_caf_execute_stop_sends_an_immediate_api_session_stop(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    stop_active = globals_["_stop_caf_api_session_background"]
    calls = []

    class FakeApiSession:
        def __init__(self, _env, _config, on_log):
            self.on_log = on_log

        def stop_active_session(self):
            calls.append("stop_active_session")
            self.on_log("[CAF APP] Stop signal sent to the active CAF session.")

        def stop(self):
            calls.append("close")

    env = MagicMock()
    monkeypatch.setitem(globals_, "_CafAppSession", FakeApiSession)
    monkeypatch.setitem(globals_, "_environment_for_config", MagicMock(return_value=env))
    shared = {"logs": [], "caf_api_stop_requested": True}

    stop_active({}, shared)

    assert calls == ["stop_active_session", "close"]
    assert shared["caf_api_stop_requested"] is False
    assert any("Stop signal sent" in line for line in shared["logs"])
    env.close.assert_called_once()


def test_caf_api_manages_only_the_app_it_starts():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    session_class = globals_["_CafAppSession"]
    session = session_class.__new__(session_class)
    session.config = {"execution_target": "ssh"}
    session.env = MagicMock(host="caf.example")
    session.on_log = MagicMock()
    session._managed_app_pid = None
    session._managed_app_log = None
    session._request = MagicMock(side_effect=[RuntimeError("connection refused"), MagicMock()])
    session._request.return_value.json.return_value = {"status": "idle"}

    # The second request is the readiness probe after the launcher returns.
    ready_response = MagicMock()
    ready_response.json.return_value = {"status": "idle"}
    session._request.side_effect = [RuntimeError("connection refused"), ready_response]
    session.env.execute.return_value = {"stdout": "4242\n", "stderr": "", "exit_code": 0}

    assert session._ensure_app_running() == {"status": "idle"}
    assert session._managed_app_pid == "4242"
    assert "nohup ./start_ws.sh" in session.env.execute.call_args.args[0]

    session._stop_managed_app()

    assert session._managed_app_pid is None
    assert any("kill -TERM 4242" in call.args[0] for call in session.env.execute.call_args_list)


def test_caf_live_ssh_run_keeps_normal_exit_status_after_channel_closes():
    """A normal Paramiko command exit closes its channel before we read status."""
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    execute_live = plugin.run_evaluation.__func__.__globals__["_execute_caf_run_live"]
    env = MagicMock(spec=SSHEnvironment)
    env.remote_cwd = "/opt/cyber-agent-flow"
    channel = MagicMock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = True
    channel.closed = True
    channel.recv_exit_status.return_value = 0
    env.get_client.return_value.get_transport.return_value.open_session.return_value = channel

    result = execute_live(env, "./start_cli.sh run -- 'hello'", timeout=5, on_chunk=lambda _text: None)

    assert result["exit_code"] == 0
    channel.recv_exit_status.assert_called_once()


def test_caf_live_ssh_run_recovers_when_channel_exits_before_artifact_finishes():
    """A nonzero wrapper exit must not hide CAF's completed durable run."""
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    execute_live = plugin.run_evaluation.__func__.__globals__["_execute_caf_run_live"]
    env = MagicMock(spec=SSHEnvironment)
    env.remote_cwd = "/opt/cyber-agent-flow"
    channel = MagicMock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = -1
    env.get_client.return_value.get_transport.return_value.open_session.return_value = channel

    result = execute_live(
        env, "./start_cli.sh run -- 'hello'", timeout=5, on_chunk=lambda _text: None,
        completed_artifact=lambda: True,
    )

    assert result["exit_code"] == 0
    assert result["recovered_from_artifact"] is True
    assert "early SSH channel exit" in result["stderr"]
    channel.recv_exit_status.assert_called_once()


def test_caf_live_ssh_run_recovers_when_artifact_completes_but_stream_is_silent(monkeypatch):
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    execute_live = globals_["_execute_caf_run_live"]
    clock = [0.0]

    monkeypatch.setattr(globals_["time"], "monotonic", lambda: clock[0])
    monkeypatch.setattr(globals_["time"], "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    env = MagicMock(spec=SSHEnvironment)
    env.remote_cwd = "/opt/cyber-agent-flow"
    channel = MagicMock()
    channel.recv_ready.return_value = False
    channel.recv_stderr_ready.return_value = False
    channel.exit_status_ready.return_value = False
    env.get_client.return_value.get_transport.return_value.open_session.return_value = channel

    result = execute_live(
        env, "./start_cli.sh run -- 'hello'", timeout=60, on_chunk=lambda _text: None,
        completed_artifact=lambda: clock[0] >= 10.0,
    )

    assert result["exit_code"] == 0
    assert result["recovered_from_artifact"] is True
    channel.close.assert_called()
    channel.recv_exit_status.assert_not_called()


def test_caf_stop_request_skips_remaining_commands_and_marks_run_aborted():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    globals_ = plugin.run_evaluation.__func__.__globals__
    run_caf = globals_["run_caf_cli_run"]
    run_steps = globals_["_run_caf_command_steps"]
    env = MagicMock()
    cancel_ref = [True]

    calls = run_steps(
        env, [{"commands": [{"command": "should-not-run"}]}],
        label="STARTUP", default_timeout=60, on_log=lambda _message: None,
        config={"cancel_requested_ref": cancel_ref}, helper_context=[],
    )
    assert calls == []
    env.execute.assert_not_called()

    telemetry = run_caf(env, {
        "cancel_requested_ref": cancel_ref,
        "validation_sets": [{"steps": [{"commands": [{"command": "should-not-run"}]}]}],
    }, lambda _message: None)
    assert telemetry["run_aborted"] is True
    assert telemetry["validation_passed"] is False
    env.execute.assert_not_called()


def test_caf_validation_editor_supports_prompt_checks():
    assert _validation_bot_supports_prompts("caf_cli") is True
    assert _validation_bot_prompt_title("caf_cli") == "Configured CYBERAGENTFLOW CLI"


def test_caf_startup_and_completion_use_the_caf_llm_judge_toggle():
    assert _bot_prefix_from_state_key("caf_cli_startup_commands") == "caf_cli"
    assert _bot_prefix_from_state_key("caf_cli_completion_commands") == "caf_cli"


def test_caf_execution_log_text_strips_terminal_controls():
    refresh_bot_plugins()
    plugin = get_bot_plugin("caf_cli_run_bot")
    clean_log = plugin.run_evaluation.__func__.__globals__["_clean_execution_log_text"]

    assert clean_log("\x1b[31merror\x1b[0m\r\nready\x00") == "error\nready"
