"""CAF + managed llama.cpp server bot plugin.

Runs a CyberAgentFlow evaluation the same way CafCliRunPlugin does, except
ModelScope itself owns the lifecycle of the llama-server backend CAF talks
to: it starts a managed, `--metrics`-enabled llama-server before the run,
locks CAF's provider/URL/model to that server, and tears the server down
afterward — capturing its Prometheus `/metrics` delta alongside the CAF
run's own telemetry.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from core.bot_types.base import StatusItem
from core.bot_types.llama_server_bot import LlamaServerBotPlugin
from core.llama_metrics import fetch_llama_server_metrics, llama_server_metrics_delta
from core.models import resolve_model_path, scan_gguf_models, scan_gguf_models_via_env
from core.utils import effective_verify_ssl
from plugins.bot_types.caf_cli_run import (
    CAF_CLI_RUN_SESSION_DEFAULTS,
    CAF_CLI_RUN_STATE_KEY_MAP,
    CAF_DEFAULT_DIRECTORY,
    CAF_DEFAULT_SSH_PORT,
    CafActiveSessionError,
    CafAppRestartRequiredError,
    CafCliRunPlugin,
    CafSessionStartTimeoutError,
    _environment_for_config,
)


CAF_LLAMA_DEFAULT_MODEL_DIRECTORY = "~/.cache/huggingface/hub"


CAF_LLAMA_SRV_STATE_KEY_MAP: dict[str, str] = {
    "caf_llama_srv_binary_path": "binary_path",
    "caf_llama_srv_model_dir": "model_dir",
    "caf_llama_srv_model_name": "model_name",
    "caf_llama_srv_tokens": "tokens",
    "caf_llama_srv_server_ready_timeout": "server_ready_timeout",
    "caf_llama_srv_en_temp": "en_temp",
    "caf_llama_srv_temperature": "temperature",
    "caf_llama_srv_en_gpu_layers": "en_gpu_layers",
    "caf_llama_srv_gpu_layers": "gpu_layers",
    "caf_llama_srv_en_threads": "en_threads",
    "caf_llama_srv_threads": "threads",
    "caf_llama_srv_flash_attn": "flash_attn",
    "caf_llama_srv_en_top_k": "en_top_k",
    "caf_llama_srv_top_k": "top_k",
    "caf_llama_srv_en_top_p": "en_top_p",
    "caf_llama_srv_top_p": "top_p",
    "caf_llama_srv_en_min_p": "en_min_p",
    "caf_llama_srv_min_p": "min_p",
    "caf_llama_srv_en_repeat_penalty": "en_repeat_penalty",
    "caf_llama_srv_repeat_penalty": "repeat_penalty",
    "caf_llama_srv_en_freq_penalty": "en_freq_penalty",
    "caf_llama_srv_freq_penalty": "freq_penalty",
    "caf_llama_srv_en_seed": "en_seed",
    "caf_llama_srv_seed": "seed",
    "caf_llama_srv_en_rope_freq_base": "en_rope_freq_base",
    "caf_llama_srv_rope_freq_base": "rope_freq_base",
    "caf_llama_srv_en_rope_freq_scale": "en_rope_freq_scale",
    "caf_llama_srv_rope_freq_scale": "rope_freq_scale",
    "caf_llama_srv_custom_flags": "custom_flags",
    "caf_llama_srv_server_host": "server_host",
    "caf_llama_srv_server_port": "server_port",
}

_CAF_LLAMA_SRV_DEFAULT_CONFIG: dict[str, Any] = {
    "binary_path": "",
    "model_dir": CAF_LLAMA_DEFAULT_MODEL_DIRECTORY,
    "model_name": "",
    "tokens": 32768,
    "server_ready_timeout": 300,
    "en_temp": False,
    "temperature": 0.8,
    "en_gpu_layers": False,
    "gpu_layers": 99,
    "en_threads": False,
    "threads": 4,
    "flash_attn": False,
    "en_top_k": False,
    "top_k": 40,
    "en_top_p": False,
    "top_p": 0.9,
    "en_min_p": False,
    "min_p": 0.1,
    "en_repeat_penalty": False,
    "repeat_penalty": 1.1,
    "en_freq_penalty": False,
    "freq_penalty": 0.0,
    "en_seed": False,
    "seed": -1,
    "en_rope_freq_base": False,
    "rope_freq_base": 10000.0,
    "en_rope_freq_scale": False,
    "rope_freq_scale": 1.0,
    "custom_flags": "--jinja --parallel 1",
    "server_host": "127.0.0.1",
    "server_port": 8080,
}


def _derive_local_url(config: dict[str, Any]) -> str:
    """Build the URL a process on the same host as the managed server uses.

    Normalizes a wildcard bind host (0.0.0.0/::/[::]) to 127.0.0.1 — mirrors
    core.bot_types.llama_server_bot._server_base_url. This is correct for
    BOTH local and SSH execution targets: whoever connects to the managed
    server locally (CAF's own process, wherever it runs) reaches it through
    that same host's own loopback interface, not through an SSH tunnel.
    """
    host = str(config.get("server_host") or "127.0.0.1").strip()
    port = int(config.get("server_port") or 8080)
    client_host = "127.0.0.1" if host in ("0.0.0.0", "::", "[::]") else host
    return f"http://{client_host}:{port}"


def _resolve_binary_and_model(config: dict[str, Any]) -> tuple[str, str]:
    """Resolve the llama-server binary and model path for the configured target.

    Local execution resolves paths against the ModelScope host's filesystem
    (mirrors core/evaluator.py's local managed-server binary/model handling).
    SSH execution leaves both untouched: start_remote_managed_llama_server does
    its own "~/"-expansion on the remote side, and a remote-only path must
    never be stat'd against the ModelScope host's filesystem.
    """
    binary = str(config.get("binary_path") or "llama-server").strip() or "llama-server"
    is_local = config.get("execution_target", "local") == "local"
    if is_local:
        binary = os.path.expanduser(binary)
        if os.path.isdir(binary):
            binary = os.path.join(binary, "llama-server")

    model_dir = str(config.get("model_dir") or "")
    model_name = str(config.get("model_name") or "")
    model_path = resolve_model_path(model_dir, model_name, local=is_local)
    return binary, model_path


def scan_caf_llama_models(config: dict[str, Any]) -> tuple[list[dict], str]:
    """Recursively discover inference GGUF models under Model Directory.

    Model Directory may be a directory (scanned recursively, no depth limit)
    or a single direct .gguf file (including a HuggingFace snapshot symlink).
    Scans wherever Execution Target points (local/SSH), reusing the same
    shared connection CAF itself runs on and the same scan primitive
    Llama-Server-Bot uses — see core.models.scan_gguf_models_via_env.
    """
    model_dir = str(config.get("model_dir") or "").strip()
    if not model_dir:
        return [], "Set Model Directory first."

    if config.get("execution_target", "local") != "ssh":
        expanded = os.path.expanduser(model_dir)
        if not os.path.exists(expanded):
            return [], f"Model directory or file not found: {model_dir}"
        models = scan_gguf_models(expanded)
        return [{**item, "path": item["name"]} for item in models], ""

    env = _environment_for_config(config)
    try:
        return scan_gguf_models_via_env(env, model_dir)
    except Exception as exc:
        return [], f"Model scan failed: {exc}"
    finally:
        if hasattr(env, "close"):
            env.close()


class CafLlamaBotPlugin(CafCliRunPlugin):
    """CyberAgentFlow evaluation against a ModelScope-managed llama-server."""

    type_id = "caf_llama_bot"
    label = "CAF + llama.cpp"
    dashboard_metrics_key = "caf_llama_metrics_matrix"
    icon = "🦙"
    default_project_name = "CAF + llama.cpp"
    state_key_map = {
        **{k: v for k, v in CAF_CLI_RUN_STATE_KEY_MAP.items() if k != "caf_cli_run_bot_metric_thresholds"},
        "caf_llama_bot_metric_thresholds": "metric_thresholds",
        **CAF_LLAMA_SRV_STATE_KEY_MAP,
    }
    session_defaults = {
        **{k: v for k, v in CAF_CLI_RUN_SESSION_DEFAULTS.items() if k != "caf_cli_run_bot_metric_thresholds"},
        # Keep inherited caf_cli_* fallbacks identical to CafCliRunPlugin.
        # core.state merges every plugin's session_defaults globally, so
        # overriding shared keys here would silently change the original CAF
        # bot before an active project's config is hydrated. This plugin locks
        # its connection in default_config/normalize/flush/render instead.
        "caf_llama_bot_metric_thresholds": {},
        "caf_llama_srv_binary_path": "",
        "caf_llama_srv_model_dir": CAF_LLAMA_DEFAULT_MODEL_DIRECTORY,
        "caf_llama_srv_model_name": "",
        "caf_llama_srv_tokens": 32768,
        "caf_llama_srv_server_ready_timeout": 300,
        "caf_llama_srv_en_temp": False,
        "caf_llama_srv_temperature": 0.8,
        "caf_llama_srv_en_gpu_layers": False,
        "caf_llama_srv_gpu_layers": 99,
        "caf_llama_srv_en_threads": False,
        "caf_llama_srv_threads": 4,
        "caf_llama_srv_flash_attn": False,
        "caf_llama_srv_en_top_k": False,
        "caf_llama_srv_top_k": 40,
        "caf_llama_srv_en_top_p": False,
        "caf_llama_srv_top_p": 0.9,
        "caf_llama_srv_en_min_p": False,
        "caf_llama_srv_min_p": 0.1,
        "caf_llama_srv_en_repeat_penalty": False,
        "caf_llama_srv_repeat_penalty": 1.1,
        "caf_llama_srv_en_freq_penalty": False,
        "caf_llama_srv_freq_penalty": 0.0,
        "caf_llama_srv_en_seed": False,
        "caf_llama_srv_seed": -1,
        "caf_llama_srv_en_rope_freq_base": False,
        "caf_llama_srv_rope_freq_base": 10000.0,
        "caf_llama_srv_en_rope_freq_scale": False,
        "caf_llama_srv_rope_freq_scale": 1.0,
        "caf_llama_srv_custom_flags": "--jinja --parallel 1",
        "caf_llama_srv_server_host": "127.0.0.1",
        "caf_llama_srv_server_port": 8080,
        "caf_llama_srv_discovered_models": [],
    }
    owned_prefixes = tuple(
        p for p in CafCliRunPlugin.owned_prefixes if p != "_caf_cli_run_bot_metric_threshold_"
    ) + ("_caf_llama_bot_metric_threshold_",)
    metric_specs = dict(LlamaServerBotPlugin.metric_specs)

    # ── Config lifecycle ─────────────────────────────────────────────────────

    def default_config(self, template_key: str = "blank") -> dict[str, Any]:
        config = super().default_config(template_key)
        config.update(_CAF_LLAMA_SRV_DEFAULT_CONFIG)
        # Lock the connection fields here too, not just in normalize/flush —
        # a freshly-created project's config must never advertise the
        # inherited Ollama defaults, even before normalization runs.
        config["caf_cli_provider"] = "openai"
        config["caf_cli_api_key"] = ""
        config["selected_model"] = config.get("model_name") or ""
        config["caf_cli_url"] = _derive_local_url(config)
        return config

    def normalize_project_config(self, config: dict[str, Any]) -> dict[str, Any]:
        config = super().normalize_project_config(config)
        if config.get("ssh_port") in (None, ""):
            config["ssh_port"] = CAF_DEFAULT_SSH_PORT
        if not str(config.get("caf_cli_directory") or "").strip():
            config["caf_cli_directory"] = CAF_DEFAULT_DIRECTORY
        if not str(config.get("model_dir") or "").strip():
            config["model_dir"] = CAF_LLAMA_DEFAULT_MODEL_DIRECTORY
        config["model_name"] = str(config.get("model_name") or "").strip()
        config["selected_model"] = config["model_name"]
        config["caf_cli_provider"] = "openai"
        config["caf_cli_api_key"] = ""
        config["caf_cli_url"] = _derive_local_url(config)
        return config

    def flush_config(self, project: dict[str, Any]) -> None:
        super().flush_config(project)
        cfg = project["config"]
        cfg["model_name"] = str(cfg.get("model_name") or "").strip()
        cfg["selected_model"] = cfg["model_name"]
        cfg["caf_cli_provider"] = "openai"
        cfg["caf_cli_api_key"] = ""
        # Recomputed from the JUST-flushed server_host/server_port — super()
        # is what actually wrote the current widget values into cfg above.
        cfg["caf_cli_url"] = _derive_local_url(cfg)

    # ── Config UI ────────────────────────────────────────────────────────────

    def _execution_target_intro(self) -> str | None:
        return (
            "This location runs **both** CyberAgentFlow and its managed llama-server model "
            "backend — the binary path and model paths below are resolved on this same host."
        )

    def _backend_section_title(self) -> str:
        return "CyberAgentFlow + llama.cpp"

    def _render_connection_fields(self) -> None:
        import streamlit as st

        if not str(st.session_state.get("caf_llama_srv_model_dir") or "").strip():
            st.session_state["caf_llama_srv_model_dir"] = CAF_LLAMA_DEFAULT_MODEL_DIRECTORY
        managed_model = st.session_state.get("caf_llama_srv_model_name", "")
        derived_live_url = _derive_local_url({
            "server_host": st.session_state.get("caf_llama_srv_server_host", "127.0.0.1"),
            "server_port": st.session_state.get("caf_llama_srv_server_port", 8080),
        })

        # Synchronize inherited connection state before any of CAF's own
        # widgets (which read their initial value from session_state[key])
        # are drawn.
        st.session_state["caf_cli_provider"] = "openai"
        st.session_state["caf_cli_api_key"] = ""
        st.session_state["caf_cli_model"] = managed_model
        st.session_state["caf_cli_url"] = derived_live_url

        st.markdown("**Model used by CyberAgentFlow**")
        if st.session_state.get("caf_cli_execution_target") == "ssh":
            st.info(
                "Execution Target is **SSH** — the managed llama-server launches on that "
                "remote host (via an SSH tunnel), so the Binary Path and Model Directory/Model "
                "below must point to files that exist **on the remote host**, not on this machine."
            )
        st.text_input(
            "llama-server Binary Path",
            key="caf_llama_srv_binary_path",
            placeholder="/usr/local/bin/llama-server",
            help="Full path to the llama-server executable. Leave blank to use `llama-server` from PATH.",
        )
        dir_col, scan_col = st.columns([4, 1])
        with dir_col:
            st.text_input(
                "Model Directory",
                key="caf_llama_srv_model_dir",
                placeholder="/home/user/models",
                help=(
                    "A directory to scan recursively (joined with Model (GGUF) below), "
                    "or a direct path to a single .gguf file to use as-is."
                ),
            )
        with scan_col:
            st.write("")
            st.write("")
            if st.button("Scan", key="btn_caf_llama_scan_models", use_container_width=True):
                self._scan_llama_models()

        discovered = st.session_state.get("caf_llama_srv_discovered_models", [])
        model_names = [item["name"] for item in discovered if item.get("name")]
        if model_names:
            current = st.session_state.get("caf_llama_srv_model_name", "")
            st.selectbox(
                "Model (GGUF)",
                options=model_names,
                index=model_names.index(current) if current in model_names else 0,
                key="caf_llama_srv_model_name",
                help="Discovered by Scan. Locks CyberAgentFlow's provider, URL, and model to this managed server.",
            )
        else:
            st.text_input(
                "Model (GGUF)",
                key="caf_llama_srv_model_name",
                help="Model filename/path, or click Scan above to pick from a scanned list. Locks CyberAgentFlow's provider, URL, and model to this managed server.",
            )
        st.caption(
            "ModelScope starts llama-server for this model and automatically locks CyberAgentFlow "
            f"to the OpenAI-compatible endpoint it derives — currently `{derived_live_url}` — so there "
            "are no separate provider, endpoint, or model fields to fill in."
        )

        with st.expander("Advanced llama.cpp runtime", expanded=True):
            col_host, col_port = st.columns([3, 1])
            with col_host:
                st.text_input(
                    "Listen Host",
                    key="caf_llama_srv_server_host",
                    placeholder="127.0.0.1",
                    help="Interface llama-server binds to. Use 127.0.0.1 for local-only or 0.0.0.0 to listen on all interfaces.",
                )
            with col_port:
                st.number_input(
                    "Listen Port", min_value=1, max_value=65535, step=1, key="caf_llama_srv_server_port",
                )
            st.number_input(
                "Context Window (tokens)", min_value=128, max_value=131072, step=256, key="caf_llama_srv_tokens",
                help="Maximum context length passed to llama-server via -c.",
            )
            st.number_input(
                "Server Startup Timeout (seconds)", min_value=10, max_value=3600, step=10,
                key="caf_llama_srv_server_ready_timeout",
                help="How long to wait for the model to load before giving up.",
            )
            st.text_input(
                "Custom Flags", key="caf_llama_srv_custom_flags", placeholder="--jinja --parallel 1 -ngl 99",
                help="Additional flags to pass to llama-server.",
            )

            st.markdown("**Sampling & performance**")
            from ui.plugin_api import render_flag_card, render_optional_param_card

            def _adv_opt(col, label, key_suffix, min_v, max_v, step, help_text, is_float=False, value_key_suffix=None, default_value=None):
                render_optional_param_card(
                    col, state_prefix="caf_llama_srv", label=label, key_suffix=key_suffix,
                    min_v=min_v, max_v=max_v, step=step, help_text=help_text, is_float=is_float,
                    value_key_suffix=value_key_suffix, default_value=default_value,
                )

            adv_cols = st.columns(3)
            _adv_opt(adv_cols[0], "Temperature", "temp", 0.0, 2.0, 0.1, "Higher values = more random (--temp).", True, value_key_suffix="temperature", default_value=0.8)
            _adv_opt(adv_cols[1], "GPU Layers", "gpu_layers", 0, 999, 1, "Layers to offload to GPU (-ngl).", default_value=99)
            _adv_opt(adv_cols[2], "Threads", "threads", 1, 256, 1, "CPU threads to use (-t).", default_value=4)

            _adv_opt(adv_cols[0], "Top K", "top_k", 0, 1000, 1, "Limit next token selection (--top-k).", default_value=40)
            _adv_opt(adv_cols[1], "Top P", "top_p", 0.0, 1.0, 0.05, "Cumulative probability (--top-p).", True, default_value=0.9)
            _adv_opt(adv_cols[2], "Min P", "min_p", 0.0, 1.0, 0.05, "Minimum probability (--min-p).", True, default_value=0.1)

            _adv_opt(adv_cols[0], "Repeat Pen.", "repeat_penalty", 0.0, 2.0, 0.1, "Penalize repetition (--repeat-penalty).", True, default_value=1.1)
            _adv_opt(adv_cols[1], "Freq Pen.", "freq_penalty", 0.0, 2.0, 0.1, "Frequency penalty (--freq-penalty).", True, default_value=0.0)
            _adv_opt(adv_cols[2], "Seed", "seed", -1, 2147483647, 1, "RNG seed (-1 for random) (--seed).", default_value=-1)

            _adv_opt(adv_cols[0], "RoPE Base", "rope_freq_base", 1000.0, 10000000.0, 1000.0, "RoPE base frequency (--rope-freq-base).", True, default_value=10000.0)
            _adv_opt(adv_cols[1], "RoPE Scale", "rope_freq_scale", 0.0, 100.0, 0.1, "RoPE frequency scale (--rope-freq-scale).", True, default_value=1.0)
            render_flag_card(adv_cols[2], key="caf_llama_srv_flash_attn", label="Flash Attn", help_text="Use Flash Attention (-fa).")

    def _scan_llama_models(self) -> None:
        import streamlit as st

        config = {config_key: st.session_state.get(state_key) for state_key, config_key in self.state_key_map.items()}
        with st.spinner("Scanning for .gguf models…"):
            models, error = scan_caf_llama_models(config)
        st.session_state["caf_llama_srv_discovered_models"] = models
        if error:
            st.error(error)
            return
        if not models:
            st.warning(f"No .gguf models found under {config.get('model_dir') or '(not set)'}.")
            return
        names = [item["name"] for item in models]
        if st.session_state.get("caf_llama_srv_model_name") not in names:
            st.session_state["caf_llama_srv_model_name"] = names[0]
        st.success(f"Found {len(models)} model(s).")

    # ── Execute UI ───────────────────────────────────────────────────────────

    def status_items(self, session_state, project: dict | None) -> list[StatusItem]:
        target = str(session_state.get("caf_cli_execution_target") or "local").upper()
        ready = target == "LOCAL" or bool(str(session_state.get("caf_cli_ssh_host") or "").strip())
        model = session_state.get("caf_llama_srv_model_name") or "not chosen"
        port = session_state.get("caf_llama_srv_server_port") or 8080
        return [
            StatusItem(f"Target: {target}", "up" if ready else "wait"),
            StatusItem("Backend: llama.cpp", "up"),
            StatusItem(f"Model: {model}", "up" if model != "not chosen" else "wait"),
            StatusItem(f"Port: {port}", "up"),
        ]

    # ── Evaluation ───────────────────────────────────────────────────────────

    def run_evaluation(self, env: Any, config: dict[str, Any], on_log: Callable[[str], None]) -> dict[str, Any]:
        from core.evaluator import _managed_llama_server_advanced_flags, _start_managed_llama_server

        config = self.normalize_project_config(config)

        model_name = str(config.get("model_name") or "").strip()
        if not model_name:
            on_log("[error] No model selected for the managed llama-server.")
            return self._aborted_telemetry(config, "No model selected for the managed llama-server.")

        managed_server = None
        llama_env = None
        metrics_before = None
        metrics_base_url = None
        verify_ssl = True

        try:
            try:
                binary, model_path = _resolve_binary_and_model(config)
                advanced_flags = _managed_llama_server_advanced_flags(config)
                ready_timeout = float(config.get("server_ready_timeout") or 300)

                if config.get("execution_target") == "ssh":
                    from core.remote_server import start_remote_managed_llama_server

                    llama_env = _environment_for_config(config)
                    managed_server = start_remote_managed_llama_server(
                        llama_env, binary, model_path, int(config["tokens"]),
                        int(config["server_port"]), config.get("server_host") or "127.0.0.1",
                        on_log, custom_flags=config.get("custom_flags", ""),
                        advanced_flags=advanced_flags, ready_timeout=ready_timeout,
                    )
                    # CAF's own shell/job process executes ON that same remote
                    # host, so it reaches llama-server at its own bind address
                    # directly — known immediately, unlike the SSH tunnel's
                    # local_port below (only known once the server is up).
                    config["caf_cli_url"] = _derive_local_url(config)
                    metrics_base_url = f"http://127.0.0.1:{managed_server.local_port}"
                else:
                    managed_server = _start_managed_llama_server(
                        binary, model_path, int(config["tokens"]), int(config["server_port"]),
                        config.get("server_host") or "127.0.0.1", on_log,
                        custom_flags=config.get("custom_flags", ""),
                        advanced_flags=advanced_flags, ready_timeout=ready_timeout,
                    )
                    config["caf_cli_url"] = _derive_local_url(config)
                    metrics_base_url = config["caf_cli_url"]

                config["caf_cli_provider"] = "openai"
                verify_ssl = effective_verify_ssl(metrics_base_url, config.get("caf_cli_verify_ssl", True))
                metrics_before = fetch_llama_server_metrics(metrics_base_url, verify_ssl=verify_ssl)
            except Exception as exc:
                on_log(f"[error] Failed to start managed llama-server: {exc}")
                return self._aborted_telemetry(config, f"Failed to start managed llama-server: {exc}")

            try:
                telemetry = super().run_evaluation(env, config, on_log)
            except (CafActiveSessionError, CafAppRestartRequiredError, CafSessionStartTimeoutError):
                # CAF's own retry/confirmation-dialog UI must see these unchanged.
                raise
            except Exception as exc:
                metrics_after = fetch_llama_server_metrics(metrics_base_url, verify_ssl=verify_ssl)
                return self._aborted_telemetry(
                    config, str(exc), metrics_before=metrics_before, metrics_after=metrics_after,
                )

            metrics_after = fetch_llama_server_metrics(metrics_base_url, verify_ssl=verify_ssl)
            telemetry["llama_server_metrics"] = llama_server_metrics_delta(metrics_before, metrics_after)
            telemetry["llama_server_metric_snapshots"] = {"before": metrics_before, "after": metrics_after}
            telemetry["run_bot_type"] = self.type_id
            telemetry["run_backend"] = f"{telemetry.get('run_backend', 'CAF')} + managed llama-server"
            return telemetry
        finally:
            self._teardown_managed_server(managed_server, llama_env, on_log)

    @staticmethod
    def _teardown_managed_server(managed_server: Any, llama_env: Any, on_log: Callable[[str], None]) -> None:
        """Best-effort terminate -> wait -> kill. Never raises: a cleanup
        failure must not replace a successful result or an in-flight
        exception (including CAF's own confirmation-dialog exceptions)."""
        if managed_server is not None:
            try:
                managed_server.terminate()
            except Exception as exc:
                on_log(f"[WARN] Failed to terminate managed llama-server: {exc}")
            try:
                managed_server.wait(timeout=5)
            except Exception:
                try:
                    managed_server.kill()
                except Exception as exc:
                    on_log(f"[WARN] Failed to kill managed llama-server: {exc}")
        if llama_env is not None and hasattr(llama_env, "close"):
            try:
                llama_env.close()
            except Exception as exc:
                on_log(f"[WARN] Failed to close remote environment: {exc}")

    def _aborted_telemetry(
        self, config: dict[str, Any], error: str,
        metrics_before: dict[str, Any] | None = None, metrics_after: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from core.evaluator import _init_telemetry

        telemetry = _init_telemetry(config)
        telemetry["run_bot_type"] = self.type_id
        telemetry["run_backend"] = f"CAF + managed llama-server ({config.get('caf_cli_provider') or 'openai'})"
        telemetry["run_model"] = config.get("selected_model") or config.get("model_name") or ""
        telemetry["run_aborted"] = True
        telemetry["error"] = error
        if metrics_before is not None or metrics_after is not None:
            telemetry["llama_server_metrics"] = llama_server_metrics_delta(metrics_before, metrics_after)
            telemetry["llama_server_metric_snapshots"] = {"before": metrics_before, "after": metrics_after}
        return telemetry
