"""
LLM streaming adapters for llama.cpp and Ollama backends.

Both adapters share the same <think>/<think> tag handling and buffer logic.
Callers receive a normalised response dict:
    {
        "message": {"role": "assistant", "content": str, "tool_calls"?: list},
        "usage":   {"prompt_tokens": int, "completion_tokens": int},
    }
"""
from __future__ import annotations

import json
from typing import Callable

import requests


def _normalize_openai_url(url: str) -> str:
    """Strip trailing /v1 or /v1/ so appending /v1/... never doubles it."""
    u = url.rstrip("/")
    if u.endswith("/v1"):
        u = u[:-3]
    return u


# ── Internal buffer helpers ───────────────────────────────────────────────────

def _flush_buf(buf: str, in_think: bool, on_log: Callable) -> str:
    if buf.strip():
        on_log(f"{'[THINKING]' if in_think else '[LLM]'} {buf.rstrip()}")
    return ""


def _process_think_tags(
    token: str, buf: str, in_think: bool, on_log: Callable
) -> tuple[str, bool]:
    """Split token stream on <think>/<think> boundaries, flushing segments."""
    buf += token
    while True:
        tag = "<think>" if not in_think else "</think>"
        idx = buf.find(tag)
        if idx < 0:
            break
        _flush_buf(buf[:idx], in_think, on_log)
        buf      = buf[idx + len(tag):]
        in_think = not in_think
    return buf, in_think


# ── Backend streaming calls ───────────────────────────────────────────────────

def stream_ollama(
    base_url: str,
    model: str,
    messages: list,
    tools: list,
    context_size: int,
    on_log: Callable,
) -> dict:
    """Streaming call to Ollama /api/chat. Returns normalised response dict."""
    payload: dict = {
        "model":    model,
        "messages": messages,
        "stream":   True,
        "options":  {"num_ctx": context_size},
    }
    if tools:
        payload["tools"] = tools

    resp = requests.post(
        base_url.rstrip("/") + "/api/chat",
        json=payload, stream=True, timeout=(10, None),
    )
    resp.raise_for_status()

    accumulated = ""
    usage: dict = {}
    msg: dict   = {}
    buf         = ""
    in_think    = False

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        try:
            chunk = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        token = chunk.get("message", {}).get("content", "")
        accumulated += token
        buf, in_think = _process_think_tags(token, buf, in_think, on_log)
        if len(buf) >= 80 or "\n" in buf:
            buf = _flush_buf(buf, in_think, on_log)

        if chunk.get("done"):
            buf = _flush_buf(buf, in_think, on_log)
            for tc in chunk.get("message", {}).get("tool_calls") or []:
                fn_name = tc.get("function", {}).get("name", "")
                if fn_name:
                    on_log(f"[THINKING] → selecting tool: {fn_name}")
            usage = {
                "prompt_tokens":     chunk.get("prompt_eval_count", 0),
                "completion_tokens": chunk.get("eval_count", 0),
            }
            msg = chunk.get("message", {})
            msg["content"] = accumulated
            break

    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function", {})
        if isinstance(fn.get("arguments"), dict):
            fn["arguments"] = json.dumps(fn["arguments"])

    return {"message": msg, "usage": usage}


def stream_llama_cpp(
    base_url: str,
    model: str,
    messages: list,
    tools: list,
    context_size: int,
    on_log: Callable,
    verify: bool = True,
    api_key: str | None = None,
) -> dict:
    """Streaming call to llama.cpp /v1/chat/completions (OpenAI SSE). Returns normalised dict."""
    payload: dict = {
        "messages":       messages,
        "stream":         True,
        "stream_options": {"include_usage": True},
    }
    if model:
        payload["model"] = model
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    headers: dict = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    resp = requests.post(
        _normalize_openai_url(base_url) + "/v1/chat/completions",
        json=payload, headers=headers or None, stream=True, timeout=(10, None), verify=verify,
    )
    resp.raise_for_status()

    accumulated:    str  = ""
    tool_calls_raw: list = []
    usage: dict          = {}
    buf                  = ""
    in_think             = False

    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.startswith("data: "):
            continue
        payload_str = line[6:].strip()
        if payload_str == "[DONE]":
            break
        try:
            chunk = json.loads(payload_str)
        except json.JSONDecodeError:
            continue

        choice = (chunk.get("choices") or [{}])[0]
        delta  = choice.get("delta", {})
        token  = delta.get("content") or ""
        accumulated += token
        buf, in_think = _process_think_tags(token, buf, in_think, on_log)
        if len(buf) >= 80 or "\n" in buf:
            buf = _flush_buf(buf, in_think, on_log)

        for tc_delta in delta.get("tool_calls") or []:
            idx = tc_delta.get("index", 0)
            while len(tool_calls_raw) <= idx:
                tool_calls_raw.append({
                    "id": "", "type": "function",
                    "function": {"name": "", "arguments": ""},
                })
            tc = tool_calls_raw[idx]
            fn_name = tc_delta.get("function", {}).get("name", "")
            tc["id"]                    += tc_delta.get("id", "")
            tc["function"]["name"]      += fn_name
            tc["function"]["arguments"] += tc_delta.get("function", {}).get("arguments", "")
            if fn_name:
                on_log(f"[THINKING] → tool: {tc['function']['name']}")

        if chunk.get("usage"):
            usage = chunk["usage"]

    _flush_buf(buf, in_think, on_log)

    msg: dict = {"role": "assistant", "content": accumulated}
    if tool_calls_raw:
        msg["tool_calls"] = [tc for tc in tool_calls_raw if tc["function"]["name"]]

    return {
        "message": msg,
        "usage": {
            "prompt_tokens":     usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        },
    }
