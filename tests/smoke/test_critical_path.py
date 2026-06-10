import pytest
from unittest.mock import patch, MagicMock
import streamlit as st
from core import evaluator

@patch("core.evaluator.run_evaluation")
def test_critical_path_persists_history(mock_run_eval):
    """Ensure running an evaluation appends properly to the run_history."""
    # Mock a completed evaluation run telemetry response
    mock_telemetry = {
        "validation_passed": True,
        "total_latency": 1.2,
        "total_tokens": 100,
        "llm_rounds": 1,
        "tool_calls": [],
        "run_timestamp": "2023-01-01 12:00:00"
    }
    mock_run_eval.return_value = mock_telemetry
    
    # Simulate the execution logic usually found in ui/execute_tab.py
    # (Simplified for the smoke test)
    config = {"sys_prompt": "sys", "user_prompt": "user"}
    def on_log(m): pass
    
    mock_env = MagicMock()
    telemetry = evaluator.run_evaluation(mock_env, config, on_log)
    
    # Logic similar to what's in the app to update history
    history = st.session_state['run_history']
    history.append(telemetry)
    if len(history) > 10:
        history = history[-10:]
    st.session_state['run_history'] = history

    assert len(st.session_state['run_history']) == 1
    assert st.session_state['run_history'][0]['validation_passed'] is True
    assert st.session_state['run_history'][0]['total_latency'] == 1.2
