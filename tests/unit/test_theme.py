"""Regression coverage for ModelScope's presentation-only theme layer."""
from __future__ import annotations

import json
from contextlib import nullcontext

from core.settings_store import load_settings, save_settings
from ui import terminal
from ui.theme import PALETTES, css_tokens, resolve_theme


def test_system_preference_follows_browser_theme() -> None:
    assert resolve_theme("system", "light") == "light"
    assert resolve_theme("system", "dark") == "dark"
    assert resolve_theme("system", None) == "dark"


def test_explicit_preference_wins_over_browser_theme() -> None:
    assert resolve_theme("light", "dark") == "light"
    assert resolve_theme("dark", "light") == "dark"


def test_each_palette_contains_terminal_and_semantic_colours() -> None:
    required = {"bg", "surface", "text", "accent", "success", "soft_fail", "warn", "error", "terminal_bg", "terminal_text"}
    assert required <= PALETTES["light"].keys()
    assert required <= PALETTES["dark"].keys()
    light_tokens = css_tokens("light")
    assert "#f6f8fa" in light_tokens
    assert "--terminal-bg" in light_tokens


def test_ui_theme_persists_without_affecting_project_data(tmp_path, monkeypatch) -> None:
    path = tmp_path / "settings.json"
    monkeypatch.setattr("core.settings_store._SETTINGS_PATH", path)
    project = {"id": "one", "name": "One", "type": "bash_bot", "config": {"timeout": 30}}
    save_settings({"ui_theme": "light", "projects": [project]})

    stored = json.loads(path.read_text())
    assert stored["ui_theme"] == "light"
    assert stored["projects"] == [project]
    assert load_settings()["ui_theme"] == "light"


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
