# ModelScope CLI Reference

All CLI commands must be run from the `ModelScope/` directory.

If you have installed the project into your environment (`pip install -e .`), you can use the `modelscope` entry point:
```bash
modelscope project --file my_project.json
```

Otherwise, if you are running it directly from the repository, be sure to use the virtual environment's python executable:
```bash
.venv/bin/python cli.py project --file my_project.json
```

## `project` — run exported UI projects

You can export a bot's configuration from the ModelScope UI as a JSON file and run it entirely headlessly via the CLI. The CLI will automatically create the correct environment (Local, SSH, PCT) and run the evaluation logic.

This is the headless entry point for **Bash-Bot**, **Llama-CLI-Bot**, and
**Llama-Server-Bot** projects. Managed Llama-Server projects start the configured
`llama-server` binary, wait for `/health`, collect its Prometheus metrics, and
stop it when the evaluation completes. Each non-dry run writes `run.log`,
`telemetry.json`, and a credential-sanitized `config.json` under `logs/sessions/`.

| Flag | Description |
|------|-------------|
| `-f`, `--file PATH` | _(required)_ Path to the exported project JSON file |
| `--dry-run` | Print the loaded configuration (redacting passwords) and exit without running |
| `-v`, `--verbose` | Enable DEBUG-level logging |
| `--ssh-user USER` | Override the SSH username |
| `--ssh-password PASS` | Override the SSH password |
| `--ssh-key-path PATH` | Override the SSH key path |
| `--sudo-password PASS` | Override the sudo password |
| `--openai-api-key KEY` | Override the OpenAI API key |
| `--llm-helper-api-key KEY` | Override the LLM Judge / prompt-helper API key |

> **Note on Credentials:** Passwords (like SSH and OpenAI keys) are automatically stripped from the JSON when exported from the UI for security. You can either manually edit the JSON file, or pass them securely at runtime via the override flags above or their corresponding environment variables (e.g. `MODELSCOPE_SSH_PASSWORD`, `MODELSCOPE_LLM_HELPER_API_KEY`).
> **If both a password and an SSH key path are provided, the SSH key is preferred.**

---

## `run` — single evaluation

```bash
modelscope run [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--model MODEL` | _(none)_ | Model name or ID to pass to the backend |
| `--backend {llama.cpp,ollama}` | `llama.cpp` | Inference backend |
| `--llm-url URL` | backend default | LLM server URL (overrides backend default) |
| `--context-size N` | `4096` | Context window size in tokens |
| `--scenario NAME` | `"Scenario 1 – File Creation"` | Scenario name (see `modelscope scenarios`) |
| `--system-prompt TEXT` | scenario default | Override the scenario's system prompt |
| `--user-prompt TEXT` | scenario default | Override the scenario's user prompt |
| `--mcp-url URL` | _(empty)_ | MCP tool server URL |
| `--ssh-host HOST` | _(none)_ | Remote SSH host; enables remote SSH execution |
| `--ssh-port PORT` | `22` | Remote SSH port |
| `--ssh-user USER` | `root` | Remote SSH username |
| `--ssh-password PASS` | _(none)_ | Remote SSH password |
| `--ssh-key-path PATH` | _(none)_ | Path to SSH private key file |
| `--json` | off | Print full telemetry dict as JSON on completion |
| `-v`, `--verbose` | off | Enable DEBUG-level logging |
| `--dry-run` | off | Print assembled config (redacting password) and exit |
| `--session-dir PATH` | `logs/sessions/` | Override root directory for session logs |

---

## `batch` — queue of jobs

```bash
modelscope batch --jobs-file PATH [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--jobs-file PATH` | _(required)_ | Path to a JSON array of job spec objects |
| `--parallel N` | `1` | Number of concurrent jobs |
| `--output-dir PATH` | `./batch_results` | Directory for CSV + JSON summary output |
| `-v`, `--verbose` | off | Enable DEBUG-level logging |

SSH jobs are not supported in batch mode. If a job spec contains `ssh_host`, the CLI prints a warning and skips that job.

---

## `sessions` — browse past session logs

```bash
modelscope sessions list [--sessions-dir PATH] [-n N]
modelscope sessions show SESSION [--sessions-dir PATH]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--sessions-dir PATH` | `logs/sessions/` | Override the sessions root directory |
| `-n N`, `--limit N` | `20` | Maximum sessions to display (most recent first) |
| `SESSION` | _(required)_ | Full session dir name or trailing 8-char run ID (e.g. `828cc8a1`) |

---

## `scenarios` — list and inspect scenarios

```bash
modelscope scenarios [--describe NAME]
```

| Flag | Description |
|------|-------------|
| `--describe NAME` | Print the full config for the named scenario |

---

## Legacy flags (backward compatible)

```bash
modelscope --list-scenarios          # same as: modelscope scenarios
modelscope --model qwen2.5 ...       # auto-inserts 'run' subcommand
```

---

## Config file overrides

Persistent defaults can be set in `~/.modelscope/cli.json`. Keys use the long-form flag names with underscores.

```json
{
  "backend": "ollama",
  "llm_url": "http://localhost:11434",
  "context_size": 8192
}
```

If `cli.json` is absent and `pyyaml` is installed, `~/.modelscope/cli.yaml` is tried instead.

**Merge order (lowest to highest priority):**

1. argparse built-in defaults
2. `~/.modelscope/cli.json`
3. Environment variables: `MODELSCOPE_<DEST>` (e.g. `MODELSCOPE_MODEL`, `MODELSCOPE_LLM_URL`, `MODELSCOPE_SSH_PASSWORD`)
4. Explicit CLI flags

## Exit codes

| Code | Meaning |
|------|---------|
| `0` | Evaluation passed (validation command succeeded) |
| `1` | Evaluation failed or validation returned non-zero |
