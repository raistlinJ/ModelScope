"""Theme selection and shared palettes for the Streamlit UI."""
from __future__ import annotations

from typing import Literal, Mapping

import streamlit as st

ThemeName = Literal["light", "dark"]
ThemePreference = Literal["system", "light", "dark"]


PALETTES: dict[ThemeName, dict[str, str]] = {
    "dark": {
        "bg": "#0d1117", "surface": "#161b22", "surface2": "#21262d",
        "surface_hover": "#262c36", "input_bg": "#21262d",
        "button_bg": "#21262d", "dialog_bg": "#161b22", "popover_bg": "#161b22",
        "overlay": "rgba(1,4,9,0.72)",
        "border": "#30363d", "border_subtle": "rgba(48,54,61,0.72)",
        "text": "#e6edf3", "input_text": "#b8c2cc", "muted": "#8b949e", "accent": "#2dd4bf",
        "accent_hi": "#5eead4", "accent_dim": "rgba(45,212,191,0.15)",
        "accent_glow": "rgba(45,212,191,0.08)", "on_accent": "#0d1117",
        "success": "#3fb950", "success_dim": "rgba(63,185,80,0.18)",
        "soft_fail": "#ec4899", "soft_fail_dim": "rgba(236,72,153,0.16)",
        "warn": "#f0883e", "warn_dim": "rgba(240,136,62,0.16)",
        "error": "#f85149", "error_dim": "rgba(248,81,73,0.18)",
        "on_error": "#ffffff",
        "info": "#56b6c2", "command": "#d946ef", "prompt": "#4ade80",
        "shadow": "rgba(0,0,0,0.28)", "terminal_bg": "#0d1117",
        "terminal_text": "#c9d1d9", "terminal_border": "#30363d",
        "tooltip_bg": "#30363d", "tooltip_icon": "#c9d1d9",
        "tooltip_border": "#6e7681",
    },
    "light": {
        "bg": "#f6f8fa", "surface": "#ffffff", "surface2": "#eef2f5",
        "surface_hover": "#e7ecf0", "input_bg": "#ffffff",
        "button_bg": "#ffffff", "dialog_bg": "#ffffff", "popover_bg": "#ffffff",
        "overlay": "rgba(31,35,40,0.36)",
        "border": "#d0d7de", "border_subtle": "rgba(208,215,222,0.92)",
        "text": "#1f2328", "input_text": "#46515c", "muted": "#59636e", "accent": "#0f766e",
        "accent_hi": "#115e59", "accent_dim": "rgba(15,118,110,0.12)",
        "accent_glow": "rgba(15,118,110,0.06)", "on_accent": "#ffffff",
        "success": "#1a7f37", "success_dim": "rgba(26,127,55,0.12)",
        "soft_fail": "#be185d", "soft_fail_dim": "rgba(190,24,93,0.12)",
        "warn": "#9a6700", "warn_dim": "rgba(154,103,0,0.12)",
        "error": "#cf222e", "error_dim": "rgba(207,34,46,0.10)",
        "on_error": "#ffffff",
        "info": "#0969da", "command": "#8250df", "prompt": "#1a7f37",
        "shadow": "rgba(31,35,40,0.10)", "terminal_bg": "#fbfcfd",
        "terminal_text": "#24292f", "terminal_border": "#d0d7de",
        # Fixed dark badge — same in both palettes so the help button reads
        # as a deliberate control rather than following the surrounding text
        # colour; only the border (added in dark mode) changes per theme.
        "tooltip_bg": "#30363d", "tooltip_icon": "#c9d1d9",
        "tooltip_border": "transparent",
    },
}


def resolve_theme(preference: object, system_theme: object) -> ThemeName:
    """Resolve a persisted preference, safely defaulting to dark."""
    if preference in ("light", "dark"):
        return preference
    return "light" if system_theme == "light" else "dark"


def current_theme() -> ThemeName:
    """Return the active presentation theme for the current Streamlit run."""
    system = getattr(getattr(getattr(st, "context", None), "theme", None), "type", None)
    return resolve_theme(st.session_state.get("ui_theme", "system"), system)


def palette(theme: ThemeName | None = None) -> Mapping[str, str]:
    return PALETTES[theme or current_theme()]


def terminal_palette(theme: ThemeName | None = None) -> Mapping[str, str]:
    """Return the explicit colours required inside terminal component iframes."""
    values = palette(theme)
    return {
        key: values[key]
        for key in (
            "terminal_bg", "terminal_text", "terminal_border", "accent", "info",
            "muted", "success", "warn", "error", "command", "prompt",
        )
    }


def css_tokens(theme: ThemeName) -> str:
    """Return CSS custom properties used by the shared UI stylesheet."""
    values = palette(theme)
    return f"""
    <style id="modelscope-theme-tokens">
    :root {{
        --bg:{values['bg']}; --surface:{values['surface']}; --surface2:{values['surface2']};
        --surface-hover:{values['surface_hover']}; --input-bg:{values['input_bg']};
        --button-bg:{values['button_bg']}; --dialog-bg:{values['dialog_bg']};
        --popover-bg:{values['popover_bg']}; --overlay:{values['overlay']};
        --border:{values['border']}; --border-subtle:{values['border_subtle']};
        --text:{values['text']}; --input-text:{values['input_text']}; --muted:{values['muted']}; --accent:{values['accent']};
        --accent-hi:{values['accent_hi']}; --accent-dim:{values['accent_dim']};
        --accent-glow:{values['accent_glow']}; --on-accent:{values['on_accent']};
        --success:{values['success']}; --success-dim:{values['success_dim']};
        --soft-fail:{values['soft_fail']}; --soft-fail-dim:{values['soft_fail_dim']};
        --warn:{values['warn']}; --warn-dim:{values['warn_dim']};
        --error:{values['error']}; --error-dim:{values['error_dim']}; --on-error:{values['on_error']};
        --info:{values['info']}; --cmd-color:{values['command']}; --prompt-color:{values['prompt']};
        --shadow:{values['shadow']}; --terminal-bg:{values['terminal_bg']};
        --terminal-text:{values['terminal_text']}; --terminal-border:{values['terminal_border']};
        --tooltip-bg:{values['tooltip_bg']}; --tooltip-icon:{values['tooltip_icon']};
        --tooltip-border:{values['tooltip_border']};
    }}
    </style>
    """
