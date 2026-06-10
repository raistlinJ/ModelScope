import pytest
import json
from unittest.mock import patch, MagicMock
import requests
from core import mcp_manager

@patch("requests.post")
def test_fetch_tools_from_server_full_flow(mock_post):
    # 1. Initialize
    mock_init = MagicMock()
    mock_init.ok = True
    mock_init.headers = {"mcp-session-id": "sess-999"}
    
    # 2. Notification (initialized)
    mock_notify = MagicMock()
    mock_notify.ok = True
    
    # 3. List Tools
    mock_list = MagicMock()
    mock_list.ok = True
    mock_list.json.return_value = {
        "result": {
            "tools": [
                {"name": "echo", "description": "repeats"},
                {"name": "ls", "description": "lists"}
            ]
        }
    }
    
    mock_post.side_effect = [mock_init, mock_notify, mock_list]
    
    names = mcp_manager.fetch_tools_from_server("http://localhost:8000")
    
    assert names == ["echo", "ls"]
    assert mock_post.call_count == 3
    
    # Verify session ID was passed to subsequent calls
    args2 = mock_post.call_args_list[1]
    assert args2[1]["headers"]["mcp-session-id"] == "sess-999"
    args3 = mock_post.call_args_list[2]
    assert args3[1]["headers"]["mcp-session-id"] == "sess-999"

@patch("requests.post")
def test_fetch_tools_from_server_http_error(mock_post):
    mock_init = MagicMock()
    mock_init.ok = False
    mock_init.status_code = 500
    mock_post.return_value = mock_init
    
    names = mcp_manager.fetch_tools_from_server()
    assert names == []

@patch("requests.post", side_effect=requests.exceptions.Timeout())
def test_fetch_tools_from_server_timeout(mock_post):
    names = mcp_manager.fetch_tools_from_server()
    assert names == []

@patch("requests.post")
def test_fetch_tools_from_server_malformed_json(mock_post):
    mock_init = MagicMock()
    mock_init.ok = True
    mock_init.headers = {}
    
    mock_notify = MagicMock()
    
    mock_list = MagicMock()
    mock_list.ok = True
    mock_list.json.side_effect = ValueError("Not JSON")
    
    mock_post.side_effect = [mock_init, mock_notify, mock_list]
    
    names = mcp_manager.fetch_tools_from_server()
    assert names == []

@patch("core.mcp_manager.fetch_tools_from_server")
@patch("core.mcp_manager.load_tools_from_json")
def test_discover_tools_enrichment(mock_load_json, mock_fetch_server, tmp_path):
    # Server gives names
    mock_fetch_server.return_value = ["tool_a"]
    # JSON gives names + descriptions
    mock_load_json.return_value = [
        {"name": "tool_a", "description": "This is tool A"},
        {"name": "tool_b", "description": "This is tool B"}
    ]
    
    script = str(tmp_path / "index.js")
    tools = mcp_manager.discover_tools(script)
    
    assert len(tools) == 1
    assert tools[0]["name"] == "tool_a"
    assert tools[0]["description"] == "This is tool A"
