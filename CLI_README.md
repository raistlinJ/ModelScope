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

`project` is the headless entry point for **every** bot type — Bash-Bot,
Llama-CLI-Bot, Llama-Server-Bot, Llama-Server-ProxBatch, CAF Standard and
CAF + llama.cpp. It reads the project's `type`, resolves that bot's plugin
through the bot-type registry and hands the run to it, so a CLI run behaves
exactly like the Execute tab.

Managed Llama-Server projects start the configured `llama-server` binary, wait
for `/health`, collect its Prometheus metrics, and stop it when the evaluation
completes. ProxBatch projects run the workflow once per selected LXC and roll
the per-container results up into one record. Each non-dry run writes
`run.log`, `telemetry.json`, and a credential-sanitized `config.json` under
`logs/sessions/`.

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

## Exit codes

Returned by `project`, so a run can gate a script or a CI step.

| Code | Meaning |
|------|---------|
| `0` | Validation passed, or the project configures no validation |
| `1` | Validation failed, or the run was aborted |
| `2` | Bad arguments (argparse) |
| `130` | Interrupted with Ctrl+C |
