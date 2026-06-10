from config.metrics import make_metric

SCENARIOS: dict[str, dict] = {
    "Scenario 1 – File Creation": {
        "system_prompt": (
            "You are an autonomous AI agent with access to file system tools. "
            "When given a task, use the file_creator tool to complete it exactly as specified. "
            "Write the file content carefully — numbers should be one per line. "
            "After calling the tool, confirm the result and do not repeat the same call."
        ),
        "user_prompt": (
            "Create a file at /tmp/test that contains the numbers 1 through 10, "
            "one number per line."
        ),
        "validation_command": "cat /tmp/test",
        "fail_patterns": [
            "no such file", "file not found", "cannot open",
            "permission denied", "no such directory",
        ],
        "expected_stdout": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
        "pre_run_cleanup": ["/tmp/test"],
        "related_tool": "file_creator",
        "default_metrics": [
            make_metric("M-001", "Task Completion",        "task_completion"),
            make_metric("M-002", "Tool Was Called",         "tool_called",           tool_name="file_creator"),
            make_metric("M-003", "Tool Call Count",         "tool_call_count",       max_calls=5),
            make_metric("M-004", "No Repeated Calls",       "no_repeated_calls"),
            make_metric("M-005", "Max LLM Iterations",      "max_iterations",        max_iter=5),
            make_metric("M-006", "Latency",                 "latency",               max_seconds=60.0),
            make_metric("M-007", "Token Limit",             "token_limit",           max_tokens=120),
            make_metric("M-008", "Tool Usage Efficiency",   "tool_usage_efficiency", max_calls=5),
            make_metric("M-009", "Goal Achievement",        "goal_achievement"),
            make_metric("M-010", "Path Efficiency",         "path_efficiency",
                        expected_sequence="file_creator", allow_extra_steps=1,
                        penalize_backtracking=True),
            make_metric("M-011", "No Error in Output",      "no_error_output"),
            make_metric("M-012", "Tool Output Contains",    "tool_output_contains",
                        tool_name="file_creator", text="success"),
        ],
    },

    "Scenario 2 – Network Scan": {
        "system_prompt": (
            "You are an autonomous AI agent with access to network scanning tools. "
            "When given a task, use the run_nmap_scan tool to complete it. "
            "Target 127.0.0.1 unless otherwise specified. "
            "Report all open ports found in your final answer."
        ),
        "user_prompt": (
            "Scan my machine (127.0.0.1) with nmap using fast scan flags "
            "and tell me which ports are open."
        ),
        "validation_command": "nmap -F 127.0.0.1",
        "fail_patterns": [
            "failed", "unreachable", "refused", "no route", "not found",
        ],
        "related_tool": "run_nmap_scan",
        "default_metrics": [
            make_metric("M-001", "Task Completion",          "task_completion"),
            make_metric("M-002", "Tool Was Called",           "tool_called",           tool_name="run_nmap_scan"),
            make_metric("M-003", "Tool Call Count",           "tool_call_count",       max_calls=3),
            make_metric("M-004", "No Repeated Calls",         "no_repeated_calls"),
            make_metric("M-005", "Max LLM Iterations",        "max_iterations",        max_iter=5),
            make_metric("M-006", "Latency",                   "latency",               max_seconds=60.0),
            make_metric("M-007", "Token Limit",               "token_limit",           max_tokens=120),
            make_metric("M-008", "Response Contains 'port'",  "content_contains",      text="port"),
            make_metric("M-009", "Tool Usage Efficiency",     "tool_usage_efficiency", max_calls=3),
            make_metric("M-010", "Goal Achievement",          "goal_achievement"),
            make_metric("M-011", "No Error in Output",        "no_error_output"),
            make_metric("M-012", "Path Efficiency",           "path_efficiency",
                        expected_sequence="run_nmap_scan", allow_extra_steps=0,
                        penalize_backtracking=True),
        ],
    },

    "Custom": {
        "system_prompt": (
            "You are a helpful AI assistant with access to MCP tools. "
            "Complete the given task using the available tools. "
            "Always verify the result of each tool call before declaring success."
        ),
        "user_prompt": (
            "Describe what you would like the agent to do. "
            "The agent has access to the MCP tools configured above."
        ),
        "validation_command": "",
        "fail_patterns": [],
        "related_tool": None,
        "default_metrics": [
            make_metric("M-001", "Task Completion",    "task_completion"),
            make_metric("M-002", "Tool Call Count",    "tool_call_count",  max_calls=5),
            make_metric("M-003", "No Repeated Calls",  "no_repeated_calls"),
            make_metric("M-004", "Latency",            "latency",          max_seconds=30.0),
            make_metric("M-005", "No Error in Output", "no_error_output"),
        ],
    },
}

DEFAULT_SCENARIO = "Scenario 1 – File Creation"
