"""
Smoke tests for the validation-set creation and management flow in
_render_bash_validation() (ui/config_tab.py, ~line 1850).

The key navigation pattern (understanding required to read these tests):

  * The selectbox that selects the active validation set uses
    key="bash_val_active_set_idx".  Real Streamlit ignores the index= kwarg
    when a session-state key already exists; it reads the widget value from
    session state.  So the code writes the clamped index directly to
    st.session_state["bash_val_active_set_idx"] before calling selectbox —
    and that is what we assert on, NOT the selectbox index= kwarg.

  * _bash_val_nav_to is a one-shot navigation intent set by Add/Delete so
    that the next render picks up the clamped index before the selectbox is
    instantiated.

Coverage:
  1. _clamped_idx computation — int idx, string-of-int idx, out-of-range idx
  2. _bash_val_nav_to pop mechanism — key is consumed, navigation lands correctly
  3. Adding sets — list grows, new set has correct default structure
  4. Deleting sets — list shrinks, active index clamps on next render
  5. Edge cases — add when no sets exist (0→1), delete the last set
"""
import copy
import pytest
from unittest.mock import MagicMock, patch
import streamlit as st


# ---------------------------------------------------------------------------
# Shared fixture: minimal project dict that _flush_bash_config / _push_undo
# can write into without raising.
# ---------------------------------------------------------------------------

@pytest.fixture
def project():
    return {
        "id": "test-proj",
        "name": "Test Project",
        "config": {},
    }


# ---------------------------------------------------------------------------
# Session state builder
# ---------------------------------------------------------------------------

def _make_state(overrides: dict | None = None) -> dict:
    """Return a plain dict seeded with all keys _render_bash_validation needs."""
    base = {
        "bash_validation_sets": [],
        "bash_val_editor_nonce": 0,
        "_step_id_counter": 0,
        "_undo_stack": [],
        # Keys consumed by _flush_bash_config
        "bash_execution_target": "local",
        "bash_ssh_host": "",
        "bash_ssh_port": 22,
        "bash_ssh_user": "root",
        "bash_ssh_password": "",
        "bash_ssh_key_path": "",
        "bash_startup_commands": [],
        "bash_timeout": 60,
        "bash_completion_commands": [],
        "bash_validation_commands": [],
        "bash_fail_patterns": [],
        "bash_metrics_matrix": [],
        "bash_sudo": False,
    }
    if overrides:
        base.update(overrides)
    return base


class _MockSessionState(dict):
    """dict subclass with attribute access and setdefault that works like Streamlit's."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    def __setattr__(self, key, value):
        self[key] = value

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]


# ---------------------------------------------------------------------------
# Mock harness helpers
# ---------------------------------------------------------------------------

def _make_columns_side_effect():
    """Return a side_effect for st.columns that unpacks into N context-manager mocks."""
    def _columns(spec, *args, **kwargs):
        n = spec if isinstance(spec, int) else len(spec)
        cols = [MagicMock(__enter__=lambda s: s, __exit__=lambda *a: None)
                for _ in range(n)]
        return cols
    return _columns


def _make_container():
    c = MagicMock()
    c.__enter__ = lambda s: s
    c.__exit__ = lambda *a: None
    return c


def _render(state_dict: dict, project: dict, *,
            add_pressed: bool = False,
            delete_pressed: bool = False,
            sel_idx: int = 0,
            nav_to=None):
    """
    Drive _render_bash_validation with a fully mocked Streamlit environment.

    Parameters
    ----------
    state_dict   : The session state dict (mutated in-place during the render).
    project      : Minimal project dict.
    add_pressed  : Whether the "Add Validation Set" button should return True.
    delete_pressed: Whether the "Delete active set" button should return True.
    sel_idx      : Value that st.selectbox returns (simulates which set is active).
    nav_to       : If not None, seeds _bash_val_nav_to before the render.

    Returns
    -------
    The _MockSessionState after the render completes (or after st.rerun() fires).
    A StopIteration is raised by our st.rerun stub and caught here so callers
    always get the final state regardless of whether a rerun occurred.
    """
    from ui.config_tab import _render_bash_validation

    if nav_to is not None:
        state_dict["_bash_val_nav_to"] = nav_to

    mock_state = _MockSessionState(state_dict)

    def _button_factory(*args, key=None, **kwargs):
        if key == "btn_add_set_bash":
            return add_pressed
        if key == "btn_del_active_set_bash":
            return delete_pressed
        return False

    with patch.object(st, "session_state", mock_state), \
         patch.object(st, "container", return_value=_make_container()), \
         patch.object(st, "columns", side_effect=_make_columns_side_effect()), \
         patch.object(st, "button", side_effect=_button_factory), \
         patch.object(st, "selectbox", return_value=sel_idx), \
         patch("streamlit.data_editor", MagicMock(return_value=[]), create=True), \
         patch("streamlit.column_config", MagicMock(), create=True), \
         patch.object(st, "text_input", return_value=""), \
         patch.object(st, "rerun", side_effect=StopIteration("rerun")), \
         patch("ui.config_tab._push_undo"), \
         patch("ui.config_tab._flush_bash_config"), \
         patch("core.settings_store.save_settings"):
        try:
            _render_bash_validation(project)
        except StopIteration:
            pass  # st.rerun() is a halt; state is already written at this point

    return mock_state


# ---------------------------------------------------------------------------
# Convenience set factories
# ---------------------------------------------------------------------------

def _set(name: str, _id: int = 1) -> dict:
    return {"name": name, "description": "",
            "steps": [{"_id": _id, "delay_seconds": 0.0, "commands": []}]}


# ===========================================================================
# 1. _clamped_idx computation
#
# The clamped index is written to session_state["bash_val_active_set_idx"]
# unconditionally (line 1879 of config_tab.py) so we assert on that key.
# ===========================================================================

class TestClampedIdx:
    """Covers the bug fix: int(_stored_idx) tolerates string-typed values."""

    def _two_set_state(self):
        return _make_state({"bash_validation_sets": [_set("Alpha", 1), _set("Beta", 2)]})

    def test_int_idx_within_range(self, project):
        """Integer 1 with two sets → active_set_idx written as 1."""
        state = _render(self._two_set_state(), project, nav_to=1)
        assert state["bash_val_active_set_idx"] == 1

    def test_string_int_idx_within_range(self, project):
        """String '1' is coerced to int 1 — this was the actual bug fix."""
        state = _render(self._two_set_state(), project, nav_to="1")
        assert state["bash_val_active_set_idx"] == 1

    def test_string_idx_needs_clamping(self, project):
        """String '5' with only 2 sets clamps to 1 (len-1)."""
        state = _render(self._two_set_state(), project, nav_to="5")
        assert state["bash_val_active_set_idx"] == 1

    def test_int_idx_needs_clamping(self, project):
        """Integer 5 with only 2 sets clamps to 1."""
        state = _render(self._two_set_state(), project, nav_to=5)
        assert state["bash_val_active_set_idx"] == 1

    def test_zero_is_always_valid(self, project):
        """Index 0 is within range; must not be clamped below 0."""
        state = _render(self._two_set_state(), project, nav_to=0)
        assert state["bash_val_active_set_idx"] == 0

    def test_max_guards_against_negative(self, project):
        """Out-of-range negative string clamps to 0 via max(0, ...)."""
        # max(0, min(int('-3'), 1)) = max(0, -3) = 0
        state = _render(self._two_set_state(), project, nav_to="-3")
        assert state["bash_val_active_set_idx"] == 0


# ===========================================================================
# 2. _bash_val_nav_to pop mechanism
# ===========================================================================

class TestNavToPop:
    """After _bash_val_nav_to is consumed, the key must be absent from state."""

    def _one_set_state(self):
        return _make_state({"bash_validation_sets": [_set("SetA")]})

    def test_nav_to_key_removed_after_render(self, project):
        """_bash_val_nav_to is popped during the render and gone afterward."""
        state = _render(self._one_set_state(), project, nav_to=0)
        assert "_bash_val_nav_to" not in state

    def test_nav_to_consumed_sets_correct_active_idx(self, project):
        """The value popped from _bash_val_nav_to determines bash_val_active_set_idx."""
        two_sets = _make_state({"bash_validation_sets": [_set("A"), _set("B", 2)]})
        state = _render(two_sets, project, nav_to=1)
        assert state["bash_val_active_set_idx"] == 1

    def test_nav_to_absent_falls_back_to_active_set_idx(self, project):
        """Without _bash_val_nav_to, bash_val_active_set_idx is the fallback."""
        two_sets = _make_state({
            "bash_validation_sets": [_set("A"), _set("B", 2)],
            "bash_val_active_set_idx": 1,
        })
        # nav_to=None → no key seeded
        state = _render(two_sets, project)
        assert state["bash_val_active_set_idx"] == 1


# ===========================================================================
# 3. Adding sets
# ===========================================================================

class TestAddSet:
    """Verify add-set action populates state correctly."""

    def test_add_to_empty_list_grows_to_one(self, project):
        """Adding to an empty list creates exactly one set (0→1)."""
        state = _render(_make_state(), project, add_pressed=True)
        assert len(state["bash_validation_sets"]) == 1

    def test_new_set_has_required_keys(self, project):
        """New set dict must have name, description, and steps keys."""
        state = _render(_make_state(), project, add_pressed=True)
        new_set = state["bash_validation_sets"][0]
        assert "name" in new_set
        assert "description" in new_set
        assert "steps" in new_set

    def test_new_set_description_is_empty_string(self, project):
        """New set description defaults to empty string."""
        state = _render(_make_state(), project, add_pressed=True)
        assert state["bash_validation_sets"][0]["description"] == ""

    def test_new_set_has_one_step(self, project):
        """New set contains exactly one step."""
        state = _render(_make_state(), project, add_pressed=True)
        steps = state["bash_validation_sets"][0]["steps"]
        assert isinstance(steps, list)
        assert len(steps) == 1

    def test_new_step_has_empty_commands(self, project):
        """The initial step has an empty commands list."""
        state = _render(_make_state(), project, add_pressed=True)
        step = state["bash_validation_sets"][0]["steps"][0]
        assert step.get("commands") == []

    def test_add_sets_nav_to_new_index(self, project):
        """After adding, _bash_val_nav_to is set to the new set's index."""
        state = _render(_make_state(), project, add_pressed=True)
        # List was empty before add → new set is at index 0
        assert state.get("_bash_val_nav_to") == 0

    def test_add_second_set_nav_to_is_one(self, project):
        """Adding a second set sets _bash_val_nav_to to 1."""
        initial = _make_state({"bash_validation_sets": [_set("Set 1")]})
        state = _render(initial, project, add_pressed=True)
        assert len(state["bash_validation_sets"]) == 2
        assert state.get("_bash_val_nav_to") == 1

    def test_nonce_incremented_on_add(self, project):
        """The editor nonce is bumped after an add so widgets re-seed."""
        initial = _make_state({"bash_val_editor_nonce": 3})
        state = _render(initial, project, add_pressed=True)
        assert state["bash_val_editor_nonce"] == 4


# ===========================================================================
# 4. Deleting sets
# ===========================================================================

class TestDeleteSet:
    """Verify delete removes the correct set and the next render clamps."""

    def _two_set_state(self):
        return _make_state({"bash_validation_sets": [_set("Alpha", 1), _set("Beta", 2)]})

    def test_delete_shrinks_list_by_one(self, project):
        """Deleting one set from a two-set list leaves one set."""
        state = _render(self._two_set_state(), project,
                        delete_pressed=True, sel_idx=0)
        assert len(state["bash_validation_sets"]) == 1

    def test_delete_removes_correct_set_at_index_zero(self, project):
        """Deleting set at index 0 leaves only 'Beta'."""
        state = _render(self._two_set_state(), project,
                        delete_pressed=True, sel_idx=0)
        assert state["bash_validation_sets"][0]["name"] == "Beta"

    def test_delete_removes_correct_set_at_index_one(self, project):
        """Deleting set at index 1 leaves only 'Alpha'."""
        state = _render(self._two_set_state(), project,
                        delete_pressed=True, sel_idx=1)
        assert state["bash_validation_sets"][0]["name"] == "Alpha"

    def test_nonce_incremented_on_delete(self, project):
        """The editor nonce is bumped after a delete."""
        initial = _make_state({
            "bash_validation_sets": [_set("Alpha", 1), _set("Beta", 2)],
            "bash_val_editor_nonce": 7,
        })
        state = _render(initial, project, delete_pressed=True, sel_idx=0)
        assert state["bash_val_editor_nonce"] == 8

    def test_clamp_on_next_render_after_delete(self, project):
        """
        After deleting the last-index set, the next render clamps
        bash_val_active_set_idx to len(remaining_sets) - 1.

        Delete index 1 from [Alpha, Beta] → [Alpha].
        Stale session idx is 1. Next render clamps to 0.
        """
        # Render 1: delete Beta (index 1)
        state = _render(self._two_set_state(), project,
                        delete_pressed=True, sel_idx=1)
        assert len(state["bash_validation_sets"]) == 1

        # Simulate the stale widget value (Streamlit would leave it at 1
        # until the next full rerun clears it).
        state["bash_val_active_set_idx"] = 1

        # Render 2: no button pressed; clamping fires
        state2 = _render(dict(state), project)
        assert state2["bash_val_active_set_idx"] == 0


# ===========================================================================
# 5. Edge cases
# ===========================================================================

class TestEdgeCases:
    """Zero-to-one transition, deleting the only set, no-crash guarantees."""

    def test_add_when_no_sets_exist(self, project):
        """Starting from zero sets and adding one produces exactly one set."""
        state = _render(_make_state({"bash_validation_sets": []}), project,
                        add_pressed=True)
        assert len(state["bash_validation_sets"]) == 1

    def test_delete_only_remaining_set(self, project):
        """Deleting the one and only set leaves an empty list."""
        initial = _make_state({"bash_validation_sets": [_set("Lone")]})
        state = _render(initial, project, delete_pressed=True, sel_idx=0)
        assert state["bash_validation_sets"] == []

    def test_render_with_empty_sets_does_not_crash(self, project):
        """_render_bash_validation with no sets must not raise."""
        # If this completes without exception the test passes.
        _render(_make_state({"bash_validation_sets": []}), project)

    def test_string_zero_nav_to_is_coerced_to_int_zero(self, project):
        """'0' as _bash_val_nav_to with one set → active_set_idx == 0."""
        initial = _make_state({"bash_validation_sets": [_set("Only")]})
        state = _render(initial, project, nav_to="0")
        assert state["bash_val_active_set_idx"] == 0

    def test_add_then_render_consumes_nav_to(self, project):
        """
        The nav_to key set during add is consumed on the very next render,
        and the resulting active index matches the new set's position.
        """
        # Phase 1: add a set
        state_after_add = _render(_make_state(), project, add_pressed=True)
        assert "_bash_val_nav_to" in state_after_add, \
            "_bash_val_nav_to must be set by the add action"

        pending = state_after_add["_bash_val_nav_to"]

        # Phase 2: next render consumes the nav_to key
        state_after_render = _render(dict(state_after_add), project)
        assert "_bash_val_nav_to" not in state_after_render, \
            "_bash_val_nav_to must be consumed on the next render"
        assert state_after_render["bash_val_active_set_idx"] == pending

    def test_clamped_idx_written_unconditionally(self, project):
        """
        Even when the stored index is already in range, bash_val_active_set_idx
        is always written (no conditional guard).  This is needed because real
        Streamlit ignores index= when the key exists.
        """
        initial = _make_state({
            "bash_validation_sets": [_set("A"), _set("B", 2)],
            "bash_val_active_set_idx": 99,  # stale / absent from session_state normally
        })
        state = _render(initial, project, nav_to=1)
        # Must be clamped to 1 (not left as 99)
        assert state["bash_val_active_set_idx"] == 1
