"""
Unit tests for core.utils — ensure_http_scheme.
"""
import pytest
from core.utils import ensure_http_scheme


class TestEnsureHttpScheme:
    def test_adds_http_when_no_scheme(self):
        assert ensure_http_scheme("localhost:8080") == "http://localhost:8080"

    def test_leaves_http_url_unchanged(self):
        assert ensure_http_scheme("http://localhost:8080") == "http://localhost:8080"

    def test_leaves_https_url_unchanged(self):
        assert ensure_http_scheme("https://example.com") == "https://example.com"

    def test_empty_string_returns_empty(self):
        assert ensure_http_scheme("") == ""

    def test_none_equivalent_empty(self):
        # The function does (url or "").strip() so None must not crash
        assert ensure_http_scheme(None) == ""

    def test_strips_surrounding_whitespace(self):
        assert ensure_http_scheme("  localhost:8080  ") == "http://localhost:8080"

    def test_whitespace_only_returns_empty(self):
        assert ensure_http_scheme("   ") == ""

    def test_url_with_path_preserved(self):
        result = ensure_http_scheme("localhost:8080/v1/chat")
        assert result == "http://localhost:8080/v1/chat"

    def test_http_not_double_prefixed(self):
        # Ensure calling twice doesn't double-prefix
        url = "http://localhost"
        assert ensure_http_scheme(ensure_http_scheme(url)) == url
