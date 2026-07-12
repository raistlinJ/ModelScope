"""Static configuration constants and machine-specific paths.

Centralises every default URL, filesystem path and tunable limit so they appear
exactly once. Paths default to this developer's machine but are overridable via
environment variables for portability.
"""
import os

# LLM server defaults
LLAMA_CPP_DEFAULT_URL = "http://localhost:8080"
OLLAMA_DEFAULT_URL    = "http://localhost:11434"

# External (pre-compiled) llama.cpp endpoint — grain.utep.edu
EXTERNAL_LLAMA_CPP_URL   = "http://grain.utep.edu:11434"
EXTERNAL_LLAMA_CPP_MODEL = "unsloth/Qwen3.6-35B-A3B-GGUF:UD-Q4_K_M"

# Paths on this machine (override via env vars for portability)
LLAMA_SERVER_BIN = os.environ.get("LLAMA_SERVER_BIN", "/home/dsch/llama.cpp/build/bin/llama-server")
GGUF_MODELS_DIR  = os.environ.get("GGUF_MODELS_DIR",  "/home/dsch/llama.cpp/models")

# llama.cpp toolchain paths (used by the GGUF compile pipeline)
LLAMA_CPP_ROOT          = os.environ.get("LLAMA_CPP_ROOT",    "/home/dsch/llama.cpp")
LLAMA_QUANTIZE_BIN      = os.environ.get("LLAMA_QUANTIZE_BIN", "/home/dsch/llama.cpp/build/bin/llama-quantize")
CONVERT_HF_TO_GGUF_PY  = os.environ.get(
    "CONVERT_HF_TO_GGUF_PY", "/home/dsch/llama.cpp/convert_hf_to_gguf.py"
)

# MCP server script path (absolute, derived at import time)
_GUI_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_SCRIPT_PATH = os.path.join(_GUI_ROOT, "mcp-server", "index.js")
MCP_CONFIG_PATH = os.path.join(_GUI_ROOT, "mcp-server", "mcp_config.json")
MCP_SERVER_BASE_URL = "http://localhost:9191"

# Context window
DEFAULT_CONTEXT_SIZE = 4096
MIN_CONTEXT_SIZE     = 2048   # 512 was too small for any realistic prompt
MAX_CONTEXT_SIZE     = 32768
CONTEXT_STEP         = 512

# Run history: number of past runs kept in session state
MAX_RUN_HISTORY = 10
