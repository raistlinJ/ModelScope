"""
Batch Evaluation tab — queue multiple model/scenario combinations and run
them unattended, then inspect the consolidated results report.
"""
from __future__ import annotations

import json
import time
import uuid

import streamlit as st

from config.defaults import LLAMA_CPP_DEFAULT_URL
from core.batch_runner import BatchJob, BatchRunner
from core.environment import LocalEnvironment


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_runner() -> BatchRunner:
    """Return the persistent BatchRunner, creating it on first call."""
    if "batch_runner" not in st.session_state:
        st.session_state["batch_runner"] = BatchRunner(max_parallel=1)
    return st.session_state["batch_runner"]


def _status_colour(status: str) -> str:
    return {
        "queued":  "#8b949e",
        "running": "#f0883e",
        "done":    "#3fb950",
        "failed":  "#f85149",
    }.get(status, "#8b949e")


def _status_pill(text: str, status: str) -> str:
    colour = _status_colour(status)
    # Pill-style with transparent fill matching the semantic palette
    _bg_map = {
        "#3fb950": "rgba(63,185,80,0.18)",
        "#f85149": "rgba(248,81,73,0.18)",
        "#f0883e": "rgba(240,136,62,0.18)",
        "#8b949e": "rgba(139,148,158,0.14)",
    }
    bg = _bg_map.get(colour, "rgba(139,148,158,0.14)")
    return (
        f'<span style="background:{bg};color:{colour};padding:3px 11px;'
        f'border-radius:999px;font-size:0.70rem;font-weight:700;'
        f'border:1px solid {colour}40;display:inline-block">{text}</span>'
    )


# ── queue display ─────────────────────────────────────────────────────────────

def _render_queue(queue: list[dict]) -> None:
    """Render queued jobs table with per-row Remove buttons."""
    if not queue:
        st.info("Queue is empty — add jobs above.")
        return

    header = st.columns([1, 3, 3, 3, 2, 1])
    for col, label in zip(header, ["Priority", "Label", "Scenario", "Model", "Status", ""]):
        col.markdown(f"**{label}**")
    st.divider()

    to_remove: int | None = None
    for i, job in enumerate(queue):
        cols = st.columns([1, 3, 3, 3, 2, 1])
        cols[0].write(job["priority"])
        cols[1].write(job.get("job_label") or job["job_id"])
        cols[2].write(job["scenario_key"][:28])
        cols[3].write(str(job.get("model_label", "?"))[:24])
        status = job.get("status", "queued")
        cols[4].markdown(_status_pill(status, status), unsafe_allow_html=True)
        if cols[5].button("✕", key=f"batch_rm_{job['_uid']}", help="Remove this job"):
            to_remove = i

    if to_remove is not None:
        queue.pop(to_remove)
        st.session_state["batch_queue"] = queue
        st.rerun()


# ── results display ───────────────────────────────────────────────────────────

def _render_results(report_data: dict) -> None:
    """Render results table and per-job expandable telemetry."""
    rows = report_data.get("summary_rows", [])
    if not rows:
        st.info("No results recorded.")
        return

    # Table header
    h = st.columns([3, 3, 3, 2, 2, 2, 1, 1])
    for col, label in zip(h, ["Label", "Scenario", "Model", "Status",
                               "Latency (s)", "Tokens", "Passed", "Failed"]):
        col.markdown(f"**{label}**")
    st.divider()

    for row in rows:
        c = st.columns([3, 3, 3, 2, 2, 2, 1, 1])
        c[0].write(row.get("label") or row.get("job_id", "?"))
        c[1].write(str(row.get("scenario", ""))[:24])
        model_raw = str(row.get("model", "?"))
        c[2].write(model_raw.split("/")[-1][:24])
        status = row.get("status", "?")
        c[3].markdown(_status_pill(status, status), unsafe_allow_html=True)
        c[4].write(f"{row.get('latency', 0.0):.2f}")
        c[5].write(row.get("total_tokens", 0))
        c[6].write(row.get("passed_metrics", 0))
        c[7].write(row.get("failed_metrics", 0))

    st.divider()

    # Per-job expandable telemetry
    for row in rows:
        label = row.get("label") or row.get("job_id", "Job")
        with st.expander(f"Details — {label}"):
            if row.get("error"):
                st.error(f"Error: {row['error']}")
            telemetry = report_data.get("telemetry_map", {}).get(row.get("job_id", ""))
            if telemetry:
                st.json({k: v for k, v in telemetry.items() if k != "metrics_matrix"})
            else:
                st.write("No detailed telemetry available.")


# ── main render ───────────────────────────────────────────────────────────────

def render() -> None:
    st.header("Batch Evaluation")
    st.caption(
        "Queue multiple model / scenario combinations and run them unattended. "
        "Results are collected into a consolidated report when the batch completes."
    )

    if st.session_state.get("target_env_type") == "remote (SSH)":
        st.warning(
            "⚠️ Batch runs always execute in the **local** environment — "
            "SSH mode is not supported for batch evaluation."
        )

    runner: BatchRunner = _get_runner()
    st.session_state.setdefault("batch_queue", [])
    queue: list[dict] = st.session_state["batch_queue"]

    # ── Add Job section ────────────────────────────────────────────────────────
    with st.expander("Add Job", expanded=not bool(queue)):
        col_left, col_right = st.columns(2)

        with col_left:
            model_label = st.text_input(
                "Model label",
                value="Llama-3.1-8B",
                key="_bq_model_label",
                help="Human-readable name shown in the results table",
            )
            backend = st.selectbox(
                "Backend",
                options=["llama.cpp", "ollama"],
                key="_bq_backend",
                help="Inference backend serving the model",
            )

        with col_right:
            server_url = st.text_input(
                "Server URL",
                value=LLAMA_CPP_DEFAULT_URL,
                key="_bq_server_url",
                help="Base URL of the inference server (e.g. http://127.0.0.1:8080)",
            )
            model_path = st.text_input(
                "Model name / path",
                value="",
                key="_bq_model_path",
                help=(
                    "For llama.cpp: absolute path to the .gguf file. "
                    "For Ollama: model tag (e.g. llama3.1:8b)."
                ),
            )
            priority = st.number_input(
                "Priority",
                min_value=1,
                max_value=10,
                value=5,
                step=1,
                key="_bq_priority",
                help="Job execution priority: lower numbers (1 = highest) run first in the queue",
            )

        use_prompt_override = st.checkbox(
            "Override prompts for this job",
            key="_bq_use_override",
            help="Use custom system/user prompts instead of the scenario defaults for this job",
        )
        sys_prompt_override = ""
        user_prompt_override = ""
        if use_prompt_override:
            ov1, ov2 = st.columns(2)
            with ov1:
                sys_prompt_override = st.text_area(
                    "System prompt override",
                    height=120,
                    key="_bq_sys_prompt",
                )
            with ov2:
                user_prompt_override = st.text_area(
                    "User prompt override",
                    height=120,
                    key="_bq_user_prompt",
                )

        if st.button("Add to Queue", type="primary", key="btn_batch_add"):
            if backend == "llama.cpp" and not model_path.strip():
                st.error("Model path is required when using llama.cpp backend.")
                st.stop()
            model_config = {
                "backend_type":        backend,
                "llm_url":             server_url.strip(),
                "selected_model":      model_label.strip(),
                "selected_model_path": model_path.strip(),
                "context_size":        4096,
                "mcp_url":             "",
                "mcp_server_url":      "",
                "mcp_tools":           {},
                "mcp_running":         False,
            }
            prompt_variant: dict | None = None
            if use_prompt_override and (sys_prompt_override or user_prompt_override):
                prompt_variant = {
                    "sys_prompt":  sys_prompt_override,
                    "user_prompt": user_prompt_override,
                }

            job = BatchJob(
                scenario_key="manual",
                model_config=model_config,
                job_label=model_label.strip() or "",
                prompt_variant=prompt_variant,
                priority=int(priority),
            )
            runner.enqueue(job)

            queue.append({
                "_uid":         str(uuid.uuid4()),
                "job_id":       job.job_id,
                "priority":     job.priority,
                "job_label":    job.job_label,
                "model_label":  model_label.strip(),
                "status":       "queued",
            })
            st.session_state["batch_queue"] = queue
            st.success(f"Job added — queue now has {len(queue)} job(s).")
            st.rerun()

    # ── Queue section ──────────────────────────────────────────────────────────
    st.subheader(f"Queue ({len(queue)} job{'s' if len(queue) != 1 else ''})")

    _render_queue(queue)

    if queue:
        col_run, col_clear, _ = st.columns([2, 1, 5])

        with col_clear:
            if st.button("Clear Queue", use_container_width=True, key="btn_batch_clear"):
                runner.clear()
                st.session_state["batch_queue"] = []
                st.session_state.pop("batch_report", None)
                st.rerun()

        with col_run:
            run_clicked = st.button(
                "Run Batch",
                type="primary",
                use_container_width=True,
                key="btn_batch_run",
                disabled=not bool(queue),
            )

        if run_clicked:
            logs: list[str] = []

            def on_log(msg: str) -> None:
                logs.append(msg)

            # Optimistically mark all jobs as running in the display queue
            for entry in queue:
                entry["status"] = "running"
            st.session_state["batch_queue"] = queue

            with st.spinner(f"Running {len(queue)} job(s)…"):
                env = LocalEnvironment()
                try:
                    report = runner.run(env=env, on_log=on_log)
                finally:
                    if hasattr(env, "close"):
                        env.close()

            # Build telemetry map keyed by job_id for expandable details
            telemetry_map = {
                j.job_id: j.result
                for j in runner.get_jobs()
                if j.result
            }

            # Sync final job statuses back to the display queue
            jobs_by_id = {j.job_id: j for j in runner.get_jobs()}
            for entry in queue:
                jid = entry.get("job_id", "")
                if jid in jobs_by_id:
                    entry["status"] = jobs_by_id[jid].status
            st.session_state["batch_queue"] = queue

            st.session_state["batch_report"] = {
                "total_jobs":       report.total_jobs,
                "completed":        report.completed,
                "failed":           report.failed,
                "duration_seconds": report.duration_seconds,
                "summary_rows":     report.summary_rows,
                "telemetry_map":    telemetry_map,
                "csv":              runner.export_csv(report),
            }
            st.rerun()

    # ── Results section ────────────────────────────────────────────────────────
    report_data: dict | None = st.session_state.get("batch_report")
    if report_data:
        st.divider()
        st.subheader("Batch Results")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total jobs",    report_data["total_jobs"])
        m2.metric("Completed",     report_data["completed"])
        m3.metric("Failed",        report_data["failed"])
        m4.metric("Duration (s)",  f"{report_data['duration_seconds']:.1f}")

        st.divider()

        _render_results(report_data)

        # Download buttons
        dl1, dl2, _ = st.columns([2, 2, 4])
        with dl1:
            csv_data = report_data.get("csv", "")
            if csv_data:
                st.download_button(
                    "Export CSV",
                    data=csv_data,
                    file_name=f"batch_results_{int(time.time())}.csv",
                    mime="text/csv",
                    key="btn_batch_csv",
                )
        with dl2:
            st.download_button(
                "Export JSON",
                data=json.dumps(
                    {k: v for k, v in report_data.items() if k != "telemetry_map"},
                    indent=2,
                    default=str,
                ),
                file_name=f"batch_results_{int(time.time())}.json",
                mime="application/json",
                key="btn_batch_json",
            )
