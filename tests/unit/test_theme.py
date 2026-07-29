"""Regression coverage for ModelScope's presentation-only theme layer."""
from __future__ import annotations

import json
from contextlib import nullcontext

from core.settings_store import load_settings, save_settings
from ui import terminal
from ui.theme import PALETTES, css_tokens, resolve_theme, terminal_palette


def test_system_preference_follows_browser_theme() -> None:
    assert resolve_theme("system", "light") == "light"
    assert resolve_theme("system", "dark") == "dark"
    assert resolve_theme("system", None) == "dark"


def test_explicit_preference_wins_over_browser_theme() -> None:
    assert resolve_theme("light", "dark") == "light"
    assert resolve_theme("dark", "light") == "dark"


def test_each_palette_contains_terminal_and_semantic_colours() -> None:
    required = {
        "bg", "surface", "surface2", "surface_hover", "input_bg", "button_bg",
        "dialog_bg", "popover_bg", "overlay", "border", "text", "accent",
        "success", "soft_fail", "warn", "error", "on_error",
        "terminal_bg", "terminal_text", "terminal_border",
    }
    assert required <= PALETTES["light"].keys()
    assert required <= PALETTES["dark"].keys()
    light_tokens = css_tokens("light")
    assert "#f6f8fa" in light_tokens
    assert "--terminal-bg" in light_tokens
    assert "--dialog-bg" in light_tokens
    assert terminal_palette("light")["terminal_bg"] == PALETTES["light"]["terminal_bg"]


def test_ui_theme_persists_without_affecting_project_data(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
    project = {"id": "one", "name": "One", "type": "bash_bot", "config": {"timeout": 30}}
    save_settings({"ui_theme": "light", "projects": [project]})

    stored = json.loads(path.read_text())
    assert stored["ui_theme"] == "light"
    assert stored["projects"] == [project]
    assert load_settings()["ui_theme"] == "light"


def test_ui_theme_survives_project_switch_and_never_enters_project_config() -> None:
    """ui_theme is global presentation state — project switching must never
    purge, hydrate, or write it into a project's config bundle."""
    import streamlit as st
    from core.state import sync_project

    proj_a = {"id": "A", "name": "A", "type": "bash_bot", "config": {}}
    proj_b = {"id": "B", "name": "B", "type": "bash_bot", "config": {}}

    st.session_state.clear()
    st.session_state["projects"] = [proj_a, proj_b]

    sync_project("A")
    st.session_state["ui_theme"] = "light"

    sync_project("B")
    assert st.session_state["ui_theme"] == "light", \
        "explicit theme preference was reset on switch to project B"

    sync_project("A")
    assert st.session_state["ui_theme"] == "light", \
        "explicit theme preference did not survive A -> B -> A"

    assert "ui_theme" not in proj_a["config"]
    assert "ui_theme" not in proj_b["config"]


def test_terminal_uses_light_palette_in_inline_and_iframe_views(monkeypatch) -> None:
    class Placeholder:
        def __init__(self):
            self.markdown_calls: list[str] = []

        def markdown(self, value, **_kwargs):
            self.markdown_calls.append(value)

        def container(self):
            return nullcontext()

    logs = [{"text": "tool completed", "tag": "result"}]
    inline = Placeholder()
    terminal.render_terminal(inline, logs, lambda _line: "", theme="light")
    assert "#fbfcfd" in inline.markdown_calls[-1]

    frame_html: list[str] = []
    monkeypatch.setattr(terminal, "_COMPONENTS_AVAILABLE", True)
    monkeypatch.setattr(
        terminal,
        "_st_components",
        type("Components", (), {"html": staticmethod(lambda value, **_kwargs: frame_html.append(value))}),
        raising=False,
    )
    terminal.render_terminal(Placeholder(), logs, lambda _line: "", follow_newest=True, theme="light")
    assert frame_html
    assert "#fbfcfd" in frame_html[-1]
    assert ".log-tool" in frame_html[-1]
    assert ".log-prompt" in frame_html[-1]
    assert 'data-theme="light"' in frame_html[-1]


def test_terminal_markup_changes_when_theme_changes() -> None:
    class Placeholder:
        def __init__(self):
            self.markdown_calls: list[str] = []

        def markdown(self, value, **_kwargs):
            self.markdown_calls.append(value)

    light = Placeholder()
    dark = Placeholder()
    terminal.render_terminal(light, [], lambda _line: "", theme="light")
    terminal.render_terminal(dark, [], lambda _line: "", theme="dark")
    assert light.markdown_calls[-1] != dark.markdown_calls[-1]
    assert 'data-theme="light"' in light.markdown_calls[-1]
    assert 'data-theme="dark"' in dark.markdown_calls[-1]


def test_tooltip_icon_is_scoped_and_unfilled_not_a_solid_dot() -> None:
    from ui.styles import _CSS

    assert '[data-testid="stWidgetLabel"] svg' not in _CSS, \
        "the broad widget-label svg fill rule must be replaced by a scoped stTooltipIcon rule"
    assert '[data-testid="stTooltipIcon"]' in _CSS
    assert "fill: none !important" in _CSS
    assert "var(--tooltip-bg)" in _CSS
    assert "var(--tooltip-border)" in _CSS
    assert '[data-testid="stTooltipHoverTarget"] button {' not in _CSS, \
        "ordinary tooltip-wrapped buttons must not be styled as circular help icons"
    assert 'button[aria-label^="Help for"]' in _CSS
    assert '[data-testid="stTooltipContent"] [data-testid="stMarkdownContainer"] p' in _CSS
    assert '-webkit-text-fill-color: var(--tooltip-icon) !important' in _CSS


def test_tooltip_tokens_present_in_both_palettes() -> None:
    required = {"tooltip_bg", "tooltip_icon", "tooltip_border"}
    assert required <= PALETTES["light"].keys()
    assert required <= PALETTES["dark"].keys()
    # Dark mode needs a real border so the fixed-dark badge stays visible
    # against the also-dark page background; light mode does not.
    assert PALETTES["dark"]["tooltip_border"] != "transparent"
    tokens = css_tokens("dark")
    assert "--tooltip-bg" in tokens
    assert "--tooltip-border" in tokens


def test_checked_checkbox_has_explicit_tick_mark() -> None:
    from ui.styles import _CSS

    assert "background-color: var(--accent) !important" in _CSS
    assert "::after" in _CSS
    assert "border-width: 0 2px 2px 0 !important" in _CSS
    assert "align-items: center !important" in _CSS
    assert "flex: 0 0 16px !important" in _CSS


def test_polished_input_and_primary_rules_are_token_driven() -> None:
    from ui.styles import _CSS

    assert "--input-text" in css_tokens("light")
    assert "--input-text" in css_tokens("dark")
    assert "caret-color: var(--accent) !important" in _CSS
    # Placeholders sit at 0.85: 0.68 dropped them to 3.0:1 on the light input.
    assert "opacity: 0.85 !important" in _CSS
    assert "background: var(--accent-dim) !important" in _CSS
    assert '[data-testid="stTextInput"] [data-baseweb="input"] button' in _CSS
    assert '[data-testid="stRadio"] > label[data-testid="stWidgetLabel"]' in _CSS


def test_project_trash_is_neutral_but_confirm_delete_remains_destructive() -> None:
    from ui.styles import _CSS

    assert '[class*="st-key-btn_confirm_delete_project"] button' in _CSS
    assert '[class*="st-key-btn_del_"] button,' not in _CSS
    assert '[class*="st-key-btn-del-"] button,' not in _CSS


def test_disabled_checkbox_and_inputs_are_visually_distinct() -> None:
    from ui.styles import _CSS

    assert 'input:disabled) > span:first-child' in _CSS
    assert '[data-testid="stNumberInput"]:has(input:disabled) [data-baseweb="input"]' in _CSS
    assert '[data-testid="stTextInput"]:has(input:disabled) [data-baseweb="input"]' in _CSS
    assert "[data-testid=\"stNumberInput\"] input:disabled" in _CSS


def test_styles_cover_current_streamlit_structural_controls() -> None:
    from ui.styles import _CSS

    required_selectors = (
        '[data-testid="stDialog"]',
        '[role="dialog"][aria-modal="true"]',
        '[data-testid="stPopoverBody"]',
        '[data-testid="stTextInput"] [data-baseweb="input"]',
        '[data-testid="stSelectbox"] [data-baseweb="select"]',
        'button[data-testid="stNumberInputStepUp"]',
        'button[data-testid="stNumberInputStepDown"]',
        'button[data-testid^="stBaseButton-"]',
        '[data-testid="stPopoverButton"]',
        '[data-testid="stToggle"] button[role="switch"]',
    )
    assert all(selector in _CSS for selector in required_selectors)
    assert "background: var(--dialog-bg)" in _CSS
    assert "background: var(--input-bg)" in _CSS
    assert "background: var(--terminal-bg)" in _CSS


def test_validation_popover_explicitly_themes_its_trigger_and_portal_content() -> None:
    from ui.styles import _CSS

    assert '[data-testid="stPopoverButton"]' in _CSS
    assert '[data-testid="stPopoverBody"] > div' in _CSS
    assert '[data-baseweb="popover"] > div' in _CSS
    assert '[data-testid="stPopoverBody"] [data-testid="stCaptionContainer"] p' in _CSS


def test_connection_error_overlay_and_validation_labels_have_explicit_contrast() -> None:
    from ui.styles import _CSS
    from ui import config_tab

    assert '[data-testid="stConnectionError"]' in _CSS
    assert '-webkit-text-fill-color: var(--text) !important' in _CSS
    source = open(config_tab.__file__, encoding="utf-8").read()
    assert "color:var(--text)" in source
