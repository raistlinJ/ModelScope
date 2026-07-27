"""Unit tests for ui.optional_param_card — the reusable card renderer shared
by Llama-Server-Bot and CAF + llama.cpp's advanced runtime grids.

Uses a mocked `st` rather than Streamlit's AppTest harness: AppTest's
element-id bookkeeping for keyed layout containers (st.container(key=...))
does not tolerate multiple independent script runs sharing one pytest
process/thread, which is a harness limitation unrelated to this module's
own behaviour — already proven to render correctly in the real app via
the CAF AppTest suite. optional_param_card imports streamlit LOCALLY inside
each function (see its module docstring), so the mock is installed via
sys.modules rather than patching a module-level attribute that doesn't
exist.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import ui.optional_param_card as opc


def _fake_streamlit(checkbox_value: bool = False) -> MagicMock:
    fake_st = MagicMock()
    fake_st.session_state = {}
    fake_st.columns.return_value = (MagicMock(), MagicMock())
    fake_st.checkbox.return_value = checkbox_value
    return fake_st


def _patched(fake_st):
    return patch.dict(sys.modules, {"streamlit": fake_st})


class TestRenderOptionalParamCard:
    def test_defaults_are_set_via_setdefault_without_overwriting_existing(self):
        fake_st = _fake_streamlit()
        fake_st.session_state["plugin_a_temperature"] = 1.5  # already set by a prior run
        with _patched(fake_st):
            opc.render_optional_param_card(
                MagicMock(), state_prefix="plugin_a", label="Temperature", key_suffix="temp",
                min_v=0.0, max_v=2.0, step=0.1, help_text="h", is_float=True,
                value_key_suffix="temperature", default_value=0.8,
            )
        assert fake_st.session_state["plugin_a_temperature"] == 1.5  # untouched
        assert fake_st.session_state["plugin_a_en_temp"] is False

    def test_number_input_disabled_state_follows_the_enable_checkbox(self):
        fake_st = _fake_streamlit(checkbox_value=True)
        with _patched(fake_st):
            opc.render_optional_param_card(
                MagicMock(), state_prefix="plugin_a", label="Temperature", key_suffix="temp",
                min_v=0.0, max_v=2.0, step=0.1, help_text="h", is_float=True,
                value_key_suffix="temperature", default_value=0.8,
            )
        assert fake_st.number_input.call_args.kwargs["disabled"] is False

    def test_two_state_prefixes_never_share_keys(self):
        fake_st = _fake_streamlit()
        with _patched(fake_st):
            opc.render_optional_param_card(
                MagicMock(), state_prefix="plugin_a", label="Temperature", key_suffix="temp",
                min_v=0.0, max_v=2.0, step=0.1, help_text="h", default_value=1,
            )
            opc.render_optional_param_card(
                MagicMock(), state_prefix="plugin_b", label="Temperature", key_suffix="temp",
                min_v=0.0, max_v=2.0, step=0.1, help_text="h", default_value=2,
            )
        assert fake_st.session_state["plugin_a_temp"] == 1
        assert fake_st.session_state["plugin_b_temp"] == 2
        assert fake_st.session_state["plugin_a_en_temp"] is False
        assert fake_st.session_state["plugin_b_en_temp"] is False

    def test_container_key_is_namespaced_by_prefix_and_suffix(self):
        fake_st = _fake_streamlit()
        with _patched(fake_st):
            opc.render_optional_param_card(
                MagicMock(), state_prefix="plugin_a", label="Temperature", key_suffix="temp",
                min_v=0.0, max_v=2.0, step=0.1, help_text="h", default_value=1,
            )
        assert fake_st.container.call_args.kwargs["key"] == "advcard_plugin_a_temp"


class TestRenderFlagCard:
    def test_defaults_to_false_and_renders_a_single_checkbox(self):
        fake_st = _fake_streamlit()
        with _patched(fake_st):
            opc.render_flag_card(MagicMock(), key="plugin_a_flash_attn", label="Flash Attn")
        assert fake_st.session_state["plugin_a_flash_attn"] is False
        fake_st.checkbox.assert_called_once()
        assert fake_st.checkbox.call_args.kwargs["key"] == "plugin_a_flash_attn"

    def test_does_not_overwrite_an_existing_true_value(self):
        fake_st = _fake_streamlit()
        fake_st.session_state["plugin_a_flash_attn"] = True
        with _patched(fake_st):
            opc.render_flag_card(MagicMock(), key="plugin_a_flash_attn", label="Flash Attn")
        assert fake_st.session_state["plugin_a_flash_attn"] is True
