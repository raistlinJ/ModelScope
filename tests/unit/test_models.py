"""
Unit tests for core.models — URL scheme normalization, GGUF scanning,
Ollama model fetching, and backend detection.
"""
import pytest
from unittest.mock import patch, MagicMock
import requests as req_lib

from core.utils import ensure_http_scheme as _ensure_scheme
from core.models import (
    _is_inference_model, fetch_ollama_models, resolve_model_path,
    scan_gguf_models, scan_gguf_models_via_env,
)


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


# ── scan_gguf_models_via_env ───────────────────────────────────────────────────

class TestScanGgufModelsViaEnv:
    def test_empty_root_reports_error(self):
        models, error = scan_gguf_models_via_env(MagicMock(), "")
        assert models == []
        assert "Model Directory" in error

    def test_connection_failure_on_existence_check_is_distinct(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "", "stderr": "refused", "exit_code": -1}
        models, error = scan_gguf_models_via_env(env, "/models")
        assert models == []
        assert "connection failed" in error.lower()

    def test_missing_path_is_distinct_from_connection_failure(self):
        env = MagicMock()
        env.execute.return_value = {"stdout": "", "stderr": "", "exit_code": 1}
        models, error = scan_gguf_models_via_env(env, "/nope")
        assert models == []
        assert "not found" in error

    def test_directory_scan_has_no_type_f_restriction_and_excludes_vocab(self):
        env = MagicMock()
        env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -e
            {"stdout": "/models/a.gguf\n/models/sub/b.gguf\n", "stderr": "", "exit_code": 0},  # find
        ]
        models, error = scan_gguf_models_via_env(env, "/models")
        assert error == ""
        assert sorted(m["name"] for m in models) == ["a.gguf", "sub/b.gguf"]
        find_cmd = env.execute.call_args_list[1].args[0]
        assert "-type f" not in find_cmd
        assert "ggml-vocab-*" in find_cmd

    def test_direct_gguf_file_root_scans_as_single_model(self):
        env = MagicMock()
        env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -e
            {"stdout": "/models/one.gguf\n", "stderr": "", "exit_code": 0},  # find
        ]
        models, error = scan_gguf_models_via_env(env, "/models/one.gguf")
        assert error == ""
        assert models == [{"name": "one.gguf", "path": "one.gguf"}]

    def test_tilde_root_expands_relative_to_remote_home(self):
        env = MagicMock()
        env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -e
            {"stdout": "/home/user/models/m.gguf\n", "stderr": "", "exit_code": 0},  # find
            {"stdout": "/home/user\n", "stderr": "", "exit_code": 0},  # echo $HOME
        ]
        models, error = scan_gguf_models_via_env(env, "~/models")
        assert error == ""
        assert models == [{"name": "m.gguf", "path": "m.gguf"}]

    def test_command_failure_surfaces_stderr_distinctly(self):
        env = MagicMock()
        env.execute.side_effect = [
            {"stdout": "", "stderr": "", "exit_code": 0},  # test -e
            {"stdout": "", "stderr": "find: Permission denied", "exit_code": 1},  # find
        ]
        models, error = scan_gguf_models_via_env(env, "/models")
        assert models == []
        assert "Permission denied" in error


# ── resolve_model_path ─────────────────────────────────────────────────────────

class TestResolveModelPath:
    def test_directory_root_joins_with_selected_model(self):
        assert resolve_model_path("/models", "sub/m.gguf", local=False) == "/models/sub/m.gguf"

    def test_direct_gguf_root_is_returned_unchanged_not_double_appended(self):
        # This is the exact regression: a direct-file root must never have
        # the selected (basename) model name appended onto it again.
        assert resolve_model_path("/models/one.gguf", "one.gguf", local=False) == "/models/one.gguf"

    def test_local_directory_root_is_expanded_and_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = resolve_model_path("~/models", "m.gguf", local=True)
        assert result == str(tmp_path / "models" / "m.gguf")

    def test_local_direct_file_root_is_expanded_but_not_rejoined(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        result = resolve_model_path("~/models/one.gguf", "one.gguf", local=True)
        assert result == str(tmp_path / "models" / "one.gguf")

    def test_remote_tilde_root_is_left_unexpanded(self):
        assert resolve_model_path("~/models", "m.gguf", local=False) == "~/models/m.gguf"

    def test_no_model_name_falls_back_to_selection_only(self):
        assert resolve_model_path("/models", "", local=False) == ""

    def test_no_root_falls_back_to_selection_only(self):
        assert resolve_model_path("", "m.gguf", local=False) == "m.gguf"


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
