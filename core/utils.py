"""Shared low-level utility functions used across core modules.

This module is intentionally dependency-free (stdlib only) so any other core
module can import it without risking an import cycle.
"""
from __future__ import annotations

import re

# Matches CSI/escape sequences emitted by interactive CLIs (colour codes,
# cursor moves). Compiled once at import — _strip_ansi runs on every chunk of
# streamed remote output, so the per-call re.compile cost is worth avoiding.
_ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])')


def ensure_http_scheme(url: str) -> str:
    """Prepend http:// when url has no scheme."""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colour codes, cursor moves) from text."""
    return _ANSI_RE.sub('', text)
