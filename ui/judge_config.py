"""AI Judge configuration and results UI."""
import streamlit as st

_JUDGE_UNAVAILABLE_MSG = "Install with: pip install anthropic openai"


def _get_judge():
    try:
        from core.judge import FrontierJudge
        provider  = st.session_state.get("judge_provider", "anthropic")
        model     = st.session_state.get("judge_model", "claude-sonnet-4-6")
        api_key   = st.session_state.get("judge_api_key", "")
        temp      = st.session_state.get("judge_temperature", 0.0)
        return FrontierJudge(provider=provider, model=model, api_key=api_key, temperature=temp)
    except Exception as exc:
        st.error(f"Judge init failed: {exc}")
        return None


def render() -> None:
    st.subheader("AI Judge Configuration")
    st.caption(
        "Optional: use a cloud frontier model (Claude, GPT-4o) to score open-ended responses "
        "for correctness, coherence, and goal alignment — where deterministic metrics fall short."
    )

    judge_enabled = st.checkbox(
        "Enable AI Judge",
        key="judge_enabled",
        help="When enabled, the judge runs after each evaluation to score the LLM response.",
    )

    if not judge_enabled:
        st.info("Enable the AI Judge above to configure provider and API key.")
        return

    col1, col2 = st.columns(2)
    with col1:
        provider = st.selectbox(
            "Provider",
            options=["anthropic", "openai"],
            key="judge_provider",
            help="Cloud frontier model provider: Claude (Anthropic) or GPT-4o (OpenAI)",
        )
        default_model = "claude-sonnet-4-6" if provider == "anthropic" else "gpt-4o"
        if st.session_state.get("_last_judge_provider") != provider:
            st.session_state["judge_model"] = default_model
            st.session_state["_last_judge_provider"] = provider
        model = st.text_input("Model", key="judge_model", help="Model identifier (e.g., claude-sonnet-4-6 or gpt-4o)")
    with col2:
        st.text_input(
            "API Key",
            type="password",
            key="judge_api_key",
            help="Stored only in session state — not persisted to disk.",
        )
        st.slider(
            "Temperature",
            min_value=0.0,
            max_value=1.0,
            step=0.1,
            key="judge_temperature",
            help="0.0 = deterministic scoring (recommended for evaluation)",
        )

    judge_mode = st.radio(
        "Judge Mode",
        options=["Score all responses", "Sample mode (N responses)", "Generate ground truth only"],
        key="judge_mode",
        horizontal=True,
        help="Scoring strategy: all responses, sample N responses, or generate ground truth test cases",
    )

    if judge_mode == "Sample mode (N responses)":
        st.number_input(
            "Sample size (N)",
            min_value=1, max_value=100, value=5,
            key="judge_sample_n",
            help="Number of responses to sample for scoring (only used in sample mode)",
        )

    # Connection test
    col_test, _ = st.columns([2, 6])
    with col_test:
        if st.button("Test Connection", key="btn_judge_test"):
            api_key = st.session_state.get("judge_api_key", "")
            if not api_key:
                st.warning("Enter an API key first.")
            else:
                judge = _get_judge()
                if judge:
                    score = judge.score_response(
                        "What is 2 + 2?",
                        "The answer is 4.",
                    )
                    if score:
                        st.success(
                            f"Connected! Test score: correctness={score.correctness}, "
                            f"aggregate={score.aggregate_score:.0f}"
                        )
                    else:
                        st.error("Judge returned no score — check API key and model name.")

    st.divider()

    # ── Ground Truth Generation ────────────────────────────────────────────────
    with st.expander("Generate Synthetic Ground Truth"):
        st.caption(
            "Describe a scenario and the judge will generate diverse test cases "
            "with inputs, expected outputs, and evaluation rubrics."
        )
        scenario_desc = st.text_area(
            "Scenario Description",
            placeholder="e.g. An AI agent that creates files using the file_creator tool...",
            height=100,
            key="judge_gt_scenario",
            help="Describe the evaluation scenario for the judge to generate diverse test cases",
        )
        num_variants = st.number_input(
            "Number of test cases",
            min_value=1, max_value=10, value=3,
            key="judge_gt_variants",
            help="How many diverse test cases to generate from the scenario description",
        )

        if st.button("Generate Test Cases", key="btn_judge_generate"):
            api_key = st.session_state.get("judge_api_key", "")
            if not api_key:
                st.warning("Enter an API key first.")
            elif not scenario_desc.strip():
                st.warning("Enter a scenario description.")
            else:
                judge = _get_judge()
                if judge:
                    with st.spinner("Generating…"):
                        cases = judge.generate_ground_truth(scenario_desc, int(num_variants))
                    if cases:
                        st.success(f"Generated {len(cases)} test case(s).")
                        import json
                        st.session_state["judge_generated_cases"] = [
                            {
                                "case_id":          c.case_id,
                                "input_text":       c.input_text,
                                "expected_output":  c.expected_output,
                                "evaluation_rubric": c.evaluation_rubric,
                                "synthetic":        c.synthetic,
                            }
                            for c in cases
                        ]
                    else:
                        st.error("No cases generated — check API key and try again.")

        generated = st.session_state.get("judge_generated_cases", [])
        if generated:
            for case in generated:
                with st.expander(f"Case: {case['case_id']}"):
                    st.write("**Input:**", case["input_text"])
                    st.write("**Expected Output:**", case["expected_output"])
                    st.write("**Rubric:**", case["evaluation_rubric"])
            import json
            st.download_button(
                "⬇  Download Test Cases",
                data=json.dumps(generated, indent=2),
                file_name="generated_test_cases.json",
                mime="application/json",
            )


def render_judge_results(tel: dict) -> None:
    scores: dict = tel.get("judge_scores", {})
    if not scores:
        return

    st.subheader("AI Judge Scores")
    agg = tel.get("judge_aggregate_score", 0)
    st.metric("Aggregate Score", f"{agg:.0f} / 100")

    dims = ["correctness", "coherence", "goal_alignment", "safety", "efficiency"]
    labels = ["Correctness", "Coherence", "Goal Alignment", "Safety", "Efficiency"]
    cols = st.columns(len(dims))
    for col, dim, lbl in zip(cols, dims, labels):
        entry = scores.get(dim, {})
        score_val = entry.get("score", 0) if isinstance(entry, dict) else 0
        col.metric(lbl, f"{score_val} / 100")

    with st.expander("Judge Justifications"):
        for dim, lbl in zip(dims, labels):
            entry = scores.get(dim, {})
            just = entry.get("justification", "") if isinstance(entry, dict) else ""
            if just:
                st.markdown(f"**{lbl}:** {just}")
