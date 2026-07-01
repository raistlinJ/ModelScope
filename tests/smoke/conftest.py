"""
Smoke-test conftest: add st.dialog and other missing Streamlit attrs to the mock
module created by the root conftest, so UI modules can be imported in tests.
"""
import streamlit as st

# @st.dialog is a module-level decorator in config_tab.py and app.py.
# If the mock streamlit doesn't provide it, importing those modules fails.
if not hasattr(st, "dialog"):
    st.dialog = lambda *args, **kwargs: (lambda f: f)

# Other common Streamlit callables that UI modules may reference at import time
for _attr in ("expander", "container", "tabs", "columns", "sidebar",
              "text_input", "number_input", "selectbox", "checkbox", "button",
              "download_button", "success", "error", "warning", "info",
              "caption", "markdown", "write", "code", "divider", "empty",
              "spinner", "rerun", "header", "subheader"):
    if not hasattr(st, _attr):
        from unittest.mock import MagicMock
        setattr(st, _attr, MagicMock(return_value=MagicMock()))
