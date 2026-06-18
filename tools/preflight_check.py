#!/usr/bin/env python3
"""
Standalone preflight check for ModelScope.

Run with:
    python tools/preflight_check.py
    python tools/preflight_check.py --smoke   # include live LLM smoke test
    python tools/preflight_check.py --layer platform
    python tools/preflight_check.py --layer evaluation
"""
from __future__ import annotations

import argparse
import os
import sys

# Make the repo root importable regardless of where the script is invoked from.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO_ROOT)


def _build_state() -> dict:
    """
    Build a minimal state dict from _DEFAULTS without requiring Streamlit.
    Merges persisted settings on top so a previously-saved configuration is
    respected (backend URL, model path, MCP paths, etc.).
    """
    from core.state import _DEFAULTS                     # noqa: PLC0415
    from core.settings_store import load_settings        # noqa: PLC0415

    state: dict = dict(_DEFAULTS)
    # Overlay persisted settings (non-sensitive keys only)
    saved = load_settings()
    state.update(saved)
    return state


def _print_result(r) -> None:
    """Pretty-print a single TestResult to stdout."""
    icon = r.icon
    dur  = f"  [{r.duration_ms:.0f} ms]" if r.duration_ms else ""
    layer_tag = f"[{r.layer:10s}]"
    print(f"  {icon} {layer_tag} {r.name}{dur}")
    if r.detail:
        print(f"      {r.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ModelScope preflight checks — no Streamlit required.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Include the live LLM end-to-end smoke test (requires a running backend).",
    )
    parser.add_argument(
        "--layer",
        choices=["platform", "evaluation", "all"],
        default="all",
        help="Which layer to run (default: all).",
    )
    args = parser.parse_args()

    from core.preflight import (    # noqa: PLC0415
        run_platform_layer,
        run_evaluation_layer,
        run_all,
    )

    print("ModelScope Preflight Check")
    print("=" * 50)

    state = _build_state()

    print(f"Backend : {state.get('backend_type', '?')}")
    print(f"URL     : {state.get('llm_url', '?')}")
    print(f"Model   : {state.get('selected_model') or '(none)'}")
    print(f"Scenario: {state.get('active_scenario', '?')}")
    print()

    if args.layer == "platform":
        results = run_platform_layer(state)
    elif args.layer == "evaluation":
        results = run_evaluation_layer(state, include_llm_smoke=args.smoke)
    else:
        results = run_all(state, include_llm_smoke=args.smoke)

    passed   = [r for r in results if r.passed is True]
    failed   = [r for r in results if r.passed is False]
    skipped  = [r for r in results if r.passed is None]

    # Group by layer for readability
    layers_seen: list[str] = []
    by_layer: dict[str, list] = {}
    for r in results:
        if r.layer not in layers_seen:
            layers_seen.append(r.layer)
        by_layer.setdefault(r.layer, []).append(r)

    for layer in layers_seen:
        layer_label = "Platform Regression" if layer == "platform" else "Evaluation Integrity"
        print(f"Layer: {layer_label}")
        print("-" * 50)
        for r in by_layer[layer]:
            _print_result(r)
        print()

    print("=" * 50)
    print(
        f"Summary: {len(passed)} passed  |  "
        f"{len(failed)} failed  |  "
        f"{len(skipped)} skipped/info"
    )

    if failed:
        print()
        print("FAILED checks:")
        for r in failed:
            print(f"  - {r.name}: {r.detail}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
