"""
Model comparison mode — run the same scenario across multiple models
and produce a side-by-side metrics table.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from config.metrics import evaluate_metric
from core.evaluator import run_evaluation


@dataclass
class ComparisonConfig:
    scenario_key: str
    models: list[dict]
    sys_prompt: str = ""
    user_prompt: str = ""
    validation_command: str = ""
    fail_patterns: list = field(default_factory=list)
    metrics_matrix: list = field(default_factory=list)
    comparison_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


@dataclass
class ComparisonResult:
    comparison_id: str
    scenario_key: str
    model_results: list[dict] = field(default_factory=list)
    metric_table: list[dict] = field(default_factory=list)
    winner: str = ""
    summary: dict = field(default_factory=dict)


def build_metric_table(
    model_labels: list[str],
    results: list[dict],
    matrix: list[dict],
) -> list[dict]:
    rows = []
    for m in matrix:
        if not m.get("enabled"):
            continue
        scores: dict = {}
        for label, tel in zip(model_labels, results):
            scores[label] = evaluate_metric(m, tel)
        rows.append({
            "metric_id":   m.get("id", ""),
            "metric_name": m.get("name", ""),
            "metric_type": m.get("type", ""),
            "scores":      scores,
        })
    return rows


def run_comparison(
    config: ComparisonConfig,
    env,
    on_log: Optional[Callable] = None,
) -> ComparisonResult:
    logger = on_log or (lambda _: None)
    model_results: list[dict] = []
    model_labels: list[str] = []

    for model_cfg in config.models:
        label = model_cfg.get("label") or model_cfg.get("selected_model", "?")
        model_labels.append(label)
        logger(f"[COMPARE] Running model: {label}")

        eval_config = {
            "backend_type":       model_cfg.get("backend_type", "llama.cpp"),
            "llm_url":            model_cfg.get("llm_url", "http://127.0.0.1:8080"),
            "selected_model":     model_cfg.get("selected_model", ""),
            "context_size":       model_cfg.get("context_size", 4096),
            "sys_prompt":         config.sys_prompt,
            "user_prompt":        config.user_prompt,
            "mcp_url":            model_cfg.get("mcp_url", ""),
            "mcp_server_url":     model_cfg.get("mcp_server_url", ""),
            "mcp_tools":          model_cfg.get("mcp_tools", {}),
            "mcp_running":        model_cfg.get("mcp_running", False),
            "validation_command": config.validation_command,
            "fail_patterns":      list(config.fail_patterns),
            "metrics_matrix":     list(config.metrics_matrix),
            "active_scenario":    config.scenario_key,
            "tool_focus":         "",
            "expected_stdout":    "",
            "pre_run_cleanup":    [],
            "cancel_requested_ref": [False],
            "caf_scope":          "Narrow",
            "caf_urgency":        "Speed",
            "caf_allowed_subnets": [],
            "caf_target_credentials": [],
        }

        try:
            tel = run_evaluation(env, eval_config, logger)
        except Exception as exc:
            logger(f"[COMPARE ERROR] {label}: {exc}")
            tel = {"run_aborted": True, "error": str(exc)}

        model_results.append(tel)

    metric_table = build_metric_table(model_labels, model_results, config.metrics_matrix)

    summary: dict = {}
    for label, tel in zip(model_labels, model_results):
        enabled = [m for m in config.metrics_matrix if m.get("enabled")]
        results_list = [evaluate_metric(m, tel) for m in enabled]
        passed = sum(1 for r in results_list if r is True)
        failed = sum(1 for r in results_list if r is False)
        na     = sum(1 for r in results_list if r is None)
        total  = passed + failed
        rate   = passed / total if total else 0.0
        summary[label] = {"passed": passed, "failed": failed, "na": na, "pass_rate": rate}

    winner = max(summary, key=lambda k: summary[k]["pass_rate"]) if summary else ""

    return ComparisonResult(
        comparison_id=config.comparison_id,
        scenario_key=config.scenario_key,
        model_results=model_results,
        metric_table=metric_table,
        winner=winner,
        summary=summary,
    )
