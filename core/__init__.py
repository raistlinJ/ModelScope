"""ModelScope core: the framework's business logic, independent of the UI.

Everything here is importable from both ``app.py`` (Streamlit) and ``cli.py``,
EXCEPT the modules that touch ``streamlit`` directly (noted below). To keep
``import core`` cheap and free of a hard Streamlit dependency, this package does
*not* eagerly import its submodules — import the specific module you need, e.g.
``from core.evaluator import run_evaluation``.

Module responsibilities (one line each):

  Pure / CLI-safe (no Streamlit):
    environment      — Local/SSH execution targets + create_environment() factory
    evaluator        — local LLM agent loop, tool execution, telemetry*
    caf_runner       — remote CyberAgentFlow execution over SSH, artifact pull
    caf_state        — CAF step telemetry dataclass, phase inference, TDI scoring
    session_log      — SessionLog (writer) + SessionRepository (reader) for run logs
    judge            — LLM-as-judge scoring of a transcript
    schema_registry  — JSON-schema validation of tool/telemetry shapes
    preflight        — environment readiness checks
    test_runner      — scenario test-suite execution
    streaming        — backend HTTP adapters (Ollama, llama.cpp) for one LLM round
    models           — model discovery (GGUF on disk, served model lists)
    settings_store   — persist/load non-sensitive user settings to ~/.modelscope
    logsetup         — centralised logging configuration
    utils            — dependency-free shared helpers (ensure_http_scheme, strip_ansi)

  Streamlit-coupled (UI path only — do not import from the CLI):
    state            — session_state defaults and scenario sync
    llama_server     — local llama-server process lifecycle
    mcp_manager      — MCP tool-server lifecycle + tool invocation
    comparison       — model-comparison run orchestration (UI)
    batch_runner     — batch evaluation runner (UI)

  * evaluator imports mcp_manager, which imports streamlit, so evaluator is not
    strictly Streamlit-free today. This is a pre-existing coupling, documented
    so a future change can break it deliberately rather than by accident.
"""

# Submodule names that make up the public core API. Listed as strings rather
# than imported so ``import core`` does not transitively require Streamlit
# (several submodules import it). Use explicit ``from core.<module> import ...``.
__all__ = [
    "environment",
    "evaluator",
    "caf_runner",
    "caf_state",
    "session_log",
    "judge",
    "schema_registry",
    "preflight",
    "test_runner",
    "streaming",
    "models",
    "settings_store",
    "logsetup",
    "utils",
    "state",
    "llama_server",
    "mcp_manager",
    "comparison",
    "batch_runner",
]
