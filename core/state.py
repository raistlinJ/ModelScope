"""Streamlit session-state initialisation.

Defines the default values for bot-agnostic keys the UI stores in
``st.session_state``. Each bot-type plugin (core/bot_types/*.py) owns its own
per-project working-copy keys via ``session_defaults``/``global_keys``/
``owned_prefixes``; ``_effective_defaults()`` and friends merge those in with
this module's bot-agnostic base set. UI-only — never imported by the CLI.
"""
import copy

import streamlit as st
from core.bot_types import get_bot_plugin
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

    # NOTE: bash_bot / llama_cli_bot / llama_server_bot working-copy keys are
    # NOT defined here — each bot-type plugin owns its own session_defaults
    # (see core/bot_types/*.py). This dict only holds keys that are truly
    # bot-agnostic. _effective_defaults() below merges the two.

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

# ── Global keys — never reset on project switch ────────────────────────────────
# These are user-level preferences or internal trackers that survive every
# project switch.  Any key NOT in this set (and present in _DEFAULTS) is
# considered per-project state and will be reset on switch.

_BASE_GLOBAL_KEYS: frozenset = frozenset({
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
    # CAF / RAG / Workflow — user-level preferences, not per-project
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
# Any session-state key matching these prefixes but absent from the effective
# defaults is app-owned ephemera and must be purged on project switch.  This
# inverts the old model: instead of a growing delete-list that breaks with
# every UI refactor, we keep a small, stable keep-list (global keys) and a
# small set of namespace prefixes that catch all dynamic widget keys
# automatically. Bot-specific prefixes (validation-set widgets, LLM Helper
# panels, etc.) live on each plugin's own `owned_prefixes` — see
# core/bot_types/base.py — not here. Only prefixes shared across every bot
# type belong in this base set.
_BASE_OWNED_PREFIXES: tuple = (
    "_us_",       # unified step editor (_us_{pfx}_{step_id}_*), shared by all bot types
    "_sc_",       # script/command step editor (_sc_{pfx}_{step_id}_*), shared by all bot types
    "testing_",   # connection-test in-progress flags, shared by all bot types
)

# Dynamic keys purged by suffix — connection-test results are named
# {state_prefix}_{target_type}_test_result and must not survive a switch.
_APP_OWNED_SUFFIXES: tuple = (
    "_test_result",
)


def _effective_defaults() -> dict:
    """Bot-agnostic _DEFAULTS merged with every registered plugin's
    session_defaults. Computed fresh (not cached) so a plugin added via
    refresh_bot_plugins() takes effect without an app restart."""
    from core.bot_types import iter_bot_plugins

    merged = dict(_DEFAULTS)
    for plugin in iter_bot_plugins():
        merged.update(plugin.session_defaults)
    return merged


def _effective_global_keys() -> frozenset:
    from core.bot_types import iter_bot_plugins

    keys = set(_BASE_GLOBAL_KEYS)
    for plugin in iter_bot_plugins():
        keys |= set(plugin.global_keys)
    return frozenset(keys)


def _effective_owned_prefixes() -> tuple:
    from core.bot_types import iter_bot_plugins

    prefixes = list(_BASE_OWNED_PREFIXES)
    for plugin in iter_bot_plugins():
        prefixes.extend(plugin.owned_prefixes)
    return tuple(prefixes)


def _purge_project_state() -> None:
    """Reset all per-project registered keys and delete all app-owned dynamic keys.

    Self-sealing: any new key added to a plugin's session_defaults is
    automatically reset on switch unless explicitly added to that plugin's
    global_keys.  Any new widget key under a plugin's owned_prefixes is
    automatically deleted without updating a separate list.
    """
    defaults = _effective_defaults()
    global_keys = _effective_global_keys()
    owned_prefixes = _effective_owned_prefixes()

    # Reset all registered per-project keys to their defaults
    for key, default in defaults.items():
        if key not in global_keys:
            st.session_state[key] = copy.deepcopy(default)

    # Delete all unregistered (dynamic) app-owned widget keys
    to_delete = [
        k for k in list(st.session_state.keys())
        if k not in defaults
        and (any(k.startswith(pfx) for pfx in owned_prefixes)
             or any(k.endswith(sfx) for sfx in _APP_OWNED_SUFFIXES))
    ]
    for k in to_delete:
        del st.session_state[k]


def init_state() -> None:
    for key, default in _effective_defaults().items():
        st.session_state.setdefault(key, default)


def sync_project(project_id: str) -> None:
    """
    Sync working-copy keys from the active project's config bundle.
    Branches on bot type; call whenever active_project_id changes.

    Isolation guarantee (self-sealing): any key added to a bot-type plugin's
    session_defaults is automatically reset on switch unless explicitly added
    to that plugin's global_keys. Any dynamic widget key under a plugin's
    owned_prefixes is automatically purged.  No growing enumeration lists to
    maintain — see core/bot_types/base.py for what each plugin declares.

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
    # Clean slate: resets every plugin's session_defaults (except its
    # global_keys) and deletes every unregistered dynamic widget key under a
    # plugin's owned_prefixes.
    _purge_project_state()

    # 3. Restore incoming project run-state ─────────────────────────────────────
    st.session_state["run_logs"]            = st.session_state.get(f"run_logs_{project_id}", [])
    st.session_state["run_logs_setup"]      = st.session_state.get(f"run_logs_setup_{project_id}", [])
    st.session_state["run_logs_validation"] = st.session_state.get(f"run_logs_validation_{project_id}", [])
    st.session_state["run_completed"]       = st.session_state.get(f"run_completed_{project_id}", False)
    st.session_state["telemetry"]           = st.session_state.get(f"telemetry_{project_id}", {})

    # Volatile display-only flags — always reset on switch
    st.session_state["_exec_phase"] = ""

    # 4. Hydrate from project config via the bot-type plugin ────────────────────
    plugin = get_bot_plugin(bot_type)
    if plugin is not None:
        for state_key, cfg_key in plugin.state_key_map.items():
            if cfg_key in cfg:
                st.session_state[state_key] = cfg[cfg_key]
        for ckey in plugin.cache_keys:
            st.session_state.pop(ckey, None)

    st.session_state["_last_active_project_id"] = project_id
