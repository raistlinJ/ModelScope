"""Shared terminal-output renderer for all evaluation tabs."""
from __future__ import annotations

import re

import streamlit as st

try:
    import streamlit.components.v1 as _st_components
    _COMPONENTS_AVAILABLE = True
except ImportError:
    _COMPONENTS_AVAILABLE = False


# JavaScript injected after each terminal render to scroll the terminal to the
# bottom. Uses window.parent to escape the Streamlit iframe boundary and targets
# the .terminal-window element directly.
_SCROLL_JS = """
<script>
(function() {
    window.parent.document.querySelectorAll('.terminal-window').forEach(function(el) {
        el.scrollTop = el.scrollHeight;
    });
})();
</script>
"""


def render_terminal(
    placeholder,
    logs: list[dict],
    classify,
    empty_msg: str = "Awaiting run…",
    height: int = 500,
) -> None:
    """Render log entries as a styled HTML terminal in a Streamlit placeholder.

    Each log entry dict must have a ``text`` key and an optional ``tag`` key
    (the CSS class suffix used when no per-line classification applies).

    classify(line: str) -> str  maps a single line to a CSS tag suffix.
    Return "" for unstyled lines; the entry's own ``tag`` is used as fallback.
    """
    if not logs:
        placeholder.markdown(
            f'<div class="terminal-window" role="log" aria-live="polite" aria-label="Evaluation log" '
            f'style="height: {height}px">{empty_msg}</div>',
            unsafe_allow_html=True,
        )
        return

    lines_html: list[str] = []
    for entry in logs:
        raw = entry["text"].replace("\\n", "\n")
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        entry_tag = entry.get("tag", "")
        for sub in raw.split("\n"):
            sub_tag = classify(sub) or entry_tag
            css  = f' class="log-{sub_tag}"' if sub_tag else ""
            text = sub.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            lines_html.append(f"<span{css}>{text}</span>")

    inner = "<br>".join(lines_html)
    placeholder.markdown(
        f'<div class="terminal-window" role="log" aria-live="polite" aria-label="Evaluation log" '
        f'style="height: {height}px">{inner}</div>',
        unsafe_allow_html=True,
    )

    # Bug 8: scroll the terminal to the bottom after each render
    if hasattr(st, "html"):
        st.html(_SCROLL_JS)
    elif _COMPONENTS_AVAILABLE:
        try:
            _st_components.html(_SCROLL_JS, height=0)
        except Exception:
            pass
