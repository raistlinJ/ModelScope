"""Stable extension surface for bot-type plugins.

A plugin under ``plugins/bot_types/`` is not part of this package, so anything
it borrows from the app has to be reachable by a name that survives a refactor
in ``ui``. This module is that contract: **these are the only ``ui`` entry
points a plugin should import.** The underscore-prefixed originals behind them
are implementation detail and may be renamed, split or moved without notice.

Everything here re-dispatches on call rather than importing at module scope, so
importing this module can never create a cycle with ``ui.config_tab`` (which
``ui.execute_tab`` imports) and a test that patches the underlying module still
takes effect.

Adding to this surface is a deliberate act: it is a promise to keep the name
working. Prefer adding a hook to :class:`core.bot_types.base.BotTypePlugin`
when the shared code is the thing that needs to vary.
"""

from __future__ import annotations

from typing import Any, Callable

__all__ = [
    # Config tab
    "flush_llama_server_config",
    "normalise_pct_vmids",
    "render_command_steps",
    "render_llama_server_runtime",
    "render_llama_server_validation",
    "render_llm_prompt_helper_tab",
    "render_metric_thresholds_config",
    "render_validation_sets_ui",
    # Execute tab
    "clean_steps",
    "render_llama_execute_view",
    "run_llama_backed_bot",
    "selected_validation_sets",
    # Dashboard tab
    "configured_metric_assessments",
    "hydrate_project_history",
    "render_llama_dashboard",
    "render_run_dashboard",
    "render_scrollable_output",
    "threshold_style",
    # Shared widgets
    "render_flag_card",
    "render_optional_param_card",
    "render_terminal",
]


# ── Config tab ────────────────────────────────────────────────────────────

def flush_llama_server_config(project: dict) -> None:
    """Write the live llama-server widget values back into ``project``."""
    from ui.config_tab import _flush_llama_server_config

    return _flush_llama_server_config(project)


def normalise_pct_vmids(vmids: object) -> list[str]:
    """Return unique numeric PCT VMIDs in a stable order."""
    from ui.config_tab import _normalise_pct_vmids

    return _normalise_pct_vmids(vmids)


def render_command_steps(state_key: str, pfx: str, placeholder: str) -> None:
    """Draw the shared prompt/command step editor."""
    from ui.config_tab import _render_command_steps

    return _render_command_steps(state_key, pfx, placeholder)


def render_llama_server_runtime(project: dict) -> None:
    """Draw the llama-server Runtime sub-tab.

    Bot types that vary parts of it override the ``render_execution_target`` /
    ``render_server_setup_notice`` / ``render_bind_controls`` plugin hooks
    rather than reimplementing this.
    """
    from ui.config_tab import _render_llama_server_runtime

    return _render_llama_server_runtime(project)


def render_llama_server_validation(project: dict) -> None:
    """Draw the llama-server Validation sub-tab."""
    from ui.config_tab import _render_llama_server_validation

    return _render_llama_server_validation(project)


def render_llm_prompt_helper_tab(pfx: str) -> None:
    """Draw the LLM-helper configuration panel for one widget prefix."""
    from ui.config_tab import _render_llm_prompt_helper_tab

    return _render_llm_prompt_helper_tab(pfx)


def render_metric_thresholds_config(project: dict, prefix: str, flush_fn: Callable) -> None:
    """Draw the Metrics Config sub-tab."""
    from ui.config_tab import _render_metric_thresholds_config

    return _render_metric_thresholds_config(project, prefix, flush_fn)


def render_validation_sets_ui(project: dict, prefix: str, flush_fn: Callable) -> None:
    """Draw the validation-set editor."""
    from ui.config_tab import _render_validation_sets_ui

    return _render_validation_sets_ui(project, prefix, flush_fn)


# ── Execute tab ───────────────────────────────────────────────────────────

def clean_steps(steps_list: list) -> list:
    """Drop empty commands and steps from a startup/completion list."""
    from ui.execute_tab import _clean_steps

    return _clean_steps(steps_list)


def render_llama_execute_view(
    project: dict,
    bot_type: str = "llama_cli_bot",
    llm_label: str = "LLAMA-CLI",
    exec_prefix: str = "llama_exec",
    flush_fn: Callable | None = None,
    render_targets: Callable | None = None,
    render_progress: Callable | None = None,
    on_run_start: Callable | None = None,
    on_clear: Callable | None = None,
    allow_concurrency: bool = False,
    stop_label: str = "⏹  Stop",
) -> None:
    """Draw the Execute view for a llama-backed bot.

    The optional callables let a bot that runs against more than one target
    extend the view instead of forking it: ``render_targets`` lists the
    targets, ``render_progress`` draws live per-target status above the logs,
    and ``on_run_start`` / ``on_clear`` set up and tear down that state.
    """
    from ui.config_tab import _flush_llama_cli_config
    from ui.execute_tab import _render_llama_cli_execute

    return _render_llama_cli_execute(
        project,
        bot_type=bot_type,
        llm_label=llm_label,
        exec_prefix=exec_prefix,
        flush_fn=_flush_llama_cli_config if flush_fn is None else flush_fn,
        render_targets=render_targets,
        render_progress=render_progress,
        on_run_start=on_run_start,
        on_clear=on_clear,
        allow_concurrency=allow_concurrency,
        stop_label=stop_label,
    )


def run_llama_backed_bot(project: dict, shared: dict, bot_type: str = "llama_cli_bot") -> None:
    """Run one llama-backed evaluation, writing progress into ``shared``.

    ``shared`` is a plain dict visible to both the worker thread and the main
    Streamlit thread; this never touches ``st.session_state``.
    """
    from ui.execute_tab import _run_llama_cli_bot

    return _run_llama_cli_bot(project, shared, bot_type)


def selected_validation_sets(config: dict, exec_prefix: str = "llama_exec") -> list:
    """Return the validation sets currently ticked in the Execute tab."""
    from ui.execute_tab import _get_llama_selected_validation_sets

    return _get_llama_selected_validation_sets(config, exec_prefix)


# ── Dashboard tab ─────────────────────────────────────────────────────────

def configured_metric_assessments(project: dict, telemetry: dict) -> dict[str, dict]:
    """Score one run's telemetry against the project's metric thresholds."""
    from ui.dashboard_tab import _configured_metric_assessments

    return _configured_metric_assessments(project, telemetry)


def hydrate_project_history(project: dict) -> None:
    """Load this project's past runs from disk if the history is empty."""
    from ui.dashboard_tab import _hydrate_project_history_if_empty

    return _hydrate_project_history_if_empty(project)


def render_llama_dashboard(
    project: dict,
    bot_type: str = "llama_cli_bot",
    metrics_key: str = "llama_cli_metrics_matrix",
) -> None:
    """Draw the standard dashboard: a run picker plus the selected run."""
    from ui.dashboard_tab import _render_llama_cli_dashboard

    return _render_llama_cli_dashboard(project, bot_type=bot_type, metrics_key=metrics_key)


def render_run_dashboard(project: dict, telemetry: dict, bot_type: str, metrics_key: str) -> None:
    """Draw the dashboard body for one specific telemetry record.

    Bots that pick the run themselves — a batch selecting one target's
    result, say — call this instead of :func:`render_llama_dashboard`.
    """
    from ui.dashboard_tab import _render_llama_cli_dashboard_core

    return _render_llama_cli_dashboard_core(project, telemetry, bot_type, metrics_key)


def render_scrollable_output(label: str, value: object, *, key: str, height: int = 140) -> None:
    """Render run output in the app's bounded, scrollable control."""
    from ui.dashboard_tab import _render_scrollable_output

    return _render_scrollable_output(label, value, key=key, height=height)


def threshold_style() -> dict[str, tuple[str, str, str]]:
    """Threshold level → (label, colour, tint) used by dashboard badges."""
    from ui.dashboard_tab import _THRESHOLD_STYLE

    return _THRESHOLD_STYLE


# ── Shared widgets (already public; re-exported so plugins have one import) ──

def render_flag_card(*args: Any, **kwargs: Any):
    """Draw an on/off flag card in the advanced-runtime grid."""
    from ui.optional_param_card import render_flag_card as _impl

    return _impl(*args, **kwargs)


def render_optional_param_card(*args: Any, **kwargs: Any):
    """Draw an enable-and-set parameter card in the advanced-runtime grid."""
    from ui.optional_param_card import render_optional_param_card as _impl

    return _impl(*args, **kwargs)


def render_terminal(*args: Any, **kwargs: Any):
    """Draw the shared log terminal."""
    from ui.terminal import render_terminal as _impl

    return _impl(*args, **kwargs)
