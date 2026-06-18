#!/usr/bin/env python3
"""ModelScope CLI — terminal-only evaluation runner (Phase A, issue #8).

A parallel entry point that mirrors the Streamlit *Execute* tab without any UI
dependency. It assembles the same ``config`` dict that ``ui/execute_tab.py``
builds (lines 240-264), selects a ``LocalEnvironment`` or ``SSHEnvironment`` the
same way, and calls the *unchanged* ``core.evaluator.run_evaluation`` engine.

All progress is routed through the shared ``modelscope`` logger so the run is
observable on the terminal (issue #3). The engine's ``on_log`` contract is
preserved exactly via ``core.logsetup.logged_on_log``.

Examples:
    python cli.py --list-scenarios
    python cli.py --backend llama.cpp --model my-model --scenario "Scenario 1 – File Creation"
    python cli.py --model qwen --scenario "CAF – Reconnaissance" \
        --ssh-host 10.0.0.5 --ssh-user root --ssh-password secret \
        --user-prompt "Scan the target subnet"
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from config.defaults import (
    DEFAULT_CONTEXT_SIZE,
    LLAMA_CPP_DEFAULT_URL,
    OLLAMA_DEFAULT_URL,
)
from config.scenarios import SCENARIOS, DEFAULT_SCENARIO
from core.evaluator import run_evaluation
from core.logsetup import configure_logging, logged_on_log
from core.session_log import SessionLog


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="ModelScope terminal evaluation runner (mirrors the Execute tab).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

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
        default=None,
        help="LLM server URL. Defaults to the backend's standard local URL.",
    )
    parser.add_argument(
        "--context-size",
        type=int,
        default=DEFAULT_CONTEXT_SIZE,
        help="Context window size in tokens.",
    )

    # ── Scenario / prompts ────────────────────────────────────────────────────
    parser.add_argument(
        "--scenario",
        default=DEFAULT_SCENARIO,
        help="Scenario name (see --list-scenarios).",
    )
    parser.add_argument(
        "--system-prompt",
        default=None,
        help="Override the scenario's system prompt.",
    )
    parser.add_argument(
        "--user-prompt",
        default=None,
        help="Override the scenario's user prompt.",
    )

    # ── MCP ───────────────────────────────────────────────────────────────────
    parser.add_argument("--mcp-url", default="", help="MCP tool server URL.")

    # ── SSH / remote CAF target ───────────────────────────────────────────────
    parser.add_argument("--ssh-host", default=None, help="Remote SSH host (enables SSH/CAF mode).")
    parser.add_argument("--ssh-port", type=int, default=22, help="Remote SSH port.")
    parser.add_argument("--ssh-user", default="root", help="Remote SSH username.")
    parser.add_argument("--ssh-password", default=None, help="Remote SSH password.")
    parser.add_argument("--ssh-key-path", default=None, help="Path to SSH private key.")
    parser.add_argument(
        "--ssh-caf-dir",
        default="~/cyber-agent-flow",
        help="Remote working directory where CyberAgentFlow is installed.",
    )

    # ── CAF runtime ───────────────────────────────────────────────────────────
    parser.add_argument("--caf-scope", default=None, help="CAF scope (e.g. Narrow/Broad).")
    parser.add_argument("--caf-urgency", default=None, help="CAF urgency (e.g. Speed/Stealth).")

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
        "--list-scenarios",
        action="store_true",
        help="List available scenario names and exit.",
    )
    parser.add_argument(
        "--session-dir",
        default=None,
        help=(
            "Root directory for session logs "
            "(default: ~/.modelscope/sessions).  "
            "Each run creates a timestamped sub-directory containing "
            "run.log, telemetry.json, and config.json."
        ),
    )

    return parser.parse_args(argv)


def _build_config(args: argparse.Namespace) -> dict:
    """Assemble the evaluator config dict — mirror of execute_tab.py:240-264.

    Scenario data supplies prompts, validation, fail patterns and CAF runtime
    defaults; explicit CLI flags override the scenario where provided.
    """
    scenario = SCENARIOS.get(args.scenario, {})

    default_url = LLAMA_CPP_DEFAULT_URL if args.backend == "llama.cpp" else OLLAMA_DEFAULT_URL
    llm_url = (args.llm_url or default_url).strip()

    sys_prompt = (
        args.system_prompt
        if args.system_prompt is not None
        else scenario.get("system_prompt", "")
    )
    user_prompt = (
        args.user_prompt
        if args.user_prompt is not None
        else scenario.get("user_prompt", "")
    )

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
        "validation_command":  scenario.get("validation_command", ""),
        "fail_patterns":       scenario.get("fail_patterns", []),
        "active_scenario":     args.scenario,
        "tool_focus":          scenario.get("related_tool", ""),
        "metrics_matrix":      scenario.get("default_metrics", []),
        "expected_stdout":     scenario.get("expected_stdout", ""),
        "pre_run_cleanup":     scenario.get("pre_run_cleanup", []),
        # CLI runs are synchronous and uninterruptible; supply the same shared
        # cancel reference shape the engine expects so the contract is identical.
        "cancel_requested_ref": [False],
        # CAF 4-Pillar runtime config (CLI flags override scenario defaults).
        "caf_scope":              args.caf_scope or scenario.get("caf_scope", "Narrow"),
        "caf_urgency":            args.caf_urgency or scenario.get("caf_urgency", "Speed"),
        "caf_allowed_subnets":    scenario.get("caf_allowed_subnets", []),
        "caf_target_credentials": scenario.get("caf_target_credentials", []),
        # Make dispatch explicit instead of relying on env sniffing.
        "execution_mode":         "caf_ssh" if is_ssh else "local",
    }
    return config


def _make_env(args: argparse.Namespace, on_log):
    """Instantiate the execution environment — mirror of execute_tab.py:268-294."""
    from core.environment import LocalEnvironment, SSHEnvironment

    if args.ssh_host:
        env = SSHEnvironment(
            host=args.ssh_host,
            port=int(args.ssh_port or 22),
            username=args.ssh_user,
            password=args.ssh_password or None,
            key_path=args.ssh_key_path or None,
            remote_cwd=args.ssh_caf_dir or "~/cyber-agent-flow",
        )
        on_log(f"[INIT] Target: SSH ({args.ssh_user}@{args.ssh_host})")
        return env

    env = LocalEnvironment()
    on_log("[INIT] Target: Local")
    return env


def _print_summary(telemetry: dict) -> None:
    """Print a compact human-readable summary of the run."""
    logger = logging.getLogger("modelscope")
    if telemetry.get("run_aborted"):
        logger.warning("[DONE] Run was cancelled.")
    elif telemetry.get("validation_passed"):
        logger.info("[DONE] Evaluation complete — validation PASSED.")
    else:
        logger.info("[DONE] Evaluation complete — validation did not pass / not run.")

    summary = {
        "scenario":          telemetry.get("active_scenario"),
        "model":             telemetry.get("selected_model") or telemetry.get("model"),
        "validation_passed": telemetry.get("validation_passed"),
        "prompt_tokens":     telemetry.get("prompt_tokens"),
        "completion_tokens": telemetry.get("completion_tokens"),
        "total_tokens":      telemetry.get("total_tokens"),
        "latency_ms":        telemetry.get("latency_ms"),
    }
    logger.info(
        "[SUMMARY] " + "  ".join(f"{k}={v}" for k, v in summary.items() if v is not None)
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.list_scenarios:
        print("Available scenarios:")
        for name in SCENARIOS:
            print(f"  - {name}")
        return 0

    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    if not args.model:
        print("error: --model is required (or use --list-scenarios).", file=sys.stderr)
        return 2

    # Session log — captures run.log, telemetry.json, config.json on disk.
    session_log = SessionLog(base_dir=args.session_dir)

    # Build a thin on_log that also writes to the session log file, then
    # route it through logged_on_log so every event reaches stdout/logger too.
    def _base_on_log(msg: str) -> None:
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

    _print_summary(telemetry)

    if args.json:
        print(json.dumps(telemetry, indent=2, default=str))

    return 0 if telemetry.get("validation_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
