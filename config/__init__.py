"""ModelScope configuration: static constants, metric definitions.

Unlike :mod:`core`, these modules are pure (stdlib only, no Streamlit), so the
public names are re-exported here for convenience — ``from config import
evaluate_metric`` works alongside the fully-qualified imports.

  defaults   — URLs, paths and tunable limits (single source of truth)
  metrics    — typed metric registry, evaluator dispatch, criterion formatting
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
]
