"""
CAF evaluation state models.

Tracks CAF's UI-driven runtime configuration (Scope/Urgency) and per-step
telemetry used by the 4-Pillar metrics evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List


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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
