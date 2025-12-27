"""Input utilities for CLI commands."""

import sys

import click


def read_body_stdin(*, allow_empty: bool = False) -> str:
    """Read message body from stdin.

    Raises UsageError for interactive terminals. If allow_empty is False,
    also raises UsageError for empty input.
    """
    if sys.stdin.isatty():
        raise click.UsageError("Expected body on stdin")

    body = sys.stdin.read().strip()
    if not body and not allow_empty:
        raise click.UsageError("Body cannot be empty")

    return body


def read_stdin_optional() -> str | None:
    """Read from stdin if provided, return None if interactive.

    Use when stdin is optional (e.g., updating metadata without body).
    """
    if sys.stdin.isatty():
        return None

    return sys.stdin.read() or None
