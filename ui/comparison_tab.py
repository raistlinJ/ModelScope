"""Model comparison UI tab — run one scenario across multiple models side-by-side."""
import json
import streamlit as st
from config.defaults import LLAMA_CPP_DEFAULT_URL
from config.scenarios import SCENARIOS
from core.comparison import ComparisonConfig, run_comparison
from core.environment import LocalEnvironment


def render() -> None:
    st.header("Model Comparison")
    st.caption(
        "Run the same scenario across multiple models and compare results "
        "in a side-by-side metrics table."
    )

    st.session_state.setdefault("comparison_models", [])
    st.session_state.setdefault("comparison_result", None)

    if st.session_state.get("target_env_type") == "remote (SSH)":
        st.warning(
            "⚠️ Comparison runs always execute in the **local** environment — "
            "SSH mode is not supported for model comparison."
        )

    # ── Scenario selector ──────────────────────────────────────────────────────
    scenario_key = st.selectbox(
        "Scenario",
        options=list(SCENARIOS.keys()),
        key="comparison_scenario",
        help="All models will run this same scenario.",
    )
    scenario_data = SCENARIOS.get(scenario_key, {})

    # ── Add Model ──────────────────────────────────────────────────────────────
    with st.expander("Add Model", expanded=not bool(st.session_state.get("comparison_models"))):
        col1, col2 = st.columns(2)
        with col1:
            model_label = st.text_input(
                "Model Label",
                placeholder="e.g. Llama-3.1-8B-Q4",
                key="_cmp_label",
            )
            backend = st.selectbox(
                "Backend",
                options=["llama.cpp", "ollama"],
                key="_cmp_backend",
            )
        with col2:
            server_url = st.text_input(
                "Server URL",
                value=LLAMA_CPP_DEFAULT_URL,
                key="_cmp_url",
            )
            model_name = st.text_input(
                "Model name / path",
                placeholder="e.g. llama-3.1-8b-instruct.gguf",
                key="_cmp_model",
            )
            ctx = st.number_input(
                "Context size",
                min_value=2048, max_value=131072, value=4096,
                key="_cmp_ctx",
            )

        if st.button("Add Model", key="btn_cmp_add"):
            if not model_name.strip():
                st.error("Model name / path is required.")
                st.stop()
            models: list = st.session_state["comparison_models"]
            models.append({
                "label":          model_label or model_name or f"Model {len(models)+1}",
                "backend_type":   backend,
                "llm_url":        server_url,
                "selected_model": model_name,
                "context_size":   ctx,
            })
            st.session_state["comparison_models"] = models
            st.success(f"Model '{model_label or model_name}' added.")
            st.rerun()

    # ── Models list ────────────────────────────────────────────────────────────
    models: list = st.session_state.get("comparison_models", [])
    st.subheader(f"Models to Compare ({len(models)})")

    if len(models) < 2:
        st.warning("Add at least 2 models to enable comparison.")
    else:
        st.success(f"{len(models)} models configured — ready to compare.")

    to_remove = None
    for i, m in enumerate(models):
        mc, md = st.columns([8, 1])
        mc.write(f"**{m['label']}** — {m['backend_type']} @ `{m['llm_url']}`  |  model: `{m['selected_model']}`")
        if md.button("✕", key=f"cmp_rm_{i}"):
            to_remove = i
    if to_remove is not None:
        models.pop(to_remove)
        st.session_state["comparison_models"] = models
        st.rerun()

    # ── Run comparison ─────────────────────────────────────────────────────────
    col_run, col_clear, _ = st.columns([2, 1, 5])
    with col_run:
        if st.button(
            "⚖  Run Comparison",
            type="primary",
            use_container_width=True,
            key="btn_cmp_run",
            disabled=len(models) < 2,
        ):
            config = ComparisonConfig(
                scenario_key=scenario_key,
                models=models,
                sys_prompt=scenario_data.get("system_prompt", ""),
                user_prompt=scenario_data.get("user_prompt", ""),
                validation_command=scenario_data.get("validation_command", ""),
                fail_patterns=list(scenario_data.get("fail_patterns", [])),
                metrics_matrix=list(scenario_data.get("default_metrics", [])),
            )
            logs: list[str] = []
            with st.spinner("Running comparison across all models (sequential)…"):
                env = LocalEnvironment()
                try:
                    result = run_comparison(config, env, on_log=lambda m: logs.append(m))
                finally:
                    if hasattr(env, "close"):
                        env.close()

            st.session_state["comparison_result"] = {
                "comparison_id": result.comparison_id,
                "scenario_key":  result.scenario_key,
                "metric_table":  result.metric_table,
                "winner":        result.winner,
                "summary":       result.summary,
                "model_labels":  [m["label"] for m in models],
            }
            st.success(f"Comparison complete! Winner: **{result.winner}**")
            st.rerun()

    with col_clear:
        if st.button("Clear", use_container_width=True, key="btn_cmp_clear"):
            st.session_state["comparison_result"] = None
            st.session_state["comparison_models"] = []
            st.rerun()

    # ── Results ────────────────────────────────────────────────────────────────
    result_data: dict | None = st.session_state.get("comparison_result")
    if not result_data:
        return

    st.divider()
    st.subheader("Comparison Results")

    winner = result_data.get("winner", "")
    if winner:
        summary = result_data.get("summary", {})
        winner_rate = summary.get(winner, {}).get("pass_rate", 0)
        st.success(f"Winner: **{winner}** ({winner_rate:.0%} pass rate)")

    # Summary table
    model_labels = result_data.get("model_labels", [])
    summary = result_data.get("summary", {})
    if summary:
        st.subheader("Summary")
        cols = st.columns([3] + [2] * 4)
        for hdr, col in zip(["Model", "Passed", "Failed", "N/A", "Pass Rate"], cols):
            col.markdown(f"*{hdr}*")
        for label in model_labels:
            s = summary.get(label, {})
            cols = st.columns([3] + [2] * 4)
            cols[0].write(f"**{label}**" if label == winner else label)
            cols[1].write(s.get("passed", 0))
            cols[2].write(s.get("failed", 0))
            cols[3].write(s.get("na", 0))
            rate = s.get("pass_rate", 0)
            cols[4].markdown(
                f'<span style="color:{"#16a34a" if rate >= 0.7 else "#dc2626"}">'
                f'{rate:.0%}</span>',
                unsafe_allow_html=True,
            )

    # Metric table
    metric_table = result_data.get("metric_table", [])
    if metric_table and model_labels:
        st.subheader("Metric Detail")
        header_cols = st.columns([3, 3] + [2] * len(model_labels))
        for hdr, col in zip(["Metric", "Type"] + model_labels, header_cols):
            col.markdown(f"*{hdr}*")

        for row in metric_table:
            row_cols = st.columns([3, 3] + [2] * len(model_labels))
            row_cols[0].write(row.get("metric_name", ""))
            row_cols[1].code(row.get("metric_type", ""))
            scores = row.get("scores", {})
            for i, label in enumerate(model_labels):
                result_val = scores.get(label)
                if result_val is True:
                    row_cols[2 + i].markdown(
                        '<span style="color:#16a34a;font-weight:700">PASS ✓</span>',
                        unsafe_allow_html=True,
                    )
                elif result_val is False:
                    row_cols[2 + i].markdown(
                        '<span style="color:#dc2626;font-weight:700">FAIL ✗</span>',
                        unsafe_allow_html=True,
                    )
                else:
                    row_cols[2 + i].markdown(
                        '<span style="color:#64748b">N/A</span>',
                        unsafe_allow_html=True,
                    )

    dl_col, _ = st.columns([2, 6])
    with dl_col:
        st.download_button(
            "⬇  Export JSON",
            data=json.dumps(result_data, indent=2, default=str),
            file_name=f"comparison_{result_data.get('comparison_id', 'result')}.json",
            mime="application/json",
        )
