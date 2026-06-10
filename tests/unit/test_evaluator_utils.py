import pytest
import json
import os
from unittest.mock import MagicMock, patch
from core.evaluator import _process_think_tags, _flush_buf, _load_tool_schemas

def test_flush_buf_thinking():
    log_mock = MagicMock()
    _flush_buf("hello", True, log_mock)
    log_mock.assert_called_once_with("[THINKING] hello")

def test_flush_buf_llm():
    log_mock = MagicMock()
    _flush_buf("world", False, log_mock)
    log_mock.assert_called_once_with("[LLM] world")

def test_flush_buf_empty():
    log_mock = MagicMock()
    _flush_buf("  ", False, log_mock)
    log_mock.assert_not_called()

def test_process_think_tags_single_chunk():
    log_mock = MagicMock()
    buf, in_think = _process_think_tags("<think>reasoning</think>answer", "", False, log_mock)
    assert in_think is False
    assert buf == "answer"
    # Should call flush twice: once for reasoning, once for tags boundary logic
    assert log_mock.call_count == 1
    log_mock.assert_any_call("[THINKING] reasoning")

def test_process_think_tags_split_chunks():
    log_mock = MagicMock()
    buf = ""
    in_think = False
    
    # Chunk 1: Start tag
    buf, in_think = _process_think_tags("Some text <thi", buf, in_think, log_mock)
    assert in_think is False
    assert buf == "Some text <thi"
    
    # Chunk 2: Finish start tag
    buf, in_think = _process_think_tags("nk>reason", buf, in_think, log_mock)
    assert in_think is True
    assert buf == "reason"
    log_mock.assert_any_call("[LLM] Some text")

    # Chunk 3: End tag
    buf, in_think = _process_think_tags("ing</think>final", buf, in_think, log_mock)
    assert in_think is False
    assert buf == "final"
    log_mock.assert_any_call("[THINKING] reasoning")

def test_process_think_tags_nested_like_text():
    # Content that looks like tags but isn't
    log_mock = MagicMock()
    buf, in_think = _process_think_tags("<think>What if I say </think> but not really </think>", "", False, log_mock)
    assert in_think is False
    assert buf == " but not really </think>"
    log_mock.assert_any_call("[THINKING] What if I say")

def test_load_tool_schemas_invalid_json(tmp_path):
    log_mock = MagicMock()
    tools_json = tmp_path / "tools.json"
    tools_json.write_text("invalid json")
    
    schemas = _load_tool_schemas(str(tmp_path / "index.js"), {}, log_mock)
    assert schemas == []
    assert any("Could not load tool schemas" in str(args) for args in log_mock.call_args_list)

def test_load_tool_schemas_missing_file():
    log_mock = MagicMock()
    schemas = _load_tool_schemas("/tmp/nonexistent/index.js", {}, log_mock)
    assert schemas == []
    assert any("tools.json not found" in str(args) for args in log_mock.call_args_list)

def test_load_tool_schemas_filtering(tmp_path):
    tools_data = [
        {"name": "tool1", "description": "d1", "inputSchema": {"type": "object"}},
        {"name": "tool2", "description": "d2"}
    ]
    tools_json = tmp_path / "tools.json"
    tools_json.write_text(json.dumps(tools_data))
    
    # Only tool1 enabled
    schemas = _load_tool_schemas(str(tmp_path / "index.js"), {"tool1": True})
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "tool1"
    
    # Both enabled
    schemas = _load_tool_schemas(str(tmp_path / "index.js"), {"tool1": True, "tool2": True})
    assert len(schemas) == 2
