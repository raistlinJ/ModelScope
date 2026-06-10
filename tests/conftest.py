import pytest
from unittest.mock import MagicMock
import sys
import types

# Mock streamlit before any other imports
if "streamlit" not in sys.modules:
    mock_st = types.ModuleType("streamlit")
    mock_st.session_state = {}
    sys.modules["streamlit"] = mock_st

import streamlit as st

@pytest.fixture(autouse=True)
def mock_streamlit_state(monkeypatch):
    """Automatically mocks streamlit session state for all tests."""
    # Create a mock object that behaves like a dictionary and object
    class MockState(dict):
        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError:
                raise AttributeError(f"'MockState' object has no attribute '{key}'")
        def __setattr__(self, key, value):
            self[key] = value
        def setdefault(self, key, default):
            if key not in self:
                self[key] = default
            return self[key]

    mock_state = MockState({
        "backend_type": "llama.cpp",
        "llm_url": "http://localhost:8080",
        "llm_models": ["llama3-8b.gguf"],
        "selected_model": "llama3-8b.gguf",
        "run_history": [],
        "context_size": 2048,
        "mcp_running": False,
        "llama_server_running": False,
        "cancel_requested": False,
        "metrics_matrix": [],
        "fail_patterns": [],
        "sys_prompt": "",
        "user_prompt": "",
    })

    # Inject it into st.session_state
    monkeypatch.setattr(st, "session_state", mock_state)
    return mock_state
