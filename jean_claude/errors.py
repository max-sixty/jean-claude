"""Error handling for jean-claude CLI."""

from __future__ import annotations

import sys

import click
from googleapiclient.errors import HttpError

from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


class ErrorHandlingGroup(click.Group):
    """Click group that handles errors with clean output.

    Subclasses can override _http_error_message to add domain-specific context.
    """

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except HttpError as e:
            self._handle_error(self._http_error_message(e))
        except JeanClaudeError as e:
            self._handle_error(str(e))

    def _handle_error(self, message: str) -> None:
        """Log error and exit cleanly."""
        logger.error(message)
        sys.exit(1)

    def _http_error_message(self, e: HttpError) -> str:
        """Convert HttpError to user-friendly message.

        Subclasses can override to add domain-specific context for 404s etc.
        """
        status = e.resp.status
        reason = e._get_reason()

        if status == 404:
            return f"Not found: {reason}"
        if status == 403:
            # Check for specific API-not-enabled error
            error_str = str(e)
            if (
                "not been used" in error_str.lower()
                or "not enabled" in error_str.lower()
            ):
                return f"API not enabled: {reason}. Enable at https://console.cloud.google.com/apis/library"
            return f"Permission denied: {reason}"
        if status == 400:
            return f"Invalid request: {reason}"
        if status == 401:
            return f"Authentication failed: {reason}. Try 'jean-claude auth' to re-authenticate."
        if status == 429:
            return f"Rate limit exceeded: {reason}. Wait a moment and try again."
        return f"API error ({status}): {reason}"
