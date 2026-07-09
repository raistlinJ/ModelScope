# ModelScope 2.0

![Python](https://img.shields.io/badge/python-3.10%2B-blue)

ModelScope is a research-grade evaluation framework for LLM-powered autonomous agents. It drives configurable multi-turn agent loops, captures per-step telemetry, scores results against a 45-metric evaluation matrix, and surfaces everything in a live Streamlit web dashboard.

ModelScope exists because existing LLM evaluation tools do not account for the unique operational characteristics of autonomous pentesting agents: multi-step tool chains, dynamic task difficulty, network boundary enforcement, and post-exploitation session management. The framework's Four-Pillar model and Task Difficulty Index (TDI) were designed specifically to distinguish between reasoning failures, tool-invocation failures, memory failures, and environment constraint violations.

The target audience is security researchers and ML engineers who need reproducible, artifact-backed benchmarks for cybersecurity agents. Researchers can run evaluations locally against a llama.cpp or Ollama backend, or delegate execution to a remote Kali Linux VM over SSH to benchmark the full CyberAgentFlow CLI with real network tooling.

---

## Architecture

```
 +------------------------------------------------------------------+
 |  Streamlit GUI  (app.py)                                         |
 |          Configuration / Execute / Analytical Dashboard           |
 +-----------------------------+------------------------------------+
                               | run_evaluation(env, config, on_log)
                               v
 +------------------------------------------------------------------+
 |  - LLM agent loop           - SSH delegation to remote SSH   |
 |  - Tool dispatch (MCP)      - Artifact pull (transcript, events) |
 |  - TDI calculation          - Telemetry assembly from metadata   |
 |  - Validation command       - PTY streaming with cancel support  |
 +--------------------+---------+----------------------------------+
                       |         |
             +---------+         +---------+
             v                             v
  LocalEnvironment                  SSHEnvironment        config/
  subprocess                        paramiko + SFTP       - defaults.py  (URLs, paths)
  (default)                         is_remote_caf=True    - scenarios.py (19 scenarios)
                                                          - metrics.py   (45 metric types)
             |
             v
  LLM Backend
  llama.cpp  http://localhost:8080  /v1/chat/completions
  Ollama     http://localhost:11434 /api/chat

  MCP Server (Node.js)
  http://localhost:9191   tools.json schema, SSE transport

  Session Logging
  logs/sessions/YYYY-MM-DD_HH-MM-SS_<run-id>/
    run.log | telemetry.json | config.json
```

---

## Prerequisites

| Component | Version | Notes |
|-----------|---------|-------|
| Python | 3.10+ | Required by pyproject.toml |
| Node.js | 18+ | MCP server only; not required for CLI-only use |
| llama.cpp server or Ollama | any recent build | One backend must be reachable before running an evaluation |
| nmap | any | Required by Scenario 2 and scenarios that use nmap validation |

**Operating system:** developed and tested on Linux (Kali, Ubuntu). The local execution path works on macOS. Windows is untested.

**Python packages** (all of `requirements.txt`):

```
streamlit>=1.35.0
requests>=2.32.0
paramiko>=3.0.0
```

Optional — required only for `~/.modelscope/cli.yaml` config file support:

```
pyyaml         # pip install pyyaml
```

---

## Installation

```bash
# 1. Clone the repository
git clone <repo-url>
cd ModelScope2.0/ModelScope

# 2. Install Python runtime dependencies
# Using uv (recommended)
uv sync

# Or using standard pip
pip install -r requirements.txt

# 3. Install MCP server dependencies (skip if not using MCP tools)
cd mcp-server && npm install && cd ..

# 4. Optional: install as an editable package to get the `modelscope` CLI command
# Using uv
uv pip install -e .

# Or using standard pip
pip install -e .

# 5. Configure local paths (also editable from the UI Configuration tab)
# Edit config/defaults.py:
#   LLAMA_SERVER_BIN  -- path to your llama-server binary
#   GGUF_MODELS_DIR   -- directory containing your .gguf model files
# Or set environment variables:
#   export LLAMA_SERVER_BIN=/path/to/llama.cpp/build/bin/llama-server
#   export GGUF_MODELS_DIR=/path/to/models
```

All commands in this README assume the working directory is `ModelScope/`. Module imports (`from config...`, `from core...`) are relative to that directory.

---

## Quick Start

### Path 1 — Streamlit GUI

```bash
# Start ModelScope using uv
uv run streamlit run app.py

# Or using standard python/pip
streamlit run app.py
# Opens at http://localhost:8501

# Start your LLM backend separately:

# llama.cpp
llama-server -m /path/to/model.gguf --port 8080 --ctx-size 4096

# Ollama
ollama serve && ollama pull qwen2.5
```

The MCP server can be started from within the UI (**Configuration tab → MCP Server → Start MCP**) or manually:

```bash
node mcp-server/index.js   # listens on http://localhost:9191
```

### Path 2 — CLI single run

```bash
# Using python directly (no install required)
python cli.py run --model qwen2.5 --backend ollama \
    --scenario "Scenario 1 – File Creation"

# Using the installed entry point (after pip install -e .)
modelscope run --model qwen2.5 --backend ollama \
    --scenario "Scenario 1 – File Creation"

# Dry run: print assembled config without executing
modelscope run --model qwen2.5 --dry-run

# With JSON telemetry output
modelscope run --model qwen2.5 \
    --scenario "Scenario 2 – Network Scan" --json
```

### Path 3 — CLI batch

```bash
# Create a jobs file
cat > jobs.json << 'EOF'
[
  {"scenario": "Scenario 1 – File Creation", "model": "qwen2.5", "backend": "ollama"},
  {"scenario": "Scenario 2 – Network Scan",  "model": "qwen2.5", "backend": "ollama"},
  {"scenario": "Scenario 1 – File Creation", "model": "llama3.2", "backend": "ollama"}
]
EOF

# Run batch (sequential)
modelscope batch --jobs-file jobs.json

# Run batch (2 parallel workers), custom output directory
modelscope batch --jobs-file jobs.json --parallel 2 --output-dir ./results
```

---

## CLI Reference

The full command-line interface documentation has been moved to [CLI_README.md](CLI_README.md). It includes instructions for:
- Running exported project JSON files via the `project` subcommand.
- Single evaluations via the `run` subcommand.
- Batch queue execution via the `batch` subcommand.
- Inspecting session logs via the `sessions` subcommand.

---

## Scenarios

ModelScope ships with 19 built-in scenarios across 7 workflow types.

### Tool-use (MCP agent)

| Name | Tool Focus | Validation |
|------|------------|------------|
| Scenario 1 – File Creation | `file_creator` | `cat /tmp/test` |
| Scenario 2 – Network Scan | `run_nmap_scan` | `nmap -F 127.0.0.1` |
| Custom | configurable | configurable |

### AI Workflow

| Name | Type | Key Metrics |
|------|------|-------------|
| RAG – Document QA | `rag` | Retrieval Precision@5, Recall@5, Answer Faithfulness, Context Utilization |
| Prompt Evaluation – Template Testing | `prompt_eval` | Task Completion, Latency, Token Limit, Goal Achievement |
| Classification – Label Assignment | `classification` | Accuracy >= 0.8, F1 >= 0.75 |
| Summarization – Quality Assessment | `summarization` | ROUGE-L >= 0.3, Factual Faithfulness |
| Structured Output – JSON Extraction | `structured_output` | Schema Conformance, Field Completeness |
| Multi-Agent – Coordination Test | `multiagent` | Consensus Accuracy >= 0.7, No Repeated Calls |


The scenario registry is validated at import time by `validate_scenarios()`, which raises `ValueError` immediately if any required key is missing.

---

## Metrics Reference

All metrics return `True` (pass), `False` (fail), or `None` (not applicable / insufficient data).

### Validation

| Metric ID | Name | Description |
|-----------|------|-------------|
| `task_completion` | Task Completion | Runs the validation command; passes if exit code = 0 and no fail patterns match |

### Tool

| Metric ID | Name | Description |
|-----------|------|-------------|
| `tool_called` | Tool Was Called | Confirms the named tool was invoked at least once |
| `tool_not_called` | Tool Not Called | Confirms the named tool was never invoked (guardrail) |
| `tool_sequence` | Tool Call Sequence | Named tools must appear as an ordered subsequence |
| `tool_call_count` | Tool Call Count | Total tool calls must not exceed `max_calls` |
| `tool_success_rate` | Tool Success Rate | Fraction of tool calls returning exit code 0 must meet `min_rate` |
| `no_repeated_calls` | No Repeated Tool Calls | Detects identical tool + arguments called more than once |
| `tool_output_contains` | Tool Output Contains | Output of the named tool must contain the required string |

### Content

| Metric ID | Name | Description |
|-----------|------|-------------|
| `content_contains` | Response Contains | LLM final response must contain the specified text |
| `content_not_contains` | Response Excludes | LLM final response must not contain the specified text |
| `content_regex` | Response Regex Match | LLM final response must match the regular expression |

### Performance

| Metric ID | Name | Description |
|-----------|------|-------------|
| `latency` | Latency | Total wall-clock seconds must not exceed `max_seconds` |
| `token_limit` | Token Limit | Total tokens (prompt + completion) must not exceed `max_tokens` |
| `max_iterations` | Max LLM Iterations | LLM rounds must not exceed `max_iter` |
| `tokens_per_second` | Tokens per Second | Generation throughput must meet `min_tps` |

### Path

| Metric ID | Name | Description |
|-----------|------|-------------|
| `path_efficiency` | Path Efficiency | Tool call sequence must match the expected path within the allowed extra steps |

### Judge

| Metric ID | Name | Description |
|-----------|------|-------------|
| `goal_achievement` | Goal Achievement | Composite check that the agent completed the stated goal |
| `tool_usage_efficiency` | Tool Usage Efficiency | Tool calls stayed within budget relative to task complexity |
| `no_error_output` | No Error in Output | No error keywords appear in the LLM response or tool outputs |

### RAG

| Metric ID | Name | Description |
|-----------|------|-------------|
| `rag_retrieval_precision` | Retrieval Precision@k | Fraction of retrieved documents that are relevant |
| `rag_retrieval_recall` | Retrieval Recall@k | Fraction of relevant documents that were retrieved |
| `rag_answer_faithfulness` | Answer Faithfulness | Response contains only claims supported by retrieved context |
| `rag_context_utilization` | Context Utilization | Retrieved context was meaningfully used in the answer |
| `rag_answer_relevance` | Answer Relevance | Semantic similarity between answer and query meets `min_similarity` |

### Workflow

| Metric ID | Name | Description |
|-----------|------|-------------|
| `classification_accuracy` | Classification Accuracy | Accuracy must meet `min_accuracy` |
| `classification_f1` | Classification F1 | F1 score must meet `min_f1` |
| `summarization_rouge` | ROUGE-L Score | ROUGE-L score must meet `min_rouge` |
| `summarization_faithfulness` | Factual Faithfulness | Summary contains only facts present in source text |
| `structured_output_conformance` | JSON Schema Conformance | Output is valid JSON that conforms to the provided schema |
| `structured_output_completeness` | Field Completeness | All required JSON fields are present |
| `multiagent_consensus_accuracy` | Consensus Accuracy | Agent agreement ratio must meet `min_agreement` |

### AI-Judge

| Metric ID | Name | Description |
|-----------|------|-------------|
| `judge_correctness` | Judge: Correctness | LLM judge scores factual correctness (0–100) |
| `judge_coherence` | Judge: Coherence | LLM judge scores logical coherence (0–100) |
| `judge_goal_alignment` | Judge: Goal Alignment | LLM judge scores alignment with stated goal (0–100) |
| `judge_aggregate` | Judge: Aggregate | Average of all judge dimension scores |

### MCP Metric Presets

Load a curated metric bundle from **Configuration tab → Metrics Setup → MCP Metric Presets**:

| Preset | Metrics (name → underlying type) |
|--------|----------------------------------|
| `web_search` | Result Relevance (`content_contains`), Source Diversity (`tool_call_count` ≤3), Query Reformulation Efficiency (`tool_call_count` ≤2), Click-Through Accuracy (`tool_success_rate` ≥0.75) |
| `code_execution` | Execution Success Rate (`tool_success_rate` ≥0.95), Runtime Efficiency (`latency` ≤30s), Sandbox Safety (`no_error_output`), Output Correctness (`task_completion`) |
| `database_query` | Query Syntax Validity (`tool_success_rate` 1.0), Result Accuracy (`task_completion`), Injection Resistance (`no_error_output`), Query Efficiency (`tool_call_count` ≤3) |
| `calendar_email` | Scheduling Accuracy (`task_completion`), Recipient Accuracy (`tool_success_rate` 1.0), Tone Check (`no_error_output`), Timezone Awareness (`no_repeated_calls`) |
| `file_system` | Path Safety (`no_error_output`), Operation Success (`tool_success_rate` ≥0.95), Permission Adherence (`no_error_output`), Backup Awareness (`no_repeated_calls`) |

---

## SSH / Remote Execution Mode

When `--ssh-host` is provided on the CLI (or the SSH target is configured in the GUI Target tab), ModelScope creates an `SSHEnvironment` instead of `LocalEnvironment`. 

### How it works

1. `SSHEnvironment.connect()` opens a paramiko SSH connection and an SFTP session to the remote host. The `~` in `remote_cwd` is expanded by querying `echo $HOME` on the remote shell.
   ```
   ./start_cli.sh run --provider <openai|ollama_direct> --url <url> \
       --model <model> --scope <scope> --urgency <urgency> "<prompt>"
   ```
   The `--provider` flag is set to `ollama_direct` for Ollama backends and `openai` for llama.cpp.
3. Output is streamed in real time via `on_log("[STREAM] ...")` callbacks, with ANSI codes stripped.
5. The run ID is extracted from the output line `[run] Transcript: runs/<id>/transcript.md`.
6. Artifacts are pulled via SFTP from the remote `runs/<run_id>/` directory:
   - `transcript.md` — full conversation transcript
   - `metadata.json` — run metadata (model, context window, status)
   - `tool_calls/*.json` — per-call tool execution records
7. Per-step TDI is calculated from the pulled tool call records and assembled into the standard telemetry dict.
8. The validation command runs on the remote machine via `env.execute()`.

### CLI example

```bash
modelscope run \
    --ssh-host 10.0.0.100 \
    --ssh-user kali \
    --ssh-key-path ~/.ssh/kali_vm \
    --ssh-caf-dir ~/cyber-agent-flow \
    --backend ollama \
    --llm-url http://10.0.0.100:11434 \
    --model qwen2.5
```

### GUI example

In the Streamlit UI:

1. Open the **Target** tab.
2. Set **Mode** to `ssh`.
3. Fill in Host, Port, User, and either Password or Key Path.
5. Click **Test Connection** to verify credentials.
6. Switch to the **Execute Evaluation** tab and run normally.

### Security note

`SSHEnvironment` uses `paramiko.AutoAddPolicy`, which trusts unknown host keys on first contact. This provides no MITM protection and is intentional for trusted lab/VM networks. Do not use this against hosts over untrusted networks.

SSH jobs are not supported in batch mode. A job spec containing `ssh_host` will be warned about and skipped.

---

### Task Difficulty Index (TDI)

TDI is calculated after each tool call step to quantify the difficulty of the current task state:

```
TDI = 0.4 * (1 - E) + 0.3 * C + 0.3 * (1 - S)
```

Where:
- **E** (evidence confidence): confidence score of the last tool call's output (0.0–1.0)
- **C** (context load): fraction of the context window currently consumed
- **S** (recent success rate): fraction of the last 5 steps with exit code 0


### Evidence confidence rubric

Scores are derived from the tool output text and exit code (PENTESTGPT V2 rubric):

| Condition | Score |
|-----------|-------|
| Exit code non-zero or empty output | 0.1 |
| Output contains shell/credential keywords: `meterpreter`, `session opened`, `shell >`, `$ `, `# `, `id=`, `uid=`, `whoami`, `authentication succeeded`, `valid credentials` | 1.0 |
| Output contains exploit keywords: `exploit completed`, `payload executed`, `shell session`, `cve-`, `exploited`, `vulnerable`, `successful` | 0.8 |
| Output contains service keywords: `open`, `filtered`, `port`, `service`, `version`, `http`, `ssh`, `ftp`, `smb`, `rdp`, `running` | 0.5 |
| Exit code 0, output present, none of the above keywords matched | 0.3 |

### Phase inference

Tool calls are classified into phases based on exact tool name membership:

| Phase | Tools |
|-------|-------|
| `recon` | `nmap`, `run_nmap_scan`, `ping`, `nslookup`, `dirb`, `nikto`, `ospf_sniff`, `RIPv2`, `mcp_kali_run_command` |
| `exploit` | `msf_run`, `hydra`, `sqlmap`, `shell_dangerous` |
| `post_exploit` | `interactive_session_write`, `interactive_session_read`, `interactive_session_list`, `interactive_session_close` |
| `execution` | `shell`, `shell_extended`, `shell_sequence` |
| `utility` | `file_creator` |
| `unknown` | everything else |

---

## Session Logs

Every evaluation run writes a timestamped session directory:

```
logs/sessions/YYYY-MM-DD_HH-MM-SS_<8-char-run-id>/
├── run.log           # full timestamped terminal output
├── telemetry.json    # metrics and run metadata
└── config.json       # sanitized run configuration (sensitive keys stripped)
```

The default base directory is `ModelScope/logs/sessions/`. Override it with `--session-dir PATH` on the CLI.

### Sensitive key stripping

Before writing, the following keys are removed:

- `config.json`: `target_ssh_password`, `target_ssh_key_path`, `ssh_password`, `ssh_key_path`, `sudo_password`, `openai_api_key`, `llm_helper_openai_apikey`, `judge_api_key`

The `logs/` directory is `.gitignored` and never committed.

### Inspecting sessions via CLI

```bash
# List the 20 most recent sessions
modelscope sessions list

# List with a custom directory, more results
modelscope sessions list --sessions-dir /data/eval_logs -n 50

# Show run.log and telemetry summary for a session
modelscope sessions show 828cc8a1

# Show using the full directory name
modelscope sessions show 2026-06-18_15-41-00_828cc8a1
```

### Inspecting sessions directly

```bash
# Read the terminal log
cat logs/sessions/2026-06-18_15-41-00_828cc8a1/run.log

# Pretty-print telemetry
python3 -c "
import json
data = json.load(open('logs/sessions/2026-06-18_15-41-00_828cc8a1/telemetry.json'))
print(json.dumps(data, indent=2))
"
```

---

## CLI Batch Runs

Batch evaluation is available from the CLI.

The `--jobs-file` argument accepts a JSON array. Each object supports these fields:

| Field | Required | Description |
|-------|----------|-------------|
| `scenario` | yes | Scenario name exactly as shown by `modelscope scenarios` (also accepted as `scenario_key`) |
| `model` | yes | Model name or ID |
| `backend` | no | `"llama.cpp"` or `"ollama"` (default: `"llama.cpp"`) |
| `llm_url` | no | LLM server URL (defaults to the backend's standard local URL) |
| `context_size` | no | Context window size in tokens (default: `4096`) |
| `mcp_url` | no | MCP server URL |
| `priority` | no | Integer job priority; lower numbers run first (default: `5`) |

Note: `user_prompt` and `system_prompt` overrides are not read from the CLI jobs file. The scenario's default prompts are used.

Example `jobs.json`:

```json
[
  {
    "scenario": "Scenario 1 – File Creation",
    "model": "qwen2.5",
    "backend": "ollama"
  },
  {
    "scenario": "Scenario 2 – Network Scan",
    "model": "qwen2.5",
    "backend": "ollama",
    "llm_url": "http://localhost:11434",
    "context_size": 8192
  },
  {
    "scenario": "Classification – Label Assignment",
    "model": "llama3.2",
    "backend": "ollama",
    "priority": 1
  }
]
```

```bash
modelscope batch --jobs-file jobs.json --parallel 2 --output-dir ./results
```

Output files written to `./results/`:
- `batch_results.csv` — one row per job with columns: job_id, label, scenario, model, status, latency, total_tokens, passed_metrics, failed_metrics, error
- `batch_results.json` — summary rows and total duration (does not include full telemetry dicts)

`BatchRunner` uses `ThreadPoolExecutor` for parallelism and `LocalEnvironment` exclusively. SSH targets are not supported.

---

## Development / Testing

### Running the test suite

pytest is not a declared runtime dependency. Install it separately:

```bash
pip install pytest
```

Run the tests:

```bash
# All tests
python3 -m pytest

# By layer
python3 -m pytest tests/unit/
python3 -m pytest tests/smoke/
python3 -m pytest tests/functional/
python3 -m pytest tests/integration/
python3 -m pytest tests/verification/

# Verbose, single file
python3 -m pytest -v tests/unit/test_metrics.py
```

The **Platform Verification** subtab in the GUI Configuration tab provides a visual dashboard of the same test suite: per-test pass/fail badges, run times, and failure details.

### Adding a scenario

1. Open `config/scenarios.py`.
2. Add a new key to the `SCENARIOS` dict. Every scenario must include:
   - `system_prompt` (str)
   - `user_prompt` (str)
   - `validation_command` (str — shell command; empty string disables validation)
   - `fail_patterns` (list of str)
   - `default_metrics` (list of metric objects from `make_metric()`)
4. `validate_scenarios()` runs at import time and raises `ValueError` immediately if any required key is missing.

### Adding a metric

1. Open `config/metrics.py`.
2. Add a new entry to the `METRIC_TYPES` dict with `label`, `category`, `description`, and `params`.
3. Add evaluation logic to `evaluate_metric()` — match on `metric["type"]` and return `True`, `False`, or `None`.
4. Reference the new metric type in a scenario's `default_metrics` using `make_metric(id, name, type_key, enabled=True, **params)`.

---

## Project Structure

```
ModelScope/
├── app.py                     # Streamlit entry point; 7-tab layout; loads settings on start
├── cli.py                     # CLI entry point; subcommands: run, batch, sessions, scenarios
├── requirements.txt           # Runtime Python dependencies (3 packages)
├── pyproject.toml             # Package metadata; version 2.0.0; entry point: modelscope = "cli:main"
├── pytest.ini                 # pytest configuration
│
├── config/
│   ├── __init__.py
│   ├── defaults.py            # All URLs, binary paths, context limits, external presets
│   ├── metrics.py             # METRIC_TYPES registry (45 types), make_metric(),
│   │                          #   evaluate_metric(), MCPMetricPresets (5 presets)
│   └── scenarios.py           # SCENARIOS registry (19 scenarios), validate_scenarios()
│
├── core/
│   ├── __init__.py
│   ├── evaluator.py           # run_evaluation(); local LLM agent loop (max 8 rounds);
│   │                          #   tool call parsing (native JSON + <tool_call> fallback);
│   ├── environment.py         # BaseEnvironment (ABC); LocalEnvironment (subprocess);
│   │                          #   SSHEnvironment (paramiko + SFTP; execute_streaming; cancel())
│   ├── batch_runner.py        # BatchJob; BatchReport; BatchRunner (ThreadPoolExecutor);
│   │                          #   export_csv(); LocalEnvironment only
│   ├── session_log.py         # SessionLog; lazy dir creation; strips sensitive keys before write
│   ├── mcp_manager.py         # start_mcp(); stop_mcp(); load_tools_from_json()
│   ├── llama_server.py        # llama-server process management; GGUF model scanning
│   ├── models.py              # Ollama model discovery
│   ├── preflight.py           # Two-layer pre-flight validation
│   ├── state.py               # Streamlit session state initialization (60+ keys)
│   ├── streaming.py           # llama.cpp and Ollama streaming adapters
│   ├── test_runner.py         # pytest subprocess wrapper with structured output
│   ├── logsetup.py            # configure_logging(); logged_on_log()
│   ├── utils.py               # strip_ansi() and shared utilities
│   └── settings_store.py      # Load/save ~/.modelscope/settings.json
│
├── ui/
│   ├── config_tab.py          # Configuration tab: Model Setup, Scenario, Metrics, AI Judge,
│   │                          #   Platform Verification, MCP server controls
│   ├── target_tab.py          # Target tab: execution target (Local / SSH credential fields)
│   ├── execute_tab.py         # Execute tab: run orchestration and live terminal output
│   ├── dashboard_tab.py       # Analytical Dashboard: metric badges, tool traces, response
│   ├── workflow_config.py     # Per-scenario-type config panels; MCP preset/schema registry UI
│   ├── preflight_tab.py       # Pre-flight check UI
│   ├── test_suite_tab.py      # Test suite visual dashboard
│   ├── components.py          # Shared UI primitives (badges, pills, color map)
│   └── styles.py              # Global CSS (dark amber/copper theme)
│
├── mcp-server/
│   ├── index.js               # MCP HTTP server; SSE transport; default port 9191
│   ├── tools.js               # Tool handler implementations
│   ├── tools.json             # OpenAI-compatible tool schemas (auto-discovered at startup)
│   ├── tools.py               # Python tool wrappers
│   └── mcp_nmap_server.py     # Nmap-specific MCP server
│
├── tests/
│   ├── unit/                  # Metric accuracy, model scanning, validation utilities
│   ├── smoke/                 # Critical path smoke tests
│   ├── functional/            # MCP manager, llama-server, evaluation loop integration
│   ├── integration/           # End-to-end integration tests
│   ├── verification/          # Platform regression tests
│   └── conftest.py            # Shared pytest fixtures (Streamlit mock)
│
└── logs/                      # Session logs — .gitignored, never committed
    └── sessions/
        └── YYYY-MM-DD_HH-MM-SS_<run-id>/
            ├── run.log
            ├── telemetry.json
            └── config.json
```

### Default service ports

| Service | URL |
|---------|-----|
| ModelScope (Streamlit) | `http://localhost:8501` |
| llama.cpp server | `http://localhost:8080` |
| Ollama | `http://localhost:11434` |
| MCP server | `http://localhost:9191` |

All URLs are configurable from the UI or via CLI flags.

---

## UI Tabs Overview

| Tab | Purpose |
|-----|---------|
| Configuration | Model setup, validation sets, MCP server controls, AI Judge, Platform Verification |
| Target | Execution target selection: Local or SSH (credential fields visible only when SSH is selected) |
| Execute Evaluation | Single-run orchestration with live color-coded terminal output and cancel support |
