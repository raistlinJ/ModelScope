"""
Regression tests for the state.py <-> bot_types plugin aggregation.

core/state.py used to hardcode every bash_/llama_cli_/llama_server_ prefixed
session-state default directly, duplicating what each bot-type plugin already
declared for its own project config. That duplication is exactly what let the
plugin's state_key_map get out of sync with state.py's actual defaults (e.g.
llama_cli_bot's llm_helper_* keys were in its state_key_map but never in
state.py's _DEFAULTS, so they were silently deleted rather than reset to a
default on project switch).

core/state.py now aggregates _effective_defaults()/_effective_global_keys()/
_effective_owned_prefixes() from whatever plugins core.bot_types.iter_bot_plugins()
returns, so a brand-new plugin's session state Just Works without touching
core/state.py at all. These tests prove that property directly with a
synthetic plugin, rather than only exercising the three built-in ones.
"""
from unittest.mock import patch

import core.state as state
from core.bot_types.base import BotTypePlugin


class _FakePlugin(BotTypePlugin):
    type_id = "fake_bot"
    label = "Fake-Bot"
    session_defaults = {
        "fake_bot_widget_a": "default-a",
        "fake_bot_widget_b": 42,
    }
    global_keys = frozenset({"fake_bot_widget_b"})
    owned_prefixes = ("fake_bot_dynamic_",)


def _with_fake_plugin(builtin_plugins=()):
    return patch("core.bot_types.iter_bot_plugins", return_value=(*builtin_plugins, _FakePlugin()))


class TestEffectiveDefaultsAggregation:
    def test_plugin_session_defaults_are_merged_in(self):
        with _with_fake_plugin():
            merged = state._effective_defaults()
        assert merged["fake_bot_widget_a"] == "default-a"
        assert merged["fake_bot_widget_b"] == 42

    def test_base_defaults_still_present_alongside_plugin_defaults(self):
        with _with_fake_plugin():
            merged = state._effective_defaults()
        # A bot-agnostic key from state.py's own _DEFAULTS must still be there.
        assert "projects" in merged
        assert "backend_type" in merged

    def test_plugin_global_keys_are_merged_in(self):
        with _with_fake_plugin():
            keys = state._effective_global_keys()
        assert "fake_bot_widget_b" in keys
        assert "fake_bot_widget_a" not in keys
        # Base global keys must still be present.
        assert "projects" in keys

    def test_plugin_owned_prefixes_are_merged_in(self):
        with _with_fake_plugin():
            prefixes = state._effective_owned_prefixes()
        assert "fake_bot_dynamic_" in prefixes
        assert "_us_" in prefixes  # base prefix, shared by all bot types


class TestNewPluginIsolationWithoutTouchingStatePy:
    """The actual point of the refactor: a plugin state.py has never heard of
    still gets correct isolation semantics for free."""

    def test_new_plugin_key_resets_on_project_switch(self):
        import streamlit as st

        proj_a = {"id": "A", "type": "fake_bot", "config": {}}
        proj_b = {"id": "B", "type": "fake_bot", "config": {}}

        with _with_fake_plugin(), patch("core.state.get_bot_plugin", return_value=_FakePlugin()):
            st.session_state.clear()
            st.session_state["projects"] = [proj_a, proj_b]

            state.sync_project("A")
            st.session_state["fake_bot_widget_a"] = "set-by-user"

            state.sync_project("B")
            assert st.session_state["fake_bot_widget_a"] == "default-a", \
                "a session_defaults key from an unrelated plugin did not reset on switch"

    def test_new_plugin_global_key_survives_project_switch(self):
        import streamlit as st

        proj_a = {"id": "A", "type": "fake_bot", "config": {}}
        proj_b = {"id": "B", "type": "fake_bot", "config": {}}

        with _with_fake_plugin(), patch("core.state.get_bot_plugin", return_value=_FakePlugin()):
            st.session_state.clear()
            st.session_state["projects"] = [proj_a, proj_b]

            state.sync_project("A")
            st.session_state["fake_bot_widget_b"] = 999

            state.sync_project("B")
            assert st.session_state["fake_bot_widget_b"] == 999, \
                "a declared global_keys entry was reset on switch instead of preserved"

    def test_new_plugin_dynamic_widget_key_purged_on_switch(self):
        import streamlit as st

        proj_a = {"id": "A", "type": "fake_bot", "config": {}}
        proj_b = {"id": "B", "type": "fake_bot", "config": {}}

        with _with_fake_plugin(), patch("core.state.get_bot_plugin", return_value=_FakePlugin()):
            st.session_state.clear()
            st.session_state["projects"] = [proj_a, proj_b]

            state.sync_project("A")
            st.session_state["fake_bot_dynamic_row_3"] = "ephemeral"

            state.sync_project("B")
            assert "fake_bot_dynamic_row_3" not in st.session_state, \
                "a dynamic widget key under owned_prefixes was not purged on switch"


class TestNoBotSpecificCodeInStatePy:
    """Direct regression test for the original complaint: core/state.py should
    contain no bash_/llama_cli_/llama_server_-prefixed default assignments —
    those belong to the respective bot-type plugin modules."""

    def test_state_py_declares_no_bot_prefixed_defaults(self):
        with open("core/state.py") as f:
            source = f.read()
        # Only the two pre-existing legacy global keys should mention these
        # prefixes at all (they're the old sidebar server-management keys,
        # not part of the llama_server_bot project type's own config).
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith('"'):
                continue
            for prefix in ("bash_", "llama_cli_", "llama_server_"):
                if stripped.startswith(f'"{prefix}'):
                    assert prefix == "llama_server_" and (
                        "llama_server_bin" in stripped or "llama_server_running" in stripped
                    ), f"unexpected bot-specific key still hardcoded in core/state.py: {stripped!r}"
