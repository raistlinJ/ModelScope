"""Theme selection and shared palettes for the Streamlit UI."""
from __future__ import annotations

from typing import Literal, Mapping

import streamlit as st

ThemeName = Literal["light", "dark"]
ThemePreference = Literal["system", "light", "dark"]


PALETTES: dict[ThemeName, dict[str, str]] = {
    "dark": {
        "bg": "#0d1117", "surface": "#161b22", "surface2": "#21262d",
        "border": "#30363d", "border_subtle": "rgba(48,54,61,0.72)",
        "text": "#e6edf3", "muted": "#8b949e", "accent": "#2dd4bf",
        "accent_hi": "#5eead4", "accent_dim": "rgba(45,212,191,0.15)",
        "accent_glow": "rgba(45,212,191,0.08)", "on_accent": "#0d1117",
        "success": "#3fb950", "success_dim": "rgba(63,185,80,0.18)",
        "soft_fail": "#ec4899", "soft_fail_dim": "rgba(236,72,153,0.16)",
        "warn": "#f0883e", "warn_dim": "rgba(240,136,62,0.16)",
        "error": "#f85149", "error_dim": "rgba(248,81,73,0.18)",
        "info": "#56b6c2", "command": "#d946ef", "prompt": "#4ade80",
        "shadow": "rgba(0,0,0,0.28)", "terminal_bg": "#0d1117",
        "terminal_text": "#c9d1d9", "terminal_border": "#30363d",
    },
    "light": {
        "bg": "#f6f8fa", "surface": "#ffffff", "surface2": "#eef2f5",
        "border": "#d0d7de", "border_subtle": "rgba(208,215,222,0.92)",
        "text": "#1f2328", "muted": "#59636e", "accent": "#0f766e",
        "accent_hi": "#115e59", "accent_dim": "rgba(15,118,110,0.12)",
        "accent_glow": "rgba(15,118,110,0.06)", "on_accent": "#ffffff",
        "success": "#1a7f37", "success_dim": "rgba(26,127,55,0.12)",
        "soft_fail": "#be185d", "soft_fail_dim": "rgba(190,24,93,0.12)",
        "warn": "#9a6700", "warn_dim": "rgba(154,103,0,0.12)",
        "error": "#cf222e", "error_dim": "rgba(207,34,46,0.10)",
        "info": "#0969da", "command": "#8250df", "prompt": "#1a7f37",
        "shadow": "rgba(31,35,40,0.10)", "terminal_bg": "#fbfcfd",
        "terminal_text": "#24292f", "terminal_border": "#d0d7de",
    },
}


def resolve_theme(preference: object, system_theme: object) -> ThemeName:
    """Resolve a persisted preference, safely defaulting to dark."""
    if preference in ("light", "dark"):
        return preference
    return "light" if system_theme == "light" else "dark"


def current_theme() -> ThemeName:
    """Return the active presentation theme for the current Streamlit run."""
    system = getattr(getattr(st.context, "theme", None), "type", None)
    return resolve_theme(st.session_state.get("ui_theme", "system"), system)


def palette(theme: ThemeName | None = None) -> Mapping[str, str]:
    return PALETTES[theme or current_theme()]


def css_tokens(theme: ThemeName) -> str:
    """Return CSS custom properties used by the shared UI stylesheet."""
    values = palette(theme)
    return f"""
    <style id="modelscope-theme-tokens">
    :root {{
        --bg:{values['bg']}; --surface:{values['surface']}; --surface2:{values['surface2']};
        --border:{values['border']}; --border-subtle:{values['border_subtle']};
        --text:{values['text']}; --muted:{values['muted']}; --accent:{values['accent']};
        --accent-hi:{values['accent_hi']}; --accent-dim:{values['accent_dim']};
        --accent-glow:{values['accent_glow']}; --on-accent:{values['on_accent']};
        --success:{values['success']}; --success-dim:{values['success_dim']};
        --soft-fail:{values['soft_fail']}; --soft-fail-dim:{values['soft_fail_dim']};
        --warn:{values['warn']}; --warn-dim:{values['warn_dim']};
        --error:{values['error']}; --error-dim:{values['error_dim']};
        --info:{values['info']}; --cmd-color:{values['command']}; --prompt-color:{values['prompt']};
        --shadow:{values['shadow']}; --terminal-bg:{values['terminal_bg']};
        --terminal-text:{values['terminal_text']}; --terminal-border:{values['terminal_border']};
    }}
    </style>
    """
