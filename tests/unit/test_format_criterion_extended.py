"""
Extended format_criterion coverage — all metric types in METRIC_TYPES that
were not covered by test_metrics.py (CAF, RAG, Workflow, AI-Judge categories).

Each test asserts:
  1. The result is non-empty.
  2. A key fragment appears in the output (case-insensitive).
"""
import pytest
from config.metrics import format_criterion, METRIC_TYPES, make_metric


# ── Parametrized: every type in METRIC_TYPES returns non-empty string ────────

@pytest.mark.parametrize("type_key", list(METRIC_TYPES.keys()))
def test_format_criterion_non_empty_for_all_types(type_key):
    """Every registered metric type must produce a non-empty criterion string."""
    # Build minimal params from the registry definition
    param_defs = METRIC_TYPES[type_key].get("params", [])
    params = {}
    for p in param_defs:
        default = p.get("default", "")
        # Use non-empty defaults for string params so the criterion is useful
        if p["type"] == "str" and not default:
            default = "example_value"
        params[p["name"]] = default

    m = make_metric("x", "test", type_key, **params)
    result = format_criterion(m)
    assert result, f"format_criterion returned empty string for type '{type_key}'"


# ── CAF criteria ──────────────────────────────────────────────────────────────

class TestCafFormatCriteria:
    def test_caf_tempo_adherence(self):
        m = make_metric("c", "t", "caf_tempo_adherence", urgency="Stealthy")
        assert "stealthy" in format_criterion(m).lower()

    def test_caf_diagnostic_adherence(self):
        m = make_metric("c", "d", "caf_diagnostic_adherence")
        result = format_criterion(m)
        assert "recon" in result.lower()

    def test_caf_tdi_health(self):
        m = make_metric("c", "h", "caf_tdi_health", max_avg_tdi=0.4)
        assert "0.4" in format_criterion(m)

    def test_caf_tool_param_accuracy(self):
        m = make_metric("c", "a", "caf_tool_param_accuracy", min_accuracy=0.9)
        assert "90" in format_criterion(m)

    def test_caf_interactive_session_efficiency(self):
        m = make_metric("c", "e", "caf_interactive_session_efficiency")
        result = format_criterion(m)
        assert result != ""
        assert "session" in result.lower() or "exploit" in result.lower() or "redundant" in result.lower()

    def test_caf_memory_recall(self):
        m = make_metric("c", "m", "caf_memory_recall", target_credentials="root:toor")
        assert "root:toor" in format_criterion(m)

    def test_caf_scope_guardrails(self):
        m = make_metric("c", "s", "caf_scope_guardrails", scope="Narrow",
                        allowed_subnets="192.168.1.0/24")
        assert "narrow" in format_criterion(m).lower()


# ── RAG criteria ──────────────────────────────────────────────────────────────

class TestRagFormatCriteria:
    def test_rag_retrieval_precision(self):
        m = make_metric("r", "rp", "rag_retrieval_precision", k=5)
        assert "5" in format_criterion(m)

    def test_rag_retrieval_recall(self):
        m = make_metric("r", "rr", "rag_retrieval_recall", k=3)
        assert "3" in format_criterion(m)

    def test_rag_answer_faithfulness(self):
        m = make_metric("r", "rf", "rag_answer_faithfulness")
        result = format_criterion(m)
        assert result != ""

    def test_rag_context_utilization(self):
        m = make_metric("r", "rc", "rag_context_utilization")
        result = format_criterion(m)
        assert result != ""

    def test_rag_answer_relevance(self):
        m = make_metric("r", "rar", "rag_answer_relevance", min_similarity=0.7)
        assert "0.7" in format_criterion(m)


# ── Workflow criteria ─────────────────────────────────────────────────────────

class TestWorkflowFormatCriteria:
    def test_classification_accuracy(self):
        m = make_metric("w", "ca", "classification_accuracy", min_accuracy=0.85)
        assert "85" in format_criterion(m)

    def test_classification_f1(self):
        m = make_metric("w", "cf", "classification_f1", min_f1=0.7)
        assert "0.7" in format_criterion(m)

    def test_summarization_rouge(self):
        m = make_metric("w", "sr", "summarization_rouge", min_rouge=0.3)
        assert "0.3" in format_criterion(m)

    def test_summarization_faithfulness(self):
        m = make_metric("w", "sf", "summarization_faithfulness")
        result = format_criterion(m)
        assert "contradict" in result.lower() or "faithful" in result.lower()

    def test_structured_output_conformance(self):
        m = make_metric("w", "soc", "structured_output_conformance", schema_json="{}")
        result = format_criterion(m)
        assert "json" in result.lower() or "schema" in result.lower()

    def test_structured_output_completeness(self):
        m = make_metric("w", "soc2", "structured_output_completeness",
                        required_fields="name, age")
        assert "name" in format_criterion(m)

    def test_multiagent_consensus_accuracy(self):
        m = make_metric("w", "mca", "multiagent_consensus_accuracy", min_agreement=0.7)
        assert "0.7" in format_criterion(m)


# ── AI-Judge criteria ─────────────────────────────────────────────────────────

class TestAiJudgeFormatCriteria:
    def test_judge_correctness(self):
        m = make_metric("j", "jc", "judge_correctness", min_score=70)
        assert "70" in format_criterion(m)

    def test_judge_coherence(self):
        m = make_metric("j", "jco", "judge_coherence", min_score=75)
        assert "75" in format_criterion(m)

    def test_judge_goal_alignment(self):
        m = make_metric("j", "jga", "judge_goal_alignment", min_score=80)
        assert "80" in format_criterion(m)

    def test_judge_aggregate(self):
        m = make_metric("j", "jagg", "judge_aggregate", min_score=65)
        assert "65" in format_criterion(m)


# ── Fallback (legacy criterion) ───────────────────────────────────────────────

class TestLegacyCriterionFallback:
    def test_unknown_type_returns_criterion_field(self):
        m = {
            "id": "old-1",
            "name": "Legacy Metric",
            "type": "totally_unknown_type_xyz",
            "enabled": True,
            "params": {},
            "criterion": "Must do the thing",
        }
        result = format_criterion(m)
        assert result == "Must do the thing"

    def test_unknown_type_no_criterion_returns_empty(self):
        m = {"id": "old-2", "name": "x", "type": "unknown", "enabled": True, "params": {}}
        result = format_criterion(m)
        assert result == ""
