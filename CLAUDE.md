# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

### Development & Setup
- **Install Dependencies:** `pip install -r requirements.txt`
- **Install as Editable Package:** `pip install -e .` (Enables the `modelscope` CLI command)
- **Install MCP Server:** `cd mcp-server && npm install && cd ..`

### Running the Application
- **Launch Streamlit GUI:** `streamlit run app.py`
- **CLI Single Run:** `python cli.py run --model <model> --backend <backend> --scenario "<scenario_name>"`
- **CLI Batch Run:** `modelscope batch --jobs-file jobs.json`
- **List Scenarios:** `modelscope scenarios`
- **Browse Sessions:** `modelscope sessions list`

### Testing
- **Run All Tests:** `python3 -m pytest`
- **Run Specific Test Layer:** `python3 -m pytest tests/<unit|smoke|functional|integration|verification>/`
- **Run Single Test File:** `python3 -m pytest -v tests/unit/<test_file>.py`

## Architecture Overview

ModelScope is a research-grade evaluation framework for LLM-powered autonomous agents, specifically cybersecurity agents.

### High-Level Structure
- **Frontends:**
    - `app.py`: Streamlit GUI with 7 tabs for configuration, execution, and analysis.
    - `cli.py`: Command-line interface for automated runs, batching, and session management.
- **Core Logic (`core/`):**
    - `evaluator.py`: Manages the local LLM agent loop (prompt $\rightarrow$ tool call $\rightarrow$ telemetry).
    - `caf_runner.py`: Handles remote execution of CyberAgentFlow (CAF) via SSH, pulling artifacts and streaming output.
    - `environment.py`: Abstraction layer (`BaseEnvironment`) for where code executes (`LocalEnvironment` vs. `SSHEnvironment`).
    - `session_log.py`: Manages per-run structured logging.
- **Configuration (`config/`):**
    - `scenarios.py`: Registry of built-in evaluation scenarios (prompts, validation commands, metrics).
    - `metrics.py`: Strategy-based metric registry (`METRIC_TYPES`) and evaluation logic.
    - `defaults.py`: Global constants, URLs, and filesystem paths.
- **MCP Server (`mcp-server/`):** A Node.js/Python hybrid server providing tool schemas and execution for agents.

### Execution Modes
1. **Local Mode:** Direct LLM $\rightarrow$ Tool loop executed via `core.evaluator`.
2. **Remote CAF Mode:** Entire evaluation delegated to a remote Kali Linux VM running the CAF CLI, orchestrated via `core.caf_runner`.

## Development Guidelines

### Adding a New Scenario
Add a new entry to the `SCENARIOS` dictionary in `config/scenarios.py`. Every scenario must define:
- `system_prompt`, `user_prompt`
- `validation_command` (shell command to verify success)
- `fail_patterns` (strings that indicate failure)
- `default_metrics` (list of metric objects via `make_metric()`)

### Adding a New Metric Type
1. Define the metric in `METRIC_TYPES` in `config/metrics.py` (label, category, description, params).
2. Implement an evaluator function `_eval_<name>(params, telemetry) -> bool | None`.
3. Register the function in the `_EVALUATORS` dispatch table.

### Adding an Environment
Subclass `BaseEnvironment` in `core/environment.py`, implement essential filesystem/execution methods, and update the `create_environment()` factory.

## Session Logs & Settings

### Session Logs
Every run saves structured logs to `logs/sessions/YYYY-MM-DD_HH-MM-SS_<run-id>/`:
- `run.log`: Full timestamped terminal output.
- `telemetry.json`: Metrics and run metadata.
- `config.json`: Sanitized run configuration.

The `logs/` directory is `.gitignored`.

### Settings
Non-sensitive user settings are persisted to `~/.modelscope/settings.json`.
