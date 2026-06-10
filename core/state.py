import streamlit as st
from config.defaults import (
    LLAMA_CPP_DEFAULT_URL, GGUF_MODELS_DIR,
    MCP_SCRIPT_PATH, DEFAULT_CONTEXT_SIZE,
    LLAMA_SERVER_BIN, MCP_SERVER_BASE_URL,
)
from config.scenarios import SCENARIOS, DEFAULT_SCENARIO


_SCENARIO = SCENARIOS[DEFAULT_SCENARIO]

_DEFAULTS: dict = {
    # Model / backend
    "backend_type":          "llama.cpp",
    "llm_url":               LLAMA_CPP_DEFAULT_URL,
    "model_dir":             GGUF_MODELS_DIR,
    "llm_models":            [],
    "selected_model":        None,
    "selected_model_path":   None,
    "context_size":          DEFAULT_CONTEXT_SIZE,

    # Target Environment
    "target_env_type":       "local",  # local only (ssh is a future release)
    # ── SSH target settings — FUTURE RELEASE ─────────────────────────────────
    # Remote SSH execution is planned for a future release.
    # "target_ssh_host":       "127.0.0.1",
    # "target_ssh_port":       22,
    # "target_ssh_user":       "root",
    # "target_ssh_password":   "",
    # "target_ssh_key_path":   "",
    # ─────────────────────────────────────────────────────────────────────────

    # llama-server management
    "llama_server_bin":      LLAMA_SERVER_BIN,  # editable from UI (fix #29)
    "llama_server_running":  False,

    # MCP (local)
    "mcp_url":       MCP_SCRIPT_PATH,
    "mcp_server_url": MCP_SERVER_BASE_URL,
    "mcp_tools":     {},
    "mcp_running":   False,
    # ── MCP SSH tunnel settings — FUTURE RELEASE ─────────────────────────────
    # MCP SSH tunneling is planned for a future release.
    # "mcp_use_ssh":        False,
    # "mcp_ssh_host":       "",
    # "mcp_ssh_port":       22,
    # "mcp_ssh_user":       "",
    # "mcp_ssh_password":   "",
    # "mcp_ssh_key_path":   "",
    # ─────────────────────────────────────────────────────────────────────────

    # Metrics setup
    "active_scenario":    DEFAULT_SCENARIO,
    "tool_focus":         _SCENARIO.get("related_tool", "file_creator"),
    "validation_command": _SCENARIO["validation_command"],
    "fail_patterns":      list(_SCENARIO["fail_patterns"]),
    "metrics_matrix":     list(_SCENARIO["default_metrics"]),

    # Execute
    "sys_prompt":        _SCENARIO["system_prompt"],
    "user_prompt":       _SCENARIO["user_prompt"],
    "run_logs":          [],
    "run_completed":     False,
    "cancel_requested":  False,   # set True by Cancel button (fix #16)

    # Telemetry (current run)
    "telemetry": {},

    # Run history — list of past telemetry dicts (fix #26)
    "run_history": [],

    # Internal trackers
    "_last_backend":       "llama.cpp",
    "_last_exec_scenario": DEFAULT_SCENARIO,
    # Prompt edit tracking for scenario-change warning (fix #10)
    "_prompts_user_edited": False,
}


def init_state() -> None:
    for key, default in _DEFAULTS.items():
        st.session_state.setdefault(key, default)
