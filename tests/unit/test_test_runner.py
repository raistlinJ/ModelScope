"""
Unit tests for core/test_runner.py

Tests cover: _parse(), TestItem properties, TestRunResult aggregation,
and CATEGORY_*/CATEGORY_LABELS constants.
"""
import pytest
from core.test_runner import (
    _parse,
    TestItem,
    TestRunResult,
    CATEGORY_ORDER,
    CATEGORY_LABELS,
    CATEGORY_COLOURS,
)


# ── _parse() ──────────────────────────────────────────────────────────────────

class TestParse:
    def test_empty_output_returns_empty_list(self):
        assert _parse("") == []

    def test_whitespace_only_returns_empty(self):
        assert _parse("   \n  \n") == []

    def test_single_passed_test(self):
        raw = "tests/unit/test_foo.py::test_something PASSED [ 10%]"
        items = _parse(raw)
        assert len(items) == 1
        assert items[0].status == "PASSED"
        assert items[0].module == "tests/unit/test_foo.py"
        assert items[0].name == "test_something"
        assert items[0].class_ == ""

    def test_single_failed_test(self):
        raw = "tests/unit/test_foo.py::test_bad FAILED [ 10%]"
        items = _parse(raw)
        assert len(items) == 1
        assert items[0].status == "FAILED"

    def test_error_status(self):
        raw = "tests/unit/test_foo.py::test_broken ERROR [ 10%]"
        items = _parse(raw)
        assert items[0].status == "ERROR"

    def test_skipped_status(self):
        raw = "tests/unit/test_foo.py::test_skip SKIPPED [ 10%]"
        items = _parse(raw)
        assert items[0].status == "SKIPPED"

    def test_xfailed_status(self):
        raw = "tests/unit/test_foo.py::test_xf XFAILED [ 10%]"
        items = _parse(raw)
        assert items[0].status == "XFAILED"

    def test_xpassed_status(self):
        raw = "tests/unit/test_foo.py::test_xp XPASSED [ 10%]"
        items = _parse(raw)
        assert items[0].status == "XPASSED"

    def test_class_based_test_parsed(self):
        raw = "tests/unit/test_foo.py::MyClass::test_method PASSED [ 50%]"
        items = _parse(raw)
        assert len(items) == 1
        assert items[0].class_ == "MyClass"
        assert items[0].name == "test_method"
        assert items[0].module == "tests/unit/test_foo.py"

    def test_parametrized_test_with_spaces(self):
        raw = "tests/unit/test_foo.py::test_block[;rm -rf /] PASSED [ 90%]"
        items = _parse(raw)
        assert len(items) == 1
        assert "[;rm -rf /]" in items[0].node_id
        assert items[0].status == "PASSED"

    def test_parametrized_test_brackets_no_spaces(self):
        raw = "tests/unit/test_foo.py::test_x[arg1] PASSED [ 50%]"
        items = _parse(raw)
        assert len(items) == 1
        assert "[arg1]" in items[0].node_id

    def test_duration_parsed_from_trailing_seconds(self):
        raw = "tests/unit/test_foo.py::test_fast PASSED [ 50%]   0.12s"
        items = _parse(raw)
        assert items[0].duration_ms == pytest.approx(120.0)

    def test_duration_zero_when_absent(self):
        raw = "tests/unit/test_foo.py::test_fast PASSED [ 50%]"
        items = _parse(raw)
        assert items[0].duration_ms == 0.0

    def test_error_summary_extracted_from_fail_line(self):
        raw = (
            "tests/unit/test_foo.py::test_bad FAILED [ 10%]\n"
            "FAILED tests/unit/test_foo.py::test_bad - AssertionError: expected True"
        )
        items = _parse(raw)
        assert "AssertionError" in items[0].error_summary
        assert "expected True" in items[0].error_summary

    def test_error_summary_absent_for_passing_test(self):
        raw = "tests/unit/test_foo.py::test_ok PASSED [ 50%]"
        items = _parse(raw)
        assert items[0].error_summary == ""

    def test_multiple_tests_parsed(self):
        raw = (
            "tests/unit/test_foo.py::test_a PASSED [ 50%]\n"
            "tests/unit/test_foo.py::test_b FAILED [ 100%]"
        )
        items = _parse(raw)
        assert len(items) == 2
        assert items[0].status == "PASSED"
        assert items[1].status == "FAILED"

    def test_unrelated_lines_ignored(self):
        raw = (
            "collected 5 items\n"
            "============================\n"
            "tests/unit/test_foo.py::test_ok PASSED [ 50%]\n"
            "short test summary info\n"
            "5 passed in 0.5s\n"
        )
        items = _parse(raw)
        assert len(items) == 1

    def test_node_id_preserved_exactly(self):
        raw = "tests/unit/test_foo.py::MyClass::test_method PASSED [ 50%]"
        items = _parse(raw)
        assert items[0].node_id == "tests/unit/test_foo.py::MyClass::test_method"


# ── TestItem.category ─────────────────────────────────────────────────────────

class TestItemCategory:
    def _item(self, module: str) -> TestItem:
        return TestItem(
            node_id=module + "::test_x",
            module=module, class_="", name="test_x", status="PASSED",
        )

    def test_unit_category(self):
        assert self._item("tests/unit/test_foo.py").category == "unit"

    def test_functional_category(self):
        assert self._item("tests/functional/test_bar.py").category == "functional"

    def test_smoke_category(self):
        assert self._item("tests/smoke/test_baz.py").category == "smoke"

    def test_regression_via_filename(self):
        assert self._item("tests/unit/test_regression.py").category == "regression"

    def test_regression_wins_over_unit_in_path(self):
        assert self._item("tests/regression/test_foo.py").category == "regression"

    def test_other_for_top_level_test(self):
        assert self._item("tests/test_mystery.py").category == "other"

    def test_other_for_unknown_path(self):
        assert self._item("test_no_prefix.py").category == "other"


# ── TestItem properties ───────────────────────────────────────────────────────

class TestItemProperties:
    def _item(self, node_id: str, status: str = "PASSED", **kw) -> TestItem:
        parts = node_id.split("::")
        return TestItem(
            node_id=node_id, module=parts[0], class_="",
            name=parts[-1], status=status, **kw,
        )

    def test_passed_is_true_for_passed(self):
        assert self._item("tests/unit/t.py::t", "PASSED").passed is True

    def test_passed_is_false_for_failed(self):
        assert self._item("tests/unit/t.py::t", "FAILED").passed is False

    def test_passed_is_false_for_error(self):
        assert self._item("tests/unit/t.py::t", "ERROR").passed is False

    def test_passed_is_none_for_skipped(self):
        assert self._item("tests/unit/t.py::t", "SKIPPED").passed is None

    def test_passed_is_none_for_xfailed(self):
        assert self._item("tests/unit/t.py::t", "XFAILED").passed is None

    def test_short_name_class_test(self):
        item = TestItem(
            node_id="tests/unit/t.py::MyClass::test_method",
            module="tests/unit/t.py", class_="MyClass",
            name="test_method", status="PASSED",
        )
        assert item.short_name == "test_method"

    def test_short_name_plain_test(self):
        item = TestItem(
            node_id="tests/unit/t.py::test_plain",
            module="tests/unit/t.py", class_="",
            name="test_plain", status="PASSED",
        )
        assert item.short_name == "test_plain"

    def test_short_name_parametrized(self):
        item = TestItem(
            node_id="tests/unit/t.py::test_block[;rm -rf /]",
            module="tests/unit/t.py", class_="",
            name="test_block[;rm -rf /]", status="PASSED",
        )
        assert item.short_name == "test_block[;rm -rf /]"

    def test_display_path_strips_tests_prefix(self):
        item = TestItem(
            node_id="tests/unit/t.py::t",
            module="tests/unit/t.py", class_="", name="t", status="PASSED",
        )
        assert item.display_path == "unit/t.py"

    def test_display_path_no_prefix_unchanged(self):
        item = TestItem(
            node_id="other/t.py::t",
            module="other/t.py", class_="", name="t", status="PASSED",
        )
        assert item.display_path == "other/t.py"


# ── TestRunResult aggregation ─────────────────────────────────────────────────

class TestRunResultAggregation:
    def _result(self, statuses: list[str]) -> TestRunResult:
        items = [
            TestItem(
                node_id=f"tests/unit/t.py::test_{i}",
                module="tests/unit/t.py", class_="", name=f"test_{i}",
                status=s,
            )
            for i, s in enumerate(statuses)
        ]
        return TestRunResult(items=items)

    def test_passed_count(self):
        r = self._result(["PASSED", "PASSED", "FAILED"])
        assert r.passed == 2

    def test_failed_counts_error_too(self):
        r = self._result(["PASSED", "FAILED", "ERROR"])
        assert r.failed == 2

    def test_skipped_counts_xfailed_and_xpassed(self):
        r = self._result(["PASSED", "SKIPPED", "XFAILED", "XPASSED"])
        assert r.skipped == 3

    def test_total_count(self):
        r = self._result(["PASSED", "FAILED", "SKIPPED"])
        assert r.total == 3

    def test_total_empty(self):
        r = TestRunResult(items=[])
        assert r.total == 0

    def test_pass_pct_all_pass(self):
        r = self._result(["PASSED", "PASSED"])
        assert r.pass_pct == 100.0

    def test_pass_pct_no_division_by_zero_when_empty(self):
        r = TestRunResult(items=[])
        assert r.pass_pct == 0.0

    def test_pass_pct_half(self):
        r = self._result(["PASSED", "FAILED"])
        assert r.pass_pct == 50.0

    def test_pass_pct_all_failed(self):
        r = self._result(["FAILED", "FAILED"])
        assert r.pass_pct == 0.0

    def test_by_category_groups_correctly(self):
        items = [
            TestItem(node_id="tests/unit/t.py::a", module="tests/unit/t.py",
                     class_="", name="a", status="PASSED"),
            TestItem(node_id="tests/smoke/t.py::b", module="tests/smoke/t.py",
                     class_="", name="b", status="PASSED"),
            TestItem(node_id="tests/unit/t.py::c", module="tests/unit/t.py",
                     class_="", name="c", status="FAILED"),
        ]
        r = TestRunResult(items=items)
        cats = r.by_category()
        assert len(cats["unit"]) == 2
        assert len(cats["smoke"]) == 1

    def test_by_module_groups_correctly(self):
        items = [
            TestItem(node_id="tests/unit/a.py::t1", module="tests/unit/a.py",
                     class_="", name="t1", status="PASSED"),
            TestItem(node_id="tests/unit/a.py::t2", module="tests/unit/a.py",
                     class_="", name="t2", status="FAILED"),
            TestItem(node_id="tests/unit/b.py::t3", module="tests/unit/b.py",
                     class_="", name="t3", status="PASSED"),
        ]
        r = TestRunResult(items=items)
        mods = r.by_module()
        assert "tests/unit/a.py" in mods
        assert len(mods["tests/unit/a.py"]) == 2
        assert "tests/unit/b.py" in mods
        assert len(mods["tests/unit/b.py"]) == 1

    def test_by_module_with_category_filter(self):
        items = [
            TestItem(node_id="tests/unit/t.py::a", module="tests/unit/t.py",
                     class_="", name="a", status="PASSED"),
            TestItem(node_id="tests/smoke/t.py::b", module="tests/smoke/t.py",
                     class_="", name="b", status="PASSED"),
        ]
        r = TestRunResult(items=items)
        unit_mods = r.by_module(category="unit")
        assert "tests/unit/t.py" in unit_mods
        assert "tests/smoke/t.py" not in unit_mods

    def test_error_msg_non_empty_on_failure(self):
        r = TestRunResult(error_msg="pytest timed out after 120s")
        assert bool(r.error_msg)


# ── Constants ─────────────────────────────────────────────────────────────────

class TestConstants:
    def test_category_order_contains_required_keys(self):
        for key in ("regression", "smoke", "unit", "functional"):
            assert key in CATEGORY_ORDER

    def test_category_labels_has_entry_for_each_order(self):
        for key in CATEGORY_ORDER:
            assert key in CATEGORY_LABELS

    def test_category_colours_has_entry_for_each_order(self):
        for key in CATEGORY_ORDER:
            assert key in CATEGORY_COLOURS

    def test_category_colours_are_valid_hex(self):
        import re
        hex_re = re.compile(r"^#[0-9a-fA-F]{6}$")
        for key, colour in CATEGORY_COLOURS.items():
            assert hex_re.match(colour), f"Invalid colour for {key!r}: {colour!r}"
