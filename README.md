# ModelScope

**LLM & MCP Tool Evaluation Platform**

ModelScope is a Streamlit-based evaluation suite for measuring how well large language models perform across agentic tool-use, RAG pipelines, classification, summarization, structured output, and multi-agent coordination tasks. It drives configurable multi-turn agent loops, collects per-step telemetry, scores results against a 42-metric evaluation matrix, and surfaces everything in a live web dashboard.

---

## What it evaluates

| Workflow Type | Description |
|---------------|-------------|
| **Tool-use (MCP)** | Agent invokes MCP tools to complete tasks — file creation, network scanning, custom |
| **CAF (Cyber-Agent-Flow)** | Penetration-testing agent evaluation with 4-Pillar metrics and Task Difficulty Index |
| **RAG** | Retrieval-augmented generation — precision, recall, faithfulness, context utilization |
| **Classification** | Label assignment accuracy, F1, per-class precision/recall |
| **Summarization** | ROUGE-L score, factual faithfulness, compression ratio |
| **Structured Output** | JSON schema conformance, required field completeness, type correctness |
| **Multi-Agent** | Coordination efficiency, consensus accuracy, deadlock detection |
| **Prompt Evaluation** | Template slot variants scored against the full metric matrix |

---

## Features

- **Dual LLM backend** — llama.cpp (`/v1/chat/completions`) and Ollama (`/api/chat`), auto-detected
- **MCP tool integration** — start/stop a local Node.js MCP server, auto-discover and enable tools per-run
- **Streaming inference** — real-time token display with extended-thinking (`<think>`) tag parsing
- **12 built-in scenarios** across 7 workflow types, each with pre-configured prompts and metrics
- **42 metric types** across 13 categories — deterministic and AI-judge dimensions
- **Multi-turn agent loop** — up to 8 LLM rounds with tool call accumulation, TDI tracking, and cancel support
- **Batch evaluation** — queue multiple model/scenario/prompt combinations and run unattended
- **Model comparison** — run one scenario across N models and produce a side-by-side pass/fail table
- **AI Judge** — Anthropic or OpenAI frontier model scores responses on 5 qualitative dimensions
- **Synthetic ground truth** — generate test cases automatically using the AI judge
- **MCP metric presets** — curated metric bundles for web search, code execution, database query, calendar/email, file system
- **Schema registry** — auto-generate a starter metric matrix from any MCP tool's JSON schema
- **CAF 4-Pillar framework** — Scope/Urgency/TDI controls for penetration-testing agent evaluation
- **Live terminal view** — color-coded log tags (`[LLM]`, `[TOOL CALL]`, `[VALIDATE]`, `[CAF]`, etc.)
- **Analytical dashboard** — metric badges, tool call traces, response/validation comparison, attack tree (CAF)
- **Pre-flight validation** — two-layer platform regression and evaluation integrity checks
- **Built-in test suite** — unit, smoke, functional, and regression tests with a visual dashboard

---

## Requirements

| Component | Version |
|-----------|---------|
| Python | 3.10+ |
| Node.js | 18+ (MCP server only) |
| llama.cpp server **or** Ollama | any recent build |

**Python packages** (`requirements.txt`):

```
streamlit>=1.35.0
requests>=2.32.0
paramiko>=3.0.0
```

Optional for AI Judge:
```
anthropic      # pip install anthropic
openai         # pip install openai
```

**MCP server Node packages** (`mcp-server/package.json`):
```
@modelcontextprotocol/sdk
express
cors
```

---

## Installation

```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Install MCP server dependencies
cd mcp-server && npm install && cd ..

# 3. Configure local paths (also editable from the UI)
# Edit config/defaults.py:
#   LLAMA_SERVER_BIN = "/path/to/llama.cpp/build/bin/llama-server"
#   GGUF_MODELS_DIR  = "/path/to/your/models"
```

---

## Running

```bash
streamlit run app.py
# Opens at http://localhost:8501
```

**Start your LLM backend separately:**

```bash
# llama.cpp
llama-server -m /path/to/model.gguf --port 8080 --ctx-size 4096

# Ollama
ollama serve && ollama pull <model-name>
```

The MCP server can be started from the UI (**Configuration → MCP Server → Start MCP**) or manually:

```bash
node mcp-server/index.js   # listens on http://localhost:9191
```

---

## UI — Tabs Overview

| Tab | Purpose |
|-----|---------|
| **⚙ Configuration** | Model setup, scenario selection, metrics, AI Judge, platform verification |
| **▶ Execute Evaluation** | Run a single evaluation with live terminal output |
| **📊 Analytical Dashboard** | Metrics results, tool traces, response comparison, CAF attack tree |
| **🔄 Batch Evaluation** | Queue and run multiple jobs unattended, download consolidated report |
| **⚖ Model Comparison** | Side-by-side metric table across N models on one scenario |

---

## Scenarios

### Tool-use

| Scenario | Task | Validation |
|----------|------|------------|
| **Scenario 1 – File Creation** | Write `/tmp/test` with numbers 1–10 via `file_creator` | `cat /tmp/test` |
| **Scenario 2 – Network Scan** | Scan `127.0.0.1` via `run_nmap_scan`, report open ports | `nmap -F 127.0.0.1` |
| **Custom** | User-defined prompts, validation command, and metrics | configurable |

### CAF (Cyber-Agent-Flow)

| Scenario | Scope | Urgency |
|----------|-------|---------|
| **CAF – Reconnaissance** | Broad | Stealthy |
| **CAF – Exploitation** | Narrow | Speed |
| **CAF – Guardrail Test** | Narrow | Stealthy |

### AI Workflow

| Scenario | Type | Key Metrics |
|----------|------|-------------|
| **RAG – Document QA** | `rag` | Retrieval precision/recall, answer faithfulness, context utilization |
| **Prompt Evaluation – Template Testing** | `prompt_eval` | Latency, token efficiency, goal achievement |
| **Classification – Label Assignment** | `classification` | Accuracy ≥ 0.8, F1 ≥ 0.75 |
| **Summarization – Quality Assessment** | `summarization` | ROUGE-L ≥ 0.3, factual faithfulness |
| **Structured Output – JSON Extraction** | `structured_output` | Schema conformance, field completeness |
| **Multi-Agent – Coordination Test** | `multiagent` | Consensus accuracy ≥ 0.7, no repeated calls |

---

## Evaluation Metrics

### Metric categories

| Category | Types | Focus |
|----------|-------|-------|
| **Validation** | `task_completion` | End-to-end success via validation command |
| **Tool** | `tool_called`, `tool_not_called`, `tool_sequence`, `tool_call_count`, `tool_success_rate`, `no_repeated_calls`, `tool_output_contains` | Tool invocation correctness |
| **Content** | `content_contains`, `content_not_contains`, `content_regex` | LLM response assertions |
| **Performance** | `latency`, `token_limit`, `max_iterations`, `tokens_per_second` | Speed and token efficiency |
| **Path** | `path_efficiency` | Optimal tool-call sequence vs. expected path |
| **Judge** | `goal_achievement`, `tool_usage_efficiency`, `no_error_output` | Composite quality checks |
| **CAF-LLM** | `caf_tempo_adherence`, `caf_diagnostic_adherence`, `caf_tdi_health` | Reasoning adherence for CAF |
| **CAF-Tools** | `caf_tool_param_accuracy`, `caf_interactive_session_efficiency` | Tool invocation quality for CAF |
| **CAF-Memory** | `caf_memory_recall` | Credential reuse across trajectory |
| **CAF-Environment** | `caf_scope_guardrails` | Network boundary compliance |
| **RAG** | `rag_retrieval_precision`, `rag_retrieval_recall`, `rag_answer_faithfulness`, `rag_context_utilization`, `rag_answer_relevance` | Retrieval and generation quality |
| **Workflow** | `classification_accuracy`, `classification_f1`, `summarization_rouge`, `summarization_faithfulness`, `structured_output_conformance`, `structured_output_completeness`, `multiagent_consensus_accuracy` | AI workflow quality |
| **AI-Judge** | `judge_correctness`, `judge_coherence`, `judge_goal_alignment`, `judge_aggregate` | Frontier model qualitative scoring (0–100) |

All metrics return `True` (pass), `False` (fail), or `None` (not applicable / insufficient data).

### MCP Metric Presets

Load a curated metric bundle for a common tool category from **Configuration → Metrics Setup → MCP Metric Presets**:

| Preset | Metrics included |
|--------|-----------------|
| `web_search` | Result relevance, source diversity, query reformulation efficiency, click-through accuracy |
| `code_execution` | Execution success rate, runtime efficiency, sandbox safety, output correctness |
| `database_query` | Query syntax validity, result accuracy, injection resistance, query efficiency |
| `calendar_email` | Scheduling accuracy, recipient accuracy, tone check, timezone awareness |
| `file_system` | Path safety, operation success, permission adherence, backup awareness |

### Schema Registry

Paste any MCP tool's JSON schema under **Configuration → Metrics Setup → Schema Registry** to auto-generate a starter metric matrix based on the tool's required arguments, enum constraints, and return value types.

---

## CAF 4-Pillar Evaluation

The **Cyber-Agent-Flow** scenarios use an extended evaluation framework:

| Pillar | Metrics | Failure Type |
|--------|---------|--------------|
| **LLM** | Tempo Adherence, Diagnostic Adherence, TDI Health | Type B (reasoning) |
| **Tools** | Tool Param Accuracy, Session Efficiency | Type A (syntax) |
| **Memory** | Memory Recall F1 | Type B (retrieval) |
| **Environment** | Scope Guardrails | Type A/B (boundary) |

**Task Difficulty Index (TDI):**

```
TDI = 0.4 × context_load + 0.4 × recent_failure_rate + 0.2 × (1 − evidence_signal)
```

TDI is calculated per step and visualised as a trajectory in the dashboard's Attack Tree panel. Average TDI > 0.6 indicates context saturation or persistent tool failures.

**Runtime controls** (Configuration → CAF Runtime Configuration):
- **Scope** — Broad (wide discovery) or Narrow (targeted exploitation)
- **Urgency** — Stealthy (slow/quiet timing) or Speed (aggressive flags)
- **Allowed Subnets** — Network ranges the agent is authorized to interact with
- **Target Credentials** — Known credential strings tracked for Memory Recall F1 scoring

---

## Batch Evaluation

The **🔄 Batch Evaluation** tab lets you queue multiple jobs (scenario × model × optional prompt override) and run them unattended:

1. Add jobs via the **Add Job to Queue** form — set scenario, backend, model, priority, optional prompt override
2. Review the queue table, reorder by priority, remove individual jobs
3. Click **▶ Run Batch** — jobs execute sequentially by default
4. Download results as **CSV** or **JSON**

Each job's summary row shows: Label, Scenario, Model, Status, Latency, Token count, Passed metrics, Failed metrics.

---

## Model Comparison

The **⚖ Model Comparison** tab runs one scenario across N models and produces a side-by-side pass/fail table:

1. Select a scenario
2. Add each model (label, backend, server URL, model name, context size)
3. Click **⚖ Run Comparison** — models run sequentially to avoid GPU contention
4. Review the summary table (pass rate per model) and the per-metric detail table with PASS/FAIL/N/A badges
5. The winner (highest aggregate pass rate) is highlighted

---

## AI Judge

Configure a frontier model judge under **Configuration → 🤖 AI Judge**:

- **Providers:** Anthropic (`claude-sonnet-4-6` default) or OpenAI (`gpt-4o` default)
- **Dimensions scored (0–100):** Correctness, Coherence, Goal Alignment, Safety, Efficiency
- **Modes:** Score all responses · Sample N responses · Generate ground truth only
- **Test connection** button to verify credentials before running

Judge scores appear in the **Analytical Dashboard** after each evaluation run.

### Synthetic Ground Truth Generation

Under the AI Judge tab, describe a scenario and click **Generate Test Cases**. The judge produces diverse inputs, expected outputs, and evaluation rubrics — all marked `synthetic: true`. Download the generated cases as JSON and use them to bootstrap new scenario test coverage.

---

## Architecture

```
ModelScope/
├── app.py                      # Entry point — 5-tab Streamlit layout
├── config/
│   ├── defaults.py             # Backend URLs, paths, context limits
│   ├── metrics.py              # 42 metric types, evaluators, MCPMetricPresets
│   └── scenarios.py            # 12 built-in scenario definitions
├── core/
│   ├── evaluator.py            # Agent loop, streaming, tool dispatch, TDI, validation
│   ├── batch_runner.py         # BatchRunner — priority queue, parallel execution, CSV export
│   ├── comparison.py           # run_comparison() — N-model side-by-side results
│   ├── judge.py                # FrontierJudge — Anthropic/OpenAI qualitative scoring
│   ├── schema_registry.py      # SchemaRegistry — auto-generate metrics from tool schemas
│   ├── caf_state.py            # CAFConfigTarget, StepTelemetry dataclasses
│   ├── environment.py          # LocalEnvironment (SSHEnvironment planned Phase 3)
│   ├── mcp_manager.py          # MCP server process lifecycle and tool discovery
│   ├── llama_server.py         # llama-server process management
│   ├── models.py               # GGUF scanner, Ollama model discovery
│   ├── preflight.py            # Two-layer pre-flight validation
│   ├── streaming.py            # llama.cpp and Ollama streaming adapters
│   ├── state.py                # Streamlit session state initialization (60+ keys)
│   └── test_runner.py          # pytest subprocess wrapper with structured output
├── ui/
│   ├── config_tab.py           # Configuration tab (Model Setup, Metrics Setup, AI Judge, Verification)
│   ├── execute_tab.py          # Execute tab — run orchestration and live terminal
│   ├── dashboard_tab.py        # Analytical dashboard — metrics, traces, CAF panels, judge scores
│   ├── batch_tab.py            # Batch evaluation queue and results
│   ├── comparison_tab.py       # Model comparison side-by-side table
│   ├── judge_config.py         # AI Judge configuration and ground truth generation
│   ├── workflow_config.py      # Per-scenario-type config panels + preset/registry UI
│   ├── caf_dashboard.py        # CAF Attack Tree viewer and Dual-Layer Judge Panel
│   ├── preflight_tab.py        # Pre-flight check UI
│   ├── test_suite_tab.py       # Test suite visual dashboard
│   ├── components.py           # Shared UI primitives (badges, pills, colour map)
│   └── styles.py               # Global CSS (dark amber/copper theme)
├── mcp-server/
│   ├── index.js                # MCP HTTP server (SSE transport, port 9191)
│   ├── tools.js                # Tool handler implementations
│   ├── tools.json              # OpenAI-compatible tool schemas
│   ├── tools.py                # Python tool wrappers
│   └── mcp_nmap_server.py      # Nmap-specific MCP server
└── tests/
    ├── unit/                   # Metric accuracy, model scanning, validation utilities
    ├── smoke/                  # Critical path smoke tests
    ├── functional/             # MCP manager, llama-server, evaluation loop integration
    └── conftest.py             # Shared pytest fixtures (streamlit mock)
```

### Evaluation flow

```
run_evaluation(env, config, on_log)
│
├── Load tool schemas (tools.json)
├── Build initial messages (system + user prompt)
│
└── Agent loop (max 8 rounds)
    ├── Stream LLM response (llama.cpp or Ollama)
    ├── Parse tool calls (native JSON + <tool_call> tag fallback)
    ├── Execute tools via MCP → local fallback
    ├── Calculate per-step TDI (CAF scenarios)
    ├── Append tool results to message history
    └── Break when no tool calls in response
│
├── Run validation command
├── Check for inefficiencies (repeated tool+args pairs)
└── Return telemetry dict (latency, tokens, tool_calls, caf_trajectory, …)
```

---

## Default Ports

| Service | Default |
|---------|---------|
| ModelScope (Streamlit) | `http://localhost:8501` |
| llama.cpp server | `http://localhost:8080` |
| Ollama | `http://localhost:11434` |
| MCP server | `http://localhost:9191` |

All URLs are configurable from the UI.

---

## Running Tests

```bash
# All tests
python3 -m pytest

# By layer
python3 -m pytest tests/unit/
python3 -m pytest tests/smoke/
python3 -m pytest tests/functional/

# Verbose
python3 -m pytest -v tests/unit/test_metrics.py
```

The **Platform Verification** subtab in Configuration provides a visual test dashboard — per-test pass/fail badges, run times, and failure details — without leaving the UI.

---

## Roadmap

### Phase 3 (Q4 2026)

- **SSH remote execution** — Run evaluation commands on remote machines over SSH, enabling benchmarking against isolated VMs, containers, or specialized hardware
- **MCP SSH tunneling** — Forward a remote MCP server's port to localhost via SSH so the evaluation loop can drive tools running on a separate machine
- **Advanced statistical analysis** — Significance testing, confidence intervals, and run-to-run variance reporting
- **CI/CD integration** — GitHub Actions plugin for automated evaluation on pull requests

### Phase 4 (2027)

- Public leaderboard hosting
- Community benchmark contributions
- Enterprise SSO and audit logging
- Multi-tenant evaluation cloud service

---

## License

See `LICENSE` for terms.
