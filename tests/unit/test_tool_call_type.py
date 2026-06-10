"""
Tests that tool call objects constructed by _stream_llama_cpp always carry
"type": "function", so that re-sending the assistant message to llama.cpp
does not produce HTTP 500 "Missing tool call type".
"""

import json
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Minimal stubs so we can import evaluator without its heavy deps installed
# ---------------------------------------------------------------------------
for _mod in ("streamlit", "requests", "pandas"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import importlib
evaluator = importlib.import_module("core.evaluator")
_stream_llama_cpp = evaluator._stream_llama_cpp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sse_lines(*chunks):
    """Turn dicts into b'data: {...}' byte lines the way requests iter_lines returns them."""
    lines = [f"data: {json.dumps(c)}".encode() for c in chunks]
    lines.append(b"data: [DONE]")
    return lines


def _mock_resp(lines):
    resp = MagicMock()
    resp.iter_lines.return_value = iter(lines)
    resp.raise_for_status = MagicMock()
    return resp


def _noop_log(_msg):
    pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestToolCallTypeField(unittest.TestCase):

    def _run_stream(self, sse_lines):
        """Patch requests.post and call _stream_llama_cpp, return the message dict."""
        with patch("core.evaluator.requests") as mock_req:
            mock_req.post.return_value = _mock_resp(sse_lines)
            result = _stream_llama_cpp(
                base_url="http://localhost:8080",
                model="",
                messages=[{"role": "user", "content": "create /tmp/test"}],
                tools=[],
                context_size=4096,
                on_log=_noop_log,
            )
        return result["message"]

    # ------------------------------------------------------------------
    # Case 1 — single tool call, arguments arrive in one delta
    # ------------------------------------------------------------------
    def test_single_tool_call_has_type(self):
        lines = _sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "abc123",
                 "function": {"name": "file_creator", "arguments": '{"path":"/tmp/test","content":"hello"}'}}
            ]}}]},
        )
        msg = self._run_stream(lines)
        self.assertIn("tool_calls", msg)
        tc = msg["tool_calls"][0]
        self.assertEqual(tc.get("type"), "function",
                         "Tool call missing required 'type':'function' field")

    # ------------------------------------------------------------------
    # Case 2 — arguments arrive in multiple incremental deltas
    # ------------------------------------------------------------------
    def test_incremental_delta_tool_call_has_type(self):
        lines = _sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "xyz",
                 "function": {"name": "file_cr", "arguments": '{"path"'}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "",
                 "function": {"name": "eator", "arguments": ':"/tmp/t","content":"hi"}'}}
            ]}}]},
        )
        msg = self._run_stream(lines)
        tc = msg["tool_calls"][0]
        self.assertEqual(tc.get("type"), "function")
        # Sanity-check that name and args were concatenated correctly
        self.assertEqual(tc["function"]["name"], "file_creator")
        self.assertIn("/tmp/t", tc["function"]["arguments"])

    # ------------------------------------------------------------------
    # Case 3 — two parallel tool calls
    # ------------------------------------------------------------------
    def test_multiple_tool_calls_all_have_type(self):
        lines = _sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "id0",
                 "function": {"name": "run_nmap_scan", "arguments": '{"target":"10.0.0.1"}'}},
                {"index": 1, "id": "id1",
                 "function": {"name": "file_creator", "arguments": '{"path":"/tmp/x","content":"y"}'}},
            ]}}]},
        )
        msg = self._run_stream(lines)
        for tc in msg["tool_calls"]:
            self.assertEqual(tc.get("type"), "function",
                             f"Tool call '{tc['function']['name']}' missing 'type':'function'")

    # ------------------------------------------------------------------
    # Case 4 — no tool calls → tool_calls key absent
    # ------------------------------------------------------------------
    def test_no_tool_calls_no_key(self):
        lines = _sse_lines(
            {"choices": [{"delta": {"content": "Hello, world!"}}]},
        )
        msg = self._run_stream(lines)
        self.assertNotIn("tool_calls", msg)

    # ------------------------------------------------------------------
    # Case 5 — assistant message structure is valid for re-sending
    #   (simulates the Round 2 messages list)
    # ------------------------------------------------------------------
    def test_assistant_message_round_trip_structure(self):
        """
        The assistant message appended at line 438 must be accepted by llama.cpp.
        Verify the full structure matches the OpenAI-compatible format.
        """
        lines = _sse_lines(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "callXYZ",
                 "function": {"name": "file_creator",
                              "arguments": '{"path":"/tmp/test","content":"1\\n2\\n3"}'}}
            ]}}]},
        )
        msg = self._run_stream(lines)
        tc = msg["tool_calls"][0]

        # These are the fields llama.cpp validates on the re-sent assistant message
        self.assertIn("id", tc)
        self.assertIn("type", tc)
        self.assertIn("function", tc)
        self.assertIn("name", tc["function"])
        self.assertIn("arguments", tc["function"])
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["id"], "callXYZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
