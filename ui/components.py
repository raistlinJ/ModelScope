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
        f'padding:3px 11px;border-radius:999px;'
        f'font-size:0.70rem;font-weight:700;letter-spacing:0.5px;display:inline-block">'
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
        f'padding:3px 10px;border-radius:999px;font-size:0.67rem;font-weight:700;'
        f'letter-spacing:0.4px;cursor:help;display:inline-block">{label}</span>'
    )


def status_pill(label: str, state: str) -> str:
    """Status pill using CSS classes. state: 'up' | 'down' | 'wait'."""
    return f'<span class="status-pill status-pill-{state}">{label}</span>'


# ── Semantic result badges ─────────────────────────────────────────────────────
# Using semi-transparent fills + colored text to match the .badge-pass/fail/na
# CSS classes, so inline and CSS-class badges remain visually consistent.

_PASS_BG   = "var(--success-dim)"
_FAIL_BG   = "var(--error-dim)"
_NA_BG     = "var(--surface2)"
_PASS_CLR  = "var(--success)"
_FAIL_CLR  = "var(--error)"
_NA_CLR    = "var(--muted)"
_BADGE_CSS = (
    "padding:3px 11px;border-radius:999px;font-size:0.70rem;"
    "font-weight:700;letter-spacing:0.5px;display:inline-block;border:1px solid"
)


def badge_pass(label: str = "PASS") -> str:
    """Pill-shaped PASS badge consistent with .badge-pass."""
    return (
        f'<span style="background:{_PASS_BG};color:{_PASS_CLR};'
        f'{_BADGE_CSS} var(--success)">{label}</span>'
    )


def badge_fail(label: str = "FAIL") -> str:
    """Pill-shaped FAIL badge consistent with .badge-fail."""
    return (
        f'<span style="background:{_FAIL_BG};color:{_FAIL_CLR};'
        f'{_BADGE_CSS} var(--error)">{label}</span>'
    )


def badge_na(label: str = "N/A") -> str:
    """Pill-shaped N/A badge consistent with .badge-na."""
    return (
        f'<span style="background:{_NA_BG};color:{_NA_CLR};'
        f'{_BADGE_CSS} var(--border)">{label}</span>'
    )
