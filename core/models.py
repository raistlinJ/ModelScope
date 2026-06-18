import os
import subprocess
import sys
import requests

from core.utils import ensure_http_scheme


# Vocab-only GGUF files shipped with llama.cpp — not inference models
_VOCAB_PREFIXES = ("ggml-vocab-",)


def _is_inference_model(filename: str) -> bool:
    name = os.path.basename(filename).lower()
    return not any(name.startswith(p) for p in _VOCAB_PREFIXES)


def scan_gguf_models(root_path: str) -> list[dict]:
    """Return [{name, path}] for all inference GGUF models found under root_path."""
    root_path = (root_path or "").strip()
    if not root_path:
        return []
    if os.path.isfile(root_path) and root_path.lower().endswith(".gguf"):
        if _is_inference_model(root_path):
            return [{"name": os.path.basename(root_path), "path": root_path}]
        return []
    if not os.path.isdir(root_path):
        return []

    results = []
    for dirpath, _, filenames in os.walk(root_path):
        for f in sorted(filenames):
            if f.lower().endswith(".gguf") and _is_inference_model(f):
                full = os.path.join(dirpath, f)
                rel  = os.path.relpath(full, root_path)
                try:
                    size_gb = round(os.path.getsize(full) / 1e9, 1)
                except OSError:
                    size_gb = 0.0
                results.append({"name": rel, "path": full, "size_gb": size_gb})
    return results


def fetch_ollama_models(base_url: str) -> tuple[list[dict], str]:
    """
    Return (models, error) where models is [{name, size_gb}] from Ollama /api/tags.
    error is '' on success, otherwise a human-readable message.
    """
    url = ensure_http_scheme(base_url)
    if not url:
        return [], "Server URL is empty."
    try:
        resp = requests.get(url.rstrip("/") + "/api/tags", timeout=8)
        resp.raise_for_status()
        models = [
            {"name": m["name"], "size_gb": round(m.get("size", 0) / 1e9, 1)}
            for m in resp.json().get("models", [])
        ]
        return models, ""
    except requests.exceptions.MissingSchema:
        return [], f"Invalid URL (missing scheme): {url}"
    except requests.exceptions.ConnectionError:
        return [], f"Cannot connect to {url} — is Ollama running?"
    except requests.exceptions.Timeout:
        return [], f"Timed out connecting to {url}"
    except Exception as e:
        return [], str(e)


def fetch_llama_cpp_models(base_url: str) -> tuple[list[dict], str]:
    """
    Return (models, error) where models is [{name, size_gb, context_size}]
    from a llama.cpp /v1/models endpoint.  Works for local and remote servers.
    error is '' on success, otherwise a human-readable message.
    """
    url = ensure_http_scheme(base_url)
    if not url:
        return [], "Server URL is empty."
    try:
        resp = requests.get(url.rstrip("/") + "/v1/models", timeout=8)
        resp.raise_for_status()
        data = resp.json()
        # Parse /v1/models: check both top-level `data` (OpenAI list format) and `models`
        raw_list = data.get("data") or data.get("models") or []
        models: list[dict] = []
        for m in raw_list:
            if not isinstance(m, dict):
                continue
            name = m.get("id") or m.get("name") or ""
            if not name:
                continue
            meta = m.get("meta") or {}
            size_bytes = meta.get("size") or m.get("size") or 0
            n_ctx = meta.get("n_ctx") or 0
            models.append({
                "name":         name,
                "path":         name,          # remote: "path" is the model id
                "size_gb":      round(int(size_bytes) / 1e9, 1) if size_bytes else 0.0,
                "context_size": int(n_ctx) if n_ctx else None,
                "source":       "remote",
            })
        return models, ""
    except requests.exceptions.MissingSchema:
        return [], f"Invalid URL (missing scheme): {url}"
    except requests.exceptions.ConnectionError:
        return [], f"Cannot connect to {url} — is the server running?"
    except requests.exceptions.Timeout:
        return [], f"Timed out connecting to {url}"
    except Exception as e:
        return [], str(e)


def detect_backend(url: str) -> str | None:
    """Probe url and return 'ollama', 'llama.cpp', or None."""
    base = ensure_http_scheme(url).rstrip("/")
    try:
        r = requests.get(base + "/api/tags", timeout=3)
        if r.ok and "models" in r.json():
            return "ollama"
    except Exception:
        pass
    try:
        r = requests.get(base + "/v1/models", timeout=3)
        if r.ok:
            return "llama.cpp"
    except Exception:
        pass
    return None


# ── GGUF Compile Pipeline ──────────────────────────────────────────────────────

def compile_gguf(
    source_path: str,
    output_dir: str,
    quantization: str = "Q4_K_M",
    convert_script: str | None = None,
    quantize_bin: str | None = None,
    on_log=None,
) -> tuple[bool, str]:
    """
    Convert a HuggingFace model directory to GGUF and optionally quantize it.

    Pipeline:
      1. convert_hf_to_gguf.py  → <output_dir>/<model_name>-F16.gguf
      2. llama-quantize          → <output_dir>/<model_name>-<quant>.gguf

    Returns (success, message_or_output_path).
    on_log is an optional callable(str) that receives progress lines.
    """
    from config.defaults import CONVERT_HF_TO_GGUF_PY, LLAMA_QUANTIZE_BIN, GGUF_MODELS_DIR

    convert_script = convert_script or CONVERT_HF_TO_GGUF_PY
    quantize_bin   = quantize_bin   or LLAMA_QUANTIZE_BIN
    output_dir     = output_dir     or GGUF_MODELS_DIR

    def _log(msg: str) -> None:
        if on_log:
            on_log(msg)

    # ── Validate inputs ────────────────────────────────────────────────────────
    if not os.path.isdir(source_path):
        return False, f"Source path is not a directory: {source_path}"
    if not os.path.isfile(convert_script):
        return False, f"convert_hf_to_gguf.py not found: {convert_script}"
    if quantization and not os.path.isfile(quantize_bin):
        return False, f"llama-quantize not found: {quantize_bin}"

    os.makedirs(output_dir, exist_ok=True)
    model_name = os.path.basename(source_path.rstrip("/\\"))
    f16_path   = os.path.join(output_dir, f"{model_name}-F16.gguf")
    final_path = os.path.join(output_dir, f"{model_name}-{quantization}.gguf") if quantization else f16_path

    # ── Step 1: convert HF → F16 GGUF ─────────────────────────────────────────
    _log(f"[COMPILE] Converting {model_name} to F16 GGUF…")
    convert_cmd = [
        sys.executable, convert_script,
        source_path,
        "--outfile", f16_path,
        "--outtype", "f16",
    ]
    try:
        result = subprocess.run(
            convert_cmd, capture_output=True, text=True, timeout=1800
        )
        if result.stdout:
            for line in result.stdout.splitlines()[-10:]:
                _log(f"[CONVERT] {line}")
        if result.returncode != 0:
            _log(f"[CONVERT ERROR] {result.stderr[-500:]}")
            return False, f"Conversion failed (exit {result.returncode}): {result.stderr[-300:]}"
        _log(f"[COMPILE] Conversion complete: {f16_path}")
    except subprocess.TimeoutExpired:
        return False, "Conversion timed out (30 minutes)."
    except Exception as e:
        return False, f"Conversion error: {e}"

    # ── Step 2: quantize ───────────────────────────────────────────────────────
    if quantization:
        _log(f"[COMPILE] Quantizing to {quantization}…")
        quantize_cmd = [quantize_bin, f16_path, final_path, quantization]
        try:
            result = subprocess.run(
                quantize_cmd, capture_output=True, text=True, timeout=3600
            )
            if result.stdout:
                for line in result.stdout.splitlines()[-10:]:
                    _log(f"[QUANTIZE] {line}")
            if result.returncode != 0:
                _log(f"[QUANTIZE ERROR] {result.stderr[-500:]}")
                return False, f"Quantization failed (exit {result.returncode}): {result.stderr[-300:]}"
            _log(f"[COMPILE] Quantization complete: {final_path}")
        except subprocess.TimeoutExpired:
            return False, "Quantization timed out (60 minutes)."
        except Exception as e:
            return False, f"Quantization error: {e}"

    return True, final_path
