"""Bot-type plugin interfaces and shared defaults.

Bot types are the UI/CLI-facing unit for project kinds. Each plugin owns the
metadata, project defaults, session-state hydration map, renderer dispatch, and
evaluation dispatch for one project type.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from core.environment import BaseEnvironment


OnLog = Callable[..., None]


@dataclass(frozen=True)
class StatusItem:
    label: str
    state: str = "up"


@dataclass(frozen=True)
class ProjectTemplate:
    key: str
    label: str
    caption: str = ""


COMMON_RUNTIME_DEFAULTS: dict[str, Any] = {
    "execution_target": "local",
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_user": "root",
    "ssh_password": "",
    "ssh_key_path": "",
    "sudo": False,
    "sudo_password": "",
    "pct_vmid": "",
    "startup_commands": [],
    "completion_commands": [],
    "validation_commands": [],
    "fail_patterns": [],
    "metrics_matrix": [],
    "validation_sets": [],
}


LLM_HELPER_DEFAULTS: dict[str, Any] = {
    "llm_helper_backend": "OpenAI-Compatible",
    "llm_helper_openai_url": "",
    "llm_helper_openai_apikey": "",
    "llm_helper_openai_verify_ssl": True,
    "llm_helper_ollama_url": "http://localhost:11434",
    "llm_helper_model": "",
    "llm_helper_enabled": False,
    "llm_helper_openai_models": [],
    "llm_helper_ollama_models": [],
    "llm_helper_mcp_enabled": False,
    "llm_helper_mcp_config_path": "",
    "llm_helper_mcp_tools": [],
    "llm_helper_mcp_strict": False,
}


# These values are emitted by ModelScope itself, independently of an LLM
# backend. Backend-specific catalogs belong to each bot plugin below.
COMMON_DASHBOARD_METRIC_SPECS: dict[str, dict[str, str]] = {
    "total_latency": {"label": "Latency", "unit": "s"},
    "prompts_run": {"label": "Prompts Run", "unit": "runs"},
    "commands_run": {"label": "Commands Run", "unit": "commands"},
}
COMMON_DASHBOARD_METRIC_KEYS: frozenset[str] = frozenset(COMMON_DASHBOARD_METRIC_SPECS)


class BotTypePlugin:
    """Base contract for bot-type plugins.

    session_defaults / global_keys / owned_prefixes let core.state stay
    bot-agnostic: it aggregates these from every registered plugin instead of
    hardcoding each bot type's session-state keys itself.

    - session_defaults: this bot's ``st.session_state`` working-copy keys
      (e.g. ``llama_cli_temperature``) and their reset value. Any key here
      that ISN'T in global_keys gets reset to its default on every project
      switch — this is what keeps one project's settings from leaking into
      another's (see core.state.sync_project).
    - global_keys: session_defaults keys that are actually user-level
      preferences rather than per-project state, so they should survive a
      project switch instead of being reset. Usually empty — most plugins
      have no such keys.
    - owned_prefixes: session-state key prefixes for this bot's ephemeral,
      dynamically-named widget keys (e.g. per-row validation-set widgets)
      that have no fixed name and so can't live in session_defaults. Any
      matching key is deleted (not reset) on project switch.
    """

    type_id = ""
    label = ""
    icon = ""
    default_project_name = "Project"
    state_key_map: Mapping[str, str] = {}
    cache_keys: tuple[str, ...] = ()
    templates: tuple[ProjectTemplate, ...] = ()
    session_defaults: Mapping[str, Any] = {}
    global_keys: frozenset[str] = frozenset()
    owned_prefixes: tuple[str, ...] = ()
    metric_specs: Mapping[str, Mapping[str, str]] = {}

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        return {}

    def make_project(self, project_id: str, name: str, template_key: str = "blank") -> dict[str, Any]:
        return {
            "id": project_id,
            "name": name,
            "type": self.type_id,
            "config": self.default_config(template_key),
        }

    def template_caption(self, template_key: str) -> str:
        for template in self.templates:
            if template.key == template_key:
                return template.caption
        return ""

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        if project:
            return [StatusItem(f"Project: {project.get('name', 'Unnamed')}")]
        return []

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Mutate and return a project config before CLI execution."""
        return config

    def render_config(self, project: dict[str, Any]) -> None:
        raise NotImplementedError

    def render_execute(self, project: dict[str, Any]) -> None:
        raise NotImplementedError

    def flush_mapped_config(
        self, project: dict[str, Any], session_state: Mapping[str, Any] | None = None
    ) -> None:
        """Copy this plugin's live mapped state into its project config.

        This is the default persistence/export implementation for plugins.
        A plugin only needs to declare ``state_key_map`` for its ordinary
        widget values to survive an export. Plugins with derived values may
        override :meth:`flush_config`, but should call this helper first.
        """
        if session_state is None:
            import streamlit as st
            session_state = st.session_state

        config = project.setdefault("config", {})
        for state_key, config_key in self.state_key_map.items():
            if state_key in session_state:
                config[config_key] = copy.deepcopy(session_state[state_key])

    def flush_config(self, project: dict[str, Any]) -> None:
        """Persist live mapped settings for plugins without custom handling."""
        self.flush_mapped_config(project)

    def run_evaluation(self, env: BaseEnvironment, config: dict[str, Any], on_log: OnLog) -> dict[str, Any]:
        raise NotImplementedError


def merged_defaults(*parts: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for part in parts:
        merged.update(part)
    return copy.deepcopy(merged)
