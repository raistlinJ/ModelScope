"""Shared terminal-output renderer for all evaluation tabs."""
from __future__ import annotations

import re

import streamlit as st
from ui.theme import ThemeName, current_theme, palette

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
    theme: ThemeName | None = None,
) -> None:
    """Render log entries as a styled HTML terminal in a Streamlit placeholder.

    Each log entry dict must have a ``text`` key and an optional ``tag`` key
    (the CSS class suffix used when no per-line classification applies).

    classify(line: str) -> str  maps a single line to a CSS tag suffix.
    Return "" for unstyled lines; the entry's own ``tag`` is used as fallback.
    """
    # The standard markdown renderer can be shown after a component-backed
    # live terminal has stopped.  Keep its frame fully inline so it does not
    # depend on the page stylesheet surviving a Streamlit refresh.
    active_theme = theme or current_theme()
    colors = palette(active_theme)
    terminal_style = (
        f"box-sizing:border-box;height:{height}px;overflow-y:auto;padding:14px 18px;"
        f"border:1px solid {colors['terminal_border']};border-left:3px solid {colors['accent']};"
        f"border-radius:8px;background:{colors['terminal_bg']};color:{colors['terminal_text']};"
        "font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace;"
        "white-space:pre-wrap;word-break:break-word;"
    )
    if not logs:
        placeholder.markdown(
            f'<div class="terminal-window" role="log" aria-live="polite" aria-label="Evaluation log" '
            f'style="{terminal_style}">{empty_msg}</div>',
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
          html, body {{ margin: 0; height: 100%; background: {colors['terminal_bg']}; }}
          .terminal-window {{ box-sizing: border-box; height: {height}px; overflow-y: auto;
            padding: 12px; border: 1px solid {colors['terminal_border']}; border-left: 3px solid {colors['accent']};
            border-radius: 6px; background: {colors['terminal_bg']}; color: {colors['terminal_text']};
            font: 12px/1.45 ui-monospace, SFMono-Regular,
            Menlo, Monaco, Consolas, monospace; white-space: pre-wrap; }}
          .log-init,.log-usr {{ color: {colors['info']}; }}
          .log-tools,.log-tool {{ color: {colors['accent']}; font-weight: 600; }}
          .log-llm,.log-stream {{ color: {colors['terminal_text']}; }}
          .log-thinking,.log-tokens,.log-sys {{ color: {colors['muted']}; font-style: italic; }}
          .log-result,.log-done,.log-success {{ color: {colors['success']}; font-weight: 700; }}
          .log-val,.log-warn,.log-warning,.log-decision {{ color: {colors['warn']}; font-weight: 600; }}
          .log-cancel,.log-error {{ color: {colors['error']}; font-weight: 700; }}
          .log-cmd {{ color: {colors['command']}; font-weight: 600; }}
          .log-prompt {{ color: {colors['prompt']}; font-weight: 600; }}
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
        f'style="{terminal_style}">{inner}</div>',
        unsafe_allow_html=True,
    )
