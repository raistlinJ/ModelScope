"""
Unit tests for core.streaming — stream_ollama and stream_llama_cpp.

Both adapters are driven by mocked HTTP responses (no real network calls).
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from core.streaming import stream_ollama, stream_llama_cpp, _normalize_openai_url


# ── Helpers ───────────────────────────────────────────────────────────────────

def _on_log():
    logs = []
    return logs, lambda m: logs.append(m)


def _mock_response(lines: list, raise_for_status=None):
    """Build a fake requests.Response whose iter_lines() yields bytes."""
    mock_resp = MagicMock()
    mock_resp.iter_lines.return_value = [
        ln.encode() if isinstance(ln, str) else ln for ln in lines
    ]
    if raise_for_status:
        mock_resp.raise_for_status.side_effect = raise_for_status
    else:
        mock_resp.raise_for_status.return_value = None
    return mock_resp


# ── stream_ollama ──────────────────────────────────────────────────────────────

class TestStreamOllama:
    def _ollama_chunk(self, content="", done=False, tool_calls=None, prompt_eval=0, eval_count=0):
        chunk = {
            "message": {"role": "assistant", "content": content},
            "done": done,
        }
        if tool_calls:
            chunk["message"]["tool_calls"] = tool_calls
        if done:
            chunk["prompt_eval_count"] = prompt_eval
            chunk["eval_count"] = eval_count
        return json.dumps(chunk)

    @patch("core.streaming.requests.post")
    def test_normal_text_response(self, mock_post):
        chunks = [
            self._ollama_chunk("Hello "),
            self._ollama_chunk("world", done=True, prompt_eval=10, eval_count=5),
        ]
        mock_post.return_value = _mock_response(chunks)
        _, on_log = _on_log()

        result = stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)

        assert result["message"]["content"] == "Hello world"
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5

    @patch("core.streaming.requests.post")
    def test_tool_call_in_response(self, mock_post):
        tool_call = {
            "function": {
                "name": "file_creator",
                "arguments": {"path": "/tmp/x", "content": "hi"},
            }
        }
        chunks = [
            self._ollama_chunk(done=True, tool_calls=[tool_call]),
        ]
        mock_post.return_value = _mock_response(chunks)
        _, on_log = _on_log()

        result = stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)
        tool_calls = result["message"].get("tool_calls", [])
        assert len(tool_calls) == 1
        # Arguments should be serialised to a JSON string
        assert isinstance(tool_calls[0]["function"]["arguments"], str)

    @patch("core.streaming.requests.post")
    def test_empty_lines_skipped(self, mock_post):
        chunks = [
            "",  # empty line → skipped
            self._ollama_chunk("text", done=True),
        ]
        mock_post.return_value = _mock_response(chunks)
        _, on_log = _on_log()

        result = stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)
        assert result["message"]["content"] == "text"

    @patch("core.streaming.requests.post")
    def test_http_error_propagates(self, mock_post):
        import requests
        mock_post.return_value = _mock_response([])
        mock_post.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        _, on_log = _on_log()

        with pytest.raises(Exception):
            stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)

    @patch("core.streaming.requests.post")
    def test_partial_chunks_accumulated(self, mock_post):
        # Each token arrives in a separate chunk
        chunks = [
            self._ollama_chunk("A"),
            self._ollama_chunk("B"),
            self._ollama_chunk("C", done=True),
        ]
        mock_post.return_value = _mock_response(chunks)
        _, on_log = _on_log()

        result = stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)
        assert result["message"]["content"] == "ABC"

    @patch("core.streaming.requests.post")
    def test_returns_usage_dict_keys(self, mock_post):
        chunks = [self._ollama_chunk(done=True, prompt_eval=7, eval_count=3)]
        mock_post.return_value = _mock_response(chunks)
        _, on_log = _on_log()

        result = stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)
        assert "prompt_tokens" in result["usage"]
        assert "completion_tokens" in result["usage"]

    @patch("core.streaming.requests.post")
    def test_tools_payload_sent_when_non_empty(self, mock_post):
        mock_post.return_value = _mock_response(
            [self._ollama_chunk(done=True)]
        )
        tools = [{"type": "function", "function": {"name": "file_creator"}}]
        _, on_log = _on_log()
        stream_ollama("http://localhost:11434", "llama3", [], tools, 4096, on_log)

        call_kwargs = mock_post.call_args[1]
        assert "tools" in call_kwargs.get("json", {})

    @patch("core.streaming.requests.post")
    def test_no_tools_payload_when_empty(self, mock_post):
        mock_post.return_value = _mock_response(
            [self._ollama_chunk(done=True)]
        )
        _, on_log = _on_log()
        stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)

        call_kwargs = mock_post.call_args[1]
        assert "tools" not in call_kwargs.get("json", {})

    @patch("core.streaming.requests.post")
    def test_connection_error_propagates(self, mock_post):
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        _, on_log = _on_log()

        with pytest.raises(Exception):
            stream_ollama("http://localhost:11434", "llama3", [], [], 4096, on_log)


# ── stream_llama_cpp ──────────────────────────────────────────────────────────

class TestStreamLlamaCpp:
    def _sse_line(self, content=None, tool_calls=None, done=False, usage=None):
        if done:
            return "data: [DONE]"
        chunk: dict = {
            "choices": [{"delta": {"content": content or "", "tool_calls": tool_calls or []}}]
        }
        if usage:
            chunk["usage"] = usage
        return f"data: {json.dumps(chunk)}"

    @patch("core.streaming.requests.post")
    def test_normal_text_response(self, mock_post):
        lines = [
            self._sse_line("Hello "),
            self._sse_line("world"),
            self._sse_line(done=True),
        ]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        assert result["message"]["content"] == "Hello world"

    @patch("core.streaming.requests.post")
    def test_usage_extracted_from_chunk(self, mock_post):
        lines = [
            self._sse_line("text", usage={"prompt_tokens": 20, "completion_tokens": 8}),
            self._sse_line(done=True),
        ]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        assert result["usage"]["prompt_tokens"] == 20
        assert result["usage"]["completion_tokens"] == 8

    @patch("core.streaming.requests.post")
    def test_tool_call_delta_assembled(self, mock_post):
        # Tool calls arrive as deltas with index
        tc_delta_1 = {"index": 0, "id": "call_", "function": {"name": "file_", "arguments": ""}}
        tc_delta_2 = {"index": 0, "id": "0",     "function": {"name": "creator", "arguments": '{"path":"/tmp/x"}'}}
        lines = [
            self._sse_line(tool_calls=[tc_delta_1]),
            self._sse_line(tool_calls=[tc_delta_2]),
            self._sse_line(done=True),
        ]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        tcs = result["message"].get("tool_calls", [])
        assert len(tcs) == 1
        assert "file_creator" in tcs[0]["function"]["name"]

    @patch("core.streaming.requests.post")
    def test_done_sentinel_stops_stream(self, mock_post):
        lines = [
            self._sse_line("before done"),
            self._sse_line(done=True),
            self._sse_line(" after done"),  # should not be consumed
        ]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        assert "after done" not in result["message"]["content"]

    @patch("core.streaming.requests.post")
    def test_non_data_lines_skipped(self, mock_post):
        lines = [
            ": keep-alive",           # SSE comment line
            self._sse_line("text"),
            self._sse_line(done=True),
        ]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        assert result["message"]["content"] == "text"

    @patch("core.streaming.requests.post")
    def test_http_error_propagates(self, mock_post):
        import requests
        mock_post.return_value = _mock_response([])
        mock_post.return_value.raise_for_status.side_effect = requests.exceptions.HTTPError("503")
        _, on_log = _on_log()

        with pytest.raises(Exception):
            stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)

    @patch("core.streaming.requests.post")
    def test_returns_message_and_usage_keys(self, mock_post):
        lines = [self._sse_line("x"), self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        result = stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        assert "message" in result
        assert "usage" in result

    @patch("core.streaming.requests.post")
    def test_model_omitted_when_empty(self, mock_post):
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "", [], [], 4096, on_log)
        payload = mock_post.call_args[1]["json"]
        assert "model" not in payload

    @patch("core.streaming.requests.post")
    def test_model_included_when_set(self, mock_post):
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        payload = mock_post.call_args[1]["json"]
        assert payload.get("model") == "m.gguf"

    @patch("core.streaming.requests.post")
    def test_n_ctx_not_in_payload(self, mock_post):
        """n_ctx is a llama.cpp-specific param; real OpenAI endpoints reject it."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        payload = mock_post.call_args[1]["json"]
        assert "n_ctx" not in payload

    @patch("core.streaming.requests.post")
    def test_tool_choice_auto_added_when_tools_present(self, mock_post):
        """tool_choice='auto' must be sent when tools list is non-empty."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        tools = [{"type": "function", "function": {"name": "file_creator"}}]
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "m.gguf", [], tools, 4096, on_log)
        payload = mock_post.call_args[1]["json"]
        assert payload.get("tool_choice") == "auto"

    @patch("core.streaming.requests.post")
    def test_tool_choice_absent_when_no_tools(self, mock_post):
        """tool_choice must NOT appear in payload when tools list is empty."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        payload = mock_post.call_args[1]["json"]
        assert "tool_choice" not in payload

    @patch("core.streaming.requests.post")
    def test_strips_v1_suffix_from_url(self, mock_post):
        """URL ending in /v1 must not produce /v1/v1/chat/completions."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("https://api.openai.com/v1", "gpt-4", [], [], 4096, on_log)
        called_url = mock_post.call_args[0][0]
        assert called_url == "https://api.openai.com/v1/chat/completions"

    @patch("core.streaming.requests.post")
    def test_strips_v1_trailing_slash_from_url(self, mock_post):
        """URL ending in /v1/ (trailing slash) is also normalised."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("https://api.openai.com/v1/", "gpt-4", [], [], 4096, on_log)
        called_url = mock_post.call_args[0][0]
        assert called_url == "https://api.openai.com/v1/chat/completions"

    @patch("core.streaming.requests.post")
    def test_plain_base_url_appends_v1_path(self, mock_post):
        """URL without /v1 suffix gets /v1/chat/completions appended correctly."""
        lines = [self._sse_line(done=True)]
        mock_post.return_value = _mock_response(lines)
        _, on_log = _on_log()

        stream_llama_cpp("http://localhost:8080", "m.gguf", [], [], 4096, on_log)
        called_url = mock_post.call_args[0][0]
        assert called_url == "http://localhost:8080/v1/chat/completions"


# ── _normalize_openai_url unit tests ─────────────────────────────────────────

class TestNormalizeOpenAIUrl:
    def test_strips_v1_suffix(self):
        assert _normalize_openai_url("https://api.openai.com/v1") == "https://api.openai.com"

    def test_strips_v1_trailing_slash(self):
        assert _normalize_openai_url("https://api.openai.com/v1/") == "https://api.openai.com"

    def test_plain_url_unchanged(self):
        assert _normalize_openai_url("http://localhost:8080") == "http://localhost:8080"

    def test_trailing_slash_stripped(self):
        assert _normalize_openai_url("http://localhost:8080/") == "http://localhost:8080"

    def test_strips_any_trailing_v1(self):
        # /api/v1 at the end is also stripped — function removes any trailing /v1 segment
        assert _normalize_openai_url("http://localhost:8080/api/v1") == "http://localhost:8080/api"
