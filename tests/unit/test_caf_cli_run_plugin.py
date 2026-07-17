from unittest.mock import MagicMock

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

    def stream_run(_env, _command, *, timeout, on_chunk, cancel_ref):
        assert timeout == 600
        assert cancel_ref is None
        on_chunk("[chat] live progress\n")
        return {"stdout": "[chat] live progress\n", "stderr": "", "exit_code": 0}

    monkeypatch.setitem(globals_, "_execute_caf_run_live", stream_run)
    result = run_prompt(MagicMock(), {"caf_cli_timeout": 600}, "Inspect target", logs.append)

    assert result["stdout"] == "[chat] live progress\n"
    assert "[VALIDATE CAF] [chat] live progress" in logs


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
