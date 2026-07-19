"""Shared terminal-output renderer for all evaluation tabs."""
from __future__ import annotations

import re

import streamlit as st

try:
    import streamlit.components.v1 as _st_components
    _COMPONENTS_AVAILABLE = True
except ImportError:
    _COMPONENTS_AVAILABLE = False


def render_terminal(
    placeholder,
    logs: list[dict],
    classify,
    empty_msg: str = "Awaiting run…",
    height: int = 500,
    follow_newest: bool = False,
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
    if follow_newest and _COMPONENTS_AVAILABLE:
        # A component owns its iframe DOM, so it can reliably scroll itself on
        # every Streamlit fragment refresh.  Trying to manipulate the parent
        # document from injected scripts is blocked in some Streamlit builds.
        terminal_html = f"""
        <style>
          html, body {{ margin: 0; height: 100%; background: #0d1117; }}
          .terminal-window {{ box-sizing: border-box; height: {height}px; overflow-y: auto;
            padding: 12px; border: 1px solid #30363d; border-radius: 6px;
            color: #c9d1d9; font: 12px/1.45 ui-monospace, SFMono-Regular,
            Menlo, Monaco, Consolas, monospace; white-space: pre-wrap; }}
          .log-error {{ color: #ff7b72; }} .log-success {{ color: #7ee787; }}
          .log-warning {{ color: #d29922; }}
        </style>
        <div class="terminal-window" id="terminal">{inner}</div>
        <script>
          const terminal = document.getElementById('terminal');
          terminal.scrollTop = terminal.scrollHeight;
          requestAnimationFrame(() => {{ terminal.scrollTop = terminal.scrollHeight; }});
        </script>
        """
        try:
            with placeholder.container():
                _st_components.html(terminal_html, height=height, scrolling=False)
            return
        except Exception:
            # Preserve the standard terminal if a deployment disables custom
            # components.
            pass
    placeholder.markdown(
        f'<div class="terminal-window" role="log" aria-live="polite" aria-label="Evaluation log" '
        f'style="height: {height}px">{inner}</div>',
        unsafe_allow_html=True,
    )
