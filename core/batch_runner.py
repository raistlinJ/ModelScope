"""
Batch evaluation runner — queue and execute multiple model/scenario/prompt
combinations unattended, producing a consolidated results report.
"""
from __future__ import annotations

import csv
import io
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from config.metrics import evaluate_metric
from core.environment import LocalEnvironment
from core.evaluator import run_evaluation


@dataclass
class BatchJob:
    scenario_key: str
    model_config: dict
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    prompt_variant: Optional[dict] = None
    priority: int = 5
    status: str = "queued"
    result: Optional[dict] = None
    error: Optional[str] = None
    job_label: str = ""

    def __post_init__(self):
        if not self.job_label:
            model = self.model_config.get("selected_model", "?")
            if isinstance(model, str):
                model = model.split("/")[-1]
            self.job_label = f"{self.scenario_key[:20]} | {model[:20]}"


@dataclass
class BatchReport:
    total_jobs: int = 0
    completed: int = 0
    failed: int = 0
    results: list = field(default_factory=list)
    summary_rows: list = field(default_factory=list)
    duration_seconds: float = 0.0


class BatchRunner:
    def __init__(self, max_parallel: int = 1, output_dir: str = "./batch_results"):
        self.queue: list[BatchJob] = []
        self.max_parallel = max_parallel
        self.output_dir = output_dir

    def enqueue(self, job: BatchJob) -> str:
        self.queue.append(job)
        self.queue.sort(key=lambda j: j.priority)
        return job.job_id

    def clear(self) -> None:
        self.queue = []

    def get_jobs(self) -> list[BatchJob]:
        return list(self.queue)

    def _build_config(self, job: BatchJob) -> dict:
        # Scenario concept removed - use empty defaults
        mc = job.model_config
        config = {
            "backend_type":       mc.get("backend_type", "llama.cpp"),
            "llm_url":            mc.get("llm_url", "http://127.0.0.1:8080"),
            "selected_model":     mc.get("selected_model", ""),
            "context_size":       mc.get("context_size", 4096),
            "mcp_url":            mc.get("mcp_url", ""),
            "mcp_server_url":     mc.get("mcp_server_url", ""),
            "mcp_tools":          mc.get("mcp_tools", {}),
            "mcp_running":        mc.get("mcp_running", False),
            "sys_prompt":         "",
            "user_prompt":        "",
            "validation_command": "",
            "fail_patterns":      [],
            "metrics_matrix":     [],
            "active_scenario":    job.scenario_key,
            "tool_focus":         "",
            "expected_stdout":    "",
            "pre_run_cleanup":    [],
            "cancel_requested_ref": [False],
            "caf_scope":              mc.get("caf_scope", "Narrow"),
            "caf_urgency":            mc.get("caf_urgency", "Speed"),
            "caf_allowed_subnets":    mc.get("caf_allowed_subnets", []),
            "caf_target_credentials": mc.get("caf_target_credentials", []),
        }
        if job.prompt_variant:
            config["sys_prompt"]  = job.prompt_variant.get("sys_prompt",  config["sys_prompt"])
            config["user_prompt"] = job.prompt_variant.get("user_prompt", config["user_prompt"])
        return config

    def _run_single(self, job: BatchJob, on_log: Optional[Callable] = None) -> dict:
        job.status = "running"
        logger = on_log or (lambda *a, **kw: None)
        env = LocalEnvironment()
        try:
            config = self._build_config(job)
            telemetry = run_evaluation(env, config, logger)
            job.result = telemetry
            job.status = "done"
            return telemetry
        except Exception as exc:
            job.error = str(exc)
            job.status = "failed"
            raise
        finally:
            if hasattr(env, "close"):
                env.close()

    def _make_summary_row(self, job: BatchJob) -> dict:
        tel = job.result or {}
        matrix = tel.get("metrics_matrix", [])
        results = [evaluate_metric(m, tel) for m in matrix if m.get("enabled")]
        passed = sum(1 for r in results if r is True)
        failed = sum(1 for r in results if r is False)
        return {
            "job_id":         job.job_id,
            "label":          job.job_label,
            "scenario":       job.scenario_key,
            "model":          job.model_config.get("selected_model", "?"),
            "status":         job.status,
            "latency":        round(tel.get("total_latency", 0.0), 2),
            "total_tokens":   tel.get("total_tokens", 0),
            "passed_metrics": passed,
            "failed_metrics": failed,
            "error":          job.error or "",
        }

    def run(self, env=None, on_log: Optional[Callable] = None) -> BatchReport:
        t0 = time.time()

        if self.max_parallel <= 1:
            for job in self.queue:
                try:
                    self._run_single(job, on_log)
                except Exception:
                    pass
        else:
            with ThreadPoolExecutor(max_workers=self.max_parallel) as executor:
                futures = {executor.submit(self._run_single, job, on_log): job
                           for job in self.queue}
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        pass

        results = [j.result for j in self.queue if j.result]
        summary_rows = [self._make_summary_row(j) for j in self.queue]
        completed = sum(1 for j in self.queue if j.status == "done")
        failed    = sum(1 for j in self.queue if j.status == "failed")

        return BatchReport(
            total_jobs=len(self.queue),
            completed=completed,
            failed=failed,
            results=results,
            summary_rows=summary_rows,
            duration_seconds=round(time.time() - t0, 2),
        )

    def export_csv(self, report: BatchReport) -> str:
        if not report.summary_rows:
            return ""
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=list(report.summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(report.summary_rows)
        return buf.getvalue()
