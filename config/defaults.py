import os

# LLM server defaults
LLAMA_CPP_DEFAULT_URL = "http://localhost:8080"
OLLAMA_DEFAULT_URL    = "http://localhost:11434"

# Paths on this machine (also exposed as editable state via core/state.py)
LLAMA_SERVER_BIN = "/home/dsch/llama.cpp/build/bin/llama-server"
GGUF_MODELS_DIR  = "/home/dsch/llama.cpp/models"

# MCP server script path (absolute, derived at import time)
_GUI_ROOT       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP_SCRIPT_PATH = os.path.join(_GUI_ROOT, "mcp-server", "index.js")
MCP_SERVER_BASE_URL = "http://localhost:9191"

# Context window
DEFAULT_CONTEXT_SIZE = 4096
MIN_CONTEXT_SIZE     = 2048   # 512 was too small for any realistic prompt
MAX_CONTEXT_SIZE     = 32768
CONTEXT_STEP         = 512

# Run history: number of past runs kept in session state
MAX_RUN_HISTORY = 10
