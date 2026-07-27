"""Run one bot workflow per Proxmox LXC, sequentially.

Shared by the Execute tab (live per-container cards, threaded) and the CLI
(plain sequential log stream) so both drive the same loop and produce the same
telemetry shape — a per-container breakdown plus one roll-up.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from core import batch_progress


def selected_vmids(config: Mapping[str, Any]) -> list[str]:
    """Selected LXC VMIDs, de-duplicated, in the user's chosen order."""
    raw_vmids = config.get("pct_vmids", [])
    vmids: list[str] = []
    for raw_vmid in raw_vmids if isinstance(raw_vmids, list) else []:
        vmid = str(raw_vmid).strip()
        if vmid.isdigit() and vmid not in vmids:
            vmids.append(vmid)
    return vmids


def new_batch_state(
    config: Mapping[str, Any], validation_sets: Any = None,
) -> dict[str, Any]:
    """Pending per-container progress records for one batch run.

    ``validation_sets`` lets the Execute tab plan from the sets actually ticked
    in that tab; everything else plans from the project config.
    """
    names = config.get("pct_vmid_names", {})
    names = names if isinstance(names, dict) else {}
    if validation_sets is None:
        validation_sets = config.get("validation_sets", [])
    total_units = batch_progress.plan_unit_total(
        config.get("startup_commands", []),
        validation_sets,
        config.get("completion_commands", []),
    )
    return {
        "containers": {
            vmid: batch_progress.new_container_state(
                vmid, str(names.get(vmid, "") or ""), total_units,
            )
            for vmid in selected_vmids(config)
        },
    }


def container_env(base_env: Any, vmid: str) -> Any:
    """Wrap the caller's environment so commands land inside one container."""
    from core.environment import PCTEnvironment

    return PCTEnvironment(vmid, getattr(base_env, "base_env", base_env))


def container_config(config: Mapping[str, Any], vmid: str) -> dict[str, Any]:
    """One container's run config.

    Shallow-copied on purpose: the cancellation ref must stay shared with the
    caller, while per-container keys must not leak back into the batch config.
    """
    item_config = dict(config)
    item_config.update({
        "type": "llama_server_bot",
        "execution_target": "pct",
        "pct_vmid": vmid,
        "server_in_container": True,
    })
    return item_config


def no_vmids_telemetry(bot_type: str) -> dict[str, Any]:
    return {
        "run_bot_type": bot_type,
        "run_aborted": True,
        "error": "No PCT VMIDs selected.",
        "batch_containers": [],
    }


def aggregate_batch_telemetry(
    containers: Mapping[str, dict],
    batch_results: list[dict],
    cancelled: bool = False,
    bot_type: str = "llama_server_proxbatch_bot",
) -> dict[str, Any]:
    """Roll per-container telemetry up into one record for the batch.

    The last container's record is the base so single-target consumers (the
    dashboard, metric thresholds) still find the fields they expect, with the
    batch-wide totals layered on top.
    """
    last_result = copy.deepcopy(batch_results[-1]) if batch_results else {}
    validation_results = [item.get("validation_passed") for item in batch_results]
    aggregate = {
        **last_result,
        "run_bot_type": bot_type,
        "pct_vmids": list(containers),
        "batch_results": batch_results,
        "batch_containers": [
            batch_progress.container_summary(state) for state in containers.values()
        ],
        "run_aborted": bool(cancelled) or any(
            item.get("run_aborted", False) for item in batch_results
        ),
        "total_latency": round(
            sum(float(item.get("total_latency", 0) or 0) for item in batch_results), 3,
        ),
        "tool_calls": [call for item in batch_results for call in item.get("tool_calls", [])],
        "prompt_responses": [
            response for item in batch_results for response in item.get("prompt_responses", [])
        ],
        "validation_passed": (
            all(result is True for result in validation_results)
            if validation_results and any(result is not None for result in validation_results)
            else None
        ),
        "interrupted_by_user": bool(cancelled),
    }
    if batch_results:
        aggregate["validation_exit_code"] = batch_results[-1].get("validation_exit_code")
    return aggregate


def run_pct_batch(
    containers: dict[str, dict],
    run_one: Callable[[str, dict], Mapping[str, Any] | None],
    on_log: Callable[[str], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    bot_type: str = "llama_server_proxbatch_bot",
) -> dict[str, Any]:
    """Run ``run_one`` once per container, then aggregate.

    A stop request finishes nothing further: the container in flight ends as
    aborted and the rest are recorded as skipped rather than silently dropped.
    """
    is_cancelled = is_cancelled or (lambda: False)
    log = on_log or (lambda _message: None)
    total = len(containers)
    batch_results: list[dict] = []

    for index, (vmid, state) in enumerate(containers.items(), start=1):
        if is_cancelled():
            batch_progress.skip_container(state)
            continue
        batch_progress.start_container(state)
        log(f"[SYS] PCT batch {index}/{total} — VMID {vmid}")
        result = dict(run_one(vmid, state) or {})
        result["pct_vmid"] = vmid
        batch_progress.finish_container(state, result, cancelled=is_cancelled())
        batch_results.append(result)
        log(f"[SYS] VMID {vmid} finished — {state['state']}")

    return aggregate_batch_telemetry(
        containers, batch_results, cancelled=is_cancelled(), bot_type=bot_type,
    )
