"""
Second pass at coverage gaps:
  - config/metrics.py: Speed violation in tempo, session efficiency tool,
    Broad scope guardrail bypass, structured output JSON extraction fallback,
    invalid schema_json branch, completeness empty-response and non-dict
  - core/batch_runner.py: get_jobs(), prompt_variant, env.close(), parallel exception
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


# ── BatchRunner: remaining uncovered lines ────────────────────────────────────

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

class TestBatchRunnerGetJobs:
    def test_get_jobs_returns_copy(self):
        from core.batch_runner import BatchRunner, BatchJob
        runner = BatchRunner()
        job = BatchJob("Scenario 1 – File Creation", {"selected_model": "m1"})
        runner.enqueue(job)
        jobs = runner.get_jobs()
        assert len(jobs) == 1
        assert jobs[0] is job
        # Modifying the returned list does not affect the queue
        jobs.clear()
        assert len(runner.queue) == 1


class TestBatchRunnerPromptVariant:
    def test_prompt_variant_overrides_sys_and_user(self):
        from core.batch_runner import BatchRunner, BatchJob
        runner = BatchRunner()
        job = BatchJob(
            "Scenario 1 – File Creation",
            {"selected_model": "m1", "backend_type": "llama.cpp",
             "llm_url": "http://localhost:8080", "context_size": 4096,
             "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
            prompt_variant={"sys_prompt": "CUSTOM SYS", "user_prompt": "CUSTOM USER"},
        )
        config = runner._build_config(job)
        assert config["sys_prompt"] == "CUSTOM SYS"
        assert config["user_prompt"] == "CUSTOM USER"

    def test_prompt_variant_partial_override(self):
        from core.batch_runner import BatchRunner, BatchJob
        runner = BatchRunner()
        job = BatchJob(
            "Scenario 1 – File Creation",
            {"selected_model": "m1", "backend_type": "llama.cpp",
             "llm_url": "http://localhost:8080", "context_size": 4096,
             "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
            prompt_variant={"sys_prompt": "ONLY SYS OVERRIDDEN"},
        )
        config = runner._build_config(job)
        assert config["sys_prompt"] == "ONLY SYS OVERRIDDEN"
        # user_prompt falls back to scenario default (not the variant)
        assert config["user_prompt"] != "ONLY SYS OVERRIDDEN"


class TestBatchRunnerEnvClose:
    """Cover env.close() call in _run_single (line 116).

    LocalEnvironment has no .close() method; the batch runner uses hasattr().
    We test by injecting a mock env that has a close() attribute via
    patching the LocalEnvironment constructor to return a mock.
    """

    @patch("core.batch_runner.run_evaluation")
    @patch("core.batch_runner.LocalEnvironment")
    def test_env_close_called_after_run(self, mock_env_cls, mock_eval):
        from core.batch_runner import BatchRunner, BatchJob
        fake_tel = {
            "run_timestamp": "2025-01-01", "run_scenario": "s", "run_model": "m",
            "run_backend": "llama.cpp", "run_tool_focus": "", "total_latency": 0.1,
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
            "tokens_per_second": 0, "llm_rounds": 1, "tool_calls": [],
            "validation_stdout": "", "validation_stderr": "", "validation_exit_code": 0,
            "validation_passed": True, "inefficiencies": [], "llm_response": "",
            "run_aborted": False, "metrics_matrix": [], "caf_trajectory": [], "caf_config": {},
        }
        mock_eval.return_value = fake_tel
        fake_env = MagicMock()
        mock_env_cls.return_value = fake_env

        runner = BatchRunner()
        job = BatchJob(
            "Scenario 1 – File Creation",
            {"selected_model": "m1", "backend_type": "llama.cpp",
             "llm_url": "http://localhost:8080", "context_size": 4096,
             "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
        )
        runner.enqueue(job)
        runner.run()
        fake_env.close.assert_called_once()


class TestBatchRunnerParallelException:
    """Cover the parallel executor exception handler (lines 153-154)."""

    @patch("core.batch_runner.run_evaluation", side_effect=RuntimeError("worker crash"))
    def test_parallel_exception_does_not_propagate(self, mock_eval):
        from core.batch_runner import BatchRunner, BatchJob
        runner = BatchRunner(max_parallel=2)
        for i in range(2):
            runner.enqueue(BatchJob(
                "Scenario 1 – File Creation",
                {"selected_model": f"m{i}", "backend_type": "llama.cpp",
                 "llm_url": "http://localhost:8080", "context_size": 4096,
                 "mcp_url": "", "mcp_tools": {}, "mcp_running": False},
            ))
        report = runner.run()
        assert report.total_jobs == 2
        assert report.failed == 2
        assert report.completed == 0
