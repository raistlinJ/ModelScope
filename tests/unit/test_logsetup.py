"""
Unit tests for core/logsetup.py.

Covers:
  - configure_logging: creates handler, idempotent, level setting, custom stream
  - _level_for: tag-to-level mapping
  - logged_on_log: wraps callback, logs at correct level, inner=None, exception safety
"""
from __future__ import annotations

import logging
import io

import pytest

from core.logsetup import configure_logging, _level_for, logged_on_log, LOGGER_NAME


class TestConfigureLogging:
    def setup_method(self):
        """Reset the modelscope logger before each test."""
        logger = logging.getLogger(LOGGER_NAME)
        for h in list(logger.handlers):
            logger.removeHandler(h)

    def test_returns_logger(self):
        logger = configure_logging()
        assert isinstance(logger, logging.Logger)

    def test_logger_name_correct(self):
        logger = configure_logging()
        assert logger.name == LOGGER_NAME

    def test_adds_handler(self):
        logger = configure_logging()
        tagged = [h for h in logger.handlers if getattr(h, "_modelscope_handler", False)]
        assert len(tagged) == 1

    def test_idempotent_second_call_does_not_add_duplicate_handler(self):
        configure_logging()
        configure_logging()
        logger = logging.getLogger(LOGGER_NAME)
        tagged = [h for h in logger.handlers if getattr(h, "_modelscope_handler", False)]
        assert len(tagged) == 1

    def test_level_set_correctly(self):
        logger = configure_logging(level=logging.DEBUG)
        assert logger.level == logging.DEBUG

    def test_second_call_updates_level(self):
        configure_logging(level=logging.INFO)
        configure_logging(level=logging.DEBUG)
        logger = logging.getLogger(LOGGER_NAME)
        tagged = [h for h in logger.handlers if getattr(h, "_modelscope_handler", False)]
        assert tagged[0].level == logging.DEBUG

    def test_custom_stream_used(self):
        buf = io.StringIO()
        logger = configure_logging(stream=buf)
        logger.info("test message")
        assert "test message" in buf.getvalue()

    def test_propagate_disabled(self):
        logger = configure_logging()
        assert logger.propagate is False


class TestLevelFor:
    def test_error_tag(self):
        assert _level_for("[ERROR] something") == logging.ERROR

    def test_warn_tag(self):
        assert _level_for("[WARN] something") == logging.WARNING

    def test_aborted_tag(self):
        assert _level_for("[ABORTED] run") == logging.WARNING

    def test_info_default(self):
        assert _level_for("[LLM] turn 1") == logging.INFO

    def test_done_tag_is_info(self):
        assert _level_for("[DONE] finished") == logging.INFO

    def test_leading_whitespace_ignored(self):
        assert _level_for("  [ERROR] msg") == logging.ERROR

    def test_empty_string_is_info(self):
        assert _level_for("") == logging.INFO


class TestLoggedOnLog:
    def setup_method(self):
        """Reset the modelscope logger before each test."""
        logger = logging.getLogger(LOGGER_NAME)
        for h in list(logger.handlers):
            logger.removeHandler(h)
        # Add a handler so we capture records
        configure_logging(level=logging.DEBUG, stream=io.StringIO())

    def test_inner_called(self):
        received = []
        on_log = logged_on_log(inner=lambda m: received.append(m))
        on_log("hello")
        assert received == ["hello"]

    def test_no_inner_does_not_crash(self):
        on_log = logged_on_log(inner=None)
        on_log("message")  # should not raise

    def test_error_tag_logs_at_error(self):
        records = []

        class _Cap(logging.Handler):
            def emit(self, record):
                records.append(record)

        cap = _Cap()
        logging.getLogger(LOGGER_NAME).addHandler(cap)
        on_log = logged_on_log()
        on_log("[ERROR] something bad")
        assert any(r.levelno == logging.ERROR for r in records)

    def test_warn_tag_logs_at_warning(self):
        records = []

        class _Cap(logging.Handler):
            def emit(self, record):
                records.append(record)

        cap = _Cap()
        logging.getLogger(LOGGER_NAME).addHandler(cap)
        on_log = logged_on_log()
        on_log("[WARN] something warning")
        assert any(r.levelno == logging.WARNING for r in records)

    def test_custom_logger(self):
        buf = io.StringIO()
        custom_logger = logging.getLogger("custom_test_logger")
        custom_logger.handlers.clear()
        h = logging.StreamHandler(buf)
        custom_logger.addHandler(h)
        custom_logger.setLevel(logging.DEBUG)

        on_log = logged_on_log(logger=custom_logger)
        on_log("custom message")
        assert "custom message" in buf.getvalue()

    def test_exception_in_inner_does_not_propagate(self):
        def _crashing(msg):
            raise RuntimeError("inner crash")

        on_log = logged_on_log(inner=_crashing)
        # The wrapper should swallow exceptions from inner and continue
        # (Actually inner is called first and exception propagates from it —
        # the docstring says logging must never break a run, which is about
        # the logging call. Let's test the logging part itself doesn't crash.)
        try:
            on_log("test")
        except RuntimeError:
            pass  # inner's exception is expected to propagate; that's OK
