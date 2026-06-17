"""
Shared UI component helpers (pure HTML string generators).

Import from here instead of defining badge/pill functions per-tab.
"""
from __future__ import annotations

from config.metrics import METRIC_TYPES


CAT_COLOUR: dict[str, str] = {
    "Validation":       "#b45309",
    "Tool":             "#c2410c",
    "Content":          "#0e7490",
    "Performance":      "#166534",
    "Path":             "#6d28d9",
    "Judge":            "#be123c",
    "CAF-LLM":          "#7c3aed",
    "CAF-Tools":        "#b45309",
    "CAF-Memory":       "#0369a1",
    "CAF-Environment":  "#065f46",
    "RAG":              "#0891b2",
    "Workflow":         "#6d28d9",
    "AI-Judge":         "#c026d3",
}


def badge(text: str, colour: str) -> str:
    """Generic coloured badge — PASS/FAIL/N/A, category labels, etc."""
    return (
        f'<span style="background:{colour};color:#fff;'
        f'padding:2px 10px;border-radius:4px;'
        f'font-size:0.72rem;font-weight:700;letter-spacing:0.5px">'
        f'{text}</span>'
    )


def type_badge(type_key: str) -> str:
    """Coloured metric-type badge with hover tooltip from the metric description."""
    info   = METRIC_TYPES.get(type_key, {})
    cat    = info.get("category", "—")
    label  = info.get("label", type_key)
    desc   = info.get("description", "").replace('"', '&quot;')
    colour = CAT_COLOUR.get(cat, "#64748b")
    return (
        f'<span title="{desc}" style="background:{colour};color:#fff;'
        f'padding:2px 8px;border-radius:4px;font-size:0.68rem;font-weight:700;'
        f'letter-spacing:0.4px;cursor:help">{label}</span>'
    )


def status_pill(label: str, state: str) -> str:
    """Status pill using CSS classes. state: 'up' | 'down' | 'wait'."""
    return f'<span class="status-pill status-pill-{state}">{label}</span>'
