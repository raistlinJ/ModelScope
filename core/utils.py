"""Shared low-level utility functions used across core modules."""
from __future__ import annotations


def ensure_http_scheme(url: str) -> str:
    """Prepend http:// when url has no scheme."""
    url = (url or "").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "http://" + url
    return url
