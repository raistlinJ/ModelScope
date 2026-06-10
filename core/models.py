import os
import requests


# Vocab-only GGUF files shipped with llama.cpp — not inference models
_VOCAB_PREFIXES = ("ggml-vocab-",)


def _ensure_scheme(url: str) -> str:
    """Prepend http:// if the URL has no scheme."""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


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
    url = _ensure_scheme(base_url)
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


def detect_backend(url: str) -> str | None:
    """Probe url and return 'ollama', 'llama.cpp', or None."""
    base = _ensure_scheme(url).rstrip("/")
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
