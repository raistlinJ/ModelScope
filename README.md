# ModelScope

**AI Model Benchmarking & MCP Tool Evaluation Platform**

ModelScope is a Streamlit-based evaluation framework for measuring how well large language models use tools through the Model Context Protocol (MCP). It runs configurable multi-turn agent loops, collects detailed telemetry, and scores results against a 16-metric evaluation matrix — all from a live web UI with a real-time terminal output view.

---

## Overview

Most LLM benchmarks test knowledge retrieval. ModelScope tests **agentic behavior**: given a task, does the model invoke the right tool, in the right order, with the right arguments, and produce a verifiable result? It answers that question with reproducible, scenario-driven evaluation runs.

**Typical workflow:**

1. Start a local LLM backend (llama.cpp or Ollama)
2. Start the MCP server (Node.js)
3. Open ModelScope, select a model and scenario
4. Run the evaluation — the agent loop drives the LLM, executes tool calls, validates results
5. Review pass/fail metrics on the dashboard

---

## Features

- **Dual backend support** — llama.cpp (`/v1/chat/completions`) and Ollama (`/api/chat`), with auto-detection
- **MCP tool integration** — start/stop a local Node.js MCP server, auto-discover tools, enable per-tool
- **Streaming inference** — real-time token display, extended-thinking (`<think>`) tag parsing
- **Configurable evaluation scenarios** — File Creation, Network Scan, and Custom with per-scenario prompts, validation commands, and metrics
- **16-metric evaluation matrix** across six categories: Validation, Tool, Content, Performance, Path, and Judge
- **Multi-turn agent loop** — up to 8 rounds with tool call accumulation and context management
- **Live terminal view** — color-coded log tags (`[LLM]`, `[TOOL CALL]`, `[VALIDATE]`, `[PASS]`, etc.)
- **Analytical dashboard** — metric badges, response/validation comparison, port-detection visualization
- **Pre-flight testing** — two-layer platform regression and evaluation integrity checks
- **Built-in test suite** — unit, smoke, functional, and regression tests with a visual dashboard

---

## Requirements

| Component | Version |
|-----------|---------|
| Python | 3.10+ |
| Node.js | 18+ |
| llama.cpp server **or** Ollama | any recent build |

**Python packages** (`requirements.txt`):

```
streamlit>=1.35.0
requests>=2.32.0
paramiko>=3.0.0
```

**MCP server Node packages** (`mcp-server/package.json`):

```
@modelcontextprotocol/sdk
express
cors
```

---

## Installation

### 1. Clone and install Python dependencies

```bash
git clone <repo-url> ModelScope
cd ModelScope
pip install -r requirements.txt
```

### 2. Install MCP server dependencies

```bash
cd mcp-server
npm install
cd ..
```

### 3. Configure paths

Edit `config/defaults.py` to match your environment:

```python
LLAMA_SERVER_BIN = "/path/to/llama.cpp/build/bin/llama-server"
GGUF_MODELS_DIR  = "/path/to/your/models"
```

These values are also editable from the UI at runtime.

---

## Running

### Start ModelScope

```bash
streamlit run app.py
```

The app opens at `http://localhost:8501` by default.

### Start your LLM backend

**llama.cpp:**
```bash
llama-server -m /path/to/model.gguf --port 8080 --ctx-size 4096
```

**Ollama:**
```bash
ollama serve
ollama pull <model-name>
```

### Start the MCP server

The MCP server can be started from the UI (**Configuration → MCP Server → Start MCP**), or manually:

```bash
node mcp-server/index.js
# Listens on http://localhost:9191
```

---

## Configuration

### UI — Configuration tab

| Section | What it controls |
|---------|-----------------|
| **Execution Target** | Local execution environment (SSH support planned for a future release) |
| **Backend & Model** | Backend type (llama.cpp / Ollama), server URL, model selection, context size |
| **llama-server** | Binary path, model directory, start/stop the server process |
| **MCP Server** | Script path, server URL, start/stop |
| **Metrics Setup** | Active scenario, per-metric enable/disable, metric parameters |
| **Platform Verification** | Pre-flight checks and test suite dashboard |

### Scenario selection

Three built-in scenarios are available under **Metrics Setup → Scenario**:

| Scenario | Task | Validation |
|----------|------|------------|
| **Scenario 1 – File Creation** | Write `/tmp/test` containing numbers 1–10 using `file_creator` | `cat /tmp/test` |
| **Scenario 2 – Network Scan** | Scan `127.0.0.1` with `run_nmap_scan` and report open ports | `nmap -F 127.0.0.1` |
| **Custom** | User-defined prompts, validation command, and metrics | configurable |

Switching scenarios reloads the system prompt, user prompt, validation command, and default metrics matrix. Edited prompts are preserved with a warning if the scenario changes.

---

## Evaluation Metrics

Metrics are defined in `config/metrics.py`. Each metric has a `type`, optional `params`, and a category label. The active metrics matrix is configurable per scenario.

### Categories and metric types

| Category | Metric Type | Description |
|----------|-------------|-------------|
| **Validation** | `task_completion` | Validation command exits 0 and no fail patterns match |
| **Tool** | `tool_called` | A specific tool was invoked at least once |
| | `tool_not_called` | A specific tool was never invoked |
| | `tool_sequence` | Tools were called in a required ordered subsequence |
| | `tool_call_count` | Total tool invocations within an expected range |
| | `tool_success_rate` | Fraction of tool calls that returned successfully |
| | `no_repeated_calls` | No identical tool call made more than once |
| | `tool_output_contains` | A tool's output contained an expected string |
| **Content** | `content_contains` | Final LLM response contains required text |
| | `content_not_contains` | Final LLM response does not contain prohibited text |
| | `content_regex` | Final LLM response matches a regex pattern |
| **Performance** | `latency` | Wall-clock run time under a threshold (seconds) |
| | `token_limit` | Total tokens (prompt + completion) under a limit |
| | `max_iterations` | Agent loop completed within a round limit |
| | `tokens_per_second` | Throughput above a minimum threshold |
| **Path** | `path_efficiency` | Actual tool call path vs. expected path, penalizing backtracking |
| **Judge** | `goal_achievement` | Composite: task completion + no inefficiencies + 100% tool success |
| | `tool_usage_efficiency` | No redundant or repeated tool calls |
| | `no_error_output` | No `error` / `Error` / `ERROR` strings in any tool output |

Metrics return `True` (pass), `False` (fail), or `None` (insufficient data / not applicable).

---

## MCP Tools

The MCP server at `mcp-server/` exposes two tools:

### `file_creator`

Creates a file at the specified path, creating parent directories as needed.

| Argument | Type | Description |
|----------|------|-------------|
| `path` | string (required) | Absolute file path to write |
| `content` | string (required) | File content |

Returns: `{ status, message, path, bytes_written }`

### `run_nmap_scan`

Runs an nmap scan against a target host.

| Argument | Type | Description |
|----------|------|-------------|
| `target` | string (required) | IP address or hostname to scan |
| `arguments` | string (optional) | nmap flags (default: `-F`) |

Returns: nmap stdout/stderr or an error message. Input is validated against shell injection characters.

Tool schemas are defined in `mcp-server/tools.json` (OpenAI-compatible format) and loaded automatically by the evaluator at run time.

---

## Architecture

```
ModelScope/
├── app.py                    # Entry point — Streamlit layout and tab routing
├── config/
│   ├── defaults.py           # Backend URLs, paths, context limits
│   ├── metrics.py            # Metric type definitions and evaluation logic
│   └── scenarios.py          # Built-in scenario definitions
├── core/
│   ├── evaluator.py          # Agent loop, LLM streaming, tool dispatch, validation
│   ├── models.py             # GGUF model scanning and Ollama model discovery
│   ├── llama_server.py       # llama-server process lifecycle
│   ├── mcp_manager.py        # MCP server process lifecycle
│   ├── environment.py        # LocalEnvironment execution abstraction
│   ├── preflight.py          # Two-layer pre-flight test runner
│   ├── test_runner.py        # pytest subprocess wrapper with structured output
│   └── state.py              # Streamlit session state initialization
├── ui/
│   ├── config_tab.py         # Configuration tab UI
│   ├── execute_tab.py        # Execute tab UI and run orchestration
│   ├── dashboard_tab.py      # Analytical dashboard UI
│   ├── preflight_tab.py      # Pre-flight checks UI
│   ├── test_suite_tab.py     # Test suite visualization UI
│   └── styles.py             # Global CSS (dark amber/copper theme)
├── mcp-server/
│   ├── index.js              # MCP HTTP server (SSE transport, port 9191)
│   ├── tools.js              # Tool handler implementations
│   ├── tools.json            # OpenAI-compatible tool schemas
│   └── package.json          # Node.js dependencies
└── tests/
    ├── unit/                 # Metric accuracy and utility function tests
    ├── smoke/                # Critical path smoke tests
    ├── functional/           # MCP, llama-server, and evaluation loop tests
    └── regression/           # Platform state machine regression tests
```

### Evaluation flow

```
run_evaluation(env, config, on_log)
│
├── Load tool schemas from tools.json
├── Build initial messages (system + user prompt)
│
└── Agent loop (max 8 rounds)
    ├── Stream LLM response (llama.cpp or Ollama)
    ├── Parse tool calls (JSON + <tool_call> fallback)
    ├── Execute tools (MCP → local fallback)
    ├── Append tool results to message history
    └── Check for completion or continue
│
├── Run validation command
├── Evaluate metrics matrix
└── Return telemetry dict
```

---

## Pre-flight Checks

The **Platform Verification** tab runs two layers of automated checks before you start an evaluation run.

**Layer 1 — Platform Regression**
- Session state initialization (all required keys present with correct types)
- Backend connectivity (llama.cpp or Ollama reachable within 3 s)
- Filesystem read/write access
- MCP tool schema loading

**Layer 2 — Evaluation Integrity**
- Metric evaluation against known-good and known-bad synthetic telemetry
- Tool call parsing accuracy
- Optional end-to-end smoke test (minimal single-round LLM inference)

Each check returns a pass/fail result with timing and a detail message. Run pre-flight checks after any configuration change to confirm the pipeline is healthy before benchmarking.

---

## Running Tests

```bash
# All tests
python3 -m pytest

# By category
python3 -m pytest tests/unit/
python3 -m pytest tests/smoke/
python3 -m pytest tests/functional/

# With verbose output
python3 -m pytest -v

# Single file
python3 -m pytest tests/unit/test_metric_accuracy.py
```

The test suite dashboard in the UI (Configuration → Platform Verification → Test Suite) provides a visual regression view with per-test pass/fail status, run times, and failure details.

---

## Default Ports

| Service | Default URL |
|---------|-------------|
| ModelScope (Streamlit) | `http://localhost:8501` |
| llama.cpp server | `http://localhost:8080` |
| Ollama | `http://localhost:11434` |
| MCP server | `http://localhost:9191` |

All URLs are configurable from the UI.

---

## Roadmap

### AI Workflow Expansion

- **RAG system evaluation** — Dedicated scenario type for retrieval-augmented generation pipelines: measure retrieval precision, answer faithfulness, and context utilization against a ground-truth corpus
- **Model comparison mode** — Run the same scenario across multiple models or quantization levels in a single session and produce a side-by-side metrics table
- **Prompt evaluation** — Systematic prompt variant testing: define a prompt template with named slots, enumerate variants, and score each against the same metric matrix to find the best-performing formulation
- **Additional AI workflow types** — Expand beyond tool-use evaluation to cover classification tasks, summarization quality, multi-agent coordination, and structured-output conformance

### MCP Metrics Library

- **Common MCP tool metric presets** — A curated library of pre-built metric bundles for frequently used MCP tool categories (web search, code execution, database query, calendar/email, file system), so scenarios can be assembled from proven metric sets rather than built from scratch
- **Community tool schema registry** — Import third-party MCP tool schemas and automatically generate a starter metric matrix based on the tool's argument and return-value types

### Infrastructure

- **SSH remote execution** — Run evaluation commands on remote machines over SSH, enabling benchmarking against isolated VMs, containers, or specialized hardware (planned for a future release)
- **MCP SSH tunneling** — Forward a remote MCP server's port to localhost via SSH tunnel so the evaluation loop can drive tools running on a separate machine (planned for a future release)
- **Batch evaluation** — Queue multiple model/scenario/prompt combinations and run them unattended, producing a consolidated results report

### AI-Assisted Evaluation

- **Frontier / cloud model judge** — Integrate a cloud-hosted frontier model (e.g., Claude, GPT-4o) as an optional evaluation judge: score open-ended responses for correctness, coherence, and goal alignment where deterministic metrics are insufficient, and use it to generate synthetic ground-truth for new scenarios

---

## License

See `LICENSE` for terms.
