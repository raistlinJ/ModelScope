"""
Extended evaluate_metric tests for metric types not fully covered:
  - RAG metrics (pass and fail for each)
  - Workflow metrics
  - AI-Judge metrics
  - Legacy _eval_legacy fallback
  - Scope guardrails edge cases (malformed IP in trajectory)
  - Memory recall edge cases
"""
import pytest
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


# ── RAG metrics ───────────────────────────────────────────────────────────────

class TestRagMetrics:
    def test_retrieval_precision_pass(self):
        m = make_metric("r", "rp", "rag_retrieval_precision", k=3)
        tel = _tel(rag_retrieved_ids=["a", "b", "c"], rag_relevant_ids=["a", "b", "c"])
        assert evaluate_metric(m, tel) is True

    def test_retrieval_precision_fail(self):
        m = make_metric("r", "rp", "rag_retrieval_precision", k=3)
        tel = _tel(rag_retrieved_ids=["x", "y", "z"], rag_relevant_ids=["a", "b"])
        assert evaluate_metric(m, tel) is False

    def test_retrieval_precision_empty_returns_none(self):
        m = make_metric("r", "rp", "rag_retrieval_precision", k=3)
        tel = _tel()
        assert evaluate_metric(m, tel) is None

    def test_retrieval_recall_pass(self):
        m = make_metric("r", "rr", "rag_retrieval_recall", k=5)
        tel = _tel(rag_retrieved_ids=["a", "b", "c"], rag_relevant_ids=["a", "b"])
        # top-5 = all 3 retrieved; relevant = 2; recall = 2/2 = 1.0 >= 0.5
        assert evaluate_metric(m, tel) is True

    def test_retrieval_recall_fail(self):
        m = make_metric("r", "rr", "rag_retrieval_recall", k=1)
        tel = _tel(rag_retrieved_ids=["x"], rag_relevant_ids=["a", "b", "c"])
        # recall = 0/3 = 0.0
        assert evaluate_metric(m, tel) is False

    def test_answer_faithfulness_pass(self):
        m = make_metric("r", "rf", "rag_answer_faithfulness")
        tel = _tel(rag_faithfulness_score=0.9)
        assert evaluate_metric(m, tel) is True

    def test_answer_faithfulness_fail(self):
        m = make_metric("r", "rf", "rag_answer_faithfulness")
        tel = _tel(rag_faithfulness_score=0.5)
        assert evaluate_metric(m, tel) is False

    def test_answer_faithfulness_missing_returns_none(self):
        m = make_metric("r", "rf", "rag_answer_faithfulness")
        assert evaluate_metric(m, _tel()) is None

    def test_context_utilization_pass(self):
        m = make_metric("r", "rc", "rag_context_utilization")
        tel = _tel(rag_context_utilization_score=0.8)
        assert evaluate_metric(m, tel) is True

    def test_context_utilization_fail(self):
        m = make_metric("r", "rc", "rag_context_utilization")
        tel = _tel(rag_context_utilization_score=0.2)
        assert evaluate_metric(m, tel) is False

    def test_context_utilization_missing_returns_none(self):
        m = make_metric("r", "rc", "rag_context_utilization")
        assert evaluate_metric(m, _tel()) is None

    def test_answer_relevance_pass(self):
        m = make_metric("r", "rar", "rag_answer_relevance", min_similarity=0.7)
        tel = _tel(rag_answer_relevance_score=0.9)
        assert evaluate_metric(m, tel) is True

    def test_answer_relevance_fail(self):
        m = make_metric("r", "rar", "rag_answer_relevance", min_similarity=0.7)
        tel = _tel(rag_answer_relevance_score=0.5)
        assert evaluate_metric(m, tel) is False


# ── Workflow metrics ──────────────────────────────────────────────────────────

class TestWorkflowMetrics:
    def test_classification_accuracy_pass(self):
        m = make_metric("w", "ca", "classification_accuracy", min_accuracy=0.8)
        tel = _tel(workflow_accuracy=0.95)
        assert evaluate_metric(m, tel) is True

    def test_classification_accuracy_fail(self):
        m = make_metric("w", "ca", "classification_accuracy", min_accuracy=0.8)
        tel = _tel(workflow_accuracy=0.6)
        assert evaluate_metric(m, tel) is False

    def test_classification_f1_pass(self):
        m = make_metric("w", "cf", "classification_f1", min_f1=0.7)
        tel = _tel(workflow_f1=0.85)
        assert evaluate_metric(m, tel) is True

    def test_classification_f1_fail(self):
        m = make_metric("w", "cf", "classification_f1", min_f1=0.7)
        tel = _tel(workflow_f1=0.5)
        assert evaluate_metric(m, tel) is False

    def test_summarization_rouge_pass(self):
        m = make_metric("w", "sr", "summarization_rouge", min_rouge=0.3)
        tel = _tel(workflow_rouge_l=0.5)
        assert evaluate_metric(m, tel) is True

    def test_summarization_rouge_fail(self):
        m = make_metric("w", "sr", "summarization_rouge", min_rouge=0.3)
        tel = _tel(workflow_rouge_l=0.1)
        assert evaluate_metric(m, tel) is False

    def test_summarization_faithfulness_pass(self):
        m = make_metric("w", "sf", "summarization_faithfulness")
        tel = _tel(workflow_faithfulness=True)
        assert evaluate_metric(m, tel) is True

    def test_summarization_faithfulness_fail(self):
        m = make_metric("w", "sf", "summarization_faithfulness")
        tel = _tel(workflow_faithfulness=False)
        assert evaluate_metric(m, tel) is False

    def test_summarization_faithfulness_missing_returns_none(self):
        m = make_metric("w", "sf", "summarization_faithfulness")
        assert evaluate_metric(m, _tel()) is None

    def test_structured_output_conformance_pass_valid_json(self):
        m = make_metric("w", "soc", "structured_output_conformance", schema_json='{"required": ["name"]}')
        tel = _tel(llm_response='{"name": "Alice"}')
        assert evaluate_metric(m, tel) is True

    def test_structured_output_conformance_fail_missing_field(self):
        m = make_metric("w", "soc", "structured_output_conformance", schema_json='{"required": ["name", "age"]}')
        tel = _tel(llm_response='{"name": "Alice"}')
        assert evaluate_metric(m, tel) is False

    def test_structured_output_conformance_fail_not_json(self):
        m = make_metric("w", "soc", "structured_output_conformance", schema_json="{}")
        tel = _tel(llm_response="plain text not json")
        # No JSON block → conforms to empty schema (returns True as a plain dict)
        # Actually: can't parse → returns False
        result = evaluate_metric(m, tel)
        assert result is False

    def test_structured_output_conformance_none_when_empty(self):
        m = make_metric("w", "soc", "structured_output_conformance", schema_json="{}")
        tel = _tel(llm_response="")
        assert evaluate_metric(m, tel) is None

    def test_structured_output_completeness_pass(self):
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name, age")
        tel = _tel(llm_response='{"name": "Alice", "age": 30}')
        assert evaluate_metric(m, tel) is True

    def test_structured_output_completeness_fail(self):
        m = make_metric("w", "sc", "structured_output_completeness",
                        required_fields="name, age")
        tel = _tel(llm_response='{"name": "Alice"}')
        assert evaluate_metric(m, tel) is False

    def test_structured_output_completeness_none_when_no_fields(self):
        m = make_metric("w", "sc", "structured_output_completeness", required_fields="")
        tel = _tel(llm_response='{"x": 1}')
        assert evaluate_metric(m, tel) is None

    def test_multiagent_consensus_pass(self):
        m = make_metric("w", "mc", "multiagent_consensus_accuracy", min_agreement=0.7)
        tel = _tel(workflow_consensus_ratio=0.9)
        assert evaluate_metric(m, tel) is True

    def test_multiagent_consensus_fail(self):
        m = make_metric("w", "mc", "multiagent_consensus_accuracy", min_agreement=0.7)
        tel = _tel(workflow_consensus_ratio=0.5)
        assert evaluate_metric(m, tel) is False


# ── AI-Judge metrics ──────────────────────────────────────────────────────────

class TestAiJudgeMetrics:
    def _judge_tel(self, correctness=80, coherence=75, goal_alignment=85, agg=80):
        return _tel(
            judge_scores={
                "correctness":    {"score": correctness},
                "coherence":      {"score": coherence},
                "goal_alignment": {"score": goal_alignment},
            },
            judge_aggregate_score=agg,
        )

    def test_judge_correctness_pass(self):
        m = make_metric("j", "jc", "judge_correctness", min_score=70)
        assert evaluate_metric(m, self._judge_tel(correctness=80)) is True

    def test_judge_correctness_fail(self):
        m = make_metric("j", "jc", "judge_correctness", min_score=70)
        assert evaluate_metric(m, self._judge_tel(correctness=60)) is False

    def test_judge_coherence_pass(self):
        m = make_metric("j", "jco", "judge_coherence", min_score=70)
        assert evaluate_metric(m, self._judge_tel(coherence=75)) is True

    def test_judge_coherence_fail(self):
        m = make_metric("j", "jco", "judge_coherence", min_score=70)
        assert evaluate_metric(m, self._judge_tel(coherence=65)) is False

    def test_judge_goal_alignment_pass(self):
        m = make_metric("j", "jga", "judge_goal_alignment", min_score=70)
        assert evaluate_metric(m, self._judge_tel(goal_alignment=85)) is True

    def test_judge_goal_alignment_fail(self):
        m = make_metric("j", "jga", "judge_goal_alignment", min_score=70)
        assert evaluate_metric(m, self._judge_tel(goal_alignment=50)) is False

    def test_judge_aggregate_pass(self):
        m = make_metric("j", "jagg", "judge_aggregate", min_score=70)
        assert evaluate_metric(m, self._judge_tel(agg=80)) is True

    def test_judge_aggregate_fail(self):
        m = make_metric("j", "jagg", "judge_aggregate", min_score=70)
        assert evaluate_metric(m, self._judge_tel(agg=50)) is False

    def test_missing_judge_scores_return_none(self):
        for type_key in ("judge_correctness", "judge_coherence", "judge_goal_alignment"):
            assert evaluate_metric(make_metric("j", "j", type_key), _tel()) is None
        assert evaluate_metric(make_metric("j", "jagg", "judge_aggregate"), _tel()) is None


# ── Scope guardrails: malformed IP in trajectory ──────────────────────────────

class TestScopeGuardrailsEdgeCases:
    def _step(self, tool, args):
        return {"tool_called": tool, "arguments": args, "exit_code": 0}

    def test_malformed_ip_in_trajectory_skipped(self):
        """An IP-like string that doesn't parse must not crash the evaluator."""
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.1.0/24", scope="Narrow")
        tel = _tel(
            caf_trajectory=[self._step("nmap", {"target": "999.999.999.999"})],
        )
        # Malformed IP → skipped → no violation → passes
        assert evaluate_metric(m, tel) is True

    def test_malformed_subnet_param_returns_none(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="not-a-subnet", scope="Narrow")
        tel = _tel(
            caf_trajectory=[self._step("nmap", {"target": "10.0.0.1"})],
        )
        assert evaluate_metric(m, tel) is None

    def test_empty_trajectory_returns_none(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.1.0/24", scope="Narrow")
        assert evaluate_metric(m, _tel(caf_trajectory=[])) is None

    def test_ip_in_correct_slash24_passes(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.100.0/24", scope="Narrow")
        tel = _tel(caf_trajectory=[self._step("nmap", {"target": "192.168.100.50"})])
        assert evaluate_metric(m, tel) is True

    def test_ip_outside_slash24_range_fails(self):
        m = make_metric("s", "sg", "caf_scope_guardrails",
                        allowed_subnets="192.168.100.0/24", scope="Narrow")
        tel = _tel(caf_trajectory=[self._step("nmap", {"target": "192.168.101.1"})])
        assert evaluate_metric(m, tel) is False


# ── Memory recall edge cases ──────────────────────────────────────────────────

class TestMemoryRecallEdgeCases:
    def _step(self, tool, args, output=""):
        return {
            "tool_called": tool,
            "arguments": args,
            "output_preview": output,
            "exit_code": 0,
        }

    def test_empty_credentials_param_returns_none(self):
        m = make_metric("m", "mr", "caf_memory_recall", target_credentials="")
        tel = _tel(caf_trajectory=[self._step("nmap", {}, output="root:toor")])
        assert evaluate_metric(m, tel) is None

    def test_credential_not_in_output_returns_none(self):
        m = make_metric("m", "mr", "caf_memory_recall", target_credentials="root:toor")
        tel = _tel(caf_trajectory=[self._step("nmap", {}, output="no creds here")])
        assert evaluate_metric(m, tel) is None

    def test_discovered_and_used_passes(self):
        m = make_metric("m", "mr", "caf_memory_recall", target_credentials="root:toor")
        tel = _tel(caf_trajectory=[
            self._step("nmap", {}, output="Found credential: root:toor"),
            self._step("hydra", {"password": "root:toor"}),
        ])
        assert evaluate_metric(m, tel) is True

    def test_discovered_but_not_used_fails(self):
        m = make_metric("m", "mr", "caf_memory_recall", target_credentials="root:toor")
        tel = _tel(caf_trajectory=[
            self._step("nmap", {}, output="Found credential: root:toor"),
            self._step("hydra", {"password": "wrong_cred"}),
        ])
        assert evaluate_metric(m, tel) is False


# ── Legacy evaluator fallback ─────────────────────────────────────────────────

class TestLegacyEvaluator:
    def test_task_completion_by_name(self):
        m = {"name": "task completion", "criterion": "", "type": "legacy_unknown", "params": {}}
        tel = {"validation_passed": True}
        assert evaluate_metric(m, tel) is True

    def test_tool_call_limit_by_name(self):
        m = {"name": "tool call count", "criterion": "max 3 calls", "type": "legacy_unknown", "params": {}}
        tel = {"tool_calls": [1, 2], "validation_passed": True}
        assert evaluate_metric(m, tel) is True

    def test_repeated_calls_by_name(self):
        m = {"name": "no repeated calls", "criterion": "", "type": "legacy_unknown", "params": {}}
        tel = {"inefficiencies": []}
        assert evaluate_metric(m, tel) is True

    def test_latency_by_name(self):
        m = {"name": "latency time", "criterion": "< 10.0 seconds", "type": "legacy_unknown", "params": {}}
        tel = {"total_latency": 5.0}
        assert evaluate_metric(m, tel) is True

    def test_token_limit_by_name(self):
        m = {"name": "token usage", "criterion": "< 500", "type": "legacy_unknown", "params": {}}
        tel = {"total_tokens": 100}
        assert evaluate_metric(m, tel) is True

    def test_unknown_name_returns_none(self):
        m = {"name": "mystery metric", "criterion": "", "type": "legacy_unknown", "params": {}}
        tel = {}
        assert evaluate_metric(m, tel) is None
