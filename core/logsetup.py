"""Centralised logging setup for ModelScope (Phase A — observability).

Provides a single ``configure_logging()`` entry point and a small helper,
``logged_on_log()``, that wraps an existing ``on_log(msg)`` callback so that
every evaluation event is *also* emitted to a standard Python logger. This lets
``streamlit run app.py`` surface progress on the terminal (stdout) while the
browser terminal continues to receive the same lines unchanged.

Design constraints (Phase A is additive / low blast radius):
  * No existing call sites are required to change. ``on_log`` keeps its exact
    ``Callable[[str], None]`` contract; wrapping is opt-in.
  * Idempotent: ``configure_logging()`` may be called on every Streamlit rerun
    without stacking duplicate handlers.
  * The evaluator's bracketed tags (``[LLM]``, ``[TOOL CALL]``, ``[ERROR]`` …)
    are mapped to log levels so errors/warnings stand out on the terminal.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable

LOGGER_NAME = "modelscope"

#: Bracketed event tags that should surface at WARNING level on the terminal.
_WARN_TAGS = ("[WARN]", "[ABORTED]")
#: Bracketed event tags that should surface at ERROR level on the terminal.
_ERROR_TAGS = ("[ERROR]",)


def configure_logging(level: int = logging.INFO, *, stream=None) -> logging.Logger:
    """Configure and return the shared ``modelscope`` logger.

    Idempotent: repeated calls (e.g. once per Streamlit rerun) will not attach
    duplicate stream handlers. ``stream`` defaults to ``sys.stdout`` so output
    appears in the terminal that launched ``streamlit run`` / ``python cli.py``.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    # Don't propagate to the root logger — avoids double-printing if a host
    # process (Streamlit, pytest) has already configured the root handler.
    logger.propagate = False

    target_stream = stream if stream is not None else sys.stdout
    # Idempotency: only add our handler once. We tag the handler so we can
    # recognise it across reruns regardless of the stream identity.
    for handler in logger.handlers:
        if getattr(handler, "_modelscope_handler", False):
            handler.setLevel(level)
            return logger

    handler = logging.StreamHandler(target_stream)
    handler._modelscope_handler = True  # type: ignore[attr-defined]
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s [modelscope] %(message)s",
                          datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
    return logger


def _level_for(msg: str) -> int:
    """Map an evaluator log line to a logging level based on its tag prefix."""
    stripped = msg.lstrip()
    for tag in _ERROR_TAGS:
        if stripped.startswith(tag):
            return logging.ERROR
    for tag in _WARN_TAGS:
        if stripped.startswith(tag):
            return logging.WARNING
    return logging.INFO


def logged_on_log(
    inner: Callable[[str], None] | None = None,
    logger: logging.Logger | None = None,
) -> Callable[[str], None]:
    """Wrap an ``on_log`` callback so each message is also sent to the logger.

    If ``inner`` is ``None`` the returned callback only logs (useful for the CLI
    where there is no browser terminal to feed). The original callback is always
    invoked first so existing UI behaviour is preserved even if logging raises.
    """
    log = logger if logger is not None else logging.getLogger(LOGGER_NAME)

    def _on_log(msg: str) -> None:
        if inner is not None:
            inner(msg)
        try:
            log.log(_level_for(msg), "%s", msg)
        except Exception:
            # Logging must never break an evaluation run.
            pass

    return _on_log
