"""
CAF evaluation state models.

Tracks CAF's UI-driven runtime configuration (Scope/Urgency) and per-step
telemetry used by the 4-Pillar metrics evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


# ── Phase inference ────────────────────────────────────────────────────────────

_RECON_TOOLS     = frozenset({"nmap", "run_nmap_scan", "ping", "nslookup", "dirb",
                               "nikto", "ospf_sniff", "RIPv2", "mcp_kali_run_command"})
_EXPLOIT_TOOLS   = frozenset({"msf_run", "hydra", "sqlmap", "shell_dangerous"})
_POST_EXP_TOOLS  = frozenset({"interactive_session_write", "interactive_session_read",
                               "interactive_session_list", "interactive_session_close"})
_EXEC_TOOLS      = frozenset({"shell", "shell_extended", "shell_sequence"})


def infer_phase(tool_name: str) -> str:
    """Infer the attack phase from the tool name."""
    if tool_name in _RECON_TOOLS:
        return "recon"
    if tool_name in _EXPLOIT_TOOLS:
        return "exploit"
    if tool_name in _POST_EXP_TOOLS:
        return "post_exploit"
    if tool_name in _EXEC_TOOLS:
        return "execution"
    if tool_name == "file_creator":
        return "utility"
    return "unknown"


# ── Evidence confidence rubric (PENTESTGPT V2 Table 12) ───────────────────────

def score_evidence_confidence(tool_name: str, output: str, exit_code: int) -> float:
    """
    Score evidence confidence (0–1) from tool output.

    Rubric (descending):
      1.0 — shell access obtained / valid credentials in output
      0.8 — CVE confirmed / exploit succeeded
      0.5 — open service port identified
      0.3 — tool ran and produced useful output (no errors)
      0.1 — error or empty output
    """
    if exit_code != 0:
        return 0.1
    out_lower = output.lower()
    if not out_lower.strip():
        return 0.1

    # Shell / credential acquisition
    if any(kw in out_lower for kw in (
        "meterpreter", "session opened", "shell >", "$ ", "# ",
        "logged in", "authentication succeeded", "valid credentials",
        "id=", "uid=", "whoami",
    )):
        return 1.0

    # Exploit success / CVE confirmed
    if any(kw in out_lower for kw in (
        "exploit completed", "payload executed", "shell session",
        "cve-", "exploited", "vulnerable", "successful",
    )):
        return 0.8

    # Service / version identified (nmap-style)
    if any(kw in out_lower for kw in (
        "open", "filtered", "port", "service", "version",
        "http", "ssh", "ftp", "smb", "rdp", "running",
    )):
        return 0.5

    # Tool produced useful output but nothing conclusive
    return 0.3


@dataclass
class CAFConfigTarget:
    """Mirrors CAF's Scope and Urgency prompt controls plus network boundaries."""
    scope: str = "Narrow"            # "Broad" (Discovery) or "Narrow" (Exploitation)
    urgency: str = "Speed"           # "Stealthy" or "Speed"
    allowed_subnets: List[str] = field(default_factory=list)
    target_credentials: List[str] = field(default_factory=list)


@dataclass
class StepTelemetry:
    """Per-tool-call snapshot consumed by 4-Pillar metric evaluators."""
    step_number: int
    tool_called: str
    arguments: Dict[str, Any]
    exit_code: int
    output_preview: str
    execution_time_ms: float
    context_tokens_used: int
    calculated_tdi: float = 0.0
    # TDI dimension breakdown (3-component E/C/S formula)
    tdi_e: float = 0.0   # evidence confidence (higher = easier)
    tdi_c: float = 0.0   # context load ratio (higher = harder)
    tdi_s: float = 0.0   # recent success rate (higher = easier)
    evidence_confidence: float = 0.0
    phase: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
