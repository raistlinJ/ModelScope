"""
Extended unit tests for core/models.py.

Covers:
  - fetch_ollama_models: success, connection error, timeout, missing schema, other error
  - fetch_llama_cpp_models: success, both data/models keys, empty list, errors
  - detect_backend: ollama, llama.cpp, neither
  - compile_gguf: validation errors (source not dir, script not found, quantize not found)
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from core.models import (
    fetch_ollama_models,
    fetch_llama_cpp_models,
    detect_backend,
    scan_gguf_models,
    compile_gguf,
)


# ── fetch_ollama_models ────────────────────────────────────────────────────────

class TestFetchOllamaModels:
    def test_empty_url_returns_error(self):
        models, err = fetch_ollama_models("")
        assert models == []
        assert err != ""

    def test_success(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "models": [{"name": "llama3", "size": 4_000_000_000}]
        }
        mock_resp.raise_for_status.return_value = None
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_ollama_models("http://localhost:11434")
        assert err == ""
        assert len(models) == 1
        assert models[0]["name"] == "llama3"

    def test_connection_error(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.ConnectionError("refused")):
            models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert "Cannot connect" in err

    def test_timeout(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.Timeout("timed out")):
            models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert "Timed out" in err

    def test_missing_schema(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.MissingSchema("no scheme")):
            models, err = fetch_ollama_models("no-scheme-url")
        assert models == []
        assert "Invalid URL" in err

    def test_other_exception(self):
        with patch("core.models.requests.get",
                   side_effect=ValueError("unexpected")):
            models, err = fetch_ollama_models("http://localhost:11434")
        assert models == []
        assert err != ""


# ── fetch_llama_cpp_models ────────────────────────────────────────────────────

class TestFetchLlamaCppModels:
    def test_empty_url(self):
        models, err = fetch_llama_cpp_models("")
        assert models == []
        assert err != ""

    def test_success_with_data_key(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": [{"id": "llama3-8b.gguf", "size": 0}]
        }
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert err == ""
        assert len(models) == 1
        assert models[0]["name"] == "llama3-8b.gguf"

    def test_success_with_models_key(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "models": [{"id": "qwen2.5.gguf"}]
        }
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert err == ""
        assert models[0]["name"] == "qwen2.5.gguf"

    def test_skips_entries_without_id(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": [{"no_id": True}, {"id": "model.gguf"}]
        }
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert len(models) == 1
        assert models[0]["name"] == "model.gguf"

    def test_skips_non_dict_entries(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"data": ["not_a_dict", {"id": "valid.gguf"}]}
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert len(models) == 1

    def test_connection_error(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.ConnectionError("refused")):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert models == []
        assert "Cannot connect" in err

    def test_timeout(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.Timeout("timed out")):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert models == []
        assert "Timed out" in err

    def test_missing_schema(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.MissingSchema("no scheme")):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert models == []
        assert "Invalid URL" in err

    def test_meta_context_size(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "data": [{"id": "m.gguf", "meta": {"n_ctx": 4096, "size": 4_000_000_000}}]
        }
        with patch("core.models.requests.get", return_value=mock_resp):
            models, err = fetch_llama_cpp_models("http://localhost:8080")
        assert models[0]["context_size"] == 4096
        assert models[0]["size_gb"] == pytest.approx(4.0, abs=0.1)


# ── detect_backend ────────────────────────────────────────────────────────────

class TestDetectBackend:
    def test_detects_ollama(self):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {"models": []}
        with patch("core.models.requests.get", return_value=mock_resp):
            result = detect_backend("http://localhost:11434")
        assert result == "ollama"

    def test_detects_llama_cpp(self):
        def _side_effect(url, **kw):
            if "api/tags" in url:
                raise requests.exceptions.ConnectionError()
            r = MagicMock()
            r.ok = True
            return r

        with patch("core.models.requests.get", side_effect=_side_effect):
            result = detect_backend("http://localhost:8080")
        assert result == "llama.cpp"

    def test_returns_none_when_neither(self):
        with patch("core.models.requests.get",
                   side_effect=requests.exceptions.ConnectionError()):
            result = detect_backend("http://localhost:9999")
        assert result is None


# ── scan_gguf_models ──────────────────────────────────────────────────────────

class TestScanGgufModels:
    def test_empty_path_returns_empty(self):
        assert scan_gguf_models("") == []

    def test_nonexistent_path_returns_empty(self):
        assert scan_gguf_models("/nonexistent/path/xyz") == []

    def test_single_file_path(self, tmp_path):
        f = tmp_path / "model.gguf"
        f.write_text("fake gguf")
        result = scan_gguf_models(str(f))
        assert len(result) == 1
        assert result[0]["name"] == "model.gguf"

    def test_vocab_file_excluded(self, tmp_path):
        vocab = tmp_path / "ggml-vocab-llama.gguf"
        vocab.write_text("fake vocab")
        model = tmp_path / "actual_model.gguf"
        model.write_text("fake model")
        result = scan_gguf_models(str(tmp_path))
        assert all(r["name"] != "ggml-vocab-llama.gguf" for r in result)
        assert any(r["name"] == "actual_model.gguf" for r in result)

    def test_non_gguf_files_excluded(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not a model")
        result = scan_gguf_models(str(tmp_path))
        assert result == []

    def test_recursive_scan(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "model.gguf").write_text("fake")
        result = scan_gguf_models(str(tmp_path))
        assert len(result) == 1

    def test_single_file_not_gguf_returns_empty(self, tmp_path):
        f = tmp_path / "notmodel.bin"
        f.write_text("not a gguf")
        result = scan_gguf_models(str(f))
        assert result == []

    def test_vocab_single_file_excluded(self, tmp_path):
        f = tmp_path / "ggml-vocab-bert.gguf"
        f.write_text("vocab")
        result = scan_gguf_models(str(f))
        assert result == []


# ── compile_gguf ──────────────────────────────────────────────────────────────

class TestCompileGguf:
    def test_source_not_dir_returns_failure(self, tmp_path):
        success, msg = compile_gguf(
            source_path=str(tmp_path / "nonexistent"),
            output_dir=str(tmp_path / "out"),
        )
        assert success is False
        assert "not a directory" in msg.lower() or "Source path" in msg

    def test_convert_script_not_found(self, tmp_path):
        src = tmp_path / "model"
        src.mkdir()
        success, msg = compile_gguf(
            source_path=str(src),
            output_dir=str(tmp_path / "out"),
            convert_script=str(tmp_path / "nonexistent_convert.py"),
        )
        assert success is False
        assert "convert" in msg.lower() or "not found" in msg.lower()

    def test_quantize_bin_not_found(self, tmp_path):
        src = tmp_path / "model"
        src.mkdir()
        convert = tmp_path / "convert.py"
        convert.write_text("# fake convert script")
        success, msg = compile_gguf(
            source_path=str(src),
            output_dir=str(tmp_path / "out"),
            convert_script=str(convert),
            quantize_bin=str(tmp_path / "nonexistent_quantize"),
            quantization="Q4_K_M",
        )
        assert success is False
        assert "quantize" in msg.lower() or "llama-quantize" in msg or "not found" in msg.lower()
