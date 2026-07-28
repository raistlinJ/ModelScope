"""Llama-Server ProxBatch bot plugin — one managed-server run per Proxmox LXC.

Everything this bot type owns lives here: the per-container batch loop and
telemetry roll-up, its Config/Execute/Dashboard rendering, and the plugin class
itself. Shared ModelScope helpers are imported from ``core`` and ``ui``; nothing
in ``core`` or ``ui`` imports this module — the app reaches it only through the
bot-type registry.
"""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

import streamlit as st

from core import batch_progress
from core.bot_types.base import OnLog, StatusItem
from core.bot_types.llama_server_bot import (
    LLAMA_SERVER_SESSION_DEFAULTS,
    LLAMA_SERVER_STATE_KEY_MAP,
    LlamaServerBotPlugin,
)
from core.environment import BaseEnvironment


# The managed-server controls deliberately share the established llama_server
# working-copy keys.  The only new persisted value is the selected PCT list;
# unlike Llama-Server-Bot, this type has no local or SSH target configuration.
LLAMA_SERVER_PROXBATCH_STATE_KEY_MAP = {
    key: value
    for key, value in LLAMA_SERVER_STATE_KEY_MAP.items()
    if value not in {
        "execution_target", "pct_vmid", "ssh_host", "ssh_port", "ssh_user",
        "ssh_password", "ssh_key_path",
    }
}
LLAMA_SERVER_PROXBATCH_STATE_KEY_MAP["llama_server_pct_vmids"] = "pct_vmids"
LLAMA_SERVER_PROXBATCH_STATE_KEY_MAP["llama_server_pct_template_vmid"] = "pct_template_vmid"

LLAMA_SERVER_PROXBATCH_SESSION_DEFAULTS: dict[str, Any] = {
    **LLAMA_SERVER_SESSION_DEFAULTS,
    "llama_server_pct_vmids": [],
    "llama_server_pct_template_vmid": "",
    "llama_server_proxbatch_dialog_open": False,
    "llama_server_proxbatch_containers": [],
    "llama_server_proxbatch_scan_error": "",
}

EXEC_PREFIX = "llama_server_proxbatch_exec"
DETAIL_KEY = "_proxbatch_detail_vmid"
# Kept under its original name so projects saved before the bot moved out of
# core keep their configured parallelism.
CONCURRENCY_CONFIG_KEY = "_proxbatch_concurrency"

# state → (badge, wording used on the card and in the details dialog)
_BADGES = {
    "pending":  ("⏳", "Not started"),
    "running":  ("🔄", "Running"),
    "passed":   ("✅", "Validation passed"),
    "failed":   ("❌", "Validation failed"),
    "aborted":  ("⚠️", "Aborted"),
    "complete": ("☑️", "Complete"),
    "skipped":  ("⏭️", "Skipped"),
}


def normalize_template_vmid(candidate: Any, selected_vmids: list[str]) -> str:
    """The container that stands in for the batch when scanning or testing.

    Config work (model scan, connection test) needs one concrete container to
    talk to; the batch assumes every selected LXC is set up identically. Falls
    back to the first selection whenever the stored choice is no longer in it.
    """
    template = str(candidate or "").strip()
    if template in selected_vmids:
        return template
    return selected_vmids[0] if selected_vmids else ""


# ══════════════════════════════════════════════════════════════════════════
# Batch loop and telemetry roll-up
#
# Shared by the Execute tab (live per-container cards, threaded) and the CLI
# (plain sequential log stream) so both drive the same loop and produce the
# same telemetry shape — a per-container breakdown plus one roll-up.
# ══════════════════════════════════════════════════════════════════════════


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
    max_workers: int = 1,
) -> dict[str, Any]:
    """Run ``run_one`` once per container, then aggregate.

    A stop request finishes nothing further: the container in flight ends as
    aborted and the rest are recorded as skipped rather than silently dropped.
    """
    is_cancelled = is_cancelled or (lambda: False)
    log = on_log or (lambda _message: None)
    total = len(containers)
    batch_results: list[dict] = []

    def _run_task(vmid: str, state: dict, index: int) -> dict | None:
        if is_cancelled() or state.get("cancel_requested"):
            batch_progress.skip_container(state)
            return None
        batch_progress.start_container(state)
        log(f"[SYS] PCT batch {index}/{total} — VMID {vmid}")
        result = dict(run_one(vmid, state) or {})
        result["pct_vmid"] = vmid
        batch_progress.finish_container(state, result, cancelled=is_cancelled() or state.get("cancel_requested"))
        log(f"[SYS] VMID {vmid} finished — {state['state']}")
        return result

    import concurrent.futures

    if max_workers <= 1:
        _idx = 1
        while True:
            did_work = False
            for vmid, state in list(containers.items()):
                if state.get("state") in ("pending", ""):
                    if is_cancelled() or state.get("cancel_requested"):
                        batch_progress.skip_container(state)
                        continue

                    state["state"] = "starting"
                    state["cancel_requested"] = False
                    res = _run_task(vmid, state, _idx)
                    if res is not None:
                        batch_results.append(res)
                    did_work = True
                    _idx += 1
            if not did_work:
                break
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            running_futures = {}
            _idx = 1

            while True:
                # Add any newly pending containers to the executor
                for vmid, state in containers.items():
                    if state.get("state") in ("pending", ""):
                        if is_cancelled() or state.get("cancel_requested"):
                            batch_progress.skip_container(state)
                            continue

                        future = executor.submit(_run_task, vmid, state, _idx)
                        running_futures[future] = vmid
                        # Prevent resubmitting the same pending container
                        state["state"] = "starting"
                        state["cancel_requested"] = False
                        _idx += 1

                if not running_futures:
                    break

                # Wait for at least one future to complete, then loop back
                done, not_done = concurrent.futures.wait(
                    running_futures.keys(),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                    timeout=1.0
                )

                for future in done:
                    vmid = running_futures.pop(future)
                    res = future.result()
                    if res is not None:
                        batch_results.append(res)

    return aggregate_batch_telemetry(
        containers, batch_results, cancelled=is_cancelled(), bot_type=bot_type,
    )


# ══════════════════════════════════════════════════════════════════════════
# Config tab
# ══════════════════════════════════════════════════════════════════════════


def vmid_names(cfg: dict, containers: object) -> dict[str, str]:
    """Remember each selected VMID's LXC name for the Execute tab's target list.

    A scan lives in session state and is cleared on a project switch, so the
    name is copied into the project config and only refreshed by a later scan.
    """
    names = dict(cfg.get("pct_vmid_names", {}) or {})
    for item in containers if isinstance(containers, list) else []:
        vmid = str(item.get("vmid", "")).strip()
        if vmid:
            names[vmid] = str(item.get("name", "") or "")
    return {vmid: names.get(vmid, "") for vmid in cfg.get("pct_vmids", [])}


def scan_lxc_containers(progress_callback=None) -> tuple[list[dict[str, str]], str]:
    """Read the local Proxmox LXC inventory without invoking a shell."""
    import subprocess
    import concurrent.futures

    try:
        completed = subprocess.run(
            ["pct", "list"], capture_output=True, text=True, timeout=15, check=False,
        )
    except FileNotFoundError:
        return [], "`pct` is not installed or is not available on this host."
    except subprocess.TimeoutExpired:
        return [], "Timed out while scanning Proxmox LXCs."
    if completed.returncode:
        error = (completed.stderr or completed.stdout).strip() or "pct list failed."
        return [], error

    lines = completed.stdout.splitlines()
    name_column = next(
        (line.lower().find("name") for line in lines if line.lstrip().lower().startswith("vmid")),
        -1,
    )

    # First pass: parse lines
    parsed_items = []
    for line in lines:
        fields = line.split(maxsplit=4)
        if not fields or not fields[0].isdigit():
            continue  # header and any non-container diagnostics

        vmid = fields[0]
        status = fields[1] if len(fields) > 1 else "unknown"
        name = line[name_column:].strip() if name_column >= 0 else fields[-1]
        parsed_items.append({"vmid": vmid, "status": status, "name": name, "line": line, "fields": fields})

    if progress_callback:
        progress_callback(0, len(parsed_items))

    def check_template(vmid: str) -> bool:
        try:
            cfg = subprocess.run(["pct", "config", vmid], capture_output=True, text=True, timeout=2)
            return "template: 1" in cfg.stdout
        except Exception:
            return False

    template_status = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:
        futures = {executor.submit(check_template, item["vmid"]): item["vmid"] for item in parsed_items}
        for i, future in enumerate(concurrent.futures.as_completed(futures)):
            vmid = futures[future]
            try:
                template_status[vmid] = future.result()
            except Exception:
                template_status[vmid] = False
            if progress_callback:
                progress_callback(i + 1, len(parsed_items))

    containers: list[dict[str, str]] = []
    for item in parsed_items:
        status = item["status"]
        if template_status.get(item["vmid"]):
            status = "template"

        containers.append({
            "vmid": item["vmid"],
            "status": status,
            "name": item["name"],
        })

    return containers, ""


def render_template_selector(project: dict, selected: list[str]) -> None:
    """Pick the LXC that stands in for the batch while configuring it."""

    if not selected:
        return
    # Correct the stored value only when it can no longer be selected, and only
    # here — before the widget exists. Once the selectbox is instantiated
    # Streamlit rejects any write to its key (see flush_llama_server_config).
    current = str(st.session_state.get("llama_server_pct_template_vmid", "") or "")
    if current not in selected:
        st.session_state["llama_server_pct_template_vmid"] = normalize_template_vmid(
            current, selected,
        )
    names = project.get("config", {}).get("pct_vmid_names", {})
    names = names if isinstance(names, dict) else {}
    st.selectbox(
        "Master LXC",
        options=selected,
        key="llama_server_pct_template_vmid",
        format_func=lambda vmid: f"{vmid} — {names[vmid]}" if names.get(vmid) else str(vmid),
        help=(
            "This will be used for setup; all commands/prompts will run in the same way as they run for this master LXC."
        ),
    )


def _vmid_dialog_body(project: dict) -> None:
    from ui.plugin_api import flush_llama_server_config, normalise_pct_vmids

    containers = st.session_state.get("llama_server_proxbatch_containers", [])
    if not containers:
        st.info("No LXC containers were found. Scan again after creating or starting containers.")
    else:
        selected = set(normalise_pct_vmids(st.session_state.get("llama_server_pct_vmids", [])))
        vmids = [item["vmid"] for item in containers]

        def _is_template(c: dict) -> bool:
            return c.get("status", "").lower() == "template" or "template" in c.get("name", "").lower()

        selectable_vmids = [item["vmid"] for item in containers if not _is_template(item)]
        c_all, c_invert, c_clear = st.columns(3)
        with c_all:
            if st.button("Select all", use_container_width=True):
                for vmid in vmids:
                    st.session_state[f"llama_server_proxbatch_vmid_{vmid}"] = vmid in selectable_vmids
                st.session_state["llama_server_pct_vmids"] = selectable_vmids
                st.rerun()
        with c_invert:
            if st.button("Invert selection", use_container_width=True):
                inverted = [vmid for vmid in selectable_vmids if vmid not in selected]
                for vmid in vmids:
                    st.session_state[f"llama_server_proxbatch_vmid_{vmid}"] = vmid in inverted
                st.session_state["llama_server_pct_vmids"] = inverted
                st.rerun()
        with c_clear:
            if st.button("Clear selection", use_container_width=True):
                for vmid in vmids:
                    st.session_state[f"llama_server_proxbatch_vmid_{vmid}"] = False
                st.session_state["llama_server_pct_vmids"] = []
                st.rerun()

        templates = [c for c in containers if _is_template(c)]
        regular = [c for c in containers if not _is_template(c)]
        checked: list[str] = []

        for item in regular:
            vmid = item["vmid"]
            label = f"{vmid} — {item.get('name') or 'unnamed'} ({item.get('status', 'unknown')})"
            if item.get("ip"):
                label += f" · {item['ip']}"
            if st.checkbox(label, value=vmid in selected, key=f"llama_server_proxbatch_vmid_{vmid}"):
                checked.append(vmid)

        if templates:
            st.markdown(
                "<div style='margin-top: 1rem; margin-bottom: 0.5rem; color: var(--muted); "
                "font-size: 0.85rem; font-weight: 600; text-transform: uppercase;'>Templates</div>",
                unsafe_allow_html=True,
            )
            for item in templates:
                vmid = item["vmid"]
                label = f"{vmid} — {item.get('name') or 'unnamed'} (template)"
                st.checkbox(
                    label,
                    value=False,
                    key=f"llama_server_proxbatch_vmid_{vmid}",
                    disabled=True,
                    help="Templates cannot be selected for batch execution",
                )

        st.session_state["llama_server_pct_vmids"] = checked

    st.divider()
    c_close, c_rescan = st.columns(2)
    with c_close:
        if st.button("Done", type="primary", use_container_width=True):
            flush_llama_server_config(project)
            st.session_state["llama_server_proxbatch_dialog_open"] = False
            st.rerun()
    with c_rescan:
        if st.button("Rescan", use_container_width=True):
            progress_bar = st.progress(0, text="Scanning LXC containers...")

            def update_progress(current, total):
                if total > 0:
                    progress_bar.progress(current / total, text=f"Scanning LXC containers... ({current}/{total})")

            containers, error = scan_lxc_containers(progress_callback=update_progress)
            progress_bar.empty()
            st.session_state["llama_server_proxbatch_containers"] = containers
            st.session_state["llama_server_proxbatch_scan_error"] = error
            st.rerun()


def render_vmid_dialog(project: dict) -> None:
    """Modal selector for the PCT-only batch bot's container list."""

    # st.dialog is applied here rather than at module scope so importing this
    # plugin never requires Streamlit (the CLI loads it too).
    @st.dialog("Select Proxmox LXC containers")
    def _dialog() -> None:
        _vmid_dialog_body(project)

    _dialog()


def template_env(project: dict):
    """PCT environment for the template LXC, or None with an error shown.

    Model scans and connection tests target one container and the batch then
    reuses that configuration for every selected LXC.
    """
    from core.environment import create_environment
    from ui.plugin_api import flush_llama_server_config

    flush_llama_server_config(project)
    template = str(project["config"].get("pct_template_vmid", "") or "").strip()
    if not template:
        st.warning("Select LXC containers and a template LXC first.")
        return None
    return create_environment(ssh=False, pct_vmid=template, remote_cwd=".")


# ══════════════════════════════════════════════════════════════════════════
# Execute tab
# ══════════════════════════════════════════════════════════════════════════


class _ProxBatchLog(list):
    """One container's log list, wired into progress and the batch-wide stream.

    ``run_llama_backed_bot`` appends to whatever list it finds under
    ``logs_setup`` / ``logs_validation``, so seeding those keys with this list
    is enough to track a container without the runner knowing about batches.
    """

    def __init__(self, container_state: dict, batch_shared: dict, mirror: list, vmid: str):
        super().__init__()
        self._state = container_state
        self._batch_shared = batch_shared
        self._mirror = mirror
        self._vmid = vmid

    def append(self, entry: dict) -> None:
        from core.batch_progress import observe_log

        super().append(entry)
        text = entry.get("text", "") if isinstance(entry, dict) else str(entry)
        observe_log(self._state, text)
        # The batch-wide phase drives the shared Startup/Validation/Completion
        # expander labels; "done" belongs to one container, not the batch.
        if self._state.get("phase") in ("startup", "validation", "completion"):
            self._batch_shared["phase"] = self._state["phase"]
        mirrored = dict(entry) if isinstance(entry, dict) else {}
        mirrored["text"] = f"[{self._vmid}] {text}"
        self._mirror.append(mirrored)


class _ProxBatchItem(dict):
    """Per-container run state that shares the batch's cancellation flag."""

    def __init__(self, batch_shared: dict, container_state: dict, vmid: str):
        super().__init__()
        self._batch_shared = batch_shared
        self._container_state = container_state
        self["phase"] = ""
        for key in ("logs_setup", "logs_validation"):
            stream = _ProxBatchLog(
                container_state, batch_shared, batch_shared.setdefault(key, []), vmid,
            )
            self[key] = stream
            container_state[key] = stream

    def get(self, key, default=None):
        if key == "cancel_requested":
            if getattr(self, "_container_state", {}).get("cancel_requested"):
                return True
            return self._batch_shared.get("cancel_requested", False)
        return super().get(key, default)


def run_batch(project: dict, shared: dict) -> None:
    """Run a PCT-only managed-server project once per selected LXC VMID.

    The loop and roll-up are the module-level batch helpers above so the CLI
    runs the identical batch; what belongs here is the Streamlit-side wiring —
    per-container log streams feeding the live progress cards, and the batch's
    own session log.
    """
    from core.session_log import SessionLog
    from ui.plugin_api import run_llama_backed_bot

    cfg = project.get("config", {})

    if not selected_vmids(cfg):
        shared.setdefault("logs_setup", []).append({
            "text": "[ERROR] Select at least one PCT LXC before executing.", "tag": "warn",
        })
        shared["telemetry"] = no_vmids_telemetry("llama_server_proxbatch_bot")
        shared["completed"] = True
        shared["project_id"] = project.get("id")
        return

    # The Execute tab seeds this before starting the thread so the progress
    # cards appear immediately; a headless run plans from the config instead.
    batch = shared.get("batch")
    if not isinstance(batch, dict) or not batch.get("containers"):
        batch = new_batch_state(cfg)
        shared["batch"] = batch
    containers = batch["containers"]
    for vmid in selected_vmids(cfg):
        containers.setdefault(vmid, batch_progress.new_container_state(vmid))

    def run_one(vmid: str, container_state: dict) -> dict:
        item_project = copy.deepcopy(project)
        item_project["type"] = "llama_server_bot"
        item_project["config"] = container_config(item_project["config"], vmid)
        item_shared = _ProxBatchItem(shared, container_state, vmid)
        run_llama_backed_bot(item_project, item_shared, "llama_server_bot")
        return copy.deepcopy(item_shared.get("telemetry", {}))

    concurrency = int(cfg.get(CONCURRENCY_CONFIG_KEY, 1))
    shared.setdefault("logs_setup", []).append(
        {"text": f"[SYS] Starting ProxBatch with max_workers={concurrency}", "tag": "sys"},
    )

    aggregate = run_pct_batch(
        containers,
        run_one,
        on_log=lambda message: shared.setdefault("logs_setup", []).append(
            {"text": message, "tag": "sys"},
        ),
        is_cancelled=lambda: bool(shared.get("cancel_requested")),
        max_workers=concurrency,
    )

    # Preserve a single, dashboard-visible record for the entire batch as well
    # as the individual LXC diagnostics emitted by the existing runner.
    session_log = SessionLog()
    session_log.save_telemetry(aggregate)
    session_log.save_config({
        **copy.deepcopy(project.get("config", {})),
        "type": "llama_server_proxbatch_bot",
        "active_project_id": project.get("id"),
    })
    session_log.close()
    shared["telemetry"] = aggregate
    shared["completed"] = True
    shared["project_id"] = project.get("id")


def _state_key(project: dict) -> str:
    return f"_proxbatch_batch_{project.get('id', '')}"


def _containers(project: dict) -> list[dict]:
    """Per-container records for this project's most recent batch run.

    Live records are held by reference while the worker thread runs. Once the
    session has been restarted they are gone, so the saved telemetry summaries
    stand in — same status and percentages, without the logs.
    """

    batch = st.session_state.get(_state_key(project)) or {}
    containers = list((batch.get("containers") or {}).values())
    if containers:
        return containers
    saved = (st.session_state.get("telemetry") or {}).get("batch_containers")
    return [dict(item) for item in saved] if isinstance(saved, list) else []


def _phase_label(state: dict) -> str:
    """Phase wording for a card, with the two states that have no phase yet."""
    from core.batch_progress import PENDING, PHASE_LABELS, SKIPPED

    if state.get("state") == PENDING:
        return "Queued"
    if state.get("state") == SKIPPED:
        return "Skipped"
    phase = state.get("phase", "")
    return PHASE_LABELS.get(phase, phase or "—")


def _on_run_start(project: dict, shared_state: dict) -> None:
    """Seed pending container cards before the worker thread starts.

    Planning happens here, in the main thread, because the unit count depends
    on which validation sets are ticked in this tab. The state dict is shared
    by reference, so the worker's updates land straight in what we render.
    """
    from ui.plugin_api import selected_validation_sets

    cfg = project.get("config", {})
    batch = new_batch_state(
        cfg, selected_validation_sets(cfg, EXEC_PREFIX),
    )
    shared_state["batch"] = batch
    st.session_state[_state_key(project)] = batch

    if batch.get("containers"):
        st.session_state[DETAIL_KEY] = str(list(batch["containers"].keys())[0])
    else:
        st.session_state.pop(DETAIL_KEY, None)


def _on_clear(project: dict) -> None:

    st.session_state.pop(_state_key(project), None)
    st.session_state.pop(DETAIL_KEY, None)


def _render_targets(project: dict) -> None:
    """List the containers each phase will run in, before anything runs."""
    from core.batch_progress import plan_unit_total
    from ui.plugin_api import clean_steps, selected_validation_sets

    cfg = project.get("config", {})
    vmids = selected_vmids(cfg)
    names = cfg.get("pct_vmid_names", {})
    names = names if isinstance(names, dict) else {}
    val_sets = selected_validation_sets(cfg, EXEC_PREFIX)

    with st.expander(f"**🎯 Batch Targets** — {len(vmids)} LXC container(s)", expanded=True):
        if not vmids:
            st.warning(
                "No LXC containers selected. Pick them in the Config tab under "
                "Runtime → Execution Target → Scan LXCs."
            )
            return
        template = str(cfg.get("pct_template_vmid", "") or "")
        st.caption(
            "Startup, Validation and Completion all run inside every container listed "
            "below, one container at a time, each with its own llama-server started in "
            "that container."
            + (f" All of them use the configuration verified against template LXC {template}."
               if template else "")
        )
        counts = {
            "Startup": plan_unit_total(clean_steps(cfg.get("startup_commands", [])), [], []),
            "Validation": plan_unit_total([], val_sets, []),
            "Completion": plan_unit_total([], [], clean_steps(cfg.get("completion_commands", []))),
        }
        header = "| # | VMID | Name | " + " | ".join(f"{k} ({v})" for k, v in counts.items()) + " |"
        rows = [header, "|---|---|---|:---:|:---:|:---:|"]
        for index, vmid in enumerate(vmids, start=1):
            marks = " | ".join("✓" if counts[phase] else "—" for phase in counts)
            rows.append(f"| {index} | `{vmid}` | {names.get(vmid) or '—'} | {marks} |")
        st.markdown("\n".join(rows))
        st.caption("Numbers in the headers are the commands configured for that phase.")


def _render_progress(project: dict) -> tuple[list, list] | None:
    """Live status card per container, each selectable to filter logs."""
    from core.batch_progress import batch_summary

    containers = _containers(project)
    if not containers:
        return None

    summary = batch_summary(containers)
    heading = f"**📦 Container Progress** — {summary['finished']} of {summary['total']} finished"
    if summary["running"]:
        heading += f", {summary['running']} running"
    if summary["failed"]:
        heading += f", {summary['failed']} failed or aborted"
    st.markdown(heading)

    selected_vmid = st.session_state.get(DETAIL_KEY, "")
    if not selected_vmid and containers:
        selected_vmid = str(containers[0].get("vmid", ""))
    selected_logs = None

    per_row = 3
    for row_start in range(0, len(containers), per_row):
        row = containers[row_start:row_start + per_row]
        columns = st.columns(per_row)
        for column, state in zip(columns, row):
            vmid = str(state.get("vmid"))
            is_selected = (vmid == selected_vmid)
            if is_selected:
                selected_logs = (list(state.get("logs_setup", [])), list(state.get("logs_validation", [])))

            with column, st.container(border=True):
                badge, wording = _BADGES.get(state.get("state", ""), ("•", "Unknown"))
                name = state.get("name") or ""

                sel_marker = "🟢 " if is_selected else ""
                st.markdown(f"{sel_marker}{badge} **VMID {vmid}**" + (f" · {name}" if name else ""))

                percent = int(state.get("percent", 0) or 0)
                st.progress(percent / 100, text=f"{percent}% — {wording}")
                units_started = min(int(state.get('units_started', 0)), int(state.get('total_units', 0)))
                units = f"{units_started}/{state.get('total_units', 0)} steps"
                st.caption(f"{_phase_label(state)} · {units}")
                st.caption(f"↳ {state.get('current_step') or 'Waiting to start'}")
                is_running = state.get("state") == "running"
                is_finished = state.get("state") in ("completed", "failed", "aborted", "skipped")

                if is_running or is_finished:
                    c_logs, c_action = st.columns([2.5, 1])
                    with c_logs:
                        if st.button(
                            "Showing Logs" if is_selected else "Focus Logs",
                            key=f"{EXEC_PREFIX}_detail_{vmid}",
                            use_container_width=True,
                            disabled=is_selected,
                        ):
                            st.session_state[DETAIL_KEY] = vmid
                            st.rerun()
                    with c_action:
                        if is_running:
                            if st.button("⏹", key=f"{EXEC_PREFIX}_stop_{vmid}", help="Stop Processing", use_container_width=True):
                                state["cancel_requested"] = True
                                st.rerun()
                        else:
                            run_in_progress = st.session_state.get("_run_in_progress", False)
                            if st.button("🔄", key=f"{EXEC_PREFIX}_retry_{vmid}", help="Retry container", use_container_width=True):
                                if run_in_progress:
                                    from core.batch_progress import new_container_state
                                    state.clear()
                                    state.update(new_container_state(vmid))
                                else:
                                    st.session_state["_retry_vmid"] = vmid
                                st.rerun()
                else:
                    if st.button(
                        "Showing Logs" if is_selected else "Focus Logs",
                        key=f"{EXEC_PREFIX}_detail_{vmid}",
                        use_container_width=True,
                        disabled=is_selected,
                    ):
                        st.session_state[DETAIL_KEY] = vmid
                        st.rerun()

    return selected_logs


# ══════════════════════════════════════════════════════════════════════════
# Analytical dashboard
# ══════════════════════════════════════════════════════════════════════════

_METRIC_ICONS = {
    "total_latency": "L", "prompts_run": "R", "commands_run": "C",
    "prompt_tokens": "PT", "completion_tokens": "CT", "total_tokens": "TT",
    "prompt_tokens_per_second": "P/s", "completion_tokens_per_second": "C/s",
    "prompt_seconds": "Ps", "completion_seconds": "Cs", "cli_invocations": "I",
    "requests_processing": "A", "requests_deferred": "D", "context_high_watermark": "W",
    "decode_calls": "DC", "busy_slots_per_decode": "BS",
}


def render_dashboard(
    project: dict,
    bot_type: str = "llama_server_proxbatch_bot",
    metrics_key: str = "llama_server_metrics_matrix",
) -> None:
    """ProxBatch dashboard — per-container telemetry selection."""
    from ui.plugin_api import (
        configured_metric_assessments,
        hydrate_project_history,
        render_run_dashboard,
        threshold_style,
    )

    hydrate_project_history(project)
    pid = project["id"]
    history_key = f"run_history_{pid}"
    history: list = st.session_state.get(history_key, [])
    history = [h for h in history if h.get("run_bot_type") == bot_type]

    if not history:
        st.info("No runs yet for this project — go to **Execute** and run it.")
        return

    if len(history) > 1:
        labels = []
        for i, h in enumerate(reversed(history)):
            ts  = h.get("run_timestamp", "")
            lbl = f"Run {len(history) - i}  —  {ts}"
            labels.append(lbl)
        sel_label = st.selectbox(
            "Select run", options=labels, index=0,
            key=f"{bot_type}_dash_sel_{pid}_{len(history)}",
        )
        tel = list(reversed(history))[labels.index(sel_label)]
    else:
        tel: dict = history[-1]

    batch_results = tel.get("batch_results", [])
    if not batch_results:
        st.info("No container-level telemetry available for this batch run.")
        return

    st.markdown("### Container Results")
    st.caption("Select a container below to view its specific analytics and outputs.")

    # Default to the first container
    selected_vmid = st.session_state.get(f"_dash_sel_vmid_{pid}", batch_results[0].get("pct_vmid"))

    per_row = 4
    for row_start in range(0, len(batch_results), per_row):
        row = batch_results[row_start:row_start + per_row]
        columns = st.columns(per_row)
        for column, res in zip(columns, row):
            vmid = res.get("pct_vmid")
            is_selected = (vmid == selected_vmid)

            # Validation icon logic
            assessments = configured_metric_assessments(project, res)
            badge_html = ""
            if assessments:
                for metric, assessment in assessments.items():
                    level = assessment.get("level", "not_available")
                    if level in ("unclassified", "not_available"):
                        continue
                    color = threshold_style().get(level, ("", "var(--muted)"))[1]
                    title = f"{metric}: {level.replace('_', ' ').title()}"
                    abbr = _METRIC_ICONS.get(metric, metric[:1].upper())
                    badge_html += f'<span class="run-indicator" title="{title}" style="background:{color};">{abbr}</span>'

            if not badge_html:
                val_passed = res.get("validation_passed")
                color = "var(--success)" if val_passed else ("var(--error)" if val_passed is False else "var(--muted)")
                title = "Validation Passed" if val_passed else ("Validation Failed" if val_passed is False else "No Validation")
                icon = "✓" if val_passed else ("✕" if val_passed is False else "?")
                badge_html = f'<span class="run-indicator" title="{title}" style="background:{color};">{icon}</span>'

            with column, st.container(border=True):
                st.markdown(f"{badge_html} **VMID {vmid}**", unsafe_allow_html=True)
                if st.button("View Analytics" if not is_selected else "Viewing Analytics",
                             key=f"dash_btn_{pid}_{vmid}", disabled=is_selected, use_container_width=True):
                    st.session_state[f"_dash_sel_vmid_{pid}"] = vmid
                    st.rerun()

    st.divider()

    # Find the telemetry for the selected container
    selected_res = next((r for r in batch_results if r.get("pct_vmid") == selected_vmid), batch_results[0])

    # Render the specific container's dashboard
    render_run_dashboard(project, selected_res, bot_type, metrics_key)


# ══════════════════════════════════════════════════════════════════════════
# Plugin
# ══════════════════════════════════════════════════════════════════════════


class LlamaServerProxBatchBotPlugin(LlamaServerBotPlugin):
    """Run the managed server workflow once for each selected local PCT LXC."""

    type_id = "llama_server_proxbatch_bot"
    label = "Llama-Server-ProxBatch"
    icon = "🦙"
    default_project_name = "Llama-Server ProxBatch Project"
    state_key_map = LLAMA_SERVER_PROXBATCH_STATE_KEY_MAP
    session_defaults = LLAMA_SERVER_PROXBATCH_SESSION_DEFAULTS
    owned_prefixes = (
        *LlamaServerBotPlugin.owned_prefixes,
        "llama_server_proxbatch_vmid_",
        "llama_server_proxbatch_exec_",
    )
    concurrency_config_key = CONCURRENCY_CONFIG_KEY
    exec_state_prefix = EXEC_PREFIX

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        config = super().default_config(template_key)
        config.update({
            "execution_target": "pct",
            "pct_vmids": [],
            "pct_vmid_names": {},
            "pct_template_vmid": "",
            # Every selected LXC hosts its own llama-server, so a run measures
            # each container's real hardware instead of the Proxmox host's.
            "server_in_container": True,
        })
        for key in ("pct_vmid", "ssh_host", "ssh_port", "ssh_user", "ssh_password", "ssh_key_path"):
            config.pop(key, None)
        return config

    def status_items(self, session_state: Mapping[str, Any], project: dict | None) -> list[StatusItem]:
        items = super().status_items(session_state, project)
        selected = session_state.get("llama_server_pct_vmids", [])
        count = len(selected) if isinstance(selected, list) else 0
        items.insert(2, StatusItem(f"PCT LXCs: {count} selected", "up" if count else "wait"))
        return items

    def sidebar_indicators(
        self, session_telemetry: Mapping[str, Any] | None, current_config: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Report execution coverage instead of per-metric pass/fail boxes.

        One workflow runs per selected LXC, so what matters at a glance is how
        many containers have been through it — not the last container's
        validation verdict, which the Execute tab already breaks out per
        container.
        """
        from core.run_status import batch_execution_indicator

        return batch_execution_indicator(dict(session_telemetry or {}))

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = super().normalize_project_config(config)
        raw_vmids = config.get("pct_vmids", [])
        if not isinstance(raw_vmids, list):
            raw_vmids = []
        config["pct_vmids"] = list(dict.fromkeys(
            str(vmid).strip() for vmid in raw_vmids if str(vmid).strip().isdigit()
        ))
        raw_names = config.get("pct_vmid_names", {})
        if not isinstance(raw_names, dict):
            raw_names = {}
        config["pct_vmid_names"] = {
            vmid: str(raw_names.get(vmid, "") or "") for vmid in config["pct_vmids"]
        }
        config["pct_template_vmid"] = normalize_template_vmid(
            config.get("pct_template_vmid"), config["pct_vmids"],
        )
        config["execution_target"] = "pct"
        config["server_in_container"] = True
        # Each container serves on its own address, so the inherited
        # loopback bind and client URL would be wrong here; core.pct_server
        # fills the real URL in when it starts a container's server.
        from core.pct_server import CONTAINER_BIND_HOST

        config["server_host"] = CONTAINER_BIND_HOST
        config["openai_base_url"] = ""
        config["llm_url"] = ""
        for key in ("pct_vmid", "ssh_host", "ssh_port", "ssh_user", "ssh_password", "ssh_key_path"):
            config.pop(key, None)
        return config

    def flush_ui_config(self, project: dict[str, Any], cfg: dict[str, Any]) -> None:
        """Derive this bot's PCT keys during the shared llama-server flush."""
        from core.pct_server import CONTAINER_BIND_HOST
        from ui.plugin_api import normalise_pct_vmids

        cfg["execution_target"] = "pct"
        cfg["server_in_container"] = True
        cfg["pct_vmids"] = normalise_pct_vmids(st.session_state.get("llama_server_pct_vmids", []))
        cfg["pct_vmid_names"] = vmid_names(
            cfg, st.session_state.get("llama_server_proxbatch_containers", []),
        )
        # The session key belongs to the Template LXC selectbox, so a flush may
        # only read it — render_template_selector owns correcting it, before
        # that widget is instantiated.
        cfg["pct_template_vmid"] = normalize_template_vmid(
            st.session_state.get("llama_server_pct_template_vmid", ""), cfg["pct_vmids"],
        )
        # Each container answers on its own address, so there is no single
        # client URL until core.pct_server resolves one per run.
        cfg["server_host"] = CONTAINER_BIND_HOST
        cfg["openai_base_url"] = ""
        for key in ("pct_vmid", "ssh_host", "ssh_port", "ssh_user", "ssh_password", "ssh_key_path"):
            cfg.pop(key, None)

    def run_evaluation(self, env: BaseEnvironment, config: dict[str, Any], on_log: OnLog) -> dict[str, Any]:
        """Run the managed-server workflow once inside each selected LXC.

        This is the batch itself, so the CLI gets the same per-container run
        and roll-up the Execute tab does. The environment handed in supplies
        the connection to the Proxmox host; each container is reached by
        wrapping it, not by replacing it.
        """
        if not selected_vmids(config):
            on_log("[ERROR] Select at least one PCT LXC before executing.")
            return no_vmids_telemetry(self.type_id)

        containers = new_batch_state(config)["containers"]
        cancel_ref = config.get("cancel_requested_ref") or [False]

        def run_one(vmid: str, state: dict[str, Any]) -> dict[str, Any]:
            from core.batch_progress import observe_log

            item_env = container_env(env, vmid)

            def item_log(message, *args, **kwargs):
                # Same log-derived progress the Execute tab shows live, so a
                # CLI run's telemetry carries the identical per-container detail.
                observe_log(state, str(message))
                on_log(message, *args, **kwargs)

            try:
                return super(LlamaServerProxBatchBotPlugin, self).run_evaluation(
                    item_env, container_config(config, vmid), item_log,
                )
            except Exception as exc:  # one bad container must not end the batch
                on_log(f"[ERROR] VMID {vmid} failed: {exc}")
                return {"run_aborted": True, "error": str(exc)}

        concurrency = config.get(CONCURRENCY_CONFIG_KEY, 1)

        return run_pct_batch(
            containers,
            run_one,
            on_log=on_log,
            is_cancelled=lambda: bool(cancel_ref and cancel_ref[0]),
            bot_type=self.type_id,
            max_workers=concurrency,
        )

    # ── UI entry points ────────────────────────────────────────────────
    #
    # These are called through the registry (get_bot_plugin(...)) so the ui
    # package never imports this module.

    def run_in_execute_tab(
        self, project: dict[str, Any], shared: dict[str, Any], bot_type: str,
    ) -> bool:
        run_batch(project, shared)
        return True

    def render_model_info(self, config: dict[str, Any]) -> bool:
        st.caption(f"Binary (in container): `{config.get('binary_path', '') or 'not configured'}`")
        st.caption(
            f"Listen: `{config.get('server_host', '0.0.0.0')}:{config.get('server_port', 8080)}`"
            " inside each LXC"
        )
        st.caption("Client URL: each container's own address, resolved when it starts.")
        return True

    def scan_lxc_containers(self, progress_callback=None) -> tuple[list[dict[str, str]], str]:
        return scan_lxc_containers(progress_callback)

    def render_vmid_dialog(self, project: dict[str, Any]) -> None:
        render_vmid_dialog(project)

    def render_template_selector(self, project: dict[str, Any], selected: list[str]) -> None:
        render_template_selector(project, selected)

    # ── Overrides of the shared llama-server Runtime renderer ─────────

    def render_execution_target(self, project: dict[str, Any]) -> str | None:
        """PCT-only: a container picker replaces the local/ssh/pct mode radio."""
        from ui.plugin_api import normalise_pct_vmids

        st.session_state["llama_server_execution_target"] = "pct"
        st.caption(
            "PCT-only batch execution. Each selected LXC runs the same workflow "
            "sequentially, with its own llama-server started inside it."
        )
        selected = normalise_pct_vmids(st.session_state.get("llama_server_pct_vmids", []))
        c_scan, c_selected = st.columns([1, 3])
        with c_scan:
            if st.button("Scan LXCs", key="btn_llama_server_proxbatch_scan_lxcs", use_container_width=True):
                containers, error = scan_lxc_containers()
                st.session_state["llama_server_proxbatch_containers"] = containers
                st.session_state["llama_server_proxbatch_scan_error"] = error
                st.session_state["llama_server_proxbatch_dialog_open"] = True
        with c_selected:
            if selected:
                st.caption(f"Selected VMIDs: `{', '.join(selected)}`")
            else:
                st.caption("No LXC containers selected.")
        scan_error = st.session_state.get("llama_server_proxbatch_scan_error", "")
        if scan_error:
            st.error(f"Could not scan LXCs: {scan_error}")
        if st.session_state.get("llama_server_proxbatch_dialog_open"):
            render_vmid_dialog(project)
        return "pct"

    def render_target_test(self, project: dict[str, Any], target: str) -> bool:
        """No single-target connection test: the batch has many targets."""
        return True

    def render_server_setup_notice(self, project: dict[str, Any]) -> bool:
        from ui.plugin_api import normalise_pct_vmids

        selected = normalise_pct_vmids(st.session_state.get("llama_server_pct_vmids", []))
        render_template_selector(project, selected)
        template = str(st.session_state.get("llama_server_pct_template_vmid", "") or "")
        st.info(
            "Each selected LXC runs its own llama-server **inside the container**, so the "
            "Binary Path and Model Directory/Model below must point to files that exist "
            "**inside every container** — they are assumed to be set up identically. "
            + (f"Scanning and Check Status use Master LXC **{template}**."
               if template else "Select a Master LXC to scan models or check status.")
        )
        return True

    def model_dir_help(self) -> str | None:
        return "Directory or direct .gguf file inside each LXC to scan for models."

    def render_bind_controls(self, project: dict[str, Any]) -> bool:
        # A container's loopback is unreachable from the Proxmox host, so the
        # bind address isn't the user's to choose here.
        from core.pct_server import CONTAINER_BIND_HOST

        st.number_input(
            "Listen Port",
            min_value=1,
            max_value=65535,
            step=1,
            key="llama_server_server_port",
        )
        port = int(st.session_state.get("llama_server_server_port") or 8080)
        st.session_state["llama_server_server_host"] = CONTAINER_BIND_HOST
        st.caption(
            f"Each container's llama-server binds `{CONTAINER_BIND_HOST}:{port}`; ModelScope "
            "calls it at that container's own IP address."
        )
        return True

    uses_config_probe_env = True

    def config_probe_env(self, project: dict[str, Any]):
        return template_env(project)

    def start_config_test_server(self, project: dict[str, Any], params, log):
        """Start a managed llama-server inside the Master LXC for Check Status."""
        from core.environment import create_environment
        from core.pct_server import start_pct_managed_llama_server

        cfg = project["config"]
        template = str(cfg.get("pct_template_vmid", "") or "").strip()
        if not template:
            st.session_state["_llama_server_svc_result"] = (
                "error", "Select LXC containers and a template LXC first.", "",
            )
            return None
        pct_env = create_environment(ssh=False, pct_vmid=template, remote_cwd=".")
        return start_pct_managed_llama_server(
            pct_env, template, params["binary"], params["model_path"],
            params["context_size"], params["port"], log,
            custom_flags=params["custom_flags"],
            advanced_flags=params["advanced_flags"],
            ready_timeout=params["ready_timeout"],
        )

    def render_dashboard(self, project: dict[str, Any]) -> None:
        render_dashboard(project)

    def render_config(self, project: dict[str, Any]) -> None:
        from ui.plugin_api import (
            flush_llama_server_config,
            render_llama_server_runtime,
            render_llama_server_validation,
            render_metric_thresholds_config,
        )

        st.divider()
        sub_runtime, sub_val, sub_metrics = st.tabs(
            ["🖥  Runtime", "✅  Validation", "📊  Metrics Config"]
        )
        with sub_runtime:
            render_llama_server_runtime(project)
        with sub_val:
            render_llama_server_validation(project)
        with sub_metrics:
            render_metric_thresholds_config(project, "llama_server", flush_llama_server_config)

    def render_execute(self, project: dict[str, Any]) -> None:
        from ui.plugin_api import flush_llama_server_config, render_llama_execute_view

        render_llama_execute_view(
            project,
            bot_type=self.type_id,
            llm_label="LLAMA-SERVER",
            exec_prefix=EXEC_PREFIX,
            flush_fn=flush_llama_server_config,
            render_targets=_render_targets,
            render_progress=_render_progress,
            on_run_start=_on_run_start,
            on_clear=_on_clear,
            allow_concurrency=True,
            stop_label="⏹  Stop All",
        )
