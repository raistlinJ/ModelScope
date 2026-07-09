#!/usr/bin/env python3
"""ModelScope CLI — terminal-only evaluation runner.

Entry points:
    python cli.py project --file proj.json           # run an exported project
    python cli.py scenarios                          # list scenarios
    python cli.py run --model qwen --scenario "..."  # single run
    python cli.py batch --jobs-file jobs.json        # batch queue
    python cli.py sessions list                      # browse past sessions
    python cli.py sessions show <id-or-dir>          # inspect a session

Backward compat:
    python cli.py --list-scenarios                   # still works
    python cli.py --model qwen ...                   # auto-inserts 'run'

Examples:
    python cli.py project --file bash_project.json
    python cli.py --list-scenarios
    python cli.py --backend llama.cpp --model my-model --scenario "Scenario 1 – File Creation"
    python cli.py run --model qwen2.5 --scenario "Scenario 1 – File Creation" --dry-run
    python cli.py batch --jobs-file jobs.json --parallel 2
    python cli.py sessions list
    python cli.py sessions show 828cc8a1

Note: If running directly from the repository without activating the virtual environment, prefix with `.venv/bin/`:
    .venv/bin/python cli.py project --file my_project.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
from typing import Any

# ── ANSI color support ────────────────────────────────────────────────────────

def _use_color() -> bool:
    """Return True if ANSI output is appropriate for this terminal."""
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


# ANSI escape codes
_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_GREEN  = "\033[32m"
_BLUE   = "\033[34m"
_DIM    = "\033[2m"
_WHITE  = "\033[37m"


def _c(text: str, *codes: str) -> str:
    """Wrap text in ANSI codes if color is enabled."""
    if not _use_color():
        return text
    return "".join(codes) + text + _RESET


# Tag colorization for on_log messages
_TAG_COLORS: dict[str, str] = {
    "[LLM]":        _CYAN,
    "[TOOL CALL]":  _YELLOW,
    "[TOOL RESULT]":_YELLOW,
    "[ERROR]":      _RED,
    "[INIT]":       _GREEN,
    "[SETUP]":      _GREEN,
    "[SESSION]":    _DIM,
    "[COMPLETE]":   _GREEN,
    "[ABORTED]":    _RED,
    "[CANCEL]":     _RED,
    "[WARN]":       _YELLOW,
    "[RESPONSE]":   _WHITE,
    "[CAF]":        _CYAN,
}


def _colorize_log_line(msg: str) -> str:
    """Apply tag-based coloring to a log message."""
    if not _use_color():
        return msg
    stripped = msg.lstrip()
    for tag, code in _TAG_COLORS.items():
        if stripped.startswith(tag):
            # Color just the tag portion
            idx = msg.find(tag)
            return msg[:idx] + code + tag + _RESET + msg[idx + len(tag):]
    return msg


# ── Config file support ───────────────────────────────────────────────────────

def _load_config_file() -> dict[str, Any]:
    """Load ~/.modelscope/cli.json (or cli.yaml if yaml is available).

    Merge order: config file < env vars < CLI flags.
    Returns an empty dict if no config file is found.
    """
    config_dir = pathlib.Path.home() / ".modelscope"
    result: dict[str, Any] = {}

    # Try JSON first
    json_path = config_dir / "cli.json"
    if json_path.exists():
        try:
            with open(json_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                result.update(data)
            return result
        except Exception:
            pass

    # Try YAML if available
    yaml_path = config_dir / "cli.yaml"
    if yaml_path.exists():
        try:
            import yaml  # type: ignore
            with open(yaml_path, encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if isinstance(data, dict):
                result.update(data)
        except ImportError:
            pass  # yaml not installed, silently skip
        except Exception:
            pass

    return result


# ── Box-drawing summary table ─────────────────────────────────────────────────

def _box_table(rows: list[dict], title: str = "") -> str:
    """Render a simple ASCII box-drawing table from a list of dicts."""
    if not rows:
        return "(no data)"

    cols = list(rows[0].keys())
    widths = {c: max(len(str(c)), max(len(str(r.get(c, ""))) for r in rows))
              for c in cols}

    def sep(left: str, mid: str, right: str, fill: str = "─") -> str:
        parts = [fill * (widths[c] + 2) for c in cols]
        return left + mid.join(parts) + right

    def row_line(r: dict) -> str:
        cells = [f" {str(r.get(c, '')).ljust(widths[c])} " for c in cols]
        return "│" + "│".join(cells) + "│"

    lines: list[str] = []
    if title:
        lines.append(_c(f"  {title}", _BOLD))
    lines.append(sep("┌", "┬", "┐"))
    header_cells = [f" {str(c).ljust(widths[c])} " for c in cols]
    lines.append("│" + "│".join(
        _c(cell, _BOLD) for cell in header_cells) + "│")
    lines.append(sep("├", "┼", "┤"))
    for r in rows:
        lines.append(row_line(r))
    lines.append(sep("└", "┴", "┘"))
    return "\n".join(lines)


# Progress indicator: run_evaluation is synchronous and streams structured
# [LLM] / [TOOL CALL] / [DONE] log lines through the on_log callback, which
# _ColorFormatter colorizes as they arrive.  That streaming output serves as
# the live progress indicator; a separate spinner would interleave messily
# with those lines and is intentionally omitted.

# ── Imports from ModelScope core ──────────────────────────────────────────────

from config.defaults import (
    DEFAULT_CONTEXT_SIZE,
    LLAMA_CPP_DEFAULT_URL,
    OLLAMA_DEFAULT_URL,
)
from core.evaluator import run_evaluation
from core.logsetup import configure_logging, logged_on_log
from core.session_log import SessionLog


# ── Argument parsing ──────────────────────────────────────────────────────────

def _add_run_args(parser: argparse.ArgumentParser) -> None:
    """Add all flags that apply to a single evaluation run."""
    # ── Model / backend ───────────────────────────────────────────────────────
    parser.add_argument("--model", help="Model name/id to evaluate.")
    parser.add_argument(
        "--backend",
        choices=["llama.cpp", "ollama"],
        default="llama.cpp",
        help="Inference backend.",
    )
    parser.add_argument(
        "--llm-url",
        dest="llm_url",
        default=None,
        help="LLM server URL. Defaults to the backend's standard local URL.",
    )
    parser.add_argument(
        "--context-size",
        dest="context_size",
        type=int,
        default=DEFAULT_CONTEXT_SIZE,
        help="Context window size in tokens.",
    )

    # ── System/User prompts ───────────────────────────────────────────────────
    parser.add_argument(
        "--system-prompt",
        dest="system_prompt",
        default=None,
        help="Override the system prompt.",
    )
    parser.add_argument(
        "--user-prompt",
        dest="user_prompt",
        default=None,
        help="Override the user prompt.",
    )

    # ── MCP ───────────────────────────────────────────────────────────────────
    parser.add_argument("--mcp-url", dest="mcp_url", default="", help="MCP tool server URL.")

    # ── SSH / remote target ───────────────────────────────────────────────
    parser.add_argument("--ssh-host", dest="ssh_host", default=None,
                        help="Remote SSH host (enables SSH execution).")
    parser.add_argument("--ssh-port", dest="ssh_port", type=int, default=22,
                        help="Remote SSH port.")
    parser.add_argument("--ssh-user", dest="ssh_user", default="root",
                        help="Remote SSH username.")
    parser.add_argument("--ssh-password", dest="ssh_password", default=None,
                        help="Remote SSH password.")
    parser.add_argument("--ssh-key-path", dest="ssh_key_path", default=None,
                        help="Path to SSH private key.")
    parser.add_argument("--pct-vmid", dest="pct_vmid", default=None,
                        help="Proxmox container VMID (enables PCT execution mode).")


    # ── Output / control ──────────────────────────────────────────────────────
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full telemetry dict as JSON at the end.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Print assembled config (redacting password) and exit without running.",
    )
    parser.add_argument(
        "--session-dir",
        dest="session_dir",
        default=None,
        help=(
            "Root directory for session logs "
            "(default: ModelScope/logs/sessions/). "
            "Each run creates a timestamped sub-directory containing "
            "run.log, telemetry.json, and config.json."
        ),
    )


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="modelscope",
        description="ModelScope — LLM cyber-agent evaluation framework.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""subcommands:
  run        Run a single evaluation (default when --model is given)
  batch      Execute a queue of jobs from a JSON file
  sessions   Browse past session logs

examples:
  modelscope --model qwen2.5
  modelscope run --model qwen2.5 --dry-run
  modelscope batch --jobs-file jobs.json --parallel 2
  modelscope sessions list
  modelscope sessions show 828cc8a1
""",
    )

    # ── Legacy top-level flags (backward compat) ──────────────────────────────
    # --list-scenarios removed - scenarios concept deleted
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    subparsers = parser.add_subparsers(dest="subcommand", metavar="subcommand")

    # ── run subcommand ────────────────────────────────────────────────────────
    run_p = subparsers.add_parser(
        "run",
        help="Run a single evaluation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    _add_run_args(run_p)

    # ── project subcommand ──────────────────────────────────────────────────────
    project_p = subparsers.add_parser(
        "project",
        help="Run an evaluation using a project configuration JSON file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    project_p.add_argument(
        "-f", "--file",
        dest="project_file",
        required=True,
        metavar="PATH",
        help="Path to an exported project JSON configuration file.",
    )
    project_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the loaded configuration and exit without running.",
    )
    project_p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )
    project_p.add_argument(
        "--ssh-user",
        default=None,
        help="Override the SSH username (also reads MODELSCOPE_SSH_USER).",
    )
    project_p.add_argument(
        "--ssh-password",
        default=None,
        help="Override the SSH password (also reads MODELSCOPE_SSH_PASSWORD).",
    )
    project_p.add_argument(
        "--ssh-key-path",
        default=None,
        help="Override the SSH key path (also reads MODELSCOPE_SSH_KEY_PATH).",
    )
    project_p.add_argument(
        "--sudo-password",
        default=None,
        help="Override the sudo password (also reads MODELSCOPE_SUDO_PASSWORD).",
    )
    project_p.add_argument(
        "--openai-api-key",
        default=None,
        help="Override the OpenAI API key (also reads MODELSCOPE_OPENAI_API_KEY).",
    )
    project_p.add_argument(
        "--llm-helper-api-key",
        dest="llm_helper_api_key",
        default=None,
        help="Override the LLM Judge / prompt-helper API key "
             "(also reads MODELSCOPE_LLM_HELPER_API_KEY).",
    )

    # ── batch subcommand ──────────────────────────────────────────────────────
    batch_p = subparsers.add_parser(
        "batch",
        help="Execute a queue of evaluation jobs from a JSON file.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    batch_p.add_argument(
        "--jobs-file",
        dest="jobs_file",
        required=True,
        metavar="PATH",
        help=(
            "Path to a JSON file containing a list of job specs. "
            'Example: [{"scenario": "Scenario 1 – File Creation", '
            '"model": "qwen2.5", "backend": "ollama"}]'
        ),
    )
    batch_p.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help="Number of jobs to run concurrently.",
    )
    batch_p.add_argument(
        "--output-dir",
        dest="output_dir",
        default="./batch_results",
        help="Directory for batch results (CSV + JSON summary).",
    )
    batch_p.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable DEBUG-level logging.",
    )

    # ── sessions subcommand ───────────────────────────────────────────────────
    sessions_p = subparsers.add_parser(
        "sessions",
        help="Browse past session logs.",
    )
    sessions_sub = sessions_p.add_subparsers(dest="sessions_action", metavar="action")

    sessions_list_p = sessions_sub.add_parser(
        "list",
        help="List all sessions with summary metrics.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sessions_list_p.add_argument(
        "--sessions-dir",
        dest="sessions_dir",
        default=None,
        help="Override the sessions root directory.",
    )
    sessions_list_p.add_argument(
        "-n", "--limit",
        type=int,
        default=20,
        metavar="N",
        help="Maximum number of sessions to display (most recent first).",
    )

    sessions_show_p = sessions_sub.add_parser(
        "show",
        help="Show run.log and telemetry summary for a session.",
    )
    sessions_show_p.add_argument(
        "session_id",
        metavar="SESSION",
        help=(
            "Session directory name or the trailing 8-character run ID "
            "(e.g. '2026-06-18_15-41-00_828cc8a1' or just '828cc8a1')."
        ),
    )
    sessions_show_p.add_argument(
        "--sessions-dir",
        dest="sessions_dir",
        default=None,
        help="Override the sessions root directory.",
    )

    # --scenarios subcommand removed - scenarios concept deleted

    return parser


# ── Config dict assembly ──────────────────────────────────────────────────────

def _build_config(args: argparse.Namespace) -> dict:
    """Assemble the evaluator config dict from parsed args."""
    default_url = LLAMA_CPP_DEFAULT_URL if args.backend == "llama.cpp" else OLLAMA_DEFAULT_URL
    llm_url = (args.llm_url or default_url).strip()

    sys_prompt = args.system_prompt or ""
    user_prompt = args.user_prompt or ""

    is_ssh = bool(args.ssh_host)

    config: dict = {
        "backend_type":        args.backend,
        "llm_url":             llm_url,
        "selected_model":      args.model,
        "context_size":        args.context_size,
        "sys_prompt":          sys_prompt,
        "user_prompt":         user_prompt,
        "mcp_url":             args.mcp_url,
        "mcp_server_url":      "",
        "mcp_tools":           {},
        "mcp_running":         bool(args.mcp_url),
        "validation_command":  "",
        "fail_patterns":       [],
        "active_scenario":     "",
        "tool_focus":          "",
        "metrics_matrix":      [],
        "expected_stdout":     "",
        "pre_run_cleanup":     [],
        "cancel_requested_ref": [False],
        "execution_mode":      "ssh" if is_ssh else "local",
    }
    return config


def _make_env(args: argparse.Namespace, on_log):
    """Instantiate the execution environment via the central factory."""
    from core.environment import create_environment

    if args.ssh_host:
        env = create_environment(
            ssh=True,
            host=args.ssh_host,
            port=args.ssh_port,
            username=args.ssh_user,
            password=args.ssh_password,
            key_path=args.ssh_key_path,
            remote_cwd=None,
        )
        on_log(f"[INIT] Target: SSH ({args.ssh_user}@{args.ssh_host})")
        return env

    env = create_environment(ssh=False)
    on_log("[INIT] Target: Local")
    return env


def _apply_config_file_defaults(
    args: argparse.Namespace,
    file_cfg: dict[str, Any],
    argv_used: list[str],
) -> None:
    """Apply config-file and environment-variable defaults.

    Merge order (lowest to highest priority):
      1. argparse built-in defaults
      2. Config file  (~/.modelscope/cli.json)
      3. Environment variables  MODELSCOPE_<DEST>  (e.g. MODELSCOPE_MODEL)
      4. Explicit CLI flags (these are never overwritten)

    Config file keys use the long-form flag names with underscores
    (e.g. 'llm_url', 'context_size').  Environment variables follow
    the same convention uppercased with a MODELSCOPE_ prefix
    (e.g. MODELSCOPE_LLM_URL, MODELSCOPE_CONTEXT_SIZE).
    """
    # Build the set of dest names that were explicitly passed on the CLI
    explicitly_set: set[str] = set()
    for token in argv_used:
        if token.startswith("--"):
            flag = token.split("=")[0]           # strip =value if present
            dest = flag.lstrip("-").replace("-", "_")
            explicitly_set.add(dest)
        elif token.startswith("-") and len(token) == 2:
            # Short flag like -v
            explicitly_set.add(token[1:])

    # Tier 2: config file — apply only where CLI was silent
    for key, value in file_cfg.items():
        dest = key.replace("-", "_")
        if not hasattr(args, dest):
            continue
        if dest not in explicitly_set:
            setattr(args, dest, value)

    # Tier 3: environment variables — apply only where CLI was silent.
    # An env var overrides the config file but not an explicit CLI flag.
    # Boolean flags (store_true) are set when the env var is "1", "true", or "yes".
    for dest in vars(args):
        if dest in explicitly_set:
            continue  # CLI flag wins; skip
        env_key = "MODELSCOPE_" + dest.upper()
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        current = getattr(args, dest)
        if isinstance(current, bool):
            setattr(args, dest, raw.lower() in ("1", "true", "yes"))
        elif isinstance(current, int):
            try:
                setattr(args, dest, int(raw))
            except ValueError:
                pass
        elif isinstance(current, float):
            try:
                setattr(args, dest, float(raw))
            except ValueError:
                pass
        else:
            setattr(args, dest, raw)


# ── Terminal summary table ────────────────────────────────────────────────────

def _print_run_summary(telemetry: dict) -> None:
    """Print a final ASCII summary table for a completed run."""
    from config.metrics import evaluate_metric

    passed = telemetry.get("validation_passed")
    if passed is True:
        pass_str = _c("PASSED", _GREEN, _BOLD)
    elif passed is False:
        pass_str = _c("FAILED", _RED, _BOLD)
    else:
        pass_str = _c("N/A", _DIM)

    aborted = telemetry.get("run_aborted", False)
    status_str = (_c("ABORTED", _RED, _BOLD) if aborted
                  else _c("COMPLETE", _GREEN))

    latency_s = telemetry.get("total_latency", 0.0)
    latency_ms = round(latency_s * 1000) if latency_s else 0

    # Evaluate the metrics matrix stored in telemetry (if any)
    matrix = telemetry.get("metrics_matrix", [])
    metric_results = [
        evaluate_metric(m, telemetry)
        for m in matrix
        if isinstance(m, dict) and m.get("enabled", True)
    ]
    metrics_passed = sum(1 for r in metric_results if r is True)
    metrics_failed = sum(1 for r in metric_results if r is False)
    metrics_total  = len([r for r in metric_results if r is not None])

    if metrics_total > 0:
        metrics_str = (
            _c(str(metrics_passed), _GREEN)
            + " / "
            + _c(str(metrics_failed), _RED if metrics_failed else _DIM)
            + _c(f"  ({metrics_total} evaluated)", _DIM)
        )
    else:
        metrics_str = _c("—", _DIM)

    rows = [
        {"Field": "Scenario",         "Value": telemetry.get("run_scenario", "—")},
        {"Field": "Model",            "Value": telemetry.get("run_model", "—")},
        {"Field": "Backend",          "Value": telemetry.get("run_backend", "—")},
        {"Field": "Status",           "Value": status_str},
        {"Field": "Validation",       "Value": pass_str},
        {"Field": "Metrics Passed",   "Value": metrics_str},
        {"Field": "Latency",          "Value": f"{latency_ms} ms ({latency_s:.3f}s)"},
        {"Field": "Prompt Tokens",    "Value": str(telemetry.get("prompt_tokens", 0))},
        {"Field": "Completion Tokens","Value": str(telemetry.get("completion_tokens", 0))},
        {"Field": "Total Tokens",     "Value": str(telemetry.get("total_tokens", 0))},
        {"Field": "LLM Rounds",       "Value": str(telemetry.get("llm_rounds", 0))},
        {"Field": "Tool Calls",       "Value": str(len(telemetry.get("tool_calls", [])))},
    ]
    print(_box_table(rows, title="Run Summary"))


# ── `project` subcommand ──────────────────────────────────────────────────────

def _cmd_project(args: argparse.Namespace) -> int:
    """Execute an evaluation from an exported project JSON configuration."""
    from core.session_log import SessionLog
    from core.evaluator import run_bash_evaluation, run_llama_cli_evaluation, run_evaluation
    from core.environment import create_environment

    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    # Attach colorizing formatter if terminal supports it
    if _use_color():
        logger = logging.getLogger("modelscope")
        for handler in logger.handlers:
            if getattr(handler, "_modelscope_handler", False):
                original_fmt = handler.formatter
                class _ColorFormatter(logging.Formatter):
                    def format(self, record: logging.LogRecord) -> str:
                        line = super().format(record)
                        return _colorize_log_line(line)
                color_fmt = _ColorFormatter(
                    "%(asctime)s %(levelname)-7s [modelscope] %(message)s",
                    datefmt="%H:%M:%S",
                )
                handler.setFormatter(color_fmt)
                break

    try:
        with open(args.project_file, "r") as f:
            proj = json.load(f)
    except Exception as e:
        print(_c(f"error: failed to read project file: {e}", _RED), file=sys.stderr)
        return 1

    # Extract top level config
    config = proj.get("config", {})
    bot_type = proj.get("type", "")

    # Inject required keys for run_evaluation loop
    config.setdefault("cancel_requested_ref", [False])

    # Mirror the GUI's run-config assembly (ui/execute_tab.py) so a project
    # file runs identically from the CLI.  The evaluator reads alias keys
    # (backend_type/selected_model/llm_url/context_size) that the exported
    # config stores under their flushed names.
    if bot_type == "llama_cli_bot":
        config.setdefault("type", "llama_cli_bot")
        config.setdefault("backend_type", config.get("backend", "llama.cpp"))
        config.setdefault("selected_model", config.get("model_name", ""))
        config.setdefault("context_size", config.get("tokens", 2048))
        config.setdefault("llm_url", config.get("openai_base_url", ""))
        config.setdefault("mcp_server_url", "http://127.0.0.1:9191")
        config["mcp_servers"] = [
            s for s in config.get("mcp_servers", []) if s.get("enabled")
        ]

    # Merge sensitive credentials (Flags > Env > JSON)
    def _resolve_secret(key: str, flag_name: str, env_name: str):
        flag_val = getattr(args, flag_name, None)
        if flag_val is not None:
            config[key] = flag_val
        elif os.environ.get(env_name):
            config[key] = os.environ[env_name]

    _resolve_secret("ssh_user", "ssh_user", "MODELSCOPE_SSH_USER")
    _resolve_secret("ssh_password", "ssh_password", "MODELSCOPE_SSH_PASSWORD")
    _resolve_secret("ssh_key_path", "ssh_key_path", "MODELSCOPE_SSH_KEY_PATH")
    _resolve_secret("sudo_password", "sudo_password", "MODELSCOPE_SUDO_PASSWORD")
    _resolve_secret("openai_api_key", "openai_api_key", "MODELSCOPE_OPENAI_API_KEY")
    _resolve_secret("llm_helper_openai_apikey", "llm_helper_api_key", "MODELSCOPE_LLM_HELPER_API_KEY")

    # Prefer key over password if both are present
    if config.get("ssh_key_path") and config.get("ssh_password"):
        config.pop("ssh_password", None)

    # Dry-run: print config and exit
    if getattr(args, "dry_run", False):
        safe_config = {
            k: ("***REDACTED***"
                if ("password" in k.lower() or "api_key" in k.lower() or "apikey" in k.lower())
                else v)
            for k, v in config.items()
        }
        print(_c(f"Dry-run config for project '{proj.get('name', 'Unknown')}' (no evaluation will run):", _BOLD))
        print(json.dumps(safe_config, indent=2, default=str))
        return 0

    session_log = SessionLog()

    def _base_on_log(msg: str, *args, **kwargs) -> None:
        session_log.log(msg)

    on_log = logged_on_log(inner=_base_on_log)

    exec_target = config.get("execution_target", "local")
    is_ssh = exec_target == "ssh"
    is_pct = exec_target == "pct"

    env = None
    try:
        env = create_environment(
            ssh=is_ssh,
            host=config.get("ssh_host", ""),
            port=config.get("ssh_port", 22),
            username=config.get("ssh_user", "root"),
            password=config.get("ssh_password"),
            key_path=config.get("ssh_key_path"),
            remote_cwd="",
            pct_vmid=config.get("pct_vmid") if is_pct else None,
        )
        if is_pct:
            on_log(f"[INIT] Target: PCT (VMID: {config.get('pct_vmid', '?')}) via SSH/Local")
        elif is_ssh:
            on_log(
                f"[INIT] Target: SSH "
                f"({config.get('ssh_user', 'root')}@"
                f"{config.get('ssh_host', '?')})"
            )
        else:
            on_log("[INIT] Target: Local")

        if bot_type == "bash_bot":
            telemetry = run_bash_evaluation(env, config, on_log)
        elif bot_type == "llama_cli_bot":
            telemetry = run_llama_cli_evaluation(env, config, on_log)
        else:
            # Fallback to main Cyber Agent
            telemetry = run_evaluation(env, config, on_log)

    except KeyboardInterrupt:
        print(_c("\nRun aborted by user (Ctrl+C).", _YELLOW))
        return 130
    finally:
        if env is not None and hasattr(env, "close"):
            env.close()

    if telemetry:
        session_log.save_telemetry(telemetry)
        print()
        _print_run_summary(telemetry)

    return 0


# ── `run` subcommand ──────────────────────────────────────────────────────────

def _cmd_run(args: argparse.Namespace) -> int:
    """Execute a single evaluation run."""
    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    # Attach colorizing formatter if terminal supports it
    if _use_color():
        logger = logging.getLogger("modelscope")
        for handler in logger.handlers:
            if getattr(handler, "_modelscope_handler", False):
                original_fmt = handler.formatter
                class _ColorFormatter(logging.Formatter):
                    def format(self, record: logging.LogRecord) -> str:
                        line = super().format(record)
                        return _colorize_log_line(line)
                color_fmt = _ColorFormatter(
                    "%(asctime)s %(levelname)-7s [modelscope] %(message)s",
                    datefmt="%H:%M:%S",
                )
                handler.setFormatter(color_fmt)
                break

    if not args.model:
        print(_c("error: --model is required.", _RED), file=sys.stderr)
        return 2

    # Dry-run: print config and exit
    if getattr(args, "dry_run", False):
        config = _build_config(args)
        safe_config = {
            k: ("***REDACTED***"
                if ("password" in k.lower() or "api_key" in k.lower() or "apikey" in k.lower())
                else v)
            for k, v in config.items()
        }
        # Also surface SSH connection params (they go to the env, not config dict)
        if args.ssh_host:
            safe_config["_ssh_params"] = {
                "host":     args.ssh_host,
                "port":     args.ssh_port,
                "user":     args.ssh_user,
                "password": "***REDACTED***" if args.ssh_password else None,
                "key_path": args.ssh_key_path,
            }
        print(_c("Dry-run config (no evaluation will run):", _BOLD))
        print(json.dumps(safe_config, indent=2, default=str))
        return 0

    session_log = SessionLog(base_dir=args.session_dir)

    def _base_on_log(msg: str, *args, **kwargs) -> None:
        session_log.log(msg)

    on_log = logged_on_log(inner=_base_on_log)

    config = _build_config(args)
    env = _make_env(args, on_log)

    try:
        telemetry = run_evaluation(env, config, on_log)
    finally:
        if env is not None and hasattr(env, "close"):
            env.close()

    session_log.save_telemetry(telemetry)
    session_log.save_config(config)
    session_log.close()

    _print_run_summary(telemetry)

    if args.json:
        print(json.dumps(telemetry, indent=2, default=str))

    return 0 if telemetry.get("validation_passed") else 1


# ── `batch` subcommand ────────────────────────────────────────────────────────

def _cmd_batch(args: argparse.Namespace) -> int:
    """Execute a queue of evaluation jobs from a JSON jobs file."""
    from core.batch_runner import BatchJob, BatchRunner

    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)
    logger = logging.getLogger("modelscope")

    # Load jobs file
    jobs_path = pathlib.Path(args.jobs_file)
    if not jobs_path.exists():
        print(_c(f"error: jobs file not found: {jobs_path}", _RED), file=sys.stderr)
        return 2
    try:
        with open(jobs_path, encoding="utf-8") as fh:
            job_specs: list[dict] = json.load(fh)
    except Exception as exc:
        print(_c(f"error: failed to parse jobs file: {exc}", _RED), file=sys.stderr)
        return 2

    if not isinstance(job_specs, list):
        print(_c("error: jobs file must contain a JSON array.", _RED), file=sys.stderr)
        return 2

    runner = BatchRunner(max_parallel=args.parallel, output_dir=args.output_dir)

    # Enqueue jobs — BatchRunner._run_single uses LocalEnvironment only.
    # SSH jobs are not supported by BatchRunner (it hardcodes LocalEnvironment).
    # We warn and skip them rather than silently misfiring.
    enqueued = 0
    for i, spec in enumerate(job_specs):
        if not isinstance(spec, dict):
            logger.warning("Job #%d is not a dict — skipping.", i)
            continue

        # scenario_key is no longer validated against SCENARIOS
        # Using "manual" as the default if not specified
        scenario = spec.get("scenario") or spec.get("scenario_key", "manual")

        # Warn about SSH jobs — BatchRunner only supports local execution
        if spec.get("ssh_host"):
            logger.warning(
                "Job #%d (%r): ssh_host is not supported in batch mode "
                "(BatchRunner uses LocalEnvironment). Skipping SSH job.",
                i, scenario,
            )
            continue

        default_url = (
            LLAMA_CPP_DEFAULT_URL if spec.get("backend", "llama.cpp") == "llama.cpp"
            else OLLAMA_DEFAULT_URL
        )

        model_config = {
            "backend_type":   spec.get("backend", "llama.cpp"),
            "llm_url":        spec.get("llm_url", default_url),
            "selected_model": spec.get("model", ""),
            "context_size":   spec.get("context_size", DEFAULT_CONTEXT_SIZE),
            "mcp_url":        spec.get("mcp_url", ""),
            "mcp_server_url": spec.get("mcp_server_url", ""),
            "mcp_tools":      spec.get("mcp_tools", {}),
            "mcp_running":    bool(spec.get("mcp_url", "")),
        }

        job = BatchJob(
            scenario_key=scenario,
            model_config=model_config,
            priority=spec.get("priority", 5),
        )
        runner.enqueue(job)
        enqueued += 1

    if enqueued == 0:
        print(_c("No valid jobs to run.", _YELLOW))
        return 0

    print(_c(f"Queued {enqueued} job(s). Starting batch run...", _CYAN))
    if args.parallel > 1:
        print(_c(f"Running up to {args.parallel} jobs in parallel.", _DIM))

    report = runner.run(on_log=logger.info)

    # Print progress table
    if report.summary_rows:
        table_rows = []
        for row in report.summary_rows:
            status_cell = row["status"]
            if _use_color():
                if row["status"] == "done":
                    status_cell = _c("done", _GREEN)
                elif row["status"] == "failed":
                    status_cell = _c("failed", _RED)
            table_rows.append({
                "ID":       row["job_id"],
                "Label":    row["label"][:32],
                "Status":   status_cell,
                "Latency":  f"{row['latency']}s",
                "Tokens":   str(row["total_tokens"]),
                "Pass":     str(row["passed_metrics"]),
                "Fail":     str(row["failed_metrics"]),
                "Error":    row["error"][:30] if row["error"] else "",
            })
        print(_box_table(table_rows, title="Batch Results"))

    print(
        f"\nTotal: {report.total_jobs}  "
        + _c(f"Done: {report.completed}", _GREEN)
        + "  "
        + (_c(f"Failed: {report.failed}", _RED) if report.failed else f"Failed: {report.failed}")
        + f"  Duration: {report.duration_seconds}s"
    )

    # Save outputs
    out_dir = pathlib.Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_data = runner.export_csv(report)
    if csv_data:
        csv_path = out_dir / "batch_results.csv"
        csv_path.write_text(csv_data, encoding="utf-8")
        print(f"CSV saved: {csv_path}")

    json_path = out_dir / "batch_results.json"
    json_path.write_text(
        json.dumps(
            {"summary": report.summary_rows, "duration_seconds": report.duration_seconds},
            indent=2, default=str,
        ),
        encoding="utf-8",
    )
    print(f"JSON saved: {json_path}")

    # Exit 1 if any jobs crashed or any job had metric failures
    any_fail = report.failed > 0 or any(
        r.get("failed_metrics", 0) > 0 for r in report.summary_rows
    )
    return 1 if any_fail else 0


# ── `sessions` subcommand ─────────────────────────────────────────────────────

def _session_repo(args: argparse.Namespace):
    """Build a SessionRepository honouring a --sessions-dir override.

    Directory discovery and telemetry parsing now live in
    core.session_log.SessionRepository (single source of truth); the CLI keeps
    only the table formatting below.
    """
    from core.session_log import SessionRepository
    override = getattr(args, "sessions_dir", None)
    return SessionRepository(override) if override else SessionRepository()


# Thin backward-compatible shims over SessionRepository. These preserve the
# historical cli.* call surface that the test suite (and any external scripts)
# rely on, while the actual logic lives once in core.session_log.

def _default_sessions_dir() -> pathlib.Path:
    """Return the default sessions directory (repo-relative logs/sessions/)."""
    from core.session_log import default_sessions_dir
    return default_sessions_dir()


def _find_session(sessions_dir: pathlib.Path, session_id: str) -> pathlib.Path | None:
    """Resolve a session directory by full name or trailing 8-char run ID."""
    from core.session_log import SessionRepository
    return SessionRepository(sessions_dir).find_session(session_id)


def _read_telemetry(session_dir: pathlib.Path) -> dict:
    """Read telemetry from a session dir, trying telemetry.json then telemetry_0.json."""
    from core.session_log import SessionRepository
    return SessionRepository(session_dir.parent).read_telemetry(session_dir)


def _cmd_sessions_list(args: argparse.Namespace) -> int:
    """List past sessions in a summary table."""
    repo = _session_repo(args)

    if not repo.base_dir.exists():
        print(_c(f"No sessions found. Directory does not exist: {repo.base_dir}", _YELLOW))
        return 0

    limit = getattr(args, "limit", 20)
    entries = repo.list_sessions(limit=limit)

    if not entries:
        print(_c("No session directories found.", _YELLOW))
        return 0

    rows = []
    for entry in entries:
        tel = repo.read_telemetry(entry)
        latency_s = tel.get("total_latency", None)
        latency_str = f"{round(latency_s * 1000)} ms" if latency_s is not None else "—"
        passed = tel.get("validation_passed")
        if passed is True:
            passed_str = _c("PASSED", _GREEN) if _use_color() else "PASSED"
        elif passed is False:
            passed_str = _c("FAILED", _RED) if _use_color() else "FAILED"
        else:
            passed_str = "—"

        rows.append({
            "Session":       entry.name,
            "Scenario":      (tel.get("run_scenario") or "—")[:35],
            "Model":         (tel.get("run_model") or "—")[:25],
            "Validated":     passed_str,
            "Latency":       latency_str,
            "Tokens":        str(tel.get("total_tokens", "—")),
        })

    print(_box_table(rows, title=f"Sessions  ({len(rows)} shown, most recent first)"))
    return 0


def _cmd_sessions_show(args: argparse.Namespace) -> int:
    """Show run.log and telemetry summary for a specific session."""
    repo = _session_repo(args)
    session_dir = repo.find_session(args.session_id)

    if session_dir is None:
        print(_c(f"error: session '{args.session_id}' not found in {repo.base_dir}", _RED),
              file=sys.stderr)
        return 2

    print(_c(f"Session: {session_dir.name}", _BOLD))
    print()

    # Print telemetry summary
    tel = repo.read_telemetry(session_dir)
    if tel:
        latency_s = tel.get("total_latency", 0.0)
        latency_ms = round(latency_s * 1000) if latency_s else 0
        passed = tel.get("validation_passed")
        if passed is True:
            pass_str = _c("PASSED", _GREEN, _BOLD)
        elif passed is False:
            pass_str = _c("FAILED", _RED, _BOLD)
        else:
            pass_str = "—"

        summary_rows = [
            {"Field": "Scenario",        "Value": tel.get("run_scenario", "—")},
            {"Field": "Model",           "Value": tel.get("run_model", "—")},
            {"Field": "Backend",         "Value": tel.get("run_backend", "—")},
            {"Field": "Timestamp",       "Value": tel.get("run_timestamp", "—")},
            {"Field": "Validation",      "Value": pass_str},
            {"Field": "Latency",         "Value": f"{latency_ms} ms ({latency_s:.3f}s)"},
            {"Field": "Prompt Tokens",   "Value": str(tel.get("prompt_tokens", 0))},
            {"Field": "Completion Tokens","Value": str(tel.get("completion_tokens", 0))},
            {"Field": "Total Tokens",    "Value": str(tel.get("total_tokens", 0))},
            {"Field": "LLM Rounds",      "Value": str(tel.get("llm_rounds", 0))},
            {"Field": "Tool Calls",      "Value": str(len(tel.get("tool_calls", [])))},
            {"Field": "Aborted",         "Value": str(tel.get("run_aborted", False))},
        ]
        print(_box_table(summary_rows, title="Telemetry Summary"))
        print()

    # Print run.log
    run_log_path = session_dir / "run.log"
    if run_log_path.exists():
        print(_c("── run.log ──────────────────────────────────────────────────", _DIM))
        try:
            content = run_log_path.read_text(encoding="utf-8")
            for line in content.splitlines():
                print(_colorize_log_line(line))
        except Exception as exc:
            print(_c(f"error reading run.log: {exc}", _RED))
    else:
        print(_c("(no run.log found)", _DIM))

    return 0


# ── `scenarios` subcommand ────────────────────────────────────────────────────

def _cmd_scenarios(args: argparse.Namespace) -> int:
    """List or describe scenarios (DEPRECATED)."""
    print("Error: 'scenarios' command has been removed. The scenarios concept is no longer supported.")
    print("Please configure evaluations directly using the Configuration tab.")
    return 1


# ── Argument dispatch + backward compat ──────────────────────────────────────

def _maybe_inject_run_subcommand(argv: list[str]) -> list[str]:
    """If no recognized subcommand is present, inject 'run' for backward compat.

    Handles: cli.py --model qwen ...  => cli.py run --model qwen ...
    Does not touch: cli.py -h/--help, cli.py run/batch/...

    The walk correctly skips flag values (--model VALUE) so VALUE is not
    mistaken for an unrecognized positional subcommand.
    """
    subcommands = {"run", "batch", "sessions"}
    help_flags  = {"-h", "--help"}

    # Flags that consume the next token as a value (so skip VALUE).
    # These are the long-form run-subcommand flags that take arguments.
    value_flags = {
        "--model", "--backend", "--llm-url", "--context-size",
        "--system-prompt", "--user-prompt", "--mcp-url",
        "--ssh-host", "--ssh-port", "--ssh-user", "--ssh-password",
        "--ssh-key-path", "--session-dir",
        # short forms
        "-v",
    }

    if not argv:
        return argv

    i = 0
    while i < len(argv):
        arg = argv[i]

        # Passthrough for help / legacy flags
        if arg in help_flags:
            return argv

        # Already has a recognized subcommand
        if arg in subcommands:
            return argv

        if arg.startswith("-"):
            # Flags that look like --flag=value embed value, skip entirely
            if "=" in arg:
                i += 1
                continue
            # Flags that consume the next token
            if arg in value_flags:
                i += 2  # skip flag and its value
                continue
            # Boolean/store_true flags (--json, --dry-run, --verbose, etc.)
            i += 1
            continue

        # First bare positional that is NOT a known subcommand — let parser
        # handle it naturally (will likely error or mean something).
        return argv

    # Reached the end: every token was a flag or its value.
    # Looks like the old flat invocation style — inject 'run'.
    run_indicators = {"--model", "--backend", "--llm-url",
                      "--ssh-host", "--dry-run", "--json"}
    if any(a.split("=")[0] in run_indicators for a in argv):
        return ["run"] + list(argv)

    return argv


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]

    # ── Legacy --list-scenarios flag removed - scenarios concept deleted ──────
    if "--list-scenarios" in raw_argv:
        print("Error: --list-scenarios has been removed.")
        print("The scenarios concept is no longer supported.")
        return 1

    # ── Inject 'run' for backward compat ──────────────────────────────────────
    raw_argv = _maybe_inject_run_subcommand(raw_argv)

    parser = _build_arg_parser()
    args = parser.parse_args(raw_argv)

    # ── Config file + env-var defaults: apply before dispatch ────────────────
    # Always call for the 'run' subcommand so that env-var defaults (tier 3)
    # are applied even when there is no config file on disk.
    file_cfg = _load_config_file()
    if args.subcommand == "run":
        _apply_config_file_defaults(args, file_cfg, raw_argv)

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if args.subcommand == "project":
        return _cmd_project(args)

    if args.subcommand == "run":
        return _cmd_run(args)

    elif args.subcommand == "batch":
        return _cmd_batch(args)

    elif args.subcommand == "scenarios":
        # --scenarios command removed - provide helpful error
        print("Error: 'scenarios' command has been removed. The scenarios concept is no longer supported.")
        print("Please configure evaluations directly using the Configuration tab.")
        return 1

    elif args.subcommand == "sessions":
        action = getattr(args, "sessions_action", None)
        if action == "list":
            return _cmd_sessions_list(args)
        elif action == "show":
            return _cmd_sessions_show(args)
        else:
            # No sessions sub-action: default to list
            class _FakeListArgs:
                sessions_dir = None
                limit = 20
            return _cmd_sessions_list(_FakeListArgs())  # type: ignore

    else:
        # No subcommand at all
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
