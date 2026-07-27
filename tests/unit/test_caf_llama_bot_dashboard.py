"""Unit tests for the CAF + llama.cpp bot's dashboard dispatch/rendering hooks."""

from __future__ import annotations

from unittest.mock import MagicMock

from ui import dashboard_tab


def test_render_dispatches_to_llama_cli_dashboard_with_caf_llama_metrics_key(monkeypatch):
    project = {"id": "proj-1", "type": "caf_llama_bot", "name": "CAF + llama.cpp"}
    state = {"active_project_id": "proj-1", "projects": [project]}
    monkeypatch.setattr(dashboard_tab.st, "session_state", state)
    monkeypatch.setattr(dashboard_tab, "_hydrate_project_history_if_empty", lambda p: None)
    monkeypatch.setattr(dashboard_tab, "_selected_telemetry_for_export", lambda p: None)
    monkeypatch.setattr(dashboard_tab, "_render_dashboard_heading", lambda p, t: None)
    spy = MagicMock()
    monkeypatch.setattr(dashboard_tab, "_render_llama_cli_dashboard", spy)

    dashboard_tab.render()

    spy.assert_called_once_with(project, bot_type="caf_llama_bot", metrics_key="caf_llama_metrics_matrix")


def _fake_columns(spec, **kwargs):
    n = spec if isinstance(spec, int) else len(spec)
    return tuple(MagicMock() for _ in range(n))


def test_caf_transcript_gate_renders_for_caf_llama_bot(monkeypatch):
    """bot_type='caf_llama_bot' must take the same CAF-transcript branch as
    caf_cli_run_bot, not the generic prompt_responses fallback."""
    project = {"id": "proj-2", "type": "caf_llama_bot", "name": "CAF + llama.cpp", "config": {}}
    telemetry = {
        "run_timestamp": "2026-07-24 00:00:00",
        "run_bot_type": "caf_llama_bot",
        "total_latency": 1.0,
        "prompt_responses": [{"prompt": "legacy", "response": "should not render"}],
        "tool_calls": [],
        "validation_passed": True,
        "caf_transcript_events": [
            {"type": "response", "text": "hello from the assistant"},
            {"type": "tool_result", "output": "tool ran"},
        ],
    }
    state = {"run_history_proj-2": [telemetry], "_history_hydrated_proj-2": True}
    monkeypatch.setattr(dashboard_tab.st, "session_state", state)

    fake_st = MagicMock()
    fake_st.columns.side_effect = _fake_columns
    fake_st.tabs.side_effect = lambda labels, **kw: tuple(MagicMock() for _ in labels)
    fake_st.session_state = state
    monkeypatch.setattr(dashboard_tab, "st", fake_st)

    dashboard_tab._render_llama_cli_dashboard(
        project, bot_type="caf_llama_bot", metrics_key="caf_llama_metrics_matrix"
    )

    subheader_calls = [str(call.args[0]) if call.args else "" for call in fake_st.subheader.call_args_list]
    assert any(text.startswith("CAF Transcript") for text in subheader_calls)
    assert not any(text.startswith("Prompt Responses") for text in subheader_calls)
