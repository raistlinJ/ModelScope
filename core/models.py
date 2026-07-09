"""Model discovery and inventory.

Finds available models for each backend: GGUF files on disk for llama.cpp and
the model list served by a running Ollama/llama.cpp HTTP endpoint. Returns plain
metadata; it does not start servers (that is core.llama_server) or run inference.
"""
import json
import os
import subprocess
import sys
import requests

from core.utils import ensure_http_scheme, effective_verify_ssl


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
    root_path = os.path.expanduser(root_path)
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


def normalize_openai_base_url(base_url: str) -> str:
    """Normalize an OpenAI-compatible base URL before appending /v1 routes."""
    url = ensure_http_scheme(base_url).rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3]
    return url


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


def get_ollama_status(base_url: str) -> dict:
    """Return {running: bool, version: str, error: str} by probing the Ollama service.

    Uses /api/version for a JSON version string.  Falls back gracefully — a 200
    response without JSON still counts as running (plain-text "Ollama is running").
    """
    url = ensure_http_scheme(base_url)
    if not url:
        return {"running": False, "version": "", "error": "Server URL is empty."}
    try:
        resp = requests.get(url.rstrip("/") + "/api/version", timeout=5)
        if resp.status_code == 200:
            try:
                version = resp.json().get("version", "")
            except Exception:
                version = ""
            return {"running": True, "version": version, "error": ""}
        return {
            "running": False,
            "version": "",
            "error": f"HTTP {resp.status_code} from {url}",
        }
    except requests.exceptions.MissingSchema:
        return {"running": False, "version": "", "error": f"Invalid URL (missing scheme): {url}"}
    except requests.exceptions.ConnectionError:
        return {"running": False, "version": "", "error": f"Cannot connect to {url} — is Ollama running?"}
    except requests.exceptions.Timeout:
        return {"running": False, "version": "", "error": f"Timed out connecting to {url}"}
    except Exception as e:
        return {"running": False, "version": "", "error": str(e)}


def get_ollama_running_models(base_url: str) -> tuple[list[dict], str]:
    """Return ([{name, size_gb, expires_at}], error_str) from Ollama /api/ps."""
    url = ensure_http_scheme(base_url)
    if not url:
        return [], "Server URL is empty."
    try:
        resp = requests.get(url.rstrip("/") + "/api/ps", timeout=5)
        resp.raise_for_status()
        models = []
        for m in resp.json().get("models", []):
            models.append({
                "name":       m.get("name", ""),
                "size_gb":    round(m.get("size", 0) / 1e9, 1),
                "expires_at": m.get("expires_at", ""),
            })
        return models, ""
    except requests.exceptions.MissingSchema:
        return [], f"Invalid URL (missing scheme): {url}"
    except requests.exceptions.ConnectionError:
        return [], f"Cannot connect to {url} — is Ollama running?"
    except requests.exceptions.Timeout:
        return [], f"Timed out connecting to {url}"
    except Exception as e:
        return [], str(e)


def pull_ollama_model(base_url: str, model_name: str, on_log=None) -> tuple[bool, str]:
    """Pull a model via streaming /api/pull.  Calls on_log(str) for each progress line.

    Returns (success, message).
    """
    url = ensure_http_scheme(base_url)
    if not url:
        return False, "Server URL is empty."
    model_name = model_name.strip()
    if not model_name:
        return False, "Model name is required."

    def _log(msg: str) -> None:
        if on_log:
            on_log(msg)

    try:
        resp = requests.post(
            url.rstrip("/") + "/api/pull",
            json={"name": model_name},
            stream=True,
            timeout=600,
        )
        resp.raise_for_status()

        last_status = ""
        for raw_line in resp.iter_lines():
            if not raw_line:
                continue
            try:
                data = json.loads(raw_line)
            except json.JSONDecodeError:
                _log(raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line)
                continue

            if "error" in data:
                return False, data["error"]

            status = data.get("status", "")
            completed = data.get("completed")
            total = data.get("total")
            if completed is not None and total and total > 0:
                pct = round(completed / total * 100, 1)
                line = f"{status}  {pct}%  ({round(completed / 1e6, 1)} / {round(total / 1e6, 1)} MB)"
            else:
                line = status
            if line:
                _log(line)
                last_status = status

        return True, f"Pull complete: {model_name}"
    except requests.exceptions.MissingSchema:
        return False, f"Invalid URL (missing scheme): {url}"
    except requests.exceptions.ConnectionError:
        return False, f"Cannot connect to {url} — is Ollama running?"
    except requests.exceptions.Timeout:
        return False, f"Timed out pulling {model_name}"
    except Exception as e:
        return False, str(e)


def delete_ollama_model(base_url: str, model_name: str) -> tuple[bool, str]:
    """Delete a pulled Ollama model.  Returns (success, message)."""
    url = ensure_http_scheme(base_url)
    if not url:
        return False, "Server URL is empty."
    model_name = model_name.strip()
    if not model_name:
        return False, "Model name is required."
    try:
        resp = requests.delete(
            url.rstrip("/") + "/api/delete",
            json={"name": model_name},
            timeout=15,
        )
        resp.raise_for_status()
        return True, f"Deleted: {model_name}"
    except requests.exceptions.MissingSchema:
        return False, f"Invalid URL (missing scheme): {url}"
    except requests.exceptions.ConnectionError:
        return False, f"Cannot connect to {url} — is Ollama running?"
    except requests.exceptions.Timeout:
        return False, f"Timed out deleting {model_name}"
    except requests.exceptions.HTTPError as e:
        return False, f"HTTP error: {e}"
    except Exception as e:
        return False, str(e)


def fetch_llama_cpp_models(base_url: str, verify_ssl: bool = True) -> tuple[list[dict], str]:
    """
    Return (models, error) where models is [{name, size_gb, context_size}]
    from a llama.cpp /v1/models endpoint.  Works for local and remote servers.
    error is '' on success, otherwise a human-readable message.
    """
    url = normalize_openai_base_url(base_url)
    if not url:
        return [], "Server URL is empty."
    try:
        resp = requests.get(url.rstrip("/") + "/v1/models", timeout=8,
                            verify=effective_verify_ssl(url, verify_ssl))
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
    base = normalize_openai_base_url(url)
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
