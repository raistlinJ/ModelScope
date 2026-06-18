# ModelScope — Development Notes

## Session Logs

Every CAF and standard evaluation run saves structured logs to:

    ModelScope/logs/sessions/YYYY-MM-DD_HH-MM-SS_<run-id>/

Each session directory contains:
- `run.log` — full timestamped terminal output
- `telemetry.json` (or `telemetry_0.json`, `telemetry_1.json` for multi-prompt CAF runs) — metrics and run metadata
- `config.json` — sanitised run configuration (sensitive keys stripped)

When debugging a run or analysing results, read these files directly.
The `logs/` directory is `.gitignored` and never committed.

## Settings

User settings (non-sensitive) are persisted to `~/.modelscope/settings.json`.
