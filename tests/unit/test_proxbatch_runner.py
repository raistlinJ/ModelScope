"""Unit tests for core.proxbatch — the per-container loop and roll-up.

This is the batch itself, shared by the Execute tab and the CLI, so the tests
drive it through both entry points: the pure functions here and the plugin's
run_evaluation (which is what `modelscope project` calls).
"""

from unittest.mock import MagicMock, patch

from core.batch_progress import ABORTED, FAILED, PASSED, SKIPPED
from core.bot_types import get_bot_plugin, refresh_bot_plugins
from core.environment import LocalEnvironment, PCTEnvironment
from core.proxbatch import (
    aggregate_batch_telemetry,
    container_config,
    container_env,
    new_batch_state,
    run_pct_batch,
    selected_vmids,
)


def _config(**overrides):
    config = {
        "pct_vmids": ["100", "101"],
        "pct_vmid_names": {"100": "kali-one", "101": "kali-two"},
        "startup_commands": [{"commands": [{"command": "echo start"}]}],
        "validation_sets": [{"steps": [{"commands": [{"command": "true"}]}]}],
        "completion_commands": [],
    }
    config.update(overrides)
    return config


# ── Planning ──────────────────────────────────────────────────────────────────

def test_selected_vmids_are_deduplicated_and_kept_in_order():
    assert selected_vmids({"pct_vmids": ["101", 100, "101", "bad", ""]}) == ["101", "100"]
    assert selected_vmids({"pct_vmids": "not-a-list"}) == []
    assert selected_vmids({}) == []


def test_new_batch_state_plans_every_container_from_the_config():
    containers = new_batch_state(_config())["containers"]

    assert list(containers) == ["100", "101"]
    assert containers["100"]["name"] == "kali-one"
    # one startup command + one validation command
    assert containers["100"]["total_units"] == 2
    assert containers["101"]["state"] == "pending"


def test_new_batch_state_can_plan_from_an_overridden_validation_selection():
    containers = new_batch_state(_config(), validation_sets=[])["containers"]

    assert containers["100"]["total_units"] == 1


def test_container_config_isolates_targets_but_shares_cancellation():
    cancel_ref = [False]
    config = _config(cancel_requested_ref=cancel_ref, execution_target="local")

    item = container_config(config, "101")

    assert item["pct_vmid"] == "101"
    assert item["execution_target"] == "pct"
    assert item["server_in_container"] is True
    assert item["type"] == "llama_server_bot"
    assert item["cancel_requested_ref"] is cancel_ref
    # The batch's own config must not inherit one container's target.
    assert "pct_vmid" not in config
    assert config["execution_target"] == "local"


def test_container_env_wraps_rather_than_replaces_the_callers_environment():
    base = LocalEnvironment()

    env = container_env(base, "101")

    assert isinstance(env, PCTEnvironment)
    assert env.vmid == "101"
    assert env.base_env is base
    # Re-wrapping must not nest PCT environments.
    assert container_env(env, "102").base_env is base


# ── The loop ──────────────────────────────────────────────────────────────────

def test_run_pct_batch_runs_every_container_and_rolls_the_results_up():
    containers = new_batch_state(_config())["containers"]
    logs: list[str] = []

    def run_one(vmid, state):
        return {
            "validation_passed": vmid == "100",
            "total_latency": 1.5,
            "tool_calls": [{"tool": "bash"}],
            "prompt_responses": [{"prompt": "p", "response": "r"}],
        }

    aggregate = run_pct_batch(containers, run_one, on_log=logs.append)

    assert containers["100"]["state"] == PASSED
    assert containers["101"]["state"] == FAILED
    assert aggregate["pct_vmids"] == ["100", "101"]
    assert aggregate["validation_passed"] is False
    assert aggregate["total_latency"] == 3.0
    assert len(aggregate["tool_calls"]) == 2
    assert len(aggregate["prompt_responses"]) == 2
    assert [item["vmid"] for item in aggregate["batch_containers"]] == ["100", "101"]
    assert [item["pct_vmid"] for item in aggregate["batch_results"]] == ["100", "101"]
    assert any("PCT batch 1/2 — VMID 100" in line for line in logs)


def test_run_pct_batch_skips_the_rest_after_a_stop_request():
    containers = new_batch_state(_config())["containers"]
    cancelled = []

    def run_one(vmid, state):
        cancelled.append(True)
        return {"run_aborted": True}

    aggregate = run_pct_batch(
        containers, run_one, is_cancelled=lambda: bool(cancelled),
    )

    assert containers["100"]["state"] == ABORTED
    assert containers["101"]["state"] == SKIPPED
    assert aggregate["run_aborted"] is True
    assert aggregate["interrupted_by_user"] is True


def test_aggregate_reports_no_verdict_when_nothing_validated():
    containers = new_batch_state(_config())["containers"]

    aggregate = aggregate_batch_telemetry(
        containers, [{"validation_passed": None}, {"validation_passed": None}],
    )

    assert aggregate["validation_passed"] is None
    assert aggregate["run_aborted"] is False


# ── The CLI entry point ───────────────────────────────────────────────────────

class TestPluginRunEvaluation:
    def _plugin(self):
        refresh_bot_plugins()
        return get_bot_plugin("llama_server_proxbatch_bot")

    def test_cli_run_evaluates_each_container_in_its_own_environment(self):
        plugin = self._plugin()
        base_env = LocalEnvironment()
        seen = []

        def fake_single_run(self, env, config, on_log):
            seen.append((env.vmid, config["pct_vmid"], config["server_in_container"]))
            return {"validation_passed": True, "total_latency": 2.0}

        with patch(
            "core.bot_types.llama_server_bot.LlamaServerBotPlugin.run_evaluation",
            fake_single_run,
        ):
            telemetry = plugin.run_evaluation(base_env, _config(), lambda *_a, **_k: None)

        assert seen == [("100", "100", True), ("101", "101", True)]
        assert telemetry["run_bot_type"] == "llama_server_proxbatch_bot"
        assert telemetry["validation_passed"] is True
        assert telemetry["total_latency"] == 4.0
        assert [item["state"] for item in telemetry["batch_containers"]] == [PASSED, PASSED]

    def test_cli_run_reports_an_empty_selection_instead_of_running_anywhere(self):
        plugin = self._plugin()
        logs: list[str] = []

        telemetry = plugin.run_evaluation(
            LocalEnvironment(), _config(pct_vmids=[]), lambda msg, *_a, **_k: logs.append(msg),
        )

        assert telemetry["run_aborted"] is True
        assert "No PCT VMIDs" in telemetry["error"]
        assert telemetry["batch_containers"] == []
        assert any("at least one PCT LXC" in line for line in logs)

    def test_one_broken_container_does_not_end_the_batch(self):
        plugin = self._plugin()
        logs: list[str] = []

        def fake_single_run(self, env, config, on_log):
            if config["pct_vmid"] == "100":
                raise RuntimeError("container is not running")
            return {"validation_passed": True, "total_latency": 1.0}

        with patch(
            "core.bot_types.llama_server_bot.LlamaServerBotPlugin.run_evaluation",
            fake_single_run,
        ):
            telemetry = plugin.run_evaluation(
                LocalEnvironment(), _config(), lambda msg, *_a, **_k: logs.append(msg),
            )

        states = [item["state"] for item in telemetry["batch_containers"]]
        assert states == [ABORTED, PASSED]
        assert telemetry["batch_results"][0]["error"] == "container is not running"
        assert any("VMID 100 failed" in line for line in logs)

    def test_a_cancellation_ref_stops_the_batch_between_containers(self):
        plugin = self._plugin()
        cancel_ref = [False]

        def fake_single_run(self, env, config, on_log):
            cancel_ref[0] = True
            return {"validation_passed": True}

        with patch(
            "core.bot_types.llama_server_bot.LlamaServerBotPlugin.run_evaluation",
            fake_single_run,
        ):
            telemetry = plugin.run_evaluation(
                LocalEnvironment(),
                _config(cancel_requested_ref=cancel_ref),
                lambda *_a, **_k: None,
            )

        states = [item["state"] for item in telemetry["batch_containers"]]
        assert states == [ABORTED, SKIPPED]
        assert telemetry["interrupted_by_user"] is True
