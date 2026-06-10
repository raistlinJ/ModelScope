"""
Unit tests for config/metrics.py — evaluate_metric() accuracy.

Covers:
  goal_achievement     — composite logic, all three sub-conditions
  tool_call_count      — differentiated from tool_usage_efficiency
  tool_usage_efficiency — max_calls + no redundancy; different from tool_call_count
  no_repeated_calls    — uses telemetry["inefficiencies"]; edge cases
  no_error_output      — exit_code=0 but output contains error strings
  path_efficiency      — exact sequence, extra steps, backtracking flag
  content_contains     — case-insensitive substring check
  content_regex        — regex match on llm_response
  tool_called          — basic presence check
  tool_not_called      — absence check
  task_completion      — delegates to validation_passed flag
  max_iterations       — llm_rounds ≤ max_iter
"""
import pytest
from config.metrics import evaluate_metric, make_metric


# ─── Telemetry builder helpers ────────────────────────────────────────────────

def _tel(**kwargs):
    """Build a minimal telemetry dict with sensible defaults."""
    base = {
        "validation_passed": True,
        "tool_calls": [],
        "inefficiencies": [],
        "llm_response": "",
        "total_latency": 5.0,
        "total_tokens": 500,
        "llm_rounds": 2,
        "tokens_per_second": 20.0,
    }
    base.update(kwargs)
    return base


def _tc(tool, args=None, exit_code=0, result="ok"):
    return {"tool": tool, "args": args or {}, "exit_code": exit_code, "result": result}


# ─── goal_achievement ─────────────────────────────────────────────────────────

class TestGoalAchievement:
    metric = make_metric("ga", "Goal Achievement", "goal_achievement")

    def test_pass_all_conditions_met(self):
        tel = _tel(
            validation_passed=True,
            tool_calls=[_tc("file_creator")],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is True

    def test_fail_validation_not_passed(self):
        tel = _tel(
            validation_passed=False,
            tool_calls=[_tc("file_creator")],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_has_inefficiencies(self):
        tel = _tel(
            validation_passed=True,
            tool_calls=[_tc("file_creator"), _tc("file_creator")],
            inefficiencies=["Repeated call: file_creator with identical arguments"],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_tool_returned_nonzero_exit(self):
        tel = _tel(
            validation_passed=True,
            tool_calls=[_tc("run_nmap_scan", exit_code=1)],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_none_when_validation_passed_is_none(self):
        tel = _tel(validation_passed=None)
        assert evaluate_metric(self.metric, tel) is None

    def test_fail_multiple_conditions_broken(self):
        tel = _tel(
            validation_passed=False,
            tool_calls=[_tc("file_creator", exit_code=1)],
            inefficiencies=["Repeated call: ..."],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_pass_with_multiple_successful_calls(self):
        tel = _tel(
            validation_passed=True,
            tool_calls=[
                _tc("run_nmap_scan", args={"target": "127.0.0.1"}),
                _tc("file_creator", args={"path": "/tmp/x", "content": "data"}),
            ],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is True


# ─── tool_call_count vs tool_usage_efficiency (differentiation) ──────────────

class TestToolCallCountVsToolUsageEfficiency:
    """
    tool_call_count: passes if total calls ≤ max_calls (ignores redundancy).
    tool_usage_efficiency: passes only if total calls ≤ max_calls AND no redundancy.
    They diverge when redundant calls are present but count stays under max.
    """

    def _count_metric(self, max_calls=5):
        return make_metric("tcc", "Tool Call Count", "tool_call_count", max_calls=max_calls)

    def _eff_metric(self, max_calls=5):
        return make_metric("tue", "Tool Usage Efficiency", "tool_usage_efficiency", max_calls=max_calls)

    def test_both_pass_no_redundancy_under_limit(self):
        tel = _tel(tool_calls=[_tc("file_creator")], inefficiencies=[])
        assert evaluate_metric(self._count_metric(), tel) is True
        assert evaluate_metric(self._eff_metric(), tel) is True

    def test_both_fail_over_limit(self):
        tel = _tel(tool_calls=[_tc("a")] * 6, inefficiencies=[])
        assert evaluate_metric(self._count_metric(max_calls=5), tel) is False
        assert evaluate_metric(self._eff_metric(max_calls=5), tel) is False

    def test_count_passes_but_efficiency_fails_when_redundant(self):
        """One redundant call, total = 2 (under max=5) — count PASSES, efficiency FAILS."""
        tel = _tel(
            tool_calls=[_tc("file_creator"), _tc("file_creator")],
            inefficiencies=["Repeated call: file_creator with identical arguments"],
        )
        assert evaluate_metric(self._count_metric(max_calls=5), tel) is True
        assert evaluate_metric(self._eff_metric(max_calls=5), tel) is False

    def test_count_boundary_exact_max(self):
        tel = _tel(tool_calls=[_tc("a")] * 5, inefficiencies=[])
        assert evaluate_metric(self._count_metric(max_calls=5), tel) is True

    def test_count_boundary_one_over(self):
        tel = _tel(tool_calls=[_tc("a")] * 6, inefficiencies=[])
        assert evaluate_metric(self._count_metric(max_calls=5), tel) is False

    def test_efficiency_zero_calls_is_fine(self):
        tel = _tel(tool_calls=[], inefficiencies=[])
        assert evaluate_metric(self._eff_metric(max_calls=5), tel) is True


# ─── no_repeated_calls ────────────────────────────────────────────────────────

class TestNoRepeatedCalls:
    metric = make_metric("nrc", "No Repeated Calls", "no_repeated_calls")

    def test_pass_no_calls(self):
        assert evaluate_metric(self.metric, _tel(inefficiencies=[])) is True

    def test_pass_all_unique(self):
        tel = _tel(
            tool_calls=[
                _tc("run_nmap_scan", {"target": "127.0.0.1"}),
                _tc("file_creator", {"path": "/tmp/x", "content": "a"}),
            ],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is True

    def test_fail_same_tool_same_args_twice(self):
        tel = _tel(
            inefficiencies=["Repeated call: file_creator with identical arguments"],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_multiple_repeated_calls(self):
        tel = _tel(
            inefficiencies=[
                "Repeated call: run_nmap_scan with identical arguments",
                "Repeated call: file_creator with identical arguments",
            ],
        )
        assert evaluate_metric(self.metric, tel) is False

    def test_pass_same_tool_different_args_not_repeated(self):
        """Same tool but different args → not a redundant call."""
        tel = _tel(
            tool_calls=[
                _tc("run_nmap_scan", {"target": "127.0.0.1"}),
                _tc("run_nmap_scan", {"target": "10.0.0.1"}),
            ],
            inefficiencies=[],
        )
        assert evaluate_metric(self.metric, tel) is True


# ─── no_error_output ─────────────────────────────────────────────────────────

class TestNoErrorOutput:
    metric = make_metric("neo", "No Error Output", "no_error_output")

    def test_pass_no_calls(self):
        assert evaluate_metric(self.metric, _tel(tool_calls=[])) is True

    def test_pass_clean_output(self):
        tel = _tel(tool_calls=[_tc("file_creator", result="bytes_written: 12")])
        assert evaluate_metric(self.metric, tel) is True

    def test_fail_error_in_zero_exit_result(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0, result="error: permission denied")])
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_not_found_in_output(self):
        tel = _tel(tool_calls=[_tc("run_nmap_scan", exit_code=0, result="host not found")])
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_traceback_in_output(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0, result="Traceback (most recent call last)")])
        assert evaluate_metric(self.metric, tel) is False

    def test_skip_nonzero_exit_code_for_error_strings(self):
        """exit_code ≠ 0 calls are not checked by no_error_output (they're already failures)."""
        tel = _tel(tool_calls=[_tc("run_nmap_scan", exit_code=1, result="error: host unreachable")])
        assert evaluate_metric(self.metric, tel) is True

    def test_fail_permission_denied(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0, result="permission denied")])
        assert evaluate_metric(self.metric, tel) is False

    def test_fail_failed_keyword(self):
        tel = _tel(tool_calls=[_tc("file_creator", exit_code=0, result="operation failed")])
        assert evaluate_metric(self.metric, tel) is False

    def test_pass_multiple_clean_calls(self):
        tel = _tel(tool_calls=[
            _tc("run_nmap_scan", exit_code=0, result="open 22/tcp ssh"),
            _tc("file_creator", exit_code=0, result="bytes_written: 4"),
        ])
        assert evaluate_metric(self.metric, tel) is True


# ─── path_efficiency ─────────────────────────────────────────────────────────

class TestPathEfficiency:
    def _m(self, seq="file_creator", extra=0, backtrack=True):
        return make_metric("pe", "Path Efficiency", "path_efficiency",
                           expected_sequence=seq,
                           allow_extra_steps=extra,
                           penalize_backtracking=backtrack)

    def test_pass_exact_match(self):
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(self._m("file_creator"), tel) is True

    def test_fail_missing_expected_tool(self):
        tel = _tel(tool_calls=[_tc("run_nmap_scan")])
        assert evaluate_metric(self._m("file_creator"), tel) is False

    def test_pass_with_extra_step_allowed(self):
        tel = _tel(tool_calls=[_tc("run_nmap_scan"), _tc("file_creator")])
        assert evaluate_metric(self._m("file_creator", extra=1), tel) is True

    def test_fail_too_many_extra_steps(self):
        tel = _tel(tool_calls=[_tc("a"), _tc("b"), _tc("file_creator")])
        assert evaluate_metric(self._m("file_creator", extra=0), tel) is False

    def test_fail_backtracking_penalized(self):
        tel = _tel(tool_calls=[_tc("file_creator"), _tc("file_creator")])
        assert evaluate_metric(self._m("file_creator", extra=1, backtrack=True), tel) is False

    def test_pass_backtracking_not_penalized(self):
        tel = _tel(tool_calls=[_tc("file_creator"), _tc("file_creator")])
        assert evaluate_metric(self._m("file_creator", extra=1, backtrack=False), tel) is True

    def test_pass_two_step_sequence(self):
        tel = _tel(tool_calls=[_tc("run_nmap_scan"), _tc("file_creator")])
        assert evaluate_metric(self._m("run_nmap_scan,file_creator"), tel) is True

    def test_fail_sequence_out_of_order(self):
        tel = _tel(tool_calls=[_tc("file_creator"), _tc("run_nmap_scan")])
        assert evaluate_metric(self._m("run_nmap_scan,file_creator"), tel) is False

    def test_none_when_no_expected_sequence(self):
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(self._m(""), tel) is None


# ─── content_contains ────────────────────────────────────────────────────────

class TestContentContains:
    def _m(self, text):
        return make_metric("cc", "Response Contains", "content_contains", text=text)

    def test_pass_exact(self):
        tel = _tel(llm_response="The port 22 is open.")
        assert evaluate_metric(self._m("port"), tel) is True

    def test_pass_case_insensitive(self):
        tel = _tel(llm_response="PORT 22 IS OPEN")
        assert evaluate_metric(self._m("port"), tel) is True

    def test_fail_not_present(self):
        tel = _tel(llm_response="File created successfully.")
        assert evaluate_metric(self._m("port"), tel) is False

    def test_none_empty_text_param(self):
        tel = _tel(llm_response="something")
        assert evaluate_metric(self._m(""), tel) is None


# ─── content_regex ────────────────────────────────────────────────────────────

class TestContentRegex:
    def _m(self, pattern):
        return make_metric("cr", "Response Matches Regex", "content_regex", pattern=pattern)

    def test_pass_port_pattern(self):
        tel = _tel(llm_response="Open ports: 22, 80, 443")
        assert evaluate_metric(self._m(r"\d+/tcp"), tel) is False  # no "/tcp" in string
        assert evaluate_metric(self._m(r"\b\d+\b"), tel) is True

    def test_pass_ip_pattern(self):
        tel = _tel(llm_response="Scanned 192.168.1.1")
        assert evaluate_metric(self._m(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"), tel) is True

    def test_fail_no_match(self):
        tel = _tel(llm_response="File was created.")
        assert evaluate_metric(self._m(r"\d+/tcp"), tel) is False

    def test_none_invalid_regex(self):
        tel = _tel(llm_response="anything")
        assert evaluate_metric(self._m(r"[invalid"), tel) is None

    def test_none_empty_pattern(self):
        tel = _tel(llm_response="anything")
        assert evaluate_metric(self._m(""), tel) is None


# ─── tool_called / tool_not_called ───────────────────────────────────────────

class TestToolCalledChecks:
    def test_tool_called_pass(self):
        m = make_metric("tc", "Tool Called", "tool_called", tool_name="file_creator")
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(m, tel) is True

    def test_tool_called_fail_wrong_tool(self):
        m = make_metric("tc", "Tool Called", "tool_called", tool_name="file_creator")
        tel = _tel(tool_calls=[_tc("run_nmap_scan")])
        assert evaluate_metric(m, tel) is False

    def test_tool_called_fail_no_calls(self):
        m = make_metric("tc", "Tool Called", "tool_called", tool_name="file_creator")
        tel = _tel(tool_calls=[])
        assert evaluate_metric(m, tel) is False

    def test_tool_not_called_pass(self):
        m = make_metric("tnc", "Tool Not Called", "tool_not_called", tool_name="file_creator")
        tel = _tel(tool_calls=[_tc("run_nmap_scan")])
        assert evaluate_metric(m, tel) is True

    def test_tool_not_called_fail(self):
        m = make_metric("tnc", "Tool Not Called", "tool_not_called", tool_name="file_creator")
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(m, tel) is False

    def test_tool_called_none_when_empty_tool_name(self):
        m = make_metric("tc", "Tool Called", "tool_called", tool_name="")
        tel = _tel(tool_calls=[_tc("file_creator")])
        assert evaluate_metric(m, tel) is None


# ─── task_completion ─────────────────────────────────────────────────────────

class TestTaskCompletion:
    metric = make_metric("comp", "Task Completion", "task_completion")

    def test_pass(self):
        assert evaluate_metric(self.metric, _tel(validation_passed=True)) is True

    def test_fail(self):
        assert evaluate_metric(self.metric, _tel(validation_passed=False)) is False

    def test_none(self):
        assert evaluate_metric(self.metric, _tel(validation_passed=None)) is None


# ─── max_iterations ──────────────────────────────────────────────────────────

class TestMaxIterations:
    def _m(self, max_iter=3):
        return make_metric("mi", "Max LLM Iterations", "max_iterations", max_iter=max_iter)

    def test_pass_under(self):
        assert evaluate_metric(self._m(3), _tel(llm_rounds=2)) is True

    def test_pass_exact(self):
        assert evaluate_metric(self._m(3), _tel(llm_rounds=3)) is True

    def test_fail_over(self):
        assert evaluate_metric(self._m(3), _tel(llm_rounds=4)) is False

    def test_fail_single_allowed_two_used(self):
        assert evaluate_metric(self._m(1), _tel(llm_rounds=2)) is False
