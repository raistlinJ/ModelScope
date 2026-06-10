"""
Unit tests for core.models — URL scheme normalization, GGUF scanning,
Ollama model fetching, and backend detection.
"""
import pytest
from unittest.mock import patch, MagicMock
import requests as req_lib

from core.models import _ensure_scheme, _is_inference_model, scan_gguf_models, fetch_ollama_models


# ── _ensure_scheme ────────────────────────────────────────────────────────────

class TestEnsureScheme:
    def test_adds_http_when_missing(self):
        assert _ensure_scheme("localhost:11434") == "http://localhost:11434"

    def test_adds_http_to_hostname_with_no_port(self):
        assert _ensure_scheme("grain.utep.edu") == "http://grain.utep.edu"

    def test_preserves_existing_http(self):
        assert _ensure_scheme("http://localhost:11434") == "http://localhost:11434"

    def test_preserves_existing_https(self):
        assert _ensure_scheme("https://example.com") == "https://example.com"

    def test_empty_string_stays_empty(self):
        assert _ensure_scheme("") == ""

    def test_strips_whitespace(self):
        assert _ensure_scheme("  localhost:8080  ") == "http://localhost:8080"

    def test_none_like_value(self):
        assert _ensure_scheme(None) == ""  # type: ignore[arg-type]


# ── _is_inference_model ────────────────────────────────────────────────────────

class TestIsInferenceModel:
    def test_normal_model(self):
        assert _is_inference_model("llama3-8b.gguf") is True

    def test_vocab_excluded(self):
        assert _is_inference_model("ggml-vocab-llama.gguf") is False

    def test_uppercase_extension(self):
        assert _is_inference_model("/path/to/my-model.GGUF") is True

    def test_nested_path(self):
        assert _is_inference_model("/models/sub/model.gguf") is True


# ── scan_gguf_models ──────────────────────────────────────────────────────────

class TestScanGgufModels:
    @patch("os.walk")
    @patch("os.path.isdir")
    @patch("os.path.getsize")
    def test_scans_directory(self, mock_getsize, mock_isdir, mock_walk):
        mock_isdir.return_value = True
        mock_walk.return_value = [
            ("/models", ("sub",), ("m1.gguf", "ggml-vocab-x.gguf")),
            ("/models/sub", (), ("m2.gguf",)),
        ]
        mock_getsize.return_value = 2_000_000_000  # 2 GB

        models = scan_gguf_models("/models")

        assert len(models) == 2
        names = {m["name"] for m in models}
        assert "m1.gguf" in names
        assert "sub/m2.gguf" in names
        assert models[0]["size_gb"] == 2.0

    def test_empty_path_returns_empty(self):
        assert scan_gguf_models("") == []

    def test_nonexistent_dir_returns_empty(self):
        assert scan_gguf_models("/this/does/not/exist") == []

    @patch("os.path.isfile", return_value=True)
    def test_single_file_path(self, mock_isfile):
        models = scan_gguf_models("/models/my-model.gguf")
        assert len(models) == 1
        assert models[0]["name"] == "my-model.gguf"
        assert models[0]["path"] == "/models/my-model.gguf"

    @patch("os.path.isfile", return_value=True)
    def test_vocab_file_as_path_returns_empty(self, mock_isfile):
        models = scan_gguf_models("/models/ggml-vocab-llama.gguf")
        assert models == []


# ── fetch_ollama_models ───────────────────────────────────────────────────────

class TestFetchOllamaModels:
    def _mock_ok(self, models_data: list) -> MagicMock:
        resp = MagicMock()
        resp.ok = True
        resp.json.return_value = {"models": models_data}
        resp.raise_for_status = MagicMock()
        return resp

    @patch("core.models.requests.get")
    def test_success_returns_models(self, mock_get):
        mock_get.return_value = self._mock_ok([
            {"name": "llama3:8b", "size": 5_000_000_000},
            {"name": "mistral:7b", "size": 4_500_000_000},
        ])
        models, err = fetch_ollama_models("http://localhost:11434")

        assert err == ""
        assert len(models) == 2
        assert models[0]["name"] == "llama3:8b"
        assert models[0]["size_gb"] == 5.0

    @patch("core.models.requests.get")
    def test_scheme_added_automatically(self, mock_get):
        """Regression: bare hostname must not raise MissingSchema."""
        mock_get.return_value = self._mock_ok([])
        models, err = fetch_ollama_models("grain.utep.edu:11434")

        # The call must succeed (no MissingSchema exception)
        assert err == ""
        # Verify we hit the correct URL with scheme prepended
        call_url = mock_get.call_args[0][0]
        assert call_url.startswith("http://")

    @patch("core.models.requests.get", side_effect=req_lib.exceptions.ConnectionError)
    def test_connection_error(self, _):
        models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert "Cannot connect" in err

    @patch("core.models.requests.get", side_effect=req_lib.exceptions.Timeout)
    def test_timeout(self, _):
        models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert "Timed out" in err

    def test_empty_url_returns_error_tuple(self):
        models, err = fetch_ollama_models("")
        assert models == []
        assert "empty" in err.lower()

    @patch("core.models.requests.get")
    def test_http_error(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = req_lib.exceptions.HTTPError("404")
        mock_get.return_value = resp
        models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert err != ""
