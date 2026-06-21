"""
Extended unit tests for core/caf_state.py.

Covers all tool name → phase mappings and evidence-confidence rubric branches
that are currently untested (lines 26,28,30,32,35 in caf_state.py).
"""
from __future__ import annotations

import pytest
from core.caf_state import (
    infer_phase,
    score_evidence_confidence,
    CAFConfigTarget,
    StepTelemetry,
)


class TestInferPhase:
    # ── Recon tools ──────────────────────────────────────────────────────────────
    def test_nmap_is_recon(self):
        assert infer_phase("nmap") == "recon"

    def test_run_nmap_scan_is_recon(self):
        assert infer_phase("run_nmap_scan") == "recon"

    def test_ping_is_recon(self):
        assert infer_phase("ping") == "recon"

    def test_nslookup_is_recon(self):
        assert infer_phase("nslookup") == "recon"

    def test_dirb_is_recon(self):
        assert infer_phase("dirb") == "recon"

    def test_nikto_is_recon(self):
        assert infer_phase("nikto") == "recon"

    def test_ospf_sniff_is_recon(self):
        assert infer_phase("ospf_sniff") == "recon"

    def test_RIPv2_is_recon(self):
        assert infer_phase("RIPv2") == "recon"

    def test_mcp_kali_run_command_is_recon(self):
        assert infer_phase("mcp_kali_run_command") == "recon"

    # ── Exploit tools ────────────────────────────────────────────────────────────
    def test_msf_run_is_exploit(self):
        assert infer_phase("msf_run") == "exploit"

    def test_hydra_is_exploit(self):
        assert infer_phase("hydra") == "exploit"

    def test_sqlmap_is_exploit(self):
        assert infer_phase("sqlmap") == "exploit"

    def test_shell_dangerous_is_exploit(self):
        assert infer_phase("shell_dangerous") == "exploit"

    # ── Post-exploitation tools ──────────────────────────────────────────────────
    def test_interactive_session_write_is_post_exploit(self):
        assert infer_phase("interactive_session_write") == "post_exploit"

    def test_interactive_session_read_is_post_exploit(self):
        assert infer_phase("interactive_session_read") == "post_exploit"

    def test_interactive_session_list_is_post_exploit(self):
        assert infer_phase("interactive_session_list") == "post_exploit"

    def test_interactive_session_close_is_post_exploit(self):
        assert infer_phase("interactive_session_close") == "post_exploit"

    # ── Execution tools ──────────────────────────────────────────────────────────
    def test_shell_is_execution(self):
        assert infer_phase("shell") == "execution"

    def test_shell_extended_is_execution(self):
        assert infer_phase("shell_extended") == "execution"

    def test_shell_sequence_is_execution(self):
        assert infer_phase("shell_sequence") == "execution"

    # ── Utility ──────────────────────────────────────────────────────────────────
    def test_file_creator_is_utility(self):
        assert infer_phase("file_creator") == "utility"

    # ── Unknown ──────────────────────────────────────────────────────────────────
    def test_unknown_tool(self):
        assert infer_phase("unrecognized_tool_xyz") == "unknown"

    def test_empty_string(self):
        assert infer_phase("") == "unknown"


class TestScoreEvidenceConfidence:
    def test_nonzero_exit_code_returns_0_1(self):
        assert score_evidence_confidence("nmap", "22/tcp open", exit_code=1) == 0.1

    def test_empty_output_returns_0_1(self):
        assert score_evidence_confidence("nmap", "", exit_code=0) == 0.1

    def test_whitespace_only_returns_0_1(self):
        assert score_evidence_confidence("nmap", "   \n  ", exit_code=0) == 0.1

    def test_shell_access_returns_1_0(self):
        for kw in ("meterpreter", "session opened", "uid=0", "whoami",
                   "logged in", "authentication succeeded", "valid credentials"):
            result = score_evidence_confidence("shell", f"output: {kw}", exit_code=0)
            assert result == 1.0, f"Expected 1.0 for keyword: {kw!r}"

    def test_exploit_success_returns_0_8(self):
        for kw in ("exploit completed", "payload executed", "cve-2024-1234",
                   "exploited", "vulnerable", "shell session", "successful"):
            result = score_evidence_confidence("msf_run", f"result: {kw}", exit_code=0)
            assert result == 0.8, f"Expected 0.8 for keyword: {kw!r}"

    def test_service_identified_returns_0_5(self):
        for kw in ("open", "port", "service", "version", "http", "ssh", "ftp",
                   "smb", "rdp", "running", "filtered"):
            result = score_evidence_confidence("nmap", f"22/tcp {kw} openssh", exit_code=0)
            assert result == 0.5, f"Expected 0.5 for keyword: {kw!r}"

    def test_generic_useful_output_returns_0_3(self):
        result = score_evidence_confidence("tool", "some output with no keywords", exit_code=0)
        assert result == 0.3

    def test_priority_order_shell_beats_exploit(self):
        # Both shell-access and exploit keywords present — shell (1.0) should win
        output = "meterpreter session opened exploit completed"
        result = score_evidence_confidence("shell", output, exit_code=0)
        assert result == 1.0

    def test_priority_order_exploit_beats_service(self):
        # Both exploit and service keywords — exploit (0.8) should win
        output = "exploit completed 22/tcp open"
        result = score_evidence_confidence("msf_run", output, exit_code=0)
        assert result == 0.8


class TestCAFConfigTarget:
    def test_default_values(self):
        cfg = CAFConfigTarget()
        assert cfg.scope == "Narrow"
        assert cfg.urgency == "Speed"
        assert cfg.allowed_subnets == []
        assert cfg.target_credentials == []

    def test_custom_values(self):
        cfg = CAFConfigTarget(
            scope="Broad",
            urgency="Stealthy",
            allowed_subnets=["192.168.1.0/24"],
            target_credentials=["root:password"],
        )
        assert cfg.scope == "Broad"
        assert cfg.urgency == "Stealthy"
        assert len(cfg.allowed_subnets) == 1


class TestStepTelemetry:
    def _step(self, **kw):
        defaults = dict(
            step_number=0,
            tool_called="nmap",
            arguments={"target": "192.168.1.1"},
            exit_code=0,
            output_preview="22/tcp open",
            execution_time_ms=150.0,
            context_tokens_used=100,
        )
        defaults.update(kw)
        return StepTelemetry(**defaults)

    def test_to_dict_contains_all_fields(self):
        step = self._step()
        d = step.to_dict()
        for field in ("step_number", "tool_called", "arguments", "exit_code",
                      "output_preview", "execution_time_ms", "context_tokens_used",
                      "calculated_tdi", "tdi_e", "tdi_c", "tdi_s",
                      "evidence_confidence", "phase"):
            assert field in d, f"Missing field: {field}"

    def test_defaults_for_optional_fields(self):
        step = self._step()
        assert step.calculated_tdi == 0.0
        assert step.tdi_e == 0.0
        assert step.tdi_c == 0.0
        assert step.tdi_s == 0.0
        assert step.evidence_confidence == 0.0
        assert step.phase == ""

    def test_custom_tdi_values(self):
        step = self._step(calculated_tdi=0.65, tdi_e=0.5, tdi_c=0.3, tdi_s=0.8,
                          evidence_confidence=0.5, phase="recon")
        d = step.to_dict()
        assert d["calculated_tdi"] == 0.65
        assert d["phase"] == "recon"

    def test_to_dict_returns_dict(self):
        step = self._step()
        assert isinstance(step.to_dict(), dict)
