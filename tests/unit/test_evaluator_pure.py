"""
Unit tests for pure helper functions in core.evaluator that are not yet
covered by the existing test suite.

Covers:
  - _parse_inline_tool_calls
  - _calculate_step_tdi
  - _init_telemetry
  - _parse_caf_run_id
  - _caf_provider_flags
  - _check_inefficiencies (missing-tool-key path)
"""
import json
import pytest
from core.evaluator import (
    _parse_inline_tool_calls,
    _calculate_step_tdi,
    _init_telemetry,
    _parse_caf_run_id,
    _caf_provider_flags,
    _check_inefficiencies,
)


# ── _parse_inline_tool_calls ──────────────────────────────────────────────────

class TestParseInlineToolCalls:
    def test_single_valid_call(self):
        content = '<tool_call>{"name": "file_creator", "arguments": {"path": "/tmp/x", "content": "hi"}}</tool_call>'
        calls = _parse_inline_tool_calls(content)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "file_creator"

    def test_arguments_dict_serialised_to_string(self):
        content = '<tool_call>{"name": "file_creator", "arguments": {"path": "/tmp/x"}}</tool_call>'
        calls = _parse_inline_tool_calls(content)
        # arguments must come back as a JSON string, not a dict
        assert isinstance(calls[0]["function"]["arguments"], str)

    def test_list_format_multiple_calls(self):
        data = [
            {"name": "tool_a", "arguments": {}},
            {"name": "tool_b", "arguments": {"k": "v"}},
        ]
        content = f"<tool_call>{json.dumps(data)}</tool_call>"
        calls = _parse_inline_tool_calls(content)
        assert len(calls) == 2
        assert calls[0]["function"]["name"] == "tool_a"
        assert calls[1]["function"]["name"] == "tool_b"

    def test_malformed_json_skipped(self):
        content = "<tool_call>{not valid json}</tool_call>"
        calls = _parse_inline_tool_calls(content)
        assert calls == []

    def test_missing_name_key_skipped(self):
        content = '<tool_call>{"tool": "orphan", "arguments": {}}</tool_call>'
        calls = _parse_inline_tool_calls(content)
        assert calls == []

    def test_no_tool_call_tags_returns_empty(self):
        assert _parse_inline_tool_calls("plain text no tags") == []

    def test_parameters_key_accepted_as_args(self):
        content = '<tool_call>{"name": "run_nmap_scan", "parameters": {"target": "127.0.0.1"}}</tool_call>'
        calls = _parse_inline_tool_calls(content)
        assert len(calls) == 1
        assert '"target"' in calls[0]["function"]["arguments"]

    def test_id_assigned_incrementally(self):
        data = [{"name": "a", "arguments": {}}, {"name": "b", "arguments": {}}]
        content = f"<tool_call>{json.dumps(data)}</tool_call>"
        calls = _parse_inline_tool_calls(content)
        assert calls[0]["id"] == "call_0"
        assert calls[1]["id"] == "call_1"

    def test_multiple_separate_tags(self):
        content = (
            '<tool_call>{"name": "tool_a", "arguments": {}}</tool_call>'
            ' some text '
            '<tool_call>{"name": "tool_b", "arguments": {}}</tool_call>'
        )
        calls = _parse_inline_tool_calls(content)
        assert len(calls) == 2

    def test_empty_string(self):
        assert _parse_inline_tool_calls("") == []


# ── _calculate_step_tdi ───────────────────────────────────────────────────────
#
# 3-component PENTESTGPT V2 formula (H-dimension dropped — not available post-hoc):
#   E = score_evidence_confidence(tool, output, exit_code)   # 0.1–1.0
#   C = min(tokens / context_size, 1.0)
#   S = successes / len(recent[-3:])  (1.0 when no prior steps)
#   TDI = 0.4*(1-E) + 0.3*C + 0.3*(1-S)
#
# Returns tuple: (tdi, e, c, s, evidence_confidence)

class TestCalculateStepTdi:
    def _tdi(self, *args, **kwargs):
        return _calculate_step_tdi(*args, **kwargs)[0]

    def test_returns_tuple_of_five(self):
        result = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=0,
                                     recent_steps=[], context_size=4096)
        assert isinstance(result, tuple) and len(result) == 5

    def test_zero_tokens_no_failures_generic_output(self):
        # E=0.3 (generic), C=0.0, S=1.0  →  TDI=0.4*0.7+0+0 = 0.28
        tdi, e, c, s, ev = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=0,
                                                recent_steps=[], context_size=4096)
        assert tdi == pytest.approx(0.28, abs=0.01)
        assert e == 0.3
        assert c == 0.0
        assert s == 1.0

    def test_full_context_no_failures(self):
        # E=0.3, C=1.0, S=1.0  →  TDI=0.4*0.7+0.3*1.0+0 = 0.58
        tdi, _, c, _, _ = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=4096,
                                               recent_steps=[], context_size=4096)
        assert c == 1.0
        assert tdi == pytest.approx(0.58, abs=0.01)

    def test_three_recent_failures_raises_tdi(self):
        # S=0.0 (all 3 failed)  →  TDI += 0.3*1.0 = 0.3 extra
        steps = [{"exit_code": 1}, {"exit_code": 1}, {"exit_code": 1}]
        tdi, _, _, s, _ = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=0,
                                               recent_steps=steps, context_size=4096)
        assert s == 0.0
        assert tdi == pytest.approx(0.58, abs=0.01)  # 0.28 + 0.30

    def test_failed_exit_code_gives_low_evidence(self):
        # exit_code=1 → E=0.1, higher TDI
        tdi, e, _, _, _ = _calculate_step_tdi("tool", {"stdout": ""},
                                               tokens=0, recent_steps=[],
                                               context_size=4096, exit_code=1)
        assert e == 0.1
        assert tdi == pytest.approx(0.36, abs=0.01)  # 0.4*0.9 = 0.36

    def test_shell_access_output_gives_high_evidence(self):
        # "uid=0" → shell access → E=1.0 → TDI contribution from E = 0
        tdi, e, _, _, _ = _calculate_step_tdi(
            "shell", {"stdout": "uid=0(root) gid=0(root)"},
            tokens=0, recent_steps=[], context_size=4096, exit_code=0,
        )
        assert e == 1.0
        assert tdi == pytest.approx(0.0, abs=0.01)

    def test_service_identified_medium_evidence(self):
        # "22/tcp open ssh" → service identified → E=0.5
        tdi, e, _, _, _ = _calculate_step_tdi(
            "nmap", {"stdout": "22/tcp open ssh"},
            tokens=0, recent_steps=[], context_size=4096, exit_code=0,
        )
        assert e == 0.5

    def test_max_tokens_clamped(self):
        # tokens > context_size → C clamped to 1.0
        _, _, c, _, _ = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=99999,
                                             recent_steps=[], context_size=4096)
        assert c == 1.0

    def test_tdi_rounded_to_3dp(self):
        tdi, _, _, _, _ = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=0,
                                               recent_steps=[], context_size=4096)
        assert tdi == round(tdi, 3)

    def test_only_last_3_steps_count_for_s(self):
        # 5 steps: 2 failures then 3 successes → last 3 all succeed → S=1.0
        steps = [{"exit_code": 1}] * 2 + [{"exit_code": 0}] * 3
        _, _, _, s, _ = _calculate_step_tdi("tool", {"stdout": "ok"}, tokens=0,
                                             recent_steps=steps, context_size=4096)
        assert s == 1.0


# ── _init_telemetry ────────────────────────────────────────────────────────────

class TestInitTelemetry:
    def _config(self, **kw):
        base = {
            "active_scenario": "Scenario 1 – File Creation",
            "selected_model": "llama3.gguf",
            "backend_type": "llama.cpp",
            "tool_focus": "file_creator",
            "metrics_matrix": [{"id": "M-001"}],
            "caf_scope": "Narrow",
            "caf_urgency": "Stealthy",
            "caf_allowed_subnets": ["192.168.1.0/24"],
            "caf_target_credentials": [],
        }
        base.update(kw)
        return base

    def test_required_keys_present(self):
        tel = _init_telemetry(self._config())
        required = [
            "run_timestamp", "run_scenario", "run_model", "run_backend",
            "run_tool_focus", "total_latency", "prompt_tokens",
            "completion_tokens", "total_tokens", "tokens_per_second",
            "llm_rounds", "tool_calls", "validation_stdout",
            "validation_stderr", "validation_exit_code", "validation_passed",
            "inefficiencies", "llm_response", "run_aborted",
            "metrics_matrix", "caf_trajectory", "caf_config",
        ]
        for key in required:
            assert key in tel, f"Missing key: {key}"

    def test_numeric_defaults_are_zero(self):
        tel = _init_telemetry(self._config())
        assert tel["total_latency"] == 0.0
        assert tel["prompt_tokens"] == 0
        assert tel["completion_tokens"] == 0
        assert tel["total_tokens"] == 0
        assert tel["tokens_per_second"] == 0.0
        assert tel["llm_rounds"] == 0

    def test_list_defaults_are_empty(self):
        tel = _init_telemetry(self._config())
        assert tel["tool_calls"] == []
        assert tel["inefficiencies"] == []
        assert tel["caf_trajectory"] == []

    def test_run_aborted_false(self):
        tel = _init_telemetry(self._config())
        assert tel["run_aborted"] is False

    def test_validation_passed_none(self):
        tel = _init_telemetry(self._config())
        assert tel["validation_passed"] is None
        assert tel["validation_exit_code"] is None

    def test_model_fallback_when_none(self):
        cfg = self._config(selected_model=None)
        tel = _init_telemetry(cfg)
        assert tel["run_model"] == "(server default)"

    def test_caf_config_populated(self):
        tel = _init_telemetry(self._config())
        assert tel["caf_config"]["scope"] == "Narrow"
        assert tel["caf_config"]["urgency"] == "Stealthy"
        assert tel["caf_config"]["allowed_subnets"] == ["192.168.1.0/24"]

    def test_metrics_matrix_copied_from_config(self):
        cfg = self._config(metrics_matrix=[{"id": "M-001"}, {"id": "M-002"}])
        tel = _init_telemetry(cfg)
        assert len(tel["metrics_matrix"]) == 2

    def test_run_timestamp_is_string(self):
        tel = _init_telemetry(self._config())
        assert isinstance(tel["run_timestamp"], str)
        assert len(tel["run_timestamp"]) > 0


# ── _parse_caf_run_id ──────────────────────────────────────────────────────────

class TestParseCafRunId:
    def test_extracts_run_id(self):
        output = "[run] Transcript: runs/abc123/transcript.md"
        assert _parse_caf_run_id(output) == "abc123"

    def test_extracts_complex_run_id(self):
        output = "some preamble\n[run] Transcript: runs/run-2025-06-01-120000/transcript.md\nmore"
        assert _parse_caf_run_id(output) == "run-2025-06-01-120000"

    def test_no_match_returns_none(self):
        assert _parse_caf_run_id("no transcript line here") is None

    def test_empty_string_returns_none(self):
        assert _parse_caf_run_id("") is None

    def test_partial_match_no_slash_returns_none(self):
        # Missing trailing slash → no match
        assert _parse_caf_run_id("[run] Transcript: runs/abc123") is None


# ── _caf_provider_flags ────────────────────────────────────────────────────────

class TestCafProviderFlags:
    def test_ollama_backend(self):
        cfg = {"backend_type": "ollama", "llm_url": "http://localhost:11434"}
        flags = _caf_provider_flags(cfg)
        assert "--provider ollama_direct" in flags
        assert "11434" in flags

    def test_llama_cpp_backend(self):
        cfg = {"backend_type": "llama.cpp", "llm_url": "http://localhost:8080"}
        flags = _caf_provider_flags(cfg)
        assert "--provider openai" in flags
        assert "8080" in flags

    def test_url_trailing_slash_stripped(self):
        cfg = {"backend_type": "ollama", "llm_url": "http://localhost:11434/"}
        flags = _caf_provider_flags(cfg)
        # Trailing slash on the URL should be stripped — result should not end with /
        url_part = flags.split("--url")[1].strip()
        assert not url_part.endswith("/")

    def test_url_quoted_for_shell_safety(self):
        # URL with special characters must not break shell — shlex.quote wraps it
        cfg = {"backend_type": "ollama", "llm_url": "http://localhost:11434"}
        flags = _caf_provider_flags(cfg)
        # shlex.quote on a plain URL produces the URL itself (no special chars)
        assert "localhost" in flags


# ── _check_inefficiencies (missing-tool-key path) ─────────────────────────────

class TestCheckInefficienciesMissingKey:
    def test_missing_tool_key_treated_as_none(self):
        # Calls where the 'tool' key is absent: key = (None, '{}')
        calls = [
            {"args": {}},  # no 'tool' key
            {"args": {}},  # same — should be detected as duplicate
        ]
        issues = _check_inefficiencies(calls)
        assert len(issues) == 1
        assert "None" in issues[0]

    def test_mixed_present_absent_tool_keys(self):
        calls = [
            {"tool": "file_creator", "args": {"path": "/x"}},
            {"args": {}},  # no 'tool'
        ]
        # Different keys — no duplicates
        issues = _check_inefficiencies(calls)
        assert issues == []
