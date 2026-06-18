"""Tests for console logging configuration.

CLI commands emit JSON on stdout for programmatic consumption, so INFO progress
chatter must stay off the console by default; --verbose restores it. The JSON
file log keeps full detail regardless and is disabled here (json_log=None).
"""

from __future__ import annotations

import io
import logging

import pytest

from jean_claude.logging import configure_logging, get_logger


@pytest.fixture
def console_capture():
    """Configure logging and capture what reaches the console handler.

    Yields a callable ``configure(verbose)`` that runs the real
    ``configure_logging`` and redirects the installed console handler to a
    buffer it returns. Redirecting the handler's stream (rather than
    monkeypatching ``sys.stderr``) keeps the handler's level exactly as
    configured while staying robust against pytest's own stderr capture.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level

    def configure(verbose: bool) -> io.StringIO:
        configure_logging(verbose=verbose, json_log=None)
        # json_log=None leaves exactly one handler: the stderr console handler.
        handlers = [h for h in root.handlers if type(h) is logging.StreamHandler]
        assert len(handlers) == 1, "expected exactly one console handler"
        buffer = io.StringIO()
        handlers[0].setStream(buffer)
        return buffer

    yield configure

    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)


def test_info_suppressed_by_default(console_capture):
    """INFO progress lines stay off the console so stdout reads as pure JSON."""
    output = console_capture(verbose=False)
    logger = get_logger("jean_claude.test")

    logger.info("Searching messages", query="bulbs")
    logger.warning("Approaching rate limit")

    text = output.getvalue()
    assert "Searching messages" not in text
    assert "Approaching rate limit" in text


def test_info_shown_with_verbose(console_capture):
    """--verbose lowers the console threshold so INFO/DEBUG show for debugging."""
    output = console_capture(verbose=True)
    logger = get_logger("jean_claude.test")

    logger.info("Searching messages", query="bulbs")
    logger.debug("HTTP GET", resource="messages")

    text = output.getvalue()
    assert "Searching messages" in text
    assert "HTTP GET" in text
