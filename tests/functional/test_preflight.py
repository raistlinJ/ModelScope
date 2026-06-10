"""
Tests for core.preflight — both layers of the pre-flight engine.

These tests verify that the pre-flight checks themselves are correct,
using the same known-telemetry approach that the engine uses.
"""
import os
import pytest
from unittest.mock import patch, MagicMock

from core.preflight import (
    TestResult,
    REQUIRED_STATE_KEYS,
    REQUIRED_CONFIG_KEYS,
    build_config_from_state,
    check_state_completeness,
    check_config_completeness,
    check_config_no_mutation,
    check_backend_connectivity,
    check_timeout_handling,
    check_filesystem_access,
    check_mcp_script_path,
    check_metrics_configuration,
    check_known_good_telemetry,
    check_known_bad_telemetry,
    check_validation_logic_alignment,
    check_llm_smoke,
    run_platform_layer,
    run_evaluation_layer,
    run_all,
    _make_good_telemetry,
    _make_bad_telemetry,
)
from config.scenarios import SCENARIOS, DEFAULT_SCENARIO
from config.metrics import make_metric


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _full_state() -> dict:
    """Minimal but complete session_state dict."""
    from core.state import _DEFAULTS
    return dict(_DEFAULTS)


def _scenario_metrics(scenario_name: str = DEFAULT_SCENARIO) -> list[dict]:
    return list(SCENARIOS[scenario_name]["default_metrics"])


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1 — Platform Regression
# ─────────────────────────────────────────────────────────────────────────────

class TestStateCompleteness:
    def test_complete_state_passes(self):
        r = check_state_completeness(_full_state())
        assert r.passed is True
        assert r.layer == "platform"

    def test_missing_key_fails(self):
        state = _full_state()
        del state["backend_type"]
        r = check_state_completeness(state)
        assert r.passed is False
        assert "backend_type" in r.detail

    def test_multiple_missing_keys_listed(self):
        state = _full_state()
        del state["backend_type"]
        del state["llm_url"]
        r = check_state_completeness(state)
        assert "backend_type" in r.detail
        assert "llm_url" in r.detail


class TestConfigCompleteness:
    def test_all_keys_present(self):
        r = check_config_completeness(_full_state())
        assert r.passed is True

    def test_result_reports_duration(self):
        r = check_config_completeness(_full_state())
        assert r.duration_ms >= 0

    def test_build_config_has_required_keys(self):
        config = build_config_from_state(_full_state())
        for key in REQUIRED_CONFIG_KEYS:
            assert key in config, f"Missing key: {key}"


class TestConfigNoMutation:
    def test_no_mutation(self):
        r = check_config_no_mutation(_full_state())
        assert r.passed is True

    def test_config_dict_values_unchanged(self):
        state  = _full_state()
        config = build_config_from_state(state)
        orig   = dict(config)
        # Simulate evaluator read operations
        _ = config.get("backend_type")
        _ = config.get("llm_url", "").rstrip("/")
        _ = list(config.get("fail_patterns", []))
        # No keys should be changed
        for k, v in orig.items():
            assert config[k] == v, f"Key '{k}' was mutated"


class TestBackendConnectivity:
    @patch("requests.get")
    def test_reachable_backend_passes(self, mock_get):
        mock_get.return_value.ok = True
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "http://localhost:8080"
        r = check_backend_connectivity(state)
        assert r.passed is True

    @patch("requests.get", side_effect=Exception("refused"))
    def test_unreachable_backend_returns_none(self, _):
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "http://localhost:8080"
        r = check_backend_connectivity(state)
        # Unreachable = informational (None), not a hard failure
        assert r.passed is None or r.passed is False

    def test_no_url_skips(self):
        state = _full_state()
        state["llm_url"] = ""
        r = check_backend_connectivity(state)
        assert r.passed is None
        assert "skipped" in r.detail.lower()

    def test_bare_hostname_scheme_normalized(self):
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "localhost:8080"
        with patch("requests.get") as mock_get:
            mock_get.return_value.ok = True
            r = check_backend_connectivity(state)
        # Should have called with http:// scheme
        call_url = mock_get.call_args[0][0]
        assert call_url.startswith("http://")


class TestTimeoutHandling:
    def test_unreachable_port_returns_true(self):
        """Pre-flight check passes when timeout is handled gracefully."""
        r = check_timeout_handling()
        assert r.passed is True
        assert r.duration_ms < 4000  # must complete quickly


class TestFilesystemAccess:
    def test_write_read_delete_pass(self):
        r = check_filesystem_access()
        assert r.passed is True

    def test_result_has_path_in_detail(self):
        r = check_filesystem_access()
        assert "preflight_" in r.detail or "tmp" in r.detail.lower()


class TestMcpScriptPath:
    def test_existing_file_passes(self, tmp_path):
        script = tmp_path / "index.js"
        script.write_text("// mcp")
        state = _full_state()
        state["mcp_url"] = str(script)
        r = check_mcp_script_path(state)
        assert r.passed is True

    def test_missing_file_fails(self, tmp_path):
        state = _full_state()
        state["mcp_url"] = str(tmp_path / "nonexistent.js")
        r = check_mcp_script_path(state)
        assert r.passed is False

    def test_no_path_skips(self):
        state = _full_state()
        state["mcp_url"] = ""
        r = check_mcp_script_path(state)
        assert r.passed is None
        assert "skipped" in r.detail.lower()


class TestRunPlatformLayer:
    def test_returns_all_checks(self):
        results = run_platform_layer(_full_state())
        assert len(results) == 7
        assert all(isinstance(r, TestResult) for r in results)
        assert all(r.layer == "platform" for r in results)

    def test_all_checks_have_names(self):
        for r in run_platform_layer(_full_state()):
            assert r.name.strip()


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2 — Evaluation Integrity
# ─────────────────────────────────────────────────────────────────────────────

class TestMetricsConfiguration:
    def test_valid_scenario_metrics_pass(self):
        r = check_metrics_configuration(_scenario_metrics())
        assert r.passed is True

    def test_unknown_type_fails(self):
        metrics = [make_metric("m1", "Bad", "does_not_exist")]
        r = check_metrics_configuration(metrics)
        assert r.passed is False
        assert "does_not_exist" in r.detail

    def test_empty_string_param_fails(self):
        metrics = [make_metric("m1", "Empty", "tool_called", tool_name="")]
        r = check_metrics_configuration(metrics)
        assert r.passed is False

    def test_empty_metrics_list_skips(self):
        r = check_metrics_configuration([])
        assert r.passed is None


class TestKnownGoodTelemetry:
    def test_scenario1_all_pass(self):
        metrics = _scenario_metrics("Scenario 1 – File Creation")
        r = check_known_good_telemetry(metrics, "Scenario 1 – File Creation")
        assert r.passed is True, f"Failed: {r.detail}"

    def test_scenario2_all_pass(self):
        metrics = _scenario_metrics("Scenario 2 – Network Scan")
        r = check_known_good_telemetry(metrics, "Scenario 2 – Network Scan")
        assert r.passed is True, f"Failed: {r.detail}"

    def test_empty_metrics_skips(self):
        r = check_known_good_telemetry([], "")
        assert r.passed is None

    def test_good_telemetry_structure(self):
        tel = _make_good_telemetry("Scenario 1 – File Creation")
        assert tel["validation_passed"] is True
        assert len(tel["tool_calls"]) == 1
        assert tel["tool_calls"][0]["tool"] == "file_creator"
        assert tel["inefficiencies"] == []


class TestKnownBadTelemetry:
    def test_scenario1_gate_metrics_fail(self):
        metrics = _scenario_metrics("Scenario 1 – File Creation")
        r = check_known_bad_telemetry(metrics)
        assert r.passed is True, f"Unexpected: {r.detail}"

    def test_no_gate_metrics_skips(self):
        metrics = [make_metric("m1", "L", "latency", max_seconds=30)]
        r = check_known_bad_telemetry(metrics)
        assert r.passed is None

    def test_bad_telemetry_validation_failed(self):
        tel = _make_bad_telemetry()
        assert tel["validation_passed"] is False
        assert tel["tool_calls"] == []
        assert tel["run_aborted"] is True

    def test_task_completion_fails_on_bad(self):
        from config.metrics import evaluate_metric
        m   = make_metric("m1", "TC", "task_completion")
        tel = _make_bad_telemetry()
        assert evaluate_metric(m, tel) is False

    def test_tool_called_fails_on_bad(self):
        from config.metrics import evaluate_metric
        m   = make_metric("m1", "TC", "tool_called", tool_name="file_creator")
        tel = _make_bad_telemetry()
        assert evaluate_metric(m, tel) is False


class TestValidationLogicAlignment:
    def test_passes(self):
        r = check_validation_logic_alignment()
        assert r.passed is True, f"Unexpected failure: {r.detail}"

    def test_has_meaningful_detail(self):
        r = check_validation_logic_alignment()
        assert len(r.detail) > 10


class TestLlmSmoke:
    def test_skips_when_backend_down(self):
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "http://127.0.0.1:59997"  # nothing running here
        with patch("core.preflight.requests.get", side_effect=Exception("unreachable")):
            r = check_llm_smoke(state, timeout_s=5)
        assert r.passed is None
        assert "skipped" in r.detail.lower()

    def test_skips_ollama_when_down(self):
        state = _full_state()
        state["backend_type"] = "ollama"
        state["llm_url"] = "http://127.0.0.1:59996"
        with patch("core.preflight.requests.get", side_effect=Exception("unreachable")):
            r = check_llm_smoke(state, timeout_s=5)
        assert r.passed is None

    @patch("core.evaluator.run_evaluation")
    @patch("core.preflight.requests.get")
    def test_passes_when_llm_succeeds(self, mock_get, mock_run):
        mock_get.return_value.ok = True
        mock_run.return_value = {
            "validation_passed": True,
            "run_aborted": False,
            "llm_rounds": 1,
            "tool_calls": [{"tool": "file_creator", "args": {}, "exit_code": 0}],
        }
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "http://localhost:8080"

        with patch("core.llama_server.is_running", return_value=True):
            r = check_llm_smoke(state, timeout_s=10)

        assert r.passed is True
        assert "passed" in r.detail.lower()

    @patch("core.evaluator.run_evaluation")
    @patch("core.preflight.requests.get")
    def test_fails_when_validation_fails(self, mock_get, mock_run):
        mock_get.return_value.ok = True
        mock_run.return_value = {
            "validation_passed": False,
            "run_aborted": False,
            "llm_rounds": 2,
            "tool_calls": [],
            "validation_stdout": "",
        }
        state = _full_state()
        state["backend_type"] = "llama.cpp"
        state["llm_url"] = "http://localhost:8080"

        with patch("core.llama_server.is_running", return_value=True):
            r = check_llm_smoke(state, timeout_s=10)

        assert r.passed is False


class TestRunEvaluationLayer:
    def test_returns_four_checks_without_smoke(self):
        results = run_evaluation_layer(_full_state(), include_llm_smoke=False)
        assert len(results) == 4
        assert all(r.layer == "evaluation" for r in results)

    def test_returns_five_checks_with_smoke_unreachable(self):
        state = _full_state()
        state["llm_url"] = "http://127.0.0.1:59995"
        with patch("core.preflight.requests.get", side_effect=Exception("down")):
            results = run_evaluation_layer(state, include_llm_smoke=True)
        assert len(results) == 5


class TestRunAll:
    def test_total_checks_without_smoke(self):
        # 7 platform + 4 evaluation = 11
        results = run_all(_full_state(), include_llm_smoke=False)
        assert len(results) == 11

    def test_layers_interleaved_correctly(self):
        results = run_all(_full_state(), include_llm_smoke=False)
        layers = [r.layer for r in results]
        platform_done = False
        for layer in layers:
            if layer == "evaluation":
                platform_done = True
            if platform_done and layer == "platform":
                pytest.fail("Platform check appeared after evaluation checks")

    def test_all_results_have_names_and_details(self):
        for r in run_all(_full_state(), include_llm_smoke=False):
            assert r.name.strip(), f"Empty name in result: {r}"
            assert r.detail.strip(), f"Empty detail in result: {r}"


# ── build_config_from_state ───────────────────────────────────────────────────

class TestBuildConfigFromState:
    def test_url_passed_through_verbatim(self):
        # build_config_from_state mirrors execute_tab — no scheme normalization
        # (normalization happens inside is_running / fetch_ollama_models / etc.)
        state = _full_state()
        state["llm_url"] = "http://localhost:8080"
        config = build_config_from_state(state)
        assert config["llm_url"] == "http://localhost:8080"

    def test_fail_patterns_is_copy(self):
        state = _full_state()
        state["fail_patterns"] = ["error"]
        config = build_config_from_state(state)
        config["fail_patterns"].append("injected")
        assert "injected" not in state["fail_patterns"]

    def test_cancel_ref_is_fresh_list(self):
        state = _full_state()
        config = build_config_from_state(state)
        assert config["cancel_requested_ref"] == [False]

    def test_pre_run_cleanup_from_scenario(self):
        state = _full_state()
        state["active_scenario"] = "Scenario 1 – File Creation"
        config = build_config_from_state(state)
        assert "/tmp/test" in config["pre_run_cleanup"]
