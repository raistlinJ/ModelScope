# CAF CLI Run

`caf_cli_run.py` is a ModelScope bot type for a normal CyberAgentFlow CLI
installation. It starts `start_cli.sh run …` for every configured CAF
validation prompt, so each prompt completes and exits without leaving an
interactive REPL waiting for input. This lets ModelScope run validation sets
and its LLM Judge against CAF's saved transcript, locally or on a remote host
reachable through a standard SSH shell.

Use the `CAF CLI Run` project type in the ModelScope UI or start from
`caf_cli_run.example.json`.
