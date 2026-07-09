"""Streamlit session-state initialisation.

Defines the default values for every key the UI stores in ``st.session_state``.
UI-only — never imported by the CLI.
"""
import copy

import streamlit as st
from config.defaults import (
    LLAMA_CPP_DEFAULT_URL, GGUF_MODELS_DIR,
    MCP_SCRIPT_PATH, DEFAULT_CONTEXT_SIZE,
    LLAMA_SERVER_BIN, MCP_SERVER_BASE_URL,
    EXTERNAL_LLAMA_CPP_URL, EXTERNAL_LLAMA_CPP_MODEL,
    LLAMA_QUANTIZE_BIN, CONVERT_HF_TO_GGUF_PY, GGUF_MODELS_DIR as _GGUF_DIR,
)


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

    # Active project (legacy flat key — kept for settings backwards-compat)
    "active_project":     "file_creator",

    # Projects list and active project ID (new project management system)
    "projects":           [],
    "active_project_id":  None,

    # Bash-bot working-copy keys (synced from active project on project switch)
    "bash_startup_commands":    [],
    "bash_timeout":             60,
    "bash_completion_commands": [],
    "bash_validation_commands": [],
    "bash_execution_target":    "local",
    "bash_ssh_host":            "",
    "bash_ssh_port":            22,
    "bash_ssh_user":            "root",
    "bash_ssh_password":        "",
    "bash_ssh_key_path":        "",
    "bash_fail_patterns":       [],
    "bash_metrics_matrix":      [],
    "bash_validation_sets":     [],
    "bash_sudo":                False,

    # Llama-CLI-bot working-copy keys (synced from active project on project switch)
    "llama_cli_execution_target":    "local",
    "llama_cli_ssh_host":            "",
    "llama_cli_ssh_port":            22,
    "llama_cli_ssh_user":            "root",
    "llama_cli_ssh_password":        "",
    "llama_cli_ssh_key_path":        "",
    "llama_cli_sudo":                False,
    "llama_cli_sudo_password":       "",
    "llama_cli_backend":             "llama.cpp",
    "llama_cli_binary_path":         "",
    "llama_cli_model_dir":           "",
    "llama_cli_model_name":          "",
    "llama_cli_tokens":              32768,
    "llama_cli_en_temp":             False,
    "llama_cli_temperature":         0.8,
    "llama_cli_en_gpu_layers":       False,
    "llama_cli_gpu_layers":          99,
    "llama_cli_en_threads":          False,
    "llama_cli_threads":             4,
    "llama_cli_flash_attn":          False,
    "llama_cli_en_top_k":            False,
    "llama_cli_top_k":               40,
    "llama_cli_en_top_p":            False,
    "llama_cli_top_p":               0.9,
    "llama_cli_en_min_p":            False,
    "llama_cli_min_p":               0.1,
    "llama_cli_en_repeat_penalty":   False,
    "llama_cli_repeat_penalty":      1.1,
    "llama_cli_en_freq_penalty":     False,
    "llama_cli_freq_penalty":        0.0,
    "llama_cli_en_predict":          False,
    "llama_cli_predict":             512,
    "llama_cli_en_rope_freq_base":   False,
    "llama_cli_rope_freq_base":      10000.0,
    "llama_cli_en_rope_freq_scale":  False,
    "llama_cli_rope_freq_scale":     1.0,
    "llama_cli_en_seed":             False,
    "llama_cli_seed":                -1,
    "llama_cli_custom_flags":        "",
    "llama_cli_server_port":         18080,
    "llama_cli_openai_base_url":     "",
    "llama_cli_openai_verify_ssl":   True,
    "llama_cli_openai_api_key":      "",
    "llama_cli_mcp_config_path":     "",
    "llama_cli_mcp_servers":         [],
    "llama_cli_prompts":             [],
    "llama_cli_commands":            [],
    "llama_cli_steps":               [],  # unified step editor (type: prompt|command)
    "llama_cli_startup_commands":    [],
    "llama_cli_completion_commands": [],
    "llama_cli_timeout":             120,
    "llama_cli_validation_sets":     [],
    "llama_cli_metrics_matrix":      [],
    "llama_cli_validation_commands": [],
    "llama_cli_fail_patterns":       [],
    "llama_cli_system_prompt":       "",
    # Metrics setup - no scenario dependency
    "tool_focus":         "file_creator",
    "validation_command": "",
    "fail_patterns":      [],
    "metrics_matrix":     [],

    # Execute
    "sys_prompt":        "",
    "user_prompt":       "",
    "run_logs":          [],
    "run_logs_setup":      [],
    "run_logs_validation": [],
    "run_completed":     False,
    "cancel_requested":  False,
    "_run_in_progress":  False,
    "_exec_phase":       "",

    # Telemetry (current run)
    "telemetry": {},

    # Run history
    "run_history": [],

    # Internal trackers
    "_last_backend":           "llama.cpp",
    "_prompts_user_edited":    False,
    "_last_active_project_id": None,
    "_undo_stack":             [],
    "_show_new_project_dialog": False,
    "_css_injected":           False,

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

    # Llama-cli dialog nonce / index — per-project but not in _KEY_MAP (must be reset)
    "llama_cli_val_editor_nonce":   0,
    "llama_cli_val_active_set_idx": 0,
}

# ── Global keys — never reset on project switch ────────────────────────────────
# These are user-level preferences or internal trackers that survive every
# project switch.  Any key NOT in this set (and present in _DEFAULTS) is
# considered per-project state and will be reset on switch.

_GLOBAL_KEYS: frozenset = frozenset({
    # LLM / backend (user-level, not project-level)
    "backend_type", "llm_url", "model_dir", "llm_models", "selected_model",
    "selected_model_path", "context_size", "model_source_mode",
    "external_llm_url", "external_llm_models", "external_selected_model",
    "compile_source_path", "compile_output_dir", "compile_quantization",
    "compile_convert_script", "compile_quantize_bin",
    # Target environment (user-level SSH defaults, not project SSH)
    "target_env_type", "target_ssh_host", "target_ssh_port", "target_ssh_user",
    "target_ssh_password", "target_ssh_key_path", "target_ssh_caf_dir",
    # Server / MCP
    "llama_server_bin", "llama_server_running",
    "mcp_url", "mcp_server_url", "mcp_tools", "mcp_running",
    # Project management
    "active_project", "projects", "active_project_id",
    # Run-state — already managed by the save/restore code in sync_project;
    # must NOT be reset by the generic purge or the restore step gets clobbered
    "run_logs", "run_logs_setup", "run_logs_validation", "run_completed", "telemetry",
    # Internal trackers that must survive the switch
    "_last_backend", "_last_active_project_id", "_css_injected",
    "_show_new_project_dialog",
    # AI Judge / CAF / RAG / Workflow — user-level preferences, not per-project
    "judge_enabled", "judge_provider", "judge_model", "judge_api_key",
    "judge_temperature", "judge_mode",
    "caf_scope", "caf_urgency", "caf_allowed_subnets", "caf_target_credentials",
    "rag_corpus_path", "rag_retrieval_k", "rag_query",
    "rag_ground_truth_answer", "rag_ground_truth_doc_ids",
    # Workflow
    "workflow_test_cases", "workflow_variants", "classification_labels",
    "summarization_reference", "summarization_source",
    "structured_output_schema", "structured_output_required_fields",
    "multiagent_num_agents",
    # Execute prompts (user-level)
    "sys_prompt", "user_prompt",
    # Metrics setup (user-level defaults)
    "tool_focus", "validation_command", "fail_patterns", "metrics_matrix",
    # Run history (user-level log)
    "run_history",
    # Undo stack
    "_undo_stack",
})

# ── App-owned namespace prefixes ───────────────────────────────────────────────
# Any session-state key matching these prefixes but absent from _DEFAULTS is
# app-owned ephemera and must be purged on project switch.  This inverts the
# old model: instead of a growing delete-list that breaks with every UI refactor,
# we keep a small, stable keep-list (_GLOBAL_KEYS) and a small set of namespace
# prefixes that catch all dynamic widget keys automatically.
#
# Prefix inventory (see ui/config_tab.py):
#   {pfx}_val_add_name_*   – validation set name text_input ({pfx} = bash, llama_cli)
#   {pfx}_val_add_desc_*   – validation set description text_input
#   _{pfx}_val_dialog_steps_* – validation dialog steps state
#   {pfx}_val_en_*         – validation set enabled checkbox
#   _sc_{pfx}_{step_id}_*  – script/command step editor (bash_bot steps)
#   _us_{pfx}_{step_id}_*  – unified step editor (llama-cli steps)
#   llama_mcp_en_*         – MCP server enable toggles (positional checkbox)
#   {pfx}_llm_helper_*     – LLM Helper panel widgets
#   _llama_openai_*_widget – OpenAI-compatible widget keys
#   _llama_openai_ssl_widget, _llama_openai_model_sel, etc.
_APP_OWNED_PREFIXES: tuple = (
    "_us_",       # unified step editor (_us_{pfx}_{step_id}_*)
    "_sc_",       # script/command step editor (_sc_{pfx}_{step_id}_*)
    "llama_mcp_en_",  # MCP server enable toggles
    "bash_val_",      # bash validation set widgets (name, desc, enabled, etc.)
    "_bash_val_",     # bash validation dialog steps (_bash_val_dialog_steps_*)
    "llama_cli_val_", # llama-cli validation set widgets
    "_llama_cli_val_", # llama-cli validation dialog steps
    "_llama_openai_",  # OpenAI-compatible widget keys
    "_llama_model_sel",  # model selector widget
    "_llama_preset_sel",  # preset selector
    "llama_cli_llm_helper_",  # llama-cli LLM helper widgets
    "bash_llm_helper_",     # bash LLM helper widgets
)


def _purge_project_state() -> None:
    """Reset all per-project registered keys and delete all app-owned dynamic keys.

    Self-sealing: any new key added to _DEFAULTS is automatically reset on switch
    unless explicitly added to _GLOBAL_KEYS.  Any new widget key under an app-owned
    namespace prefix is automatically deleted without updating a separate list.
    """
    # Reset all registered per-project keys to their defaults
    for key, default in _DEFAULTS.items():
        if key not in _GLOBAL_KEYS:
            st.session_state[key] = copy.deepcopy(default)

    # Delete all unregistered (dynamic) app-owned widget keys
    to_delete = [
        k for k in list(st.session_state.keys())
        if k not in _DEFAULTS
        and any(k.startswith(pfx) for pfx in _APP_OWNED_PREFIXES)
    ]
    for k in to_delete:
        del st.session_state[k]


def init_state() -> None:
    for key, default in _DEFAULTS.items():
        st.session_state.setdefault(key, default)


# ── Per-bot-type key maps used by sync_project for config hydration ─────────────

_BASH_KEY_MAP: dict = {
    "bash_startup_commands":    "startup_commands",
    "bash_timeout":             "bash_timeout",
    "bash_completion_commands": "completion_commands",
    "bash_validation_commands": "validation_commands",
    "bash_execution_target":    "execution_target",
    "bash_ssh_host":            "ssh_host",
    "bash_ssh_port":            "ssh_port",
    "bash_ssh_user":            "ssh_user",
    "bash_ssh_password":        "ssh_password",
    "bash_ssh_key_path":        "ssh_key_path",
    "bash_fail_patterns":       "fail_patterns",
    "bash_metrics_matrix":      "metrics_matrix",
    "bash_validation_sets":     "validation_sets",
    "bash_sudo":                "sudo",
    "bash_llm_helper_backend":  "llm_helper_backend",
    "bash_llm_helper_openai_url": "llm_helper_openai_url",
    "bash_llm_helper_openai_apikey": "llm_helper_openai_apikey",
    "bash_llm_helper_openai_verify_ssl": "llm_helper_openai_verify_ssl",
    "bash_llm_helper_ollama_url": "llm_helper_ollama_url",
    "bash_llm_helper_model":    "llm_helper_model",
    "bash_llm_helper_enabled":  "llm_helper_enabled",
    "bash_llm_helper_openai_models": "llm_helper_openai_models",
    "bash_llm_helper_ollama_models": "llm_helper_ollama_models",
}

_LLAMA_KEY_MAP: dict = {
    "llama_cli_execution_target":    "execution_target",
    "llama_cli_ssh_host":            "ssh_host",
    "llama_cli_ssh_port":            "ssh_port",
    "llama_cli_ssh_user":            "ssh_user",
    "llama_cli_ssh_password":        "ssh_password",
    "llama_cli_ssh_key_path":        "ssh_key_path",
    "llama_cli_sudo":                "sudo",
    "llama_cli_sudo_password":       "sudo_password",
    "llama_cli_backend":             "backend",
    "llama_cli_binary_path":         "binary_path",
    "llama_cli_model_dir":           "model_dir",
    "llama_cli_model_name":          "model_name",
    "llama_cli_tokens":              "tokens",
    "llama_cli_en_temp":             "en_temp",
    "llama_cli_temperature":         "temperature",
    "llama_cli_en_gpu_layers":       "en_gpu_layers",
    "llama_cli_gpu_layers":          "gpu_layers",
    "llama_cli_en_threads":          "en_threads",
    "llama_cli_threads":             "threads",
    "llama_cli_flash_attn":          "flash_attn",
    "llama_cli_en_top_k":            "en_top_k",
    "llama_cli_top_k":               "top_k",
    "llama_cli_en_top_p":            "en_top_p",
    "llama_cli_top_p":               "top_p",
    "llama_cli_en_min_p":            "en_min_p",
    "llama_cli_min_p":               "min_p",
    "llama_cli_en_repeat_penalty":   "en_repeat_penalty",
    "llama_cli_repeat_penalty":      "repeat_penalty",
    "llama_cli_en_freq_penalty":     "en_freq_penalty",
    "llama_cli_freq_penalty":        "freq_penalty",
    "llama_cli_en_predict":          "en_predict",
    "llama_cli_predict":             "predict",
    "llama_cli_en_rope_freq_base":   "en_rope_freq_base",
    "llama_cli_rope_freq_base":      "rope_freq_base",
    "llama_cli_en_rope_freq_scale":  "en_rope_freq_scale",
    "llama_cli_rope_freq_scale":     "rope_freq_scale",
    "llama_cli_en_seed":             "en_seed",
    "llama_cli_seed":                "seed",
    "llama_cli_custom_flags":        "custom_flags",
    "llama_cli_server_port":         "server_port",
    "llama_cli_openai_base_url":     "openai_base_url",
    "llama_cli_openai_verify_ssl":   "openai_verify_ssl",
    "llama_cli_openai_api_key":      "openai_api_key",
    "llama_cli_mcp_config_path":     "mcp_config_path",
    "llama_cli_mcp_servers":         "mcp_servers",
    "llama_cli_prompts":             "prompts",
    "llama_cli_commands":            "commands",
    "llama_cli_steps":               "steps",
    "llama_cli_startup_commands":    "startup_commands",
    "llama_cli_completion_commands": "completion_commands",
    "llama_cli_timeout":             "timeout",
    "llama_cli_validation_commands": "validation_commands",
    "llama_cli_fail_patterns":       "fail_patterns",
    "llama_cli_metrics_matrix":      "metrics_matrix",
    "llama_cli_validation_sets":     "validation_sets",
    "llama_cli_system_prompt":       "system_prompt",
    "llama_cli_llm_helper_backend":  "llm_helper_backend",
    "llama_cli_llm_helper_openai_url": "llm_helper_openai_url",
    "llama_cli_llm_helper_openai_apikey": "llm_helper_openai_apikey",
    "llama_cli_llm_helper_openai_verify_ssl": "llm_helper_openai_verify_ssl",
    "llama_cli_llm_helper_ollama_url": "llm_helper_ollama_url",
    "llama_cli_llm_helper_model":    "llm_helper_model",
    "llama_cli_llm_helper_enabled":  "llm_helper_enabled",
    "llama_cli_llm_helper_openai_models": "llm_helper_openai_models",
    "llama_cli_llm_helper_ollama_models": "llm_helper_ollama_models",
}

# Per-project discovery caches that must be invalidated on project switch.
_LLAMA_CACHE_KEYS: tuple = (
    "llama_cli_discovered_models",
    "llama_cli_openai_models",
    "_llama_svc_result",
    "_llama_svc_cmd",
)


def sync_project(project_id: str) -> None:
    """
    Sync working-copy keys from the active project's config bundle.
    Branches on bot type; call whenever active_project_id changes.

    Isolation guarantee (self-sealing): any key added to _DEFAULTS is
    automatically reset on switch unless explicitly added to _GLOBAL_KEYS.
    Any dynamic widget key under an app-owned namespace prefix is
    automatically purged.  No growing enumeration lists to maintain.

    Run-state (run_logs, telemetry, etc.) is saved under the outgoing project
    key and restored from the incoming project key, so each project retains
    its own log history.

    Call order:
      1. Save outgoing run state
      2. _purge_project_state() — clean slate
      3. Restore incoming run state
      4. Hydrate from project config (bot-type-specific)
      5. Invalidate per-project discovery caches
    """
    projects = st.session_state.get("projects", [])
    project  = next((p for p in projects if p["id"] == project_id), None)
    if project is None:
        return
    cfg      = project.get("config", {})
    bot_type = project.get("type", "bash_bot")

    # 1. Persist outgoing project run-state ─────────────────────────────────────
    _outgoing_pid = st.session_state.get("_last_active_project_id")
    if _outgoing_pid:
        st.session_state[f"run_logs_{_outgoing_pid}"]            = st.session_state.get("run_logs", [])
        st.session_state[f"run_logs_setup_{_outgoing_pid}"]      = st.session_state.get("run_logs_setup", [])
        st.session_state[f"run_logs_validation_{_outgoing_pid}"] = st.session_state.get("run_logs_validation", [])
        st.session_state[f"run_completed_{_outgoing_pid}"]       = st.session_state.get("run_completed", False)
        st.session_state[f"telemetry_{_outgoing_pid}"]           = st.session_state.get("telemetry", {})

    # 2. Purge all per-project state and app-owned dynamic widget keys ─────────
    # Clean slate: resets all _DEFAULTS keys (except _GLOBAL_KEYS) and deletes
    # every unregistered dynamic widget key under _APP_OWNED_PREFIXES.
    _purge_project_state()

    # 3. Restore incoming project run-state ─────────────────────────────────────
    st.session_state["run_logs"]            = st.session_state.get(f"run_logs_{project_id}", [])
    st.session_state["run_logs_setup"]      = st.session_state.get(f"run_logs_setup_{project_id}", [])
    st.session_state["run_logs_validation"] = st.session_state.get(f"run_logs_validation_{project_id}", [])
    st.session_state["run_completed"]       = st.session_state.get(f"run_completed_{project_id}", False)
    st.session_state["telemetry"]           = st.session_state.get(f"telemetry_{project_id}", {})

    # Volatile display-only flags — always reset on switch
    st.session_state["_exec_phase"] = ""

    # 4. Hydrate from project config (bot-type-specific) ────────────────────────
    if bot_type == "bash_bot":
        for state_key, cfg_key in _BASH_KEY_MAP.items():
            if cfg_key in cfg:
                st.session_state[state_key] = cfg[cfg_key]

    elif bot_type == "llama_cli_bot":
        for state_key, cfg_key in _LLAMA_KEY_MAP.items():
            if cfg_key in cfg:
                st.session_state[state_key] = cfg[cfg_key]
        # Invalidate per-project discovery caches
        for ckey in _LLAMA_CACHE_KEYS:
            st.session_state.pop(ckey, None)

    st.session_state["_last_active_project_id"] = project_id