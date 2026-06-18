"""Shared terminal-output renderer for all evaluation tabs."""
from __future__ import annotations

import re


def render_terminal(
    placeholder,
    logs: list[dict],
    classify,
    empty_msg: str = "Awaiting run…",
) -> None:
    """Render log entries as a styled HTML terminal in a Streamlit placeholder.

    Each log entry dict must have a ``text`` key and an optional ``tag`` key
    (the CSS class suffix used when no per-line classification applies).

    classify(line: str) -> str  maps a single line to a CSS tag suffix.
    Return "" for unstyled lines; the entry's own ``tag`` is used as fallback.
    """
    if not logs:
        placeholder.markdown(
            f'<div class="terminal-window">{empty_msg}</div>',
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
        f'<div class="terminal-window">{inner}</div>',
        unsafe_allow_html=True,
    )
