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
    # Session-state key holding this bot's configured metric matrix. The
    # dashboard scores a run against the thresholds stored under it.
    dashboard_metrics_key = ""
    # Project-config key for the Execute tab's parallelism setting, for bots
    # that opt into it. Named per bot so an existing project keeps its value.
    concurrency_config_key = "_batch_concurrency"
    # Backend shown when a project has not recorded one yet.
    default_backend = "llama.cpp"
    # Execute-tab widget-key prefix this bot reads its ticked validation
    # sets from. Bots sharing a runner must not share a prefix.
    exec_state_prefix = "llama_exec"

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

    def sidebar_indicators(
        self, telemetry: Mapping[str, Any] | None, current_config: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Return this bot's status boxes for one sidebar project row.

        The default is a per-metric pass/fail readout of the last run. Bot
        types whose run isn't a single pass/fail — a batch that runs the same
        workflow on many targets, say — override this.
        """
        from core.run_status import sidebar_status_indicators

        return sidebar_status_indicators(dict(telemetry or {}), current_config)

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        """Mutate and return a project config before CLI execution."""
        return config

    def render_config(self, project: dict[str, Any]) -> None:
        raise NotImplementedError

    def render_execute(self, project: dict[str, Any]) -> None:
        raise NotImplementedError

    def render_dashboard(self, project: dict[str, Any]) -> None:
        raise NotImplementedError

    # ── Optional hooks ────────────────────────────────────────────────
    #
    # These let a bot type extend a shared renderer or runner without the
    # shared code testing for its type_id. Returning None/False everywhere
    # means "not overridden — draw the default", so a plugin only implements
    # the ones it actually changes.

    def run_in_execute_tab(
        self, project: dict[str, Any], shared: dict[str, Any], bot_type: str,
    ) -> bool:
        """Take over the Execute tab's run. Return True when handled."""
        return False

    def flush_ui_config(self, project: dict[str, Any], config: dict[str, Any]) -> None:
        """Derive extra config keys during a bot family's shared UI flush."""
        return None

    # True when this bot's binary and model paths live on a probe target
    # rather than on the ModelScope host, so local filesystem checks and the
    # "is something already listening here?" pre-check do not apply.
    uses_config_probe_env = False

    def config_probe_env(self, project: dict[str, Any]):
        """Environment for config-time probing (model scan, status check).

        Only consulted when ``uses_config_probe_env`` is set. Returning None
        means the target is not usable yet and the plugin has already said why.
        """
        return None

    def start_config_test_server(
        self, project: dict[str, Any], params: Mapping[str, Any], log: Callable[[str], None],
    ):
        """Start the managed server behind the Config tab's Check Status.

        Return a process handle, or None to fall through to the shared
        local/SSH paths.
        """
        return None

    def render_execution_target(self, project: dict[str, Any]) -> str | None:
        """Draw this bot's Execution Target control.

        Returns the resolved target, or None to let the shared renderer draw
        its own control.
        """
        return None

    def render_target_test(self, project: dict[str, Any], target: str) -> bool:
        """Draw this bot's connection-test controls. True when handled."""
        return False

    def render_server_setup_notice(self, project: dict[str, Any]) -> bool:
        """Draw the Server Setup preamble. True when handled."""
        return False

    def render_bind_controls(self, project: dict[str, Any]) -> bool:
        """Draw the listen host/port controls. True when handled."""
        return False

    def model_dir_help(self) -> str | None:
        """Override the Model Directory help text, or None for the default."""
        return None

    def render_model_info(self, config: dict[str, Any]) -> bool:
        """Draw the bot-specific lines of the Execute tab's Model Info panel.

        True when handled; False leaves the generic binary/URL summary.
        """
        return False

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
