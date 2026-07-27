"""Shared, stateless optional-parameter card renderer.

Used by Llama-Server-Bot and CAF + llama.cpp's advanced llama-server runtime
sections — identical three-column card layout and enabled/disabled visual
treatment (see the `advcard_` rule in ui/styles.py), independent per-plugin
state-key namespaces. Each caller passes its own `state_prefix`, so no
plugin's session keys, cache, or serialized config fields are touched by
another's.

Streamlit is imported locally inside each function, not at module level:
tests/conftest.py installs a bare streamlit stub before collection, and
whichever module binds `import streamlit as st` first keeps that binding
for the rest of the process — a module-level import here would freeze
onto the stub even in tests that later swap in the real package.
"""
from __future__ import annotations


def render_optional_param_card(
    col,
    *,
    state_prefix: str,
    label: str,
    key_suffix: str,
    min_v: float,
    max_v: float,
    step: float,
    help_text: str,
    is_float: bool = False,
    value_key_suffix: str | None = None,
    default_value: float | int | None = None,
) -> None:
    """Render one optional numeric parameter as a bordered card: an enable
    checkbox plus its numeric input. Reads/writes only
    `{state_prefix}_en_{key_suffix}` and
    `{state_prefix}_{value_key_suffix or key_suffix}` — the same two state
    keys the old detached checkbox/input grid used, so every existing
    value, default, and serialized config field is preserved.
    """
    import streamlit as st

    en_key = f"{state_prefix}_en_{key_suffix}"
    value_key = f"{state_prefix}_{value_key_suffix or key_suffix}"
    st.session_state.setdefault(en_key, False)
    if default_value is not None:
        st.session_state.setdefault(value_key, default_value)

    with col:
        with st.container(border=True, key=f"advcard_{state_prefix}_{key_suffix}"):
            c1, c2 = st.columns([0.22, 0.78], gap="small")
            with c1:
                st.write("")
                enabled = st.checkbox(
                    f"en_{key_suffix}", key=en_key, label_visibility="collapsed",
                    help=f"Enable {label}",
                )
            with c2:
                st.number_input(
                    label,
                    min_value=float(min_v) if is_float else int(min_v),
                    max_value=float(max_v) if is_float else int(max_v),
                    step=float(step) if is_float else int(step),
                    key=value_key,
                    disabled=not enabled,
                    help=help_text,
                    format="%.2f" if is_float else None,
                )


def render_flag_card(col, *, key: str, label: str, help_text: str | None = None) -> None:
    """Render a standalone boolean flag (e.g. Flash Attention) in the same
    card pattern as render_optional_param_card, for grid consistency."""
    import streamlit as st

    st.session_state.setdefault(key, False)
    with col:
        with st.container(border=True, key=f"advcard_{key}"):
            st.checkbox(label, key=key, help=help_text)
