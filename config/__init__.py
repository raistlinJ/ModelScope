"""ModelScope configuration: static constants, metric definitions, scenarios.

Unlike :mod:`core`, these modules are pure (stdlib only, no Streamlit), so the
public names are re-exported here for convenience — ``from config import
SCENARIOS, evaluate_metric`` works alongside the fully-qualified imports.

  defaults   — URLs, paths and tunable limits (single source of truth)
  metrics    — typed metric registry, evaluator dispatch, criterion formatting
  scenarios  — built-in named evaluation presets
"""

from config.defaults import (
    DEFAULT_CONTEXT_SIZE,
    MIN_CONTEXT_SIZE,
    MAX_CONTEXT_SIZE,
    MAX_RUN_HISTORY,
    MCP_SERVER_BASE_URL,
)
from config.metrics import (
    METRIC_TYPES,
    CATEGORIES,
    make_metric,
    format_criterion,
    evaluate_metric,
)
from config.scenarios import (
    SCENARIOS,
    DEFAULT_SCENARIO,
    validate_scenarios,
)

__all__ = [
    # defaults
    "DEFAULT_CONTEXT_SIZE",
    "MIN_CONTEXT_SIZE",
    "MAX_CONTEXT_SIZE",
    "MAX_RUN_HISTORY",
    "MCP_SERVER_BASE_URL",
    # metrics
    "METRIC_TYPES",
    "CATEGORIES",
    "make_metric",
    "format_criterion",
    "evaluate_metric",
    # scenarios
    "SCENARIOS",
    "DEFAULT_SCENARIO",
    "validate_scenarios",
]
