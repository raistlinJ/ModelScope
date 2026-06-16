import pytest
import requests
from unittest.mock import patch, MagicMock
from core import models
from core.utils import ensure_http_scheme as _ensure_scheme
from core.models import _is_inference_model

@patch("requests.get")
def test_fetch_ollama_models_404(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("404 Not Found")
    mock_get.return_value = mock_resp
    
    models_list, error = models.fetch_ollama_models("http://localhost:11434")
    assert models_list == []
    assert "404" in error

@patch("requests.get")
def test_fetch_ollama_models_500(mock_get):
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
    mock_get.return_value = mock_resp
    
    models_list, error = models.fetch_ollama_models("http://localhost:11434")
    assert models_list == []
    assert "500" in error

@patch("requests.get")
def test_fetch_ollama_models_invalid_json(mock_get):
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.side_effect = ValueError("Invalid JSON")
    mock_get.return_value = mock_resp
    
    models_list, error = models.fetch_ollama_models("http://localhost:11434")
    assert models_list == []
    assert "Invalid JSON" in error

def test_is_inference_model_internal():
    # This internal helper only checks for vocab prefixes
    assert _is_inference_model("test.gguf") is True
    assert _is_inference_model("ggml-vocab-test.gguf") is False
    # It does NOT check extensions (that's done by caller)
    assert _is_inference_model("test.bin") is True

def test_ensure_scheme_internal():
    assert _ensure_scheme("localhost:11434/v1") == "http://localhost:11434/v1"
    assert _ensure_scheme("https://secure.site") == "https://secure.site"
    assert _ensure_scheme("  127.0.0.1  ") == "http://127.0.0.1"
