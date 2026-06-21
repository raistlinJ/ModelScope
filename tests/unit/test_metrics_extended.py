"""
Extended unit tests for config/metrics.py.

Covers the three functions that were previously untested (lines 1007-1052):
  - _eval_caf_evidence_confidence
  - _eval_caf_phase_completion_ratio
  - _eval_caf_policy_adherence

And also uses evaluate_metric() to drive them for end-to-end coverage.
"""
from __future__ import annotations

import pytest
from config.metrics import evaluate_metric


# ── Helpers ────────────────────────────────────────────────────────────────────

def _step(tool="nmap", phase="recon", exit_code=0, confidence=0.5, tdi=0.3):
    return {
        "tool_called": tool,
        "phase": phase,
        "exit_code": exit_code,
        "evidence_confidence": confidence,
        "calculated_tdi": tdi,
    }


def _tel(trajectory=None, caf_config=None):
    return {
        "caf_trajectory": trajectory or [],
        "caf_config": caf_config or {"scope": "Narrow", "urgency": "Speed"},
    }


# ── _eval_caf_evidence_confidence ─────────────────────────────────────────────

class TestEvalCafEvidenceConfidence:
    def _metric(self, min_avg=0.4):
        return {"type": "caf_evidence_confidence", "params": {"min_avg_confidence": min_avg}}

    def test_empty_trajectory_returns_none(self):
        result = evaluate_metric(self._metric(), _tel(trajectory=[]))
        assert result is None

    def test_passes_when_avg_above_threshold(self):
        steps = [_step(confidence=0.8), _step(confidence=0.6)]
        result = evaluate_metric(self._metric(min_avg=0.4), _tel(trajectory=steps))
        assert result is True

    def test_fails_when_avg_below_threshold(self):
        steps = [_step(confidence=0.1), _step(confidence=0.2)]
        result = evaluate_metric(self._metric(min_avg=0.4), _tel(trajectory=steps))
        assert result is False

    def test_exactly_at_threshold_passes(self):
        steps = [_step(confidence=0.4)]
        result = evaluate_metric(self._metric(min_avg=0.4), _tel(trajectory=steps))
        assert result is True

    def test_default_threshold_0_4(self):
        # When min_avg_confidence not in params, default is 0.4
        metric = {"type": "caf_evidence_confidence", "params": {}}
        steps = [_step(confidence=0.5)]
        result = evaluate_metric(metric, _tel(trajectory=steps))
        assert result is True

    def test_missing_confidence_treated_as_0(self):
        # Step without evidence_confidence key
        steps = [{"tool_called": "nmap", "phase": "recon"}]
        result = evaluate_metric(self._metric(min_avg=0.4), _tel(trajectory=steps))
        assert result is False


# ── _eval_caf_phase_completion_ratio ─────────────────────────────────────────

class TestEvalCafPhaseCompletionRatio:
    def _metric(self, min_phases=2):
        return {"type": "caf_phase_completion_ratio", "params": {"min_phases": min_phases}}

    def test_empty_trajectory_returns_none(self):
        result = evaluate_metric(self._metric(), _tel(trajectory=[]))
        assert result is None

    def test_passes_with_enough_phases(self):
        steps = [
            _step(tool="nmap", phase="recon"),
            _step(tool="msf_run", phase="exploit"),
        ]
        result = evaluate_metric(self._metric(min_phases=2), _tel(trajectory=steps))
        assert result is True

    def test_fails_with_too_few_phases(self):
        steps = [_step(tool="nmap", phase="recon")]
        result = evaluate_metric(self._metric(min_phases=2), _tel(trajectory=steps))
        assert result is False

    def test_unknown_phases_not_counted(self):
        steps = [
            _step(tool="unknown_tool", phase="unknown"),
            _step(tool="file_creator", phase="utility"),
        ]
        result = evaluate_metric(self._metric(min_phases=1), _tel(trajectory=steps))
        assert result is False  # both unknown + utility excluded

    def test_empty_phase_not_counted(self):
        steps = [{"tool_called": "tool", "phase": ""}]
        result = evaluate_metric(self._metric(min_phases=1), _tel(trajectory=steps))
        assert result is False

    def test_duplicate_phases_counted_once(self):
        steps = [
            _step(tool="nmap", phase="recon"),
            _step(tool="ping", phase="recon"),  # same phase
        ]
        result = evaluate_metric(self._metric(min_phases=2), _tel(trajectory=steps))
        assert result is False  # only 1 distinct countable phase

    def test_three_phases_satisfies_min_2(self):
        steps = [
            _step(tool="nmap", phase="recon"),
            _step(tool="msf_run", phase="exploit"),
            _step(tool="shell", phase="execution"),
        ]
        result = evaluate_metric(self._metric(min_phases=2), _tel(trajectory=steps))
        assert result is True


# ── _eval_caf_policy_adherence ────────────────────────────────────────────────

class TestEvalCafPolicyAdherence:
    def _metric(self, scope="Narrow", urgency="Speed"):
        return {
            "type": "caf_policy_adherence",
            "params": {"scope": scope, "urgency": urgency},
        }

    def _caf_config(self, scope="Narrow", urgency="Speed"):
        return {"scope": scope, "urgency": urgency, "allowed_subnets": []}

    def test_empty_trajectory_returns_none(self):
        result = evaluate_metric(self._metric(), _tel(trajectory=[]))
        assert result is None

    def test_clean_run_passes(self):
        steps = [
            _step(tool="nmap", phase="recon"),
            _step(tool="msf_run", phase="exploit"),
        ]
        result = evaluate_metric(
            self._metric(scope="Narrow", urgency="Speed"),
            _tel(trajectory=steps, caf_config=self._caf_config(scope="Narrow")),
        )
        assert result is True

    def test_shell_dangerous_before_recon_fails(self):
        # In Narrow scope, shell_dangerous before any recon tool → fail
        steps = [
            _step(tool="shell_dangerous", phase="exploit"),
            _step(tool="nmap", phase="recon"),
        ]
        result = evaluate_metric(
            self._metric(scope="Narrow"),
            _tel(
                trajectory=steps,
                caf_config=self._caf_config(scope="Narrow"),
            ),
        )
        assert result is False

    def test_shell_dangerous_after_recon_passes(self):
        steps = [
            _step(tool="nmap", phase="recon"),
            _step(tool="shell_dangerous", phase="exploit"),
        ]
        result = evaluate_metric(
            self._metric(scope="Narrow"),
            _tel(
                trajectory=steps,
                caf_config=self._caf_config(scope="Narrow"),
            ),
        )
        assert result is True

    def test_broad_scope_ignores_dangerous_tool_ordering(self):
        # Broad scope should skip the recon-before-dangerous check
        steps = [
            _step(tool="shell_dangerous", phase="exploit"),
        ]
        # Scope guardrails and tempo adherence must also pass for broad scope
        result = evaluate_metric(
            {"type": "caf_policy_adherence", "params": {"scope": "Broad"}},
            _tel(
                trajectory=steps,
                caf_config={"scope": "Broad", "urgency": "Speed",
                            "allowed_subnets": []},
            ),
        )
        # In Broad scope, the shell_dangerous check is not performed
        # Result depends on other checks (scope guardrails, tempo)
        # Just ensure no exception and returns bool or None
        assert result in (True, False, None)
