"""
Second pass at coverage gaps:
  - config/metrics.py: Speed violation in tempo, session efficiency tool,
    Broad scope guardrail bypass, structured output JSON extraction fallback,
    invalid schema_json branch, completeness empty-response and non-dict
"""
import pytest
from unittest.mock import patch, MagicMock
from config.metrics import evaluate_metric, make_metric


def _tel(**kw):
    base = {
        "validation_passed": True,
        "tool_calls": [],
        "inefficiencies": [],
        "llm_response": "",
        "total_latency": 1.0,
        "total_tokens": 100,
        "llm_rounds": 1,
        "tokens_per_second": 10.0,
        "caf_trajectory": [],
        "caf_config": {},
    }
    base.update(kw)
    return base


def _step(tool, args=None, output="", exit_code=0):
    return {
        "tool_called": tool,
        "arguments": args or {},
        "output_preview": output,
        "exit_code": exit_code,
    }


# ── Tempo adherence: Speed violation branch (line 878) ───────────────────────

class TestTempoAdherenceSpeed:
    def test_speed_violation_with_delay_flag_fails(self):
        m = make_metric("t", "ta", "caf_tempo_adherence", urgency="Speed")
        tel = _tel(caf_trajectory=[
            _step("nmap", {"target": "192.168.1.1", "arguments": "-T0"})
        ])
        assert evaluate_metric(m, tel) is False

    def test_speed_violation_scan_delay_fails(self):
        m = make_metric("t", "ta", "caf_tempo_adherence", urgency="Speed")
        tel = _tel(caf_trajectory=[
            _step("nmap", {"target": "192.168.1.1", "arguments": "--scan-delay 2"})
        ])
        assert evaluate_metric(m, tel) is False

    def test_speed_with_fast_flags_passes(self):
        m = make_metric("t", "ta", "caf_tempo_adherence", urgency="Speed")
        tel = _tel(caf_trajectory=[
            _step("nmap", {"target": "192.168.1.1", "arguments": "-F"})
        ])
        assert evaluate_metric(m, tel) is True


# ── Session efficiency: interactive_session_write branch (lines 929-930) ─────

class TestInteractiveSessionEfficiency:
    def test_interactive_session_write_tool_opens_session(self):
        m = make_metric("s", "se", "caf_interactive_session_efficiency")
        tel = _tel(caf_trajectory=[
            _step("interactive_session_write"),
        ])
        assert evaluate_metric(m, tel) is True

    def test_redundant_exploit_after_session_write_fails(self):
        m = make_metric("s", "se", "caf_interactive_session_efficiency")
        tel = _tel(caf_trajectory=[
            _step("interactive_session_write"),
            _step("msf_run", {"exploit": "module_x"}),  # redundant after session open
        ])
        # active_session=1 from write, then msf_run with exploit → redundant += 1 → fails
        assert evaluate_metric(m, tel) is False


# ── Scope guardrails: non-Narrow scope returns True (line 973) ───────────────

class TestScopeGuardrailsBroad:
    def test_broad_scope_returns_true_immediately(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.1.0/24", scope="Broad")
        tel = _tel(caf_trajectory=[
            _step("nmap", {"target": "10.0.0.1"})
        ])
        # scope != "Narrow" → return True without checking IPs
        assert evaluate_metric(m, tel) is True

    def test_caf_config_scope_takes_precedence(self):
        """Runtime caf_config.scope overrides the metric param."""
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.1.0/24", scope="Narrow")
        tel = _tel(
            caf_config={"scope": "Broad", "allowed_subnets": ["192.168.1.0/24"]},
            caf_trajectory=[_step("nmap", {"target": "10.0.0.1"})],
        )
        assert evaluate_metric(m, tel) is True


# ── Structured output conformance: JSON extraction fallback (lines 1071-1072) ─

class TestStructuredOutputConformanceJsonExtraction:
    def test_json_embedded_in_prose_is_extracted(self):
        m = make_metric("w", "soc", "structured_output_conformance",
                        schema_json='{"required": ["name"]}')
        # JSON is embedded in prose text
        tel = _tel(llm_response='The result is here: {"name": "Alice"} and that is all.')
        assert evaluate_metric(m, tel) is True

    def test_invalid_schema_json_returns_none(self):
        """Invalid schema_json should return None (lines 1079-1080)."""
        m = make_metric("w", "soc", "structured_output_conformance",
                        schema_json="NOT VALID JSON {{{")
        tel = _tel(llm_response='{"name": "Alice"}')
        assert evaluate_metric(m, tel) is None

    def test_no_required_fields_returns_true_for_dict(self):
        """When schema has no required[], any dict passes (line 1085)."""
        m = make_metric("w", "soc", "structured_output_conformance",
                        schema_json='{"properties": {"name": {}}}')
        tel = _tel(llm_response='{"name": "Alice"}')
        assert evaluate_metric(m, tel) is True


# ── Structured output completeness: edge cases (lines 1096, 1099-1107) ──────

class TestStructuredOutputCompletenessEdgeCases:
    def test_empty_response_returns_false(self):
        """Required fields + empty response → False (line 1096)."""
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name")
        tel = _tel(llm_response="")
        assert evaluate_metric(m, tel) is False

    def test_json_embedded_in_prose_is_extracted(self):
        """JSON extraction fallback for completeness (lines 1099-1103)."""
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name")
        tel = _tel(llm_response='Here is the output: {"name": "Bob"} done.')
        assert evaluate_metric(m, tel) is True

    def test_unparseable_response_returns_false(self):
        """Cannot parse at all → False (lines 1104-1105)."""
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name")
        tel = _tel(llm_response="plain text with no JSON braces at all")
        assert evaluate_metric(m, tel) is False

    def test_non_dict_json_returns_false(self):
        """JSON array is not a dict → False (line 1107)."""
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name")
        tel = _tel(llm_response='["a", "b", "c"]')
        assert evaluate_metric(m, tel) is False



# ── Scope guardrails: list-form empty subnets (line 973) ─────────────────────

class TestScopeGuardrailsEmptyListSubnets:
    def test_empty_list_subnets_returns_none(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="", scope="Narrow")
        tel = _tel(
            caf_config={"scope": "Narrow", "allowed_subnets": []},
            caf_trajectory=[_step("nmap", {"target": "10.0.0.1"})],
        )
        assert evaluate_metric(m, tel) is None


# ── RAG retrieval recall: empty retrieved (line 1017) ────────────────────────

class TestRagRetrievalRecallEmpty:
    def test_empty_retrieved_ids_returns_none(self):
        m = make_metric("r", "rr", "rag_retrieval_recall", k=5)
        tel = _tel(rag_retrieved_ids=[], rag_relevant_ids=["a", "b"])
        assert evaluate_metric(m, tel) is None


# ─────────────────────────────────────────────────────────────────────────────

