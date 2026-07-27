"""Tests for PCT list execution in Llama-Server-ProxBatch."""

from unittest.mock import patch

from core.batch_progress import ABORTED, FAILED, PASSED, SKIPPED
from core.proxbatch import new_batch_state
from ui.execute_tab import _run_llama_server_proxbatch_bot


@patch("core.session_log.SessionLog")
@patch("ui.execute_tab._run_llama_cli_bot")
def test_proxbatch_runs_each_unique_selected_vmid(mock_runner, mock_session_log):
    def run_one(item_project, shared, bot_type):
        vmid = item_project["config"]["pct_vmid"]
        assert item_project["type"] == "llama_server_bot"
        assert item_project["config"]["execution_target"] == "pct"
        assert bot_type == "llama_server_bot"
        shared["telemetry"] = {
            "run_bot_type": "llama_server_bot",
            "pct_vmid": vmid,
            "total_latency": 1.25,
            "validation_passed": True,
            "tool_calls": [{"tool": "bash", "args": {"command": "true"}}],
            "prompt_responses": [],
        }

    mock_runner.side_effect = run_one
    shared = {"logs_setup": [], "cancel_requested": False}
    project = {
        "id": "batch-project",
        "type": "llama_server_proxbatch_bot",
        "config": {"pct_vmids": ["100", 101, "100", "invalid"]},
    }

    _run_llama_server_proxbatch_bot(project, shared)

    assert mock_runner.call_count == 2
    assert shared["completed"] is True
    assert shared["telemetry"]["run_bot_type"] == "llama_server_proxbatch_bot"
    assert shared["telemetry"]["pct_vmids"] == ["100", "101"]
    assert shared["telemetry"]["validation_passed"] is True
    assert shared["telemetry"]["total_latency"] == 2.5
    mock_session_log.return_value.save_telemetry.assert_called_once()


def test_proxbatch_rejects_an_empty_vmid_list():
    shared = {"logs_setup": [], "cancel_requested": False}
    project = {
        "id": "batch-project",
        "type": "llama_server_proxbatch_bot",
        "config": {"pct_vmids": []},
    }

    _run_llama_server_proxbatch_bot(project, shared)

    assert shared["completed"] is True
    assert shared["telemetry"]["run_aborted"] is True
    assert "No PCT VMIDs" in shared["telemetry"]["error"]


# ── Per-container progress ────────────────────────────────────────────────────

def _batch_project(**config):
    base = {
        "pct_vmids": ["100", "101"],
        "pct_vmid_names": {"100": "kali-one", "101": "kali-two"},
        "startup_commands": [{"commands": [{"command": "echo start"}]}],
        "validation_sets": [],
        "completion_commands": [],
    }
    base.update(config)
    return {"id": "batch-project", "type": "llama_server_proxbatch_bot", "config": base}


@patch("core.session_log.SessionLog")
@patch("ui.execute_tab._run_llama_cli_bot")
def test_proxbatch_tracks_progress_and_logs_for_each_container(mock_runner, _session_log):
    def run_one(item_project, shared, bot_type):
        vmid = item_project["config"]["pct_vmid"]
        shared.setdefault("logs_setup", []).append({"text": f"[STARTUP] echo {vmid}", "tag": "cmd"})
        shared.setdefault("logs_validation", []).append({"text": "[VALIDATE] ok", "tag": "val"})
        shared["telemetry"] = {"validation_passed": vmid == "100", "total_latency": 1.5}

    mock_runner.side_effect = run_one
    shared = {"logs_setup": [], "logs_validation": [], "cancel_requested": False}

    _run_llama_server_proxbatch_bot(_batch_project(), shared)

    containers = shared["batch"]["containers"]
    assert containers["100"]["state"] == PASSED
    assert containers["101"]["state"] == FAILED
    assert containers["100"]["name"] == "kali-one"
    assert containers["100"]["percent"] == 100
    # One startup command was planned, and one was announced.
    assert containers["100"]["total_units"] == 1
    assert containers["100"]["units_started"] == 1

    # Each container keeps its own logs, and the batch-wide stream keeps a
    # VMID-prefixed copy so the shared terminals still show everything.
    assert [entry["text"] for entry in containers["101"]["logs_setup"]] == ["[STARTUP] echo 101"]
    assert "[101] [STARTUP] echo 101" in [entry["text"] for entry in shared["logs_setup"]]
    assert "[100] [VALIDATE] ok" in [entry["text"] for entry in shared["logs_validation"]]

    summaries = shared["telemetry"]["batch_containers"]
    assert [(item["vmid"], item["state"]) for item in summaries] == [("100", PASSED), ("101", FAILED)]
    assert "logs_setup" not in summaries[0]


@patch("core.session_log.SessionLog")
@patch("ui.execute_tab._run_llama_cli_bot")
def test_proxbatch_marks_containers_skipped_after_a_stop_request(mock_runner, _session_log):
    shared = {"logs_setup": [], "logs_validation": [], "cancel_requested": False}

    def run_one(item_project, item_shared, bot_type):
        shared["cancel_requested"] = True
        item_shared["telemetry"] = {"run_aborted": True}

    mock_runner.side_effect = run_one

    _run_llama_server_proxbatch_bot(_batch_project(), shared)

    containers = shared["batch"]["containers"]
    assert mock_runner.call_count == 1
    assert containers["100"]["state"] == ABORTED
    assert containers["101"]["state"] == SKIPPED
    assert shared["telemetry"]["run_aborted"] is True


@patch("core.session_log.SessionLog")
@patch("ui.execute_tab._run_llama_cli_bot")
def test_proxbatch_keeps_the_plan_the_execute_tab_seeded(mock_runner, _session_log):
    """The tab plans from the ticked validation sets; the runner must not re-plan."""
    project = _batch_project()
    seeded = new_batch_state(project["config"], [{"steps": [{"commands": [{"command": "true"}]}]}])
    shared = {"logs_setup": [], "cancel_requested": False, "batch": seeded}
    mock_runner.side_effect = lambda *_args: None

    _run_llama_server_proxbatch_bot(project, shared)

    assert shared["batch"] is seeded
    # one startup command + one selected validation command
    assert seeded["containers"]["100"]["total_units"] == 2
