"""Shared low-level utility functions used across core modules.

This module is intentionally dependency-free (stdlib only) so any other core
module can import it without risking an import cycle.
"""
from __future__ import annotations

import re

# Matches CSI/escape sequences emitted by interactive CLIs (colour codes,
# cursor moves, and private-mode controls such as ``ESC[?25h``). Compiled once
# at import — strip_ansi runs on every streamed output chunk.
_ANSI_RE = re.compile(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_][0-?]*[ -/]*[@-~])')


def ensure_http_scheme(url: str) -> str:
    """Prepend http:// when url has no scheme."""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url


def effective_verify_ssl(url: str, verify_ssl: bool) -> bool:
    """Return the ``verify`` flag to use when requesting *url*.

    Certificate verification only applies to https:// URLs, so the user's
    "require SSL verification" setting is ignored (treated as True) for plain
    http — including scheme-less URLs, which default to http.
    """
    return verify_ssl if ensure_http_scheme(url).startswith("https://") else True


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences (colour codes, cursor moves) from text."""
    return _ANSI_RE.sub('', text)
