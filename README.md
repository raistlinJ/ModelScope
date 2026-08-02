# ModelScope 2.0

![Python](https://img.shields.io/badge/python-3.10%2B-blue)

ModelScope is a project-based evaluation platform for LLMs, MCP tools, and autonomous cybersecurity agents. It runs repeatable workflows against local or remote targets, validates the result with configurable metrics, and preserves logs and telemetry for later analysis.

The Streamlit application keeps the workflow in three tabs:

- **Configuration** — create a project, choose a bot and execution target, define prompts or commands, select MCP tools, and configure validation.
- **Execute** — run the active project and follow its output.
- **Analytical Dashboard** — inspect run results, metric assessments, telemetry, and saved sessions.

Projects can be exported as credential-free JSON, imported into another ModelScope instance, or executed headlessly with the CLI.

## Bot types

| Bot type | Purpose |
| --- | --- |
| **Bash-Bot** | Run shell-command workflows without an LLM. |
| **Llama-CLI-Bot** | Invoke a `llama-cli` binary for each prompt. |
| **Llama-Server-Bot** | Start and supervise a configured `llama-server`, including its Prometheus telemetry. |
| **Llama-Server-ProxBatch** | Run the managed-server workflow across selected Proxmox LXC containers and aggregate the results. |
| **CAF Standard** | Evaluate a CyberAgentFlow CLI installation locally or through SSH. |
| **CAF + llama.cpp** | Run CyberAgentFlow against a ModelScope-managed `llama-server`. |

Depending on the bot, execution targets can be the local host, an SSH host, or a Proxmox LXC reached with `pct`. Each bot owns its configuration, execution behavior, and dashboard presentation through the plugin registry.

## Prerequisites

- Python 3.10 or newer
- `uv`, or `pip` in a virtual environment
- Node.js 18 or newer only when using the bundled MCP server
- The runtime required by the chosen bot, such as `llama-cli`, `llama-server`, CyberAgentFlow, SSH access, or Proxmox `pct`

ModelScope is intended for controlled research environments. Local commands run with the current user's permissions; SSH and Proxmox runs can affect remote systems or containers.

## Install

Clone the repository and enter its root, then choose one Python setup:

```bash
# uv
uv sync

# pip
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 -m pip install -e .
```

With `uv`, use `uv run modelscope` or `uv run streamlit` below. To install the editable CLI entry point explicitly into an already active environment, run:

```bash
python3 -m pip install -e .
```

The bundled MCP server is optional:

```bash
cd mcp-server
npm install
cd ..
node mcp-server/index.js
```

It listens on `http://localhost:9191` by default. The UI can also manage it. A managed llama.cpp server defaults to `http://localhost:8080`; override machine-specific binary and model paths with `LLAMA_SERVER_BIN`, `GGUF_MODELS_DIR`, and the other variables documented in [`config/defaults.py`](config/defaults.py).

## First project

Launch the UI from the repository root:

```bash
uv run streamlit run app.py
# or, in the pip environment
streamlit run app.py
```

Streamlit normally opens `http://localhost:8501`. ModelScope creates an initial Bash project when no saved projects exist.

1. In **Configuration**, create or select a project and choose one of the six bot types.
2. Select a Local, SSH, or Proxmox target where that bot supports it, then configure its commands, prompts, model, and MCP tools.
3. Add validation commands, fail patterns, metric thresholds, or validation sets as appropriate.
4. Use **Execute** to run the project.
5. Review the outcome in **Analytical Dashboard**. Use **Export** in Configuration to save a portable project JSON file.

Non-sensitive UI settings and projects are saved in `~/.modelscope/settings.json`. Exported project files omit credentials.

## Command line

The supported CLI has two command groups: `project` and `sessions`. See [`CLI_README.md`](CLI_README.md) for every option and example.

Run a project exported from the UI:

```bash
modelscope project --file my_project.json
modelscope project --file my_project.json --dry-run

# Without an editable install
python3 cli.py project --file my_project.json
```

`--dry-run` loads and prints the normalized configuration with secrets redacted, without executing it. Exported files do not contain credentials; provide overrides as flags or environment variables:

```bash
MODELSCOPE_SSH_PASSWORD='...' modelscope project --file my_project.json
modelscope project --file my_project.json --ssh-key-path /path/to/key
```

Available credential variables are `MODELSCOPE_SSH_USER`, `MODELSCOPE_SSH_PASSWORD`, `MODELSCOPE_SSH_KEY_PATH`, `MODELSCOPE_SUDO_PASSWORD`, `MODELSCOPE_OPENAI_API_KEY`, and `MODELSCOPE_LLM_HELPER_API_KEY`. When both an SSH key and password are supplied, the key is preferred.

Browse persisted runs:

```bash
modelscope sessions list
modelscope sessions show <session-name-or-run-id>
```

`project` returns `0` when validation passes or is not configured, `1` when validation fails or the run aborts, `2` for invalid arguments, and `130` when interrupted with Ctrl+C.

## Results and validation

Validation can cover task completion, response content, tool use and ordering, latency, token usage, throughput, workflow behavior, and bot-specific telemetry. Metrics report pass, fail, or not-applicable and are summarized in the dashboard; the complete registry and evaluator logic live in [`config/metrics.py`](config/metrics.py).

Each run creates a timestamped directory under:

```text
logs/sessions/YYYY-MM-DD_HH-MM-SS_<run-id>/
├── run.log
├── telemetry.json
└── config.json
```

Some multi-run workflows write indexed telemetry files such as `telemetry_0.json`. Persisted configurations are sanitized before writing. The `logs/` directory is intentionally ignored by Git.

## Repository layout

```text
app.py                 Streamlit entry point
cli.py                 project and sessions CLI
config/                defaults and metric registry
core/                  evaluators, environments, sessions, and bot registry
core/bot_types/        built-in bot plugins
plugins/bot_types/     repository-local external bot plugins
ui/                    Configuration, Execute, and Dashboard views
mcp-server/            optional Node/Python MCP tool server
tests/                  unit, smoke, and functional tests
```

## Development

```bash
python3 -m pytest
python3 -m pytest tests/unit/
python3 -m pytest -v tests/unit/test_cli.py
```

Bot plugins are discovered from `core/bot_types/`, `plugins/bot_types/`, `~/.modelscope/bot_types/`, paths in `MODELSCOPE_BOT_PLUGIN_PATH`, and the `modelscope.bot_types` Python entry-point group. Start with [`plugins/bot_types/README.md`](plugins/bot_types/README.md) and the plugin contract in [`core/bot_types/base.py`](core/bot_types/base.py). Metric and environment extension points are described in [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Security

- Run projects only against systems you own or are authorized to test.
- Treat imported project JSON as executable configuration: inspect commands, paths, prompts, and targets before running it.
- Prefer environment variables or CLI flags for credentials; do not commit secrets or session artifacts.
- SSH currently accepts unknown host keys on first connection, which is suitable only for trusted lab networks. Use isolated targets and least-privilege accounts.
- The MCP server and model endpoints bind services that should not be exposed to untrusted networks without additional access controls.

## More documentation

- [`CLI_README.md`](CLI_README.md) — complete CLI reference
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — internals, data flow, and extension points
- [`plugins/bot_types/README.md`](plugins/bot_types/README.md) — external bot plugin notes
- [`CLAUDE.md`](CLAUDE.md) — repository development guidance
