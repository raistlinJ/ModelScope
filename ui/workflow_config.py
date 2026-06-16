"""
Workflow-specific configuration UI — rendered inside the Metrics Setup tab
when a non-tool-use scenario type is selected.
"""
import streamlit as st
from config.scenarios import SCENARIOS


def render_workflow_config() -> None:
    active_key  = st.session_state.get("active_scenario", "")
    scenario    = SCENARIOS.get(active_key, {})
    stype       = scenario.get("scenario_type", "")

    if not stype or stype == "tool_use":
        _render_mcp_presets()
        return

    _TYPE_LABELS = {
        "rag":             "RAG – Document QA",
        "prompt_eval":     "Prompt Evaluation",
        "classification":  "Classification",
        "summarization":   "Summarization",
        "structured_output": "Structured Output",
        "multiagent":      "Multi-Agent",
    }
    st.info(f"Scenario type: **{_TYPE_LABELS.get(stype, stype)}**")

    if stype == "rag":
        _rag_config()
    elif stype == "prompt_eval":
        _prompt_eval_config(scenario)
    elif stype == "classification":
        _classification_config()
    elif stype == "summarization":
        _summarization_config()
    elif stype == "structured_output":
        _structured_output_config()
    elif stype == "multiagent":
        _multiagent_config()

    st.divider()
    _render_mcp_presets()
    _render_schema_registry()


def _rag_config() -> None:
    st.subheader("RAG Configuration")
    st.text_input(
        "Corpus Path",
        placeholder="/path/to/documents or JSONL file",
        key="rag_corpus_path",
        help="Directory or JSONL file containing the document corpus.",
    )
    col1, col2 = st.columns(2)
    with col1:
        st.slider("Top-k retrieval", min_value=1, max_value=20, key="rag_retrieval_k")
    with col2:
        st.text_input(
            "Ground Truth Doc IDs",
            placeholder="doc1, doc2, doc3",
            key="rag_ground_truth_doc_ids",
            help="Comma-separated document IDs that are relevant to the query.",
        )
    st.text_area("Query", height=80, key="rag_query")
    st.text_area("Ground Truth Answer", height=100, key="rag_ground_truth_answer")
    st.caption(
        "RAG metrics (precision, recall, faithfulness) are scored against these ground-truth values."
    )


def _prompt_eval_config(scenario: dict) -> None:
    st.subheader("Prompt Evaluation Configuration")
    template = scenario.get("prompt_template", "")
    slots    = scenario.get("prompt_slots", [])

    if template:
        st.markdown(f"**Template:** `{template}`")
    if slots:
        st.caption(f"Slots: {', '.join(slots)}")

    # Variant builder
    st.subheader("Prompt Variants")
    variants: list = st.session_state.get("workflow_variants", [])

    slot_vals: dict = {}
    for slot in slots:
        slot_vals[slot] = st.text_input(f"Slot: `{slot}`", key=f"_pev_slot_{slot}")

    variant_name = st.text_input("Variant Name", key="_pev_variant_name")
    if st.button("Add Variant", key="btn_pev_add_variant"):
        if variant_name:
            variants.append({"name": variant_name, "slot_values": dict(slot_vals)})
            st.session_state["workflow_variants"] = variants
            st.rerun()

    if variants:
        for i, v in enumerate(variants):
            vc, vd = st.columns([8, 1])
            vc.write(f"**{v['name']}**: {v['slot_values']}")
            if vd.button("✕", key=f"pev_rm_variant_{i}"):
                variants.pop(i)
                st.session_state["workflow_variants"] = variants
                st.rerun()

    # Test cases
    st.subheader("Test Cases")
    cases: list = st.session_state.get("workflow_test_cases", [])

    col_in, col_out = st.columns(2)
    with col_in:
        tc_input = st.text_area("Input Text", height=80, key="_pev_tc_input")
    with col_out:
        tc_expected = st.text_input("Expected Output", key="_pev_tc_expected")

    if st.button("Add Test Case", key="btn_pev_add_tc"):
        if tc_input:
            cases.append({"input": tc_input, "expected": tc_expected})
            st.session_state["workflow_test_cases"] = cases
            st.rerun()

    for i, case in enumerate(cases):
        cc, cd = st.columns([8, 1])
        cc.caption(f"Input: {case['input'][:60]}… → Expected: {case['expected'][:40]}")
        if cd.button("✕", key=f"pev_rm_tc_{i}"):
            cases.pop(i)
            st.session_state["workflow_test_cases"] = cases
            st.rerun()


def _classification_config() -> None:
    st.subheader("Classification Configuration")
    st.text_area(
        "Label Set (comma-separated)",
        placeholder="positive, negative, neutral",
        key="classification_labels",
        height=80,
    )
    cases: list = st.session_state.get("workflow_test_cases", [])

    col_in, col_lbl = st.columns(2)
    with col_in:
        tc_input = st.text_area("Test Input", height=80, key="_cls_input")
    with col_lbl:
        tc_label = st.text_input("Expected Label", key="_cls_label")

    if st.button("Add Test Input", key="btn_cls_add"):
        if tc_input:
            cases.append({"input": tc_input, "expected": tc_label})
            st.session_state["workflow_test_cases"] = cases
            st.rerun()

    if cases:
        for i, c in enumerate(cases):
            cc, cd = st.columns([8, 1])
            cc.caption(f"`{c['expected']}` ← {c['input'][:60]}")
            if cd.button("✕", key=f"cls_rm_{i}"):
                cases.pop(i)
                st.session_state["workflow_test_cases"] = cases
                st.rerun()


def _summarization_config() -> None:
    st.subheader("Summarization Configuration")
    col1, col2 = st.columns(2)
    with col1:
        st.text_area(
            "Source Text",
            height=120,
            key="summarization_source",
            placeholder="Paste the document to be summarized.",
        )
    with col2:
        st.text_area(
            "Reference Summary (ground truth)",
            height=120,
            key="summarization_reference",
            placeholder="Paste an ideal reference summary for ROUGE scoring.",
        )
    st.slider(
        "ROUGE-L Threshold",
        min_value=0.1, max_value=1.0, step=0.05,
        key="summarization_rouge_threshold",
        value=0.3,
    )


def _structured_output_config() -> None:
    st.subheader("Structured Output Configuration")
    st.text_area(
        "Expected JSON Schema",
        height=120,
        key="structured_output_schema",
        help="JSON Schema format — the model output will be validated against this.",
    )
    st.text_input(
        "Required Fields (comma-separated)",
        placeholder="name, date, amount",
        key="structured_output_required_fields",
    )
    st.text_area(
        "Sample Input",
        height=80,
        key="structured_output_sample_input",
        placeholder="Paste the unstructured text to extract from.",
    )


def _multiagent_config() -> None:
    st.subheader("Multi-Agent Configuration")
    n_agents = st.slider(
        "Number of Agents",
        min_value=2, max_value=5,
        key="multiagent_num_agents",
    )
    for i in range(n_agents):
        st.text_input(
            f"Agent {i+1} Role",
            placeholder=f"e.g. Planner, Executor, Verifier",
            key=f"multiagent_role_{i}",
        )
    st.selectbox(
        "Coordination Protocol",
        options=["sequential", "parallel", "debate"],
        key="multiagent_protocol",
        help="sequential: agents run in order; parallel: concurrent; debate: argue toward consensus",
    )


def _render_mcp_presets() -> None:
    st.subheader("MCP Metric Presets")
    st.caption(
        "Load a curated metric bundle for a common MCP tool category. "
        "This will replace the current metrics matrix."
    )
    from config.metrics import MCPMetricPresets

    PRESET_OPTIONS = [
        "(none)",
        "web_search",
        "code_execution",
        "database_query",
        "calendar_email",
        "file_system",
    ]
    preset = st.selectbox(
        "Tool Category Preset",
        options=PRESET_OPTIONS,
        key="_preset_selector",
    )
    if st.button("Load Preset", key="btn_load_preset", disabled=preset == "(none)"):
        fns = {
            "web_search":     MCPMetricPresets.web_search,
            "code_execution": MCPMetricPresets.code_execution,
            "database_query": MCPMetricPresets.database_query,
            "calendar_email": MCPMetricPresets.calendar_email,
            "file_system":    MCPMetricPresets.file_system,
        }
        fn = fns.get(preset)
        if fn:
            metrics = fn()
            st.session_state["metrics_matrix"] = metrics
            st.success(f"Loaded {len(metrics)} metrics for **{preset}**.")
            st.rerun()


def _render_schema_registry() -> None:
    with st.expander("Schema Registry — Auto-Generate Metrics"):
        st.caption(
            "Paste a MCP tool's JSON schema to auto-generate a starter metric matrix."
        )
        tool_name = st.text_input("Tool Name", key="_registry_tool_name")
        schema_json = st.text_area(
            "Tool JSON Schema",
            height=120,
            placeholder='{"properties": {"query": {"type": "string"}}, "required": ["query"]}',
            key="_registry_schema_json",
        )
        if st.button("Generate Metrics from Schema", key="btn_gen_from_schema"):
            if not tool_name or not schema_json.strip():
                st.warning("Enter a tool name and schema JSON.")
            else:
                try:
                    from core.schema_registry import SchemaRegistry
                    schema = SchemaRegistry.parse_schema_from_json(schema_json)
                    metrics = SchemaRegistry.generate_metrics_from_schema(tool_name, schema)
                    st.session_state["_registry_generated_metrics"] = metrics
                    st.success(f"Generated {len(metrics)} metric(s).")
                except Exception as exc:
                    st.error(f"Schema parse error: {exc}")

        generated = st.session_state.get("_registry_generated_metrics", [])
        if generated:
            for m in generated:
                st.write(f"• **{m['name']}** ({m['type']})")
            if st.button("Apply to Scenario", key="btn_apply_registry_metrics"):
                st.session_state["metrics_matrix"] = generated
                st.success("Metrics applied.")
                st.rerun()
