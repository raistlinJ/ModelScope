# ModelScope Architecture

ModelScope is an evaluation framework for LLM cyber agents. It drives a model
through an agentic tool-use loop (locally) or delegates an entire run to a
remote CyberAgentFlow (CAF) CLI over SSH, captures telemetry, scores it against
typed metrics, and presents the results in a Streamlit UI or a CLI.

There are two front ends over one shared core:

```
  app.py  (Streamlit UI)  ─┐
                           ├──►  core/  (business logic)  ──►  config/  (presets + metrics)
  cli.py  (terminal CLI)  ─┘
```

`config/` and most of `core/` are pure (stdlib + requests, no Streamlit) so the
CLI can use them. A handful of `core/` modules are deliberately UI-coupled and
must not be imported from the CLI — see `core/__init__.py` for the exact list.


## Module responsibilities

### `config/` — static configuration (pure, no Streamlit)
- **defaults.py** — URLs, filesystem paths and tunable limits; single source of truth.
- **metrics.py** — typed metric registry (`METRIC_TYPES`), a `type → evaluator`
  dispatch table (`_EVALUATORS`), and `evaluate_metric()` / `make_metric()` /
  `format_criterion()`. This is the Strategy pattern: adding a metric means
  registering one function, never editing an if/elif chain.
- **scenarios.py** — built-in named presets (`SCENARIOS`) bundling prompts
  and validation commands.

### `core/` — framework logic
Pure / CLI-safe:
- **environment.py** — `BaseEnvironment` abstraction + `LocalEnvironment` and
  `SSHEnvironment` concretions, plus the `create_environment()` factory. The
  only place that decides which environment to build.
- **bot_types/** — discovery-backed bot-type plugin registry. Each plugin owns
  its project defaults, session-state hydration map, Streamlit render dispatch,
  CLI normalisation, and evaluator dispatch. `BashBotPlugin` is the base
  lifecycle; `LlamaCliBotPlugin` extends it as a plugin.
- **evaluator.py** — the local LLM agent loop: send prompt → execute tool calls
  against the environment → accumulate telemetry → run validation.
- **caf_runner.py** — remote CAF execution over SSH: build the CLI command,
  stream output, parse the run ID, pull artifacts, derive telemetry.
- **caf_state.py** — CAF per-step telemetry dataclass, phase inference, Task
  Difficulty Index (TDI) scoring helpers.
- **session_log.py** — `SessionLog` (writer: per-run `run.log` / `telemetry.json`
  / `config.json`) and `SessionRepository` (reader: list / find / parse
  sessions). `default_sessions_dir()` is the single source of truth for where
  sessions live.
- **judge.py** — LLM-as-judge scoring of a transcript.
- **schema_registry.py** — JSON-schema validation of tool/telemetry shapes.
- **preflight.py** — environment readiness checks.
- **test_runner.py** — scenario test-suite execution.
- **streaming.py** — backend HTTP adapters (`stream_ollama`, `stream_llama_cpp`)
  for one LLM round.
- **models.py** — model discovery (GGUF files on disk, served model lists).
- **settings_store.py** — persist/load non-sensitive user settings.
- **logsetup.py** — centralised logging configuration.
- **utils.py** — dependency-free shared helpers (`ensure_http_scheme`,
  `strip_ansi`).

UI-coupled (import Streamlit — UI path only):
- **state.py** — session-state defaults and scenario sync.
- **llama_server.py** — local llama-server process lifecycle.
- **mcp_manager.py** — MCP tool-server lifecycle + tool invocation.
- **batch_runner.py** — CLI batch evaluation runner.

### `ui/` — Streamlit tabs and components
One module per tab (`config_tab`, `target_tab`, `execute_tab`, `caf_tab`,
`dashboard_tab`) plus shared `components`, `styles`, `terminal`. Tabs handle
widgets and presentation only — they call into `core/` for any actual work.


## Data flow: how a run happens

```
  UI Execute tab / cli.py run
        │  builds a `config` dict (prompts, backend, validation, CAF settings)
        │  builds an environment via core.environment.create_environment(...)
        ▼
  core.evaluator.run_evaluation(env, config, on_log)
        │
        ├─ execution_mode == "local":
        │     loop up to 8 turns:
        │       core.streaming.stream_*()      → one LLM round
        │       core.evaluator._execute_tool() → run tool via env (or MCP)
        │       accumulate telemetry + CAF per-step TDI
        │     core.evaluator._run_validation() → run validation command via env
        │
        └─ execution_mode == "caf_ssh"  (env.is_remote_caf is True):
              core.caf_runner.run_caf_ssh_evaluation(env, config, on_log)
                build CAF CLI command → env.execute_streaming()
                parse run_id → pull transcript.md / events.jsonl back
                derive telemetry from artifacts
        ▼
  telemetry dict returned
        │
        ├─ SessionLog.save_telemetry/save_config  → logs/sessions/<ts>_<id>/
        ├─ appended to st.session_state["run_history"]  (UI)
        └─ config.metrics.evaluate_metric()  → per-metric pass/fail in dashboard
```

The `env` instance is the single seam between "what to run" and "where it runs",
so neither the evaluator nor the metrics code knows or cares whether execution
is local or remote.


## How to extend

**Add a new scenario** — add a key to `SCENARIOS` in `config/scenarios.py`
(system prompt, sample prompts, validation command, fail patterns, metrics
matrix). The UI and CLI discover it automatically. Run `validate_scenarios()`
to sanity-check the shape.

**Add a new metric type** — in `config/metrics.py`: (1) add an entry to
`METRIC_TYPES` describing its label/category/params, (2) write an
`_eval_<name>(params, telemetry) -> bool | None` function, (3) register it in
the `_EVALUATORS` dict. No dispatch code changes.

**Add a new environment type** — in `core/environment.py`: subclass
`BaseEnvironment`, implement `execute` / `read_file` / `write_file` /
`delete_file` / `exists`, set the `is_remote_caf` capability flag, and teach
`create_environment()` how to build it. Callers stay untouched because they go
through the factory.

**Add a new bot type** — create a `BotTypePlugin` subclass. Built-in plugins are
auto-discovered from `core/bot_types/*.py`; external plugins can be exposed by
Python entry point group `modelscope.bot_types`, dropped into
`plugins/bot_types/` or `~/.modelscope/bot_types/`, or loaded from paths listed
in `MODELSCOPE_BOT_PLUGIN_PATH` (use the platform path separator for multiple
entries). A bot-type plugin provides the sidebar label/icon, new-project
defaults, project sync map, optional template metadata, UI render dispatch, CLI
config normalisation, and evaluator dispatch. The Streamlit app, configuration
tab, execute tab, project sync, and `cli.py project` all route through discovery.


## Running tests

```
cd ModelScope
python -m pytest -q                      # full suite (pytest-randomly shuffles order)
python -m pytest -q -p no:randomly       # fixed collection order
```

The full suite passes (1226 tests as of this writing). The suite uses
`pytest-randomly`; if you ever see order-dependent failures, reproduce the seed
it prints (`-p randomly --randomly-seed=<n>`) or pin order with `-p no:randomly`
to isolate whether it is a test-isolation issue (shared mocked Streamlit/session
state) rather than a product bug.

Quick import sanity check:

```
python -c "import app; import cli"
```
