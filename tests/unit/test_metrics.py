"""
Unit tests for config.metrics — evaluate_metric (all 17 types),
format_criterion, and make_metric.
"""
import pytest
from config.metrics import make_metric, evaluate_metric, format_criterion, METRIC_TYPES, CATEGORIES


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _tel(**overrides) -> dict:
    """Build a minimal telemetry dict, with optional field overrides."""
    base = {
        "validation_passed":   True,
        "total_latency":       1.5,
        "total_tokens":        100,
        "prompt_tokens":       60,
        "completion_tokens":   40,
        "tokens_per_second":   20.0,
        "llm_rounds":          2,
        "tool_calls":          [],
        "inefficiencies":      [],
        "llm_response":        "Task complete.",
        "run_aborted":         False,
    }
    base.update(overrides)
    return base


def _tc(tool: str, args: dict = None, exit_code: int = 0, result=None) -> dict:
    """Build a tool-call entry for the telemetry tool_calls list."""
    return {
        "tool":      tool,
        "args":      args or {},
        "exit_code": exit_code,
        "result":    result or {"status": "success"},
    }


# ── make_metric ────────────────────────────────────────────────────────────────

class TestMakeMetric:
    def test_fields_present(self):
        m = make_metric("x1", "My Metric", "tool_call_count", max_calls=3)
        assert m["id"] == "x1"
        assert m["name"] == "My Metric"
        assert m["type"] == "tool_call_count"
        assert m["enabled"] is True
        assert m["params"]["max_calls"] == 3

    def test_disabled_metric(self):
        m = make_metric("x2", "Disabled", "latency", enabled=False, max_seconds=10.0)
        assert m["enabled"] is False


# ── evaluate_metric — task_completion ─────────────────────────────────────────

class TestTaskCompletion:
    M = make_metric("m", "TC", "task_completion")

    def test_pass(self):
        assert evaluate_metric(self.M, _tel(validation_passed=True)) is True

    def test_fail(self):
        assert evaluate_metric(self.M, _tel(validation_passed=False)) is False

    def test_none(self):
        assert evaluate_metric(self.M, _tel(validation_passed=None)) is None


# ── evaluate_metric — tool_called ─────────────────────────────────────────────

class TestToolCalled:
    def test_present(self):
        m = make_metric("m", "TC", "tool_called", tool_name="file_creator")
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(m, tel) is True

    def test_absent(self):
        m = make_metric("m", "TC", "tool_called", tool_name="file_creator")
        assert evaluate_metric(m, _tel()) is False

    def test_empty_tool_name_returns_none(self):
        m = make_metric("m", "TC", "tool_called", tool_name="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — tool_not_called ─────────────────────────────────────────

class TestToolNotCalled:
    def test_absent_passes(self):
        m = make_metric("m", "TNC", "tool_not_called", tool_name="run_nmap_scan")
        assert evaluate_metric(m, _tel()) is True

    def test_present_fails(self):
        m = make_metric("m", "TNC", "tool_not_called", tool_name="run_nmap_scan")
        tel = _tel(tool_calls=[_tc("run_nmap_scan")])
        assert evaluate_metric(m, tel) is False

    def test_empty_tool_name_returns_none(self):
        m = make_metric("m", "TNC", "tool_not_called", tool_name="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — tool_sequence ───────────────────────────────────────────

class TestToolSequence:
    def test_exact_subsequence(self):
        m = make_metric("m", "TS", "tool_sequence", sequence="a, b, c")
        tel = _tel(tool_calls=[_tc("a"), _tc("b"), _tc("c")])
        assert evaluate_metric(m, tel) is True

    def test_subsequence_with_extras(self):
        m = make_metric("m", "TS", "tool_sequence", sequence="a, c")
        tel = _tel(tool_calls=[_tc("a"), _tc("b"), _tc("c")])
        assert evaluate_metric(m, tel) is True

    def test_wrong_order_fails(self):
        m = make_metric("m", "TS", "tool_sequence", sequence="b, a")
        tel = _tel(tool_calls=[_tc("a"), _tc("b")])
        assert evaluate_metric(m, tel) is False

    def test_missing_tool_fails(self):
        m = make_metric("m", "TS", "tool_sequence", sequence="a, b, d")
        tel = _tel(tool_calls=[_tc("a"), _tc("b")])
        assert evaluate_metric(m, tel) is False

    def test_empty_sequence_returns_none(self):
        m = make_metric("m", "TS", "tool_sequence", sequence="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — tool_call_count ─────────────────────────────────────────

class TestToolCallCount:
    def test_under_limit(self):
        m = make_metric("m", "TCC", "tool_call_count", max_calls=5)
        tel = _tel(tool_calls=[_tc("x")] * 3)
        assert evaluate_metric(m, tel) is True

    def test_at_limit(self):
        m = make_metric("m", "TCC", "tool_call_count", max_calls=3)
        tel = _tel(tool_calls=[_tc("x")] * 3)
        assert evaluate_metric(m, tel) is True

    def test_over_limit(self):
        m = make_metric("m", "TCC", "tool_call_count", max_calls=2)
        tel = _tel(tool_calls=[_tc("x")] * 3)
        assert evaluate_metric(m, tel) is False

    def test_zero_calls(self):
        m = make_metric("m", "TCC", "tool_call_count", max_calls=5)
        assert evaluate_metric(m, _tel()) is True


# ── evaluate_metric — tool_success_rate ───────────────────────────────────────

class TestToolSuccessRate:
    def test_all_success(self):
        m = make_metric("m", "TSR", "tool_success_rate", min_rate=0.9)
        tel = _tel(tool_calls=[_tc("x", exit_code=0), _tc("x", exit_code=0)])
        assert evaluate_metric(m, tel) is True

    def test_partial_failure_below_threshold(self):
        m = make_metric("m", "TSR", "tool_success_rate", min_rate=0.9)
        tel = _tel(tool_calls=[_tc("x", exit_code=0), _tc("x", exit_code=1)])
        assert evaluate_metric(m, tel) is False  # 0.5 < 0.9

    def test_partial_failure_meets_threshold(self):
        m = make_metric("m", "TSR", "tool_success_rate", min_rate=0.5)
        tel = _tel(tool_calls=[_tc("x", exit_code=0), _tc("x", exit_code=1)])
        assert evaluate_metric(m, tel) is True  # 0.5 >= 0.5

    def test_no_calls_returns_none(self):
        m = make_metric("m", "TSR", "tool_success_rate", min_rate=0.9)
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — no_repeated_calls ───────────────────────────────────────

class TestNoRepeatedCalls:
    M = make_metric("m", "NRC", "no_repeated_calls")

    def test_clean(self):
        assert evaluate_metric(self.M, _tel(inefficiencies=[])) is True

    def test_with_repeated(self):
        tel = _tel(inefficiencies=["Repeated call: file_creator with identical arguments"])
        assert evaluate_metric(self.M, tel) is False


# ── evaluate_metric — tool_output_contains ────────────────────────────────────

class TestToolOutputContains:
    def test_found_case_insensitive(self):
        m = make_metric("m", "TOC", "tool_output_contains",
                        tool_name="file_creator", text="success")
        tel = _tel(tool_calls=[_tc("file_creator", result={"status": "SUCCESS"})])
        assert evaluate_metric(m, tel) is True

    def test_not_found(self):
        m = make_metric("m", "TOC", "tool_output_contains",
                        tool_name="file_creator", text="success")
        tel = _tel(tool_calls=[_tc("file_creator", result={"status": "error"})])
        assert evaluate_metric(m, tel) is False

    def test_no_matching_tool_fails(self):
        m = make_metric("m", "TOC", "tool_output_contains",
                        tool_name="file_creator", text="success")
        tel = _tel(tool_calls=[_tc("run_nmap_scan", result={"stdout": "success"})])
        assert evaluate_metric(m, tel) is False

    def test_empty_params_returns_none(self):
        m = make_metric("m", "TOC", "tool_output_contains", tool_name="", text="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — content_contains ───────────────────────────────────────

class TestContentContains:
    def test_present_case_insensitive(self):
        m = make_metric("m", "CC", "content_contains", text="hello")
        assert evaluate_metric(m, _tel(llm_response="Well, HELLO there!")) is True

    def test_absent(self):
        m = make_metric("m", "CC", "content_contains", text="missing")
        assert evaluate_metric(m, _tel(llm_response="Not here.")) is False

    def test_empty_text_returns_none(self):
        m = make_metric("m", "CC", "content_contains", text="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — content_not_contains ────────────────────────────────────

class TestContentNotContains:
    def test_absent_passes(self):
        m = make_metric("m", "CNC", "content_not_contains", text="forbidden")
        assert evaluate_metric(m, _tel(llm_response="All good.")) is True

    def test_present_fails(self):
        m = make_metric("m", "CNC", "content_not_contains", text="port")
        assert evaluate_metric(m, _tel(llm_response="Open port 22 detected.")) is False

    def test_empty_text_returns_none(self):
        m = make_metric("m", "CNC", "content_not_contains", text="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — content_regex ──────────────────────────────────────────

class TestContentRegex:
    def test_match(self):
        m = make_metric("m", "CR", "content_regex", pattern=r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
        assert evaluate_metric(m, _tel(llm_response="IP: 192.168.1.1")) is True

    def test_no_match(self):
        m = make_metric("m", "CR", "content_regex", pattern=r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
        assert evaluate_metric(m, _tel(llm_response="No IPs here.")) is False

    def test_invalid_regex_returns_none(self):
        m = make_metric("m", "CR", "content_regex", pattern="[invalid(")
        assert evaluate_metric(m, _tel()) is None

    def test_empty_pattern_returns_none(self):
        m = make_metric("m", "CR", "content_regex", pattern="")
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — latency ─────────────────────────────────────────────────

class TestLatency:
    def test_under_max(self):
        m = make_metric("m", "L", "latency", max_seconds=10.0)
        assert evaluate_metric(m, _tel(total_latency=5.0)) is True

    def test_over_max(self):
        m = make_metric("m", "L", "latency", max_seconds=3.0)
        assert evaluate_metric(m, _tel(total_latency=5.0)) is False

    def test_at_boundary_fails(self):
        m = make_metric("m", "L", "latency", max_seconds=5.0)
        assert evaluate_metric(m, _tel(total_latency=5.0)) is False  # strict <


# ── evaluate_metric — token_limit ────────────────────────────────────────────

class TestTokenLimit:
    def test_under_limit(self):
        m = make_metric("m", "TL", "token_limit", max_tokens=500)
        assert evaluate_metric(m, _tel(total_tokens=100)) is True

    def test_over_limit(self):
        m = make_metric("m", "TL", "token_limit", max_tokens=50)
        assert evaluate_metric(m, _tel(total_tokens=100)) is False


# ── evaluate_metric — max_iterations ─────────────────────────────────────────

class TestMaxIterations:
    def test_under_max(self):
        m = make_metric("m", "MI", "max_iterations", max_iter=5)
        assert evaluate_metric(m, _tel(llm_rounds=3)) is True

    def test_at_max(self):
        m = make_metric("m", "MI", "max_iterations", max_iter=3)
        assert evaluate_metric(m, _tel(llm_rounds=3)) is True

    def test_over_max(self):
        m = make_metric("m", "MI", "max_iterations", max_iter=2)
        assert evaluate_metric(m, _tel(llm_rounds=3)) is False


# ── evaluate_metric — tokens_per_second ──────────────────────────────────────

class TestTokensPerSecond:
    def test_above_min(self):
        m = make_metric("m", "TPS", "tokens_per_second", min_tps=10.0)
        assert evaluate_metric(m, _tel(tokens_per_second=25.0)) is True

    def test_below_min(self):
        m = make_metric("m", "TPS", "tokens_per_second", min_tps=10.0)
        assert evaluate_metric(m, _tel(tokens_per_second=5.0)) is False

    def test_zero_returns_none(self):
        m = make_metric("m", "TPS", "tokens_per_second", min_tps=5.0)
        assert evaluate_metric(m, _tel(tokens_per_second=0)) is None


# ── evaluate_metric — path_efficiency ────────────────────────────────────────

class TestPathEfficiency:
    def test_exact_match(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="file_creator", allow_extra_steps=0,
                        penalize_backtracking=True)
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(m, tel) is True

    def test_extra_step_within_allowance(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="file_creator", allow_extra_steps=1,
                        penalize_backtracking=False)
        tel = _tel(tool_calls=[_tc("file_creator"), _tc("verify")])
        assert evaluate_metric(m, tel) is True

    def test_extra_step_exceeds_allowance(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="file_creator", allow_extra_steps=0,
                        penalize_backtracking=False)
        tel = _tel(tool_calls=[_tc("file_creator"), _tc("extra"), _tc("more")])
        assert evaluate_metric(m, tel) is False

    def test_backtracking_penalized(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="a, b", allow_extra_steps=5,
                        penalize_backtracking=True)
        # a → b → a = backtrack on 'a'
        tel = _tel(tool_calls=[_tc("a"), _tc("b"), _tc("a")])
        assert evaluate_metric(m, tel) is False

    def test_backtracking_not_penalized(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="a, b", allow_extra_steps=5,
                        penalize_backtracking=False)
        tel = _tel(tool_calls=[_tc("a"), _tc("b"), _tc("a")])
        assert evaluate_metric(m, tel) is True

    def test_empty_sequence_returns_none(self):
        m = make_metric("m", "PE", "path_efficiency",
                        expected_sequence="", allow_extra_steps=0,
                        penalize_backtracking=True)
        assert evaluate_metric(m, _tel()) is None


# ── evaluate_metric — goal_achievement ───────────────────────────────────────

class TestGoalAchievement:
    M = make_metric("m", "GA", "goal_achievement")

    def test_all_conditions_met(self):
        tel = _tel(
            validation_passed=True,
            inefficiencies=[],
            tool_calls=[_tc("file_creator", exit_code=0)],
        )
        assert evaluate_metric(self.M, tel) is True

    def test_validation_failed(self):
        tel = _tel(validation_passed=False, inefficiencies=[])
        assert evaluate_metric(self.M, tel) is False

    def test_with_inefficiencies(self):
        tel = _tel(validation_passed=True,
                   inefficiencies=["Repeated call"])
        assert evaluate_metric(self.M, tel) is False

    def test_tool_error(self):
        tel = _tel(
            validation_passed=True,
            inefficiencies=[],
            tool_calls=[_tc("file_creator", exit_code=1)],
        )
        assert evaluate_metric(self.M, tel) is False

    def test_no_validation_result_returns_none(self):
        tel = _tel(validation_passed=None)
        assert evaluate_metric(self.M, tel) is None


# ── evaluate_metric — tool_usage_efficiency ───────────────────────────────────

class TestToolUsageEfficiency:
    def test_pass(self):
        m = make_metric("m", "TUE", "tool_usage_efficiency", max_calls=5)
        tel = _tel(tool_calls=[_tc("x")], inefficiencies=[])
        assert evaluate_metric(m, tel) is True

    def test_too_many_calls(self):
        m = make_metric("m", "TUE", "tool_usage_efficiency", max_calls=2)
        tel = _tel(tool_calls=[_tc("x")] * 3, inefficiencies=[])
        assert evaluate_metric(m, tel) is False

    def test_with_inefficiencies(self):
        m = make_metric("m", "TUE", "tool_usage_efficiency", max_calls=5)
        tel = _tel(tool_calls=[_tc("x")],
                   inefficiencies=["Repeated call: x with identical arguments"])
        assert evaluate_metric(m, tel) is False


# ── evaluate_metric — no_error_output ─────────────────────────────────────────

class TestNoErrorOutput:
    M = make_metric("m", "NEO", "no_error_output")

    def test_clean_output(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0,
                                   result={"status": "success", "bytes_written": 10})])
        assert evaluate_metric(self.M, tel) is True

    def test_error_string_at_exit_zero_fails(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0,
                                   result={"status": "error: permission denied"})])
        assert evaluate_metric(self.M, tel) is False

    def test_error_string_at_nonzero_exit_passes(self):
        # Only checks exit_code == 0 entries — non-zero exits are not double-counted
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=1,
                                   result={"status": "error"})])
        assert evaluate_metric(self.M, tel) is True

    def test_no_calls_passes(self):
        assert evaluate_metric(self.M, _tel()) is True


# ── format_criterion ──────────────────────────────────────────────────────────

class TestFormatCriterion:
    """Each metric type must produce a non-empty human-readable string."""

    @pytest.mark.parametrize("type_key,params,expected_fragment", [
        ("task_completion",     {},                              "exit"),
        ("tool_called",         {"tool_name": "file_creator"},  "file_creator"),
        ("tool_not_called",     {"tool_name": "run_nmap_scan"}, "NOT"),
        ("tool_sequence",       {"sequence": "a, b"},           "a, b"),
        ("tool_call_count",     {"max_calls": 3},               "3"),
        ("tool_success_rate",   {"min_rate": 0.8},              "80%"),
        ("no_repeated_calls",   {},                              "duplicate"),
        ("tool_output_contains",{"tool_name": "t", "text": "ok"}, "ok"),
        ("content_contains",    {"text": "hello"},               "hello"),
        ("content_not_contains",{"text": "error"},               "error"),
        ("content_regex",       {"pattern": r"\d+"},             r"\d+"),
        ("latency",             {"max_seconds": 10},             "10"),
        ("token_limit",         {"max_tokens": 500},             "500"),
        ("max_iterations",      {"max_iter": 4},                 "4"),
        ("tokens_per_second",   {"min_tps": 5},                  "5"),
        ("path_efficiency",     {"expected_sequence": "a,b",
                                  "allow_extra_steps": 0,
                                  "penalize_backtracking": True}, "a,b"),
        ("goal_achievement",    {},                              "goal"),
        ("tool_usage_efficiency",{"max_calls": 5},              "5"),
        ("no_error_output",     {},                              "error"),
    ])
    def test_produces_fragment(self, type_key, params, expected_fragment):
        m = make_metric("x", "name", type_key, **params)
        result = format_criterion(m)
        assert result, f"format_criterion returned empty string for type '{type_key}'"
        assert expected_fragment.lower() in result.lower(), (
            f"Expected '{expected_fragment}' in '{result}' for type '{type_key}'"
        )


# ── Registry coverage ────────────────────────────────────────────────────────

def test_all_metric_types_in_registry():
    assert len(METRIC_TYPES) > 0
    for key, defn in METRIC_TYPES.items():
        assert "label" in defn, f"{key} missing 'label'"
        assert "category" in defn, f"{key} missing 'category'"
        assert defn["category"] in CATEGORIES, (
            f"{key} category '{defn['category']}' not in CATEGORIES"
        )
