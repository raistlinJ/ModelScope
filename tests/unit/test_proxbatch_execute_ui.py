"""Render tests for the Llama-Server-ProxBatch execute panels.

The suite runs against a stub Streamlit (see tests/conftest.py), so these
exercise the target listing, the per-container progress cards and the details
dialog through a recording double — enough to catch a broken layout call or a
mis-keyed widget without a browser.
"""

import pytest

import ui.execute_tab as execute_tab
from core.batch_progress import observe_log, start_container
from core.proxbatch import new_batch_state


class _Recorder:
    """Context-manager-friendly stand-in for a Streamlit container/column."""

    def __init__(self, page):
        self._page = page

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def __getattr__(self, name):
        return getattr(self._page, name)


class _StubPage:
    """Records what a render function drew, and answers widget calls."""

    def __init__(self, session_state, clicked_buttons=()):
        self.session_state = session_state
        self.clicked = set(clicked_buttons)
        self.text: list[str] = []
        self.button_keys: list[str] = []
        self.progress_calls: list[tuple[float, str]] = []
        self.expanders: list[str] = []
        self.tabs_drawn: list[str] = []
        self.reruns = 0

    # ── output ────────────────────────────────────────────────────────────
    def _record(self, body="", *args, **kwargs):
        self.text.append(str(body))

    markdown = caption = warning = info = error = success = write = _record

    def metric(self, label, value, **kwargs):
        self.text.append(f"{label}: {value}")

    def divider(self):
        pass

    # ── layout ────────────────────────────────────────────────────────────
    def container(self, *args, **kwargs):
        return _Recorder(self)

    def empty(self):
        return _Recorder(self)

    def expander(self, label, **kwargs):
        self.expanders.append(label)
        return _Recorder(self)

    def columns(self, spec, **kwargs):
        count = spec if isinstance(spec, int) else len(spec)
        return [_Recorder(self) for _ in range(count)]

    def tabs(self, labels):
        self.tabs_drawn.extend(labels)
        return [_Recorder(self) for _ in labels]

    # ── widgets ───────────────────────────────────────────────────────────
    def progress(self, value, text=""):
        self.progress_calls.append((value, text))

    def button(self, label, key=None, **kwargs):
        self.button_keys.append(key)
        return key in self.clicked

    def rerun(self, scope=None):
        self.reruns += 1


@pytest.fixture
def page(monkeypatch):
    import streamlit as st

    stub = _StubPage(st.session_state)
    monkeypatch.setattr(execute_tab, "st", stub)
    return stub


def _project():
    return {
        "id": "batch-project",
        "name": "ProxBatch",
        "type": "llama_server_proxbatch_bot",
        "config": {
            "pct_vmids": ["100", "101"],
            "pct_vmid_names": {"100": "kali-one", "101": ""},
            "startup_commands": [{"commands": [{"command": "echo start"}]}],
            "validation_sets": [{"name": "smoke", "steps": [{"commands": [{"command": "true"}]}]}],
            "completion_commands": [],
        },
    }


def _seed_batch(page, project, **container_overrides):
    batch = new_batch_state(
        project["config"], project["config"]["validation_sets"],
    )
    for vmid, updates in container_overrides.items():
        batch["containers"][vmid].update(updates)
    page.session_state[execute_tab._proxbatch_state_key(project)] = batch
    return batch


# ── Target listing ────────────────────────────────────────────────────────────

def test_targets_listing_names_every_container_and_phase(page):
    execute_tab._render_proxbatch_targets(_project())

    assert page.expanders == ["**🎯 Batch Targets** — 2 LXC container(s)"]
    table = "\n".join(page.text)
    assert "Startup (1)" in table
    assert "Validation (1)" in table
    assert "Completion (0)" in table
    assert "| 1 | `100` | kali-one |" in table
    assert "| 2 | `101` | — |" in table


def test_targets_listing_warns_when_nothing_is_selected(page):
    project = _project()
    project["config"]["pct_vmids"] = []

    execute_tab._render_proxbatch_targets(project)

    assert any("No LXC containers selected" in line for line in page.text)


# ── Progress cards ────────────────────────────────────────────────────────────

def test_progress_cards_show_percent_phase_and_current_step(page):
    project = _project()
    batch = _seed_batch(page, project)
    running = batch["containers"]["100"]
    start_container(running)
    observe_log(running, "[STARTUP] echo start")

    execute_tab._render_proxbatch_progress(project)

    body = "\n".join(page.text)
    assert "0 of 2 finished, 1 running" in body
    assert "Startup · 1/2 steps" in body
    assert "↳ echo start" in body
    assert page.progress_calls[0] == (0.5, "50% — Running")
    assert page.progress_calls[1] == (0.0, "0% — Not started")
    assert page.button_keys == [
        "llama_server_proxbatch_exec_detail_100",
        "llama_server_proxbatch_exec_detail_101",
    ]


def test_progress_panel_is_silent_before_the_first_run(page):
    execute_tab._render_proxbatch_progress(_project())

    assert page.text == []
    assert page.button_keys == []


def test_details_click_requests_a_dialog_on_the_next_app_run(page, monkeypatch):
    import streamlit as st

    stub = _StubPage(st.session_state, clicked_buttons=["llama_server_proxbatch_exec_detail_101"])
    monkeypatch.setattr(execute_tab, "st", stub)
    project = _project()
    _seed_batch(stub, project)

    execute_tab._render_proxbatch_progress(project)

    assert stub.session_state[execute_tab._PROXBATCH_DETAIL_KEY] == "101"
    assert stub.reruns == 1


def test_progress_falls_back_to_saved_telemetry_after_a_restart(page):
    project = _project()
    page.session_state["telemetry"] = {
        "batch_containers": [
            {"vmid": "100", "name": "kali-one", "state": "passed", "phase": "done",
             "percent": 100, "units_started": 2, "total_units": 2, "current_step": "Finished"},
        ],
    }

    execute_tab._render_proxbatch_progress(project)

    assert page.progress_calls == [(1.0, "100% — Validation passed")]
    assert any("1 of 1 finished" in line for line in page.text)


# ── Details dialog ────────────────────────────────────────────────────────────

def test_details_dialog_shows_that_container_s_own_logs(page):
    project = _project()
    batch = _seed_batch(page, project)
    state = batch["containers"]["100"]
    state["logs_setup"].append({"text": "[STARTUP] echo start", "tag": "cmd"})
    state["telemetry"] = {"total_latency": 4.25}
    start_container(state)

    execute_tab._render_proxbatch_detail_dialog(project, "100")

    body = "\n".join(page.text)
    assert "VMID 100 · kali-one" in body
    assert "Latency: 4.25s" in body
    assert "echo start" in body
    assert page.tabs_drawn == ["Setup/Cleanup Log", "Validation Log"]


def test_details_dialog_explains_when_logs_were_not_retained(page):
    project = _project()
    page.session_state["telemetry"] = {
        "batch_containers": [{"vmid": "100", "state": "passed", "percent": 100}],
    }

    execute_tab._render_proxbatch_detail_dialog(project, "100")

    assert any("no longer in memory" in line for line in page.text)


def test_details_dialog_handles_an_unknown_container(page):
    execute_tab._render_proxbatch_detail_dialog(_project(), "999")

    assert any("No run details recorded for VMID 999" in line for line in page.text)
