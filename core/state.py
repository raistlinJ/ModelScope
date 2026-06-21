"""Streamlit session-state initialisation and scenario synchronisation.

Defines the default values for every key the UI stores in ``st.session_state``
and keeps scenario-derived keys (prompts, validation command, CAF settings) in
sync when the active scenario changes. UI-only — never imported by the CLI.
"""
import streamlit as st
from config.defaults import (
    LLAMA_CPP_DEFAULT_URL, GGUF_MODELS_DIR,
    MCP_SCRIPT_PATH, DEFAULT_CONTEXT_SIZE,
    LLAMA_SERVER_BIN, MCP_SERVER_BASE_URL,
    EXTERNAL_LLAMA_CPP_URL, EXTERNAL_LLAMA_CPP_MODEL,
    LLAMA_QUANTIZE_BIN, CONVERT_HF_TO_GGUF_PY, GGUF_MODELS_DIR as _GGUF_DIR,
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

    # Model source mode — "pre_compiled_local", "pre_compiled_remote", or "compile"
    "model_source_mode":     "pre_compiled_local",

    # External (pre-compiled remote) endpoint
    "external_llm_url":      EXTERNAL_LLAMA_CPP_URL,
    "external_llm_models":   [],   # populated by Fetch from remote
    "external_selected_model": EXTERNAL_LLAMA_CPP_MODEL,

    # GGUF compile pipeline settings
    "compile_source_path":   "",
    "compile_output_dir":    GGUF_MODELS_DIR,
    "compile_quantization":  "Q4_K_M",
    "compile_convert_script": CONVERT_HF_TO_GGUF_PY,
    "compile_quantize_bin":  LLAMA_QUANTIZE_BIN,

    # Target Environment
    "target_env_type":       "local",

    # SSH target credentials
    "target_ssh_host":     "",
    "target_ssh_port":     22,
    "target_ssh_user":     "root",
    "target_ssh_password": "",
    "target_ssh_key_path": "",
    "target_ssh_caf_dir":  "~/cyber-agent-flow",

    # llama-server management
    "llama_server_bin":      LLAMA_SERVER_BIN,
    "llama_server_running":  False,

    # MCP (local)
    "mcp_url":               MCP_SCRIPT_PATH,
    "mcp_server_url":        MCP_SERVER_BASE_URL,
    "mcp_tools":             {},
    "mcp_running":           False,

    # Active project (drives tool_focus and active_scenario defaults)
    "active_project":     "file_creator",

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
    "cancel_requested":  False,

    # Telemetry (current run)
    "telemetry": {},

    # Run history
    "run_history": [],

    # Internal trackers
    "_last_backend":        "llama.cpp",
    "_last_exec_scenario":  DEFAULT_SCENARIO,
    "_prompts_user_edited": False,

    # CAF 4-Pillar configuration
    "caf_scope":              "Narrow",
    "caf_urgency":            "Speed",
    "caf_allowed_subnets":    [],
    "caf_target_credentials": [],

    # AI Judge configuration
    "judge_enabled":     False,
    "judge_provider":    "anthropic",
    "judge_model":       "claude-sonnet-4-6",
    "judge_api_key":     "",
    "judge_temperature": 0.0,
    "judge_mode":        "Score all responses",

    # Batch evaluation
    "batch_queue":   [],
    "batch_report":  None,

    # Model comparison
    "comparison_models": [],
    "comparison_result": None,

    # RAG configuration
    "rag_corpus_path":          "",
    "rag_retrieval_k":          5,
    "rag_query":                "",
    "rag_ground_truth_answer":  "",
    "rag_ground_truth_doc_ids": "",

    # Workflow-specific
    "workflow_test_cases":               [],
    "workflow_variants":                 [],
    "classification_labels":             "",
    "summarization_reference":           "",
    "summarization_source":              "",
    "structured_output_schema":          "{}",
    "structured_output_required_fields": "",
    "multiagent_num_agents":             2,
}


def init_state() -> None:
    for key, default in _DEFAULTS.items():
        st.session_state.setdefault(key, default)


def sync_scenario(scenario_key: str) -> None:
    """
    Sync all scenario-derived session state when the active scenario changes.
    Preserves user-edited prompts; always updates validation, metrics, and CAF config.
    """
    _s = SCENARIOS.get(scenario_key, SCENARIOS[DEFAULT_SCENARIO])
    if not st.session_state.get("_prompts_user_edited"):
        st.session_state["sys_prompt"]  = _s["system_prompt"]
        st.session_state["user_prompt"] = _s["user_prompt"]
    st.session_state["validation_command"]  = _s["validation_command"]
    st.session_state["fail_patterns"]       = list(_s["fail_patterns"])
    st.session_state["metrics_matrix"]      = list(_s["default_metrics"])
    st.session_state["_last_exec_scenario"] = scenario_key
    if "caf_scope" in _s:
        st.session_state["caf_scope"]              = _s["caf_scope"]
        st.session_state["caf_urgency"]            = _s["caf_urgency"]
        st.session_state["caf_allowed_subnets"]    = list(_s.get("caf_allowed_subnets", []))
        st.session_state["caf_target_credentials"] = list(_s.get("caf_target_credentials", []))
