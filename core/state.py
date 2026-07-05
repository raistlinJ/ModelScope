"""Streamlit session-state initialisation.

Defines the default values for every key the UI stores in ``st.session_state``.
UI-only — never imported by the CLI.
"""
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

    # Metrics setup - no scenario dependency
    "tool_focus":         "file_creator",
    "validation_command": "",
    "fail_patterns":      [],
    "metrics_matrix":     [],

    # Execute
    "sys_prompt":        "",
    "user_prompt":       "",
    "run_logs":          [],
    "run_logs_setup":    [],
    "run_logs_validation": [],
    "run_completed":     False,
    "cancel_requested":  False,
    "_run_in_progress":  False,

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
}


def init_state() -> None:
    for key, default in _DEFAULTS.items():
        st.session_state.setdefault(key, default)


# ── Per-bot-type default values used by sync_project to guarantee a clean reset ──

_BASH_DEFAULTS: dict = {
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
    "bash_llm_helper_backend":  "OpenAI-Compatible",
    "bash_llm_helper_openai_url": "",
    "bash_llm_helper_openai_apikey": "",
    "bash_llm_helper_openai_verify_ssl": True,
    "bash_llm_helper_ollama_url": "http://localhost:11434",
    "bash_llm_helper_model":    "",
    "bash_llm_helper_enabled":  False,
    "bash_llm_helper_openai_models": [],
    "bash_llm_helper_ollama_models": [],
    # UI-only state — not in _BASH_KEY_MAP (not persisted to project config)
    "bash_val_editor_nonce":    0,   # bumped on add/delete/reset to invalidate data_editor baseline
    "bash_val_active_set_idx":  0,   # persists set selection; reset to 0 on project switch
    # Judge configuration (scoped to project, not global)
    "judge_enabled":     False,
    "judge_provider":    "anthropic",
    "judge_model":       "claude-sonnet-4-6",
    "judge_temperature": 0.0,
    "judge_mode":        "Score all responses",
}

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
    # Judge configuration (scoped to project, persisted via keymap)
    "judge_enabled":     "judge_enabled",
    "judge_provider":    "judge_provider",
    "judge_model":       "judge_model",
    "judge_temperature": "judge_temperature",
    "judge_mode":        "judge_mode",
}

_LLAMA_DEFAULTS: dict = {
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
    "llama_cli_steps":               [],
    "llama_cli_startup_commands":    [],
    "llama_cli_completion_commands": [],
    "llama_cli_timeout":             60,
    "llama_cli_validation_commands": [],
    "llama_cli_fail_patterns":       [],
    "llama_cli_metrics_matrix":      [],
    "llama_cli_validation_sets":     [],
    "llama_cli_system_prompt":       "",
    "llama_cli_llm_helper_backend":  "OpenAI-Compatible",
    "llama_cli_llm_helper_openai_url": "",
    "llama_cli_llm_helper_openai_apikey": "",
    "llama_cli_llm_helper_openai_verify_ssl": True,
    "llama_cli_llm_helper_ollama_url": "http://localhost:11434",
    "llama_cli_llm_helper_model":    "",
    "llama_cli_llm_helper_enabled":  False,
    "llama_cli_llm_helper_openai_models": [],
    "llama_cli_llm_helper_ollama_models": [],
    # Judge configuration (scoped to project, not global)
    "judge_enabled":     False,
    "judge_provider":    "anthropic",
    "judge_model":       "claude-sonnet-4-6",
    "judge_temperature": 0.0,
    "judge_mode":        "Score all responses",
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
    # Judge configuration (scoped to project, persisted via keymap)
    "judge_enabled":     "judge_enabled",
    "judge_provider":    "judge_provider",
    "judge_model":       "judge_model",
    "judge_temperature": "judge_temperature",
    "judge_mode":        "judge_mode",
}

# Streamlit widget keys that carry their own session-state entry independent of
# the working-copy keys above.  These must be deleted on project switch so that
# Streamlit re-seeds them from the freshly loaded values instead of displaying
# the previous project's cached data.
_LLAMA_TRANSIENT_WIDGET_KEYS: tuple = (
    "_llama_openai_url_widget",
    "_llama_openai_ssl_widget",
    "_llama_openai_apikey_widget",
    "_llama_model_sel_widget",
    "_llama_model_sel_managed_widget",
    "_llama_openai_model_sel",
    "_llama_preset_sel",
)

# Per-project discovery caches that must be invalidated on project switch.
_LLAMA_CACHE_KEYS: tuple = (
    "llama_cli_discovered_models",
    "llama_cli_openai_models",
    "_llama_svc_result",
    "_llama_svc_cmd",
)

# Keys whose live values belong to the active project. Saved when switching
# away; restored (or defaulted) when switching to a new project.
_RUNTIME_SWAP_KEYS: tuple = (
    "run_logs",
    "run_logs_setup",
    "run_logs_validation",
    "run_completed",
    "telemetry",
)

# Prefixes of *dynamic* widget keys whose names embed step/command IDs and
# therefore cannot be enumerated statically.  On a project switch every
# session-state key that starts with one of these prefixes is deleted so that
# Streamlit re-seeds the widgets from the freshly loaded step data instead of
# continuing to display the previous project's step content.
#
# Prefix inventory (see ui/config_tab.py):
#   _us_{pfx}_{step_id}_*   – unified step editor (llama-cli steps):
#                              _type (radio/index), _content (text_area/value),
#                              _en (checkbox/value), _lr (checkbox/value),
#                              _to (number_input/value), _toggle, _open
#   _sc_{pfx}_{step_id}_*   – script/command step editor (bash_bot steps):
#                              _cmd (text_input/value), _en (checkbox/value),
#                              _lr (checkbox/value), _to (number_input/value),
#                              _delay (number_input/value), _toggle, _open
#   llama_mcp_en_            – MCP server enable toggles (positional checkbox)
#   bash_exec_vset_          – Execute-tab bash validation-set checkboxes
#   llama_exec_vset_         – Execute-tab llama validation-set checkboxes
_DYNAMIC_WIDGET_PREFIXES: tuple = (
    "_us_",
    "_sc_",
    "llama_mcp_en_",
    "_bash_val_baseline_",  # data_editor stable-baseline cache keys for validation sets
    "bash_val_editor_",     # data_editor widget keys (keyed by set index + nonce)
    "bash_val_name_",       # inline name text_input keys (keyed by set index + nonce)
    "bash_val_desc_",       # inline description text_input keys (keyed by set index + nonce)
    "bash_exec_vset_",      # Execute-tab bash validation-set checkboxes (keyed by set index)
    "llama_exec_vset_",     # Execute-tab llama validation-set checkboxes (keyed by set index)
)


def _clear_dynamic_widget_keys() -> None:
    """Delete all session-state keys that match a dynamic widget prefix."""
    to_delete = [
        k for k in list(st.session_state.keys())
        if any(k.startswith(pfx) for pfx in _DYNAMIC_WIDGET_PREFIXES)
    ]
    for k in to_delete:
        del st.session_state[k]


def sync_project(project_id: str) -> None:
    """
    Sync working-copy keys from the active project's config bundle.
    Branches on bot type; call whenever active_project_id changes.

    Isolation guarantee: every working-copy key for the new project's bot type
    is reset to its default value first, and then overlaid with whatever the
    project's config bundle actually contains.  This prevents stale values from
    the previously active project from bleeding through when the new project's
    config omits a key.

    Transient widget keys and per-bot caches (discovered model lists, OpenAI
    model lists, service-result messages) are deleted from session state so that
    Streamlit re-seeds them from the freshly-loaded values rather than
    displaying the old project's cached data.

    Dynamic step/command widget keys (keyed by step ID, e.g. _us_*, _sc_*,
    llama_mcp_en_*) are also purged so that widgets with overlapping step IDs
    across projects do not bleed the previous project's content into the next
    render.

    The current run's logs, completion flag, and telemetry are saved under the
    outgoing project's key and restored from the incoming project's key, so
    each project retains its own log history.  Only the "Clear Log" button
    actually discards run state from session memory.
    """
    projects = st.session_state.get("projects", [])
    project  = next((p for p in projects if p["id"] == project_id), None)
    if project is None:
        return
    cfg      = project.get("config", {})
    bot_type = project.get("type", "bash_bot")

    # ── Persist outgoing project run-state; restore incoming project run-state ──
    # Save the current run state under the outgoing project key so it can be
    # restored when the user switches back.  _last_active_project_id still
    # holds the outgoing ID here — it is updated only at the end of this fn.
    _outgoing_pid = st.session_state.get("_last_active_project_id")
    if _outgoing_pid:
        for _k in _RUNTIME_SWAP_KEYS:
            st.session_state[f"{_k}_{_outgoing_pid}"] = st.session_state.get(_k, _DEFAULTS.get(_k))

    for _k in _RUNTIME_SWAP_KEYS:
        st.session_state[_k] = st.session_state.get(f"{_k}_{project_id}", _DEFAULTS.get(_k))

    # Volatile flags — always reset on switch; can't resume a run mid-switch
    st.session_state["_exec_phase"]      = ""
    st.session_state["_run_in_progress"] = False
    st.session_state["cancel_requested"] = False

    # ── Purge all dynamic step/command widget keys (both bot types) ───────────
    # Must happen unconditionally: a bash project's _sc_* keys could bleed into
    # an llama project's _us_* rendering if a step _id happens to match.
    _clear_dynamic_widget_keys()

    # 1. Reset all bash/llama working-copy keys to their defaults so that any
    #    inactive bot-type keys do not survive a project switch.
    for state_key, default in _BASH_DEFAULTS.items():
        st.session_state[state_key] = default
    for state_key, default in _LLAMA_DEFAULTS.items():
        st.session_state[state_key] = default

    if bot_type == "bash_bot":
        # 2. Overlay with whatever this bash project actually stored.
        for state_key, cfg_key in _BASH_KEY_MAP.items():
            if cfg_key in cfg:
                st.session_state[state_key] = cfg[cfg_key]

    elif bot_type == "llama_cli_bot":
        # 2. Overlay with whatever this llama-cli project actually stored.
        for state_key, cfg_key in _LLAMA_KEY_MAP.items():
            if cfg_key in cfg:
                st.session_state[state_key] = cfg[cfg_key]
        # 3. Delete transient widget keys so Streamlit re-seeds them from the
        #    freshly loaded working-copy values on the next render.
        for wkey in _LLAMA_TRANSIENT_WIDGET_KEYS:
            st.session_state.pop(wkey, None)
        # 4. Invalidate per-project discovery caches.
        for ckey in _LLAMA_CACHE_KEYS:
            st.session_state.pop(ckey, None)

    st.session_state["_last_active_project_id"] = project_id
