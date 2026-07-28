"""PCT-only batch variant of the managed Llama-Server bot."""

from __future__ import annotations

from typing import Any, Mapping

from core.bot_types.base import OnLog, StatusItem
from core.environment import BaseEnvironment
from core.bot_types.llama_server_bot import (
    LLAMA_SERVER_STATE_KEY_MAP,
    LLAMA_SERVER_SESSION_DEFAULTS,
    LlamaServerBotPlugin,
)


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

    def run_evaluation(self, env: BaseEnvironment, config: dict[str, Any], on_log: OnLog) -> dict[str, Any]:
        """Run the managed-server workflow once inside each selected LXC.

        This is the batch itself, so the CLI gets the same per-container run
        and roll-up the Execute tab does. The environment handed in supplies
        the connection to the Proxmox host; each container is reached by
        wrapping it, not by replacing it.
        """
        from core.proxbatch import (
            container_config,
            container_env,
            new_batch_state,
            no_vmids_telemetry,
            run_pct_batch,
            selected_vmids,
        )

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

        import streamlit as st
        concurrency = st.session_state.get("llama_server_proxbatch_exec_concurrency", 1)

        return run_pct_batch(
            containers,
            run_one,
            on_log=on_log,
            is_cancelled=lambda: bool(cancel_ref and cancel_ref[0]),
            bot_type=self.type_id,
            max_workers=concurrency,
        )

    def render_config(self, project: dict[str, Any]) -> None:
        from ui import config_tab

        config_tab._render_llama_server_proxbatch_bot_config(project)

    def render_execute(self, project: dict[str, Any]) -> None:
        from ui import execute_tab

        execute_tab._render_llama_server_proxbatch_execute(project)
