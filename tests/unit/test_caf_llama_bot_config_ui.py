"""Streamlit AppTest coverage for the CAF + llama.cpp Fetch-models UI.

tests/conftest.py replaces sys.modules["streamlit"] with a bare stub for the
whole suite (other tests only need st.session_state as a dict). Real
rendering needs the genuine package, so `real_app_test` below swaps in real
streamlit for the duration of a single test only, then restores the stub.
This is safe because _render_connection_fields/_fetch_llama_models both do a
LOCAL `import streamlit` inside the method — every other already-imported
module keeps whatever `st` object it bound at collection time.
"""

from __future__ import annotations

import sys

import pytest


@pytest.fixture
def real_app_test():
    saved = {name: mod for name, mod in sys.modules.items() if name == "streamlit" or name.startswith("streamlit.")}
    for name in list(saved):
        del sys.modules[name]
    try:
        from streamlit.testing.v1 import AppTest

        yield AppTest
    finally:
        for name in [n for n in list(sys.modules) if n == "streamlit" or n.startswith("streamlit.")]:
            del sys.modules[name]
        sys.modules.update(saved)


def _render_connection_fields(model_dir):
    import streamlit as st
    from core.bot_types import get_bot_plugin, refresh_bot_plugins
    from core.state import _effective_defaults

    refresh_bot_plugins()
    for key, default in _effective_defaults().items():
        st.session_state.setdefault(key, default)
    st.session_state["caf_llama_srv_model_dir"] = model_dir
    plugin = get_bot_plugin("caf_llama_bot")
    plugin._render_connection_fields()


def test_model_directory_has_a_fetch_action(real_app_test, tmp_path):
    at = real_app_test.from_function(_render_connection_fields, kwargs={"model_dir": str(tmp_path)})
    at.run()
    assert not at.exception
    assert at.button(key="btn_caf_llama_fetch_models")


def test_manual_entry_before_any_fetch(real_app_test, tmp_path):
    at = real_app_test.from_function(_render_connection_fields, kwargs={"model_dir": str(tmp_path)})
    at.run()
    model_inputs = [ti for ti in at.text_input if ti.key == "caf_llama_srv_model_name"]
    assert len(model_inputs) == 1
    assert not [sb for sb in at.selectbox if sb.key == "caf_llama_srv_model_name"]


def test_successful_fetch_renders_one_dropdown_with_correct_selection(real_app_test, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "model-a.gguf").touch()
    (tmp_path / "model-b.gguf").touch()
    (tmp_path / "ggml-vocab-x.gguf").touch()

    at = real_app_test.from_function(_render_connection_fields, kwargs={"model_dir": str(tmp_path)})
    at.run()
    at.button(key="btn_caf_llama_fetch_models").click().run()

    assert not at.error
    model_boxes = [sb for sb in at.selectbox if sb.key == "caf_llama_srv_model_name"]
    assert len(model_boxes) == 1
    assert not [ti for ti in at.text_input if ti.key == "caf_llama_srv_model_name"]
    assert sorted(model_boxes[0].options) == ["model-b.gguf", "sub/model-a.gguf"]
    assert model_boxes[0].value in model_boxes[0].options


def test_empty_directory_reports_warning_and_keeps_manual_entry(real_app_test, tmp_path):
    at = real_app_test.from_function(_render_connection_fields, kwargs={"model_dir": str(tmp_path)})
    at.run()
    at.button(key="btn_caf_llama_fetch_models").click().run()

    assert not at.error
    assert at.warning
    assert not [sb for sb in at.selectbox if sb.key == "caf_llama_srv_model_name"]
    assert [ti for ti in at.text_input if ti.key == "caf_llama_srv_model_name"]


def test_missing_directory_reports_error_and_keeps_manual_entry(real_app_test):
    at = real_app_test.from_function(_render_connection_fields, kwargs={"model_dir": "/no/such/directory"})
    at.run()
    at.button(key="btn_caf_llama_fetch_models").click().run()

    assert at.error
    assert not [sb for sb in at.selectbox if sb.key == "caf_llama_srv_model_name"]
