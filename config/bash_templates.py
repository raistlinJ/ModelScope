"""
Pre-configured bash-bot project templates.

Each template is a complete project config dict (startup_commands, validation_sets,
completion_commands, etc.) that serves as a manual ground-truth baseline matching
what the equivalent AI tool does in a llama-cli-bot run.

Imported by app.py to populate the "Template" selectbox in the New Project dialog.
"""

BASH_BOT_TEMPLATES: dict[str, dict] = {

    # ── File Creator ───────────────────────────────────────────────────────────
    # Mirrors the file_creator llama-cli tool:
    #   Creates /tmp/test containing the integers 1-10, one per line.
    "file_creator": {
        "execution_target": "local",
        "ssh_host": "", "ssh_port": 22, "ssh_user": "root",
        "ssh_password": "", "ssh_key_path": "", "sudo": False, "sudo_password": "",
        "bash_timeout": 30,
        "startup_commands": [
            {
                "_id": 1, "delay_seconds": 0.0,
                "commands": [
                    {
                        "_id": 1,
                        "command": "rm -f /tmp/test",
                        "enabled": True, "long_running": False, "timeout_seconds": 10,
                    }
                ],
            },
            {
                "_id": 2, "delay_seconds": 0.5,
                "commands": [
                    {
                        "_id": 2,
                        "command": "printf '1\\n2\\n3\\n4\\n5\\n6\\n7\\n8\\n9\\n10\\n' > /tmp/test",
                        "enabled": True, "long_running": False, "timeout_seconds": 10,
                    }
                ],
            },
        ],
        "completion_commands": [
            {
                "_id": 3, "delay_seconds": 0.0,
                "commands": [
                    {
                        "_id": 4,
                        "command": "rm -f /tmp/test",
                        "enabled": True, "long_running": False, "timeout_seconds": 10,
                    }
                ],
            }
        ],
        "validation_commands": [],
        "validation_sets": [
            {
                "name": "FileCheck",
                "description": "Checks file creation and content",
                "steps": [
                    {
                        "_id": 5, "delay_seconds": 0.0,
                        "commands": [
                            {
                                "_id": 6,
                                "command": "ls /tmp/test",
                                "enabled": True, "timeout_seconds": 10,
                                "expected_output_type": "Ignore", "expected_output": "",
                            },
                            {
                                "_id": 7,
                                "command": "cat /tmp/test",
                                "enabled": True, "timeout_seconds": 10,
                                "expected_output_type": "Exact String",
                                "expected_output": "1\n2\n3\n4\n5\n6\n7\n8\n9\n10",
                            },
                        ],
                    }
                ],
            }
        ],
        "fail_patterns": ["no such file", "permission denied", "command not found"],
        "metrics_matrix": [], "validation_presets": {},
    },

    # ── Nmap Scanner ───────────────────────────────────────────────────────────
    # Mirrors the run_nmap_scan llama-cli tool:
    #   Runs nmap -F 127.0.0.1 and verifies the output contains scan results.
    "nmap_scanner": {
        "execution_target": "local",
        "ssh_host": "", "ssh_port": 22, "ssh_user": "root",
        "ssh_password": "", "ssh_key_path": "", "sudo": False, "sudo_password": "",
        "bash_timeout": 60,
        "startup_commands": [
            {
                "_id": 1, "delay_seconds": 0.0,
                "commands": [
                    {
                        "_id": 1,
                        "command": "command -v nmap > /dev/null 2>&1 || apt-get install -y nmap",
                        "enabled": True, "long_running": True, "timeout_seconds": 60,
                    }
                ],
            },
            {
                "_id": 2, "delay_seconds": 1.0,
                "commands": [
                    {
                        "_id": 2,
                        "command": "nmap -F 127.0.0.1 -oN /tmp/nmap_result.txt",
                        "enabled": True, "long_running": False, "timeout_seconds": 30,
                    }
                ],
            },
        ],
        "completion_commands": [
            {
                "_id": 3, "delay_seconds": 0.0,
                "commands": [
                    {
                        "_id": 4,
                        "command": "rm -f /tmp/nmap_result.txt",
                        "enabled": True, "long_running": False, "timeout_seconds": 10,
                    }
                ],
            }
        ],
        "validation_commands": [],
        "validation_sets": [
            {
                "name": "NmapCheck",
                "description": "Checks network scan output structure",
                "steps": [
                    {
                        "_id": 5, "delay_seconds": 0.0,
                        "commands": [
                            {
                                "_id": 6,
                                "command": "cat /tmp/nmap_result.txt",
                                "enabled": True, "timeout_seconds": 10,
                                "expected_output_type": "Regex",
                                "expected_output": r"PORT\s+STATE\s+SERVICE|Nmap scan report",
                            },
                        ],
                    }
                ],
            }
        ],
        "fail_patterns": ["failed to resolve", "permission denied", "command not found"],
        "metrics_matrix": [], "validation_presets": {},
    },
}
