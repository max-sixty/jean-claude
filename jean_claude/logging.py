"""Logging configuration for jean-claude.

Uses structlog with JSON file output for debugging and optional console output.
Log files are written to XDG state directory (or fallback paths).

Architecture based on safety-net's logging approach:
- Structlog with stdlib backend
- JSON lines for machine-readable logs
- XDG Base Directory compliance via platformdirs
- Configure once at CLI entry point
- Automatic logging of mutating API calls via LoggingHttp wrapper
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from platformdirs import user_log_dir

# Log rotation settings
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 3  # Keep 3 rotated files

if TYPE_CHECKING:
    from google_auth_httplib2 import AuthorizedHttp

APP_NAME = "jean-claude"


def get_log_dir() -> Path:
    """Get the log directory, creating it if necessary.

    Uses platformdirs.user_log_dir() for platform-specific paths:
    - Linux: ~/.local/state/jean-claude/log
    - macOS: ~/Library/Logs/jean-claude
    - Windows: C:/Users/<user>/AppData/Local/jean-claude/Logs
    """
    log_dir = Path(user_log_dir(APP_NAME, ensure_exists=True))
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def get_log_file() -> Path:
    """Get the default log file path."""
    return get_log_dir() / "jean-claude.log"


def configure_logging(
    verbose: bool = False,
    json_log: str | None = "auto",
) -> None:
    """Configure structlog for the application.

    Args:
        verbose: If True, enables DEBUG level console output. Otherwise INFO.
        json_log: Path to JSON log file. Use "-" for stdout, "auto" for default path,
                  None to disable file logging.
    """
    handlers: list[logging.Handler] = []

    # Console handler for user-visible output
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(logging.DEBUG if verbose else logging.INFO)
    console_handler.setFormatter(_get_console_formatter())
    handlers.append(console_handler)

    # JSON file handler - enabled by default for debugging
    if json_log:
        json_handler = _create_json_handler(json_log)
        if json_handler:
            handlers.append(json_handler)

    # Configure stdlib logging
    logging.basicConfig(
        level=logging.DEBUG,  # Handlers do the filtering
        handlers=handlers,
        force=True,
    )

    # Third-party loggers: filter from console but keep in file log
    # We add a filter to console handler instead of changing log levels,
    # so warnings still go to the JSON log file for debugging.
    console_handler.addFilter(_ThirdPartyFilter())

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _get_console_formatter() -> structlog.stdlib.ProcessorFormatter:
    """Get a console formatter for compact, agent-friendly output.

    Format: [level] message key=value key=value
    Example: [info] Archived 5 threads count=5
    """
    return structlog.stdlib.ProcessorFormatter(
        processor=_CompactConsoleRenderer(),
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
        ],
    )


class _CompactConsoleRenderer:
    """Compact console renderer for agent-friendly output.

    Produces: [level] message key=value key=value
    Omits timestamps (file log has them) and logger names (noise for agents).
    """

    # Keys added by structlog processors that we don't want in console output
    _OMIT_KEYS = {"timestamp", "logger"}

    def __call__(
        self,
        logger: Any,
        method_name: str,
        event_dict: MutableMapping[str, Any],
    ) -> str:
        level = event_dict.pop("level", method_name)
        event = event_dict.pop("event", "")

        # Format remaining keys compactly, omitting noisy structlog metadata
        extras = " ".join(
            f"{k}={v}" for k, v in event_dict.items() if k not in self._OMIT_KEYS
        )

        if extras:
            return f"[{level}] {event} {extras}"
        return f"[{level}] {event}"


def _create_json_handler(json_log: str) -> logging.Handler | None:
    """Create a JSON file handler.

    Args:
        json_log: Path to log file, "-" for stdout, or "auto" for default path.

    Returns:
        Handler or None if creation fails.
    """
    if json_log == "-":
        stream = sys.stdout
        handler = logging.StreamHandler(stream)
    elif json_log == "auto":
        log_path = get_log_file()
        handler = _create_file_handler(log_path)
        if handler is None:
            print(f"Warning: Could not create log file at {log_path}", file=sys.stderr)
            return None
    else:
        log_path = Path(json_log)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            print(f"Warning: Could not create log directory: {e}", file=sys.stderr)
            return None
        handler = _create_file_handler(log_path)
        if handler is None:
            print(f"Warning: Could not create log file at {log_path}", file=sys.stderr)
            return None

    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_get_json_formatter())
    return handler


def _create_file_handler(path: Path) -> logging.Handler | None:
    """Create a rotating file handler.

    Rotates when file exceeds MAX_LOG_SIZE, keeping BACKUP_COUNT old files.
    Example: jean-claude.log, jean-claude.log.1, jean-claude.log.2, jean-claude.log.3
    """
    try:
        handler = RotatingFileHandler(
            path,
            maxBytes=MAX_LOG_SIZE,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        return handler
    except OSError:
        return None


def _get_json_formatter() -> structlog.stdlib.ProcessorFormatter:
    """Get a JSON formatter for structured logs."""
    return structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.format_exc_info,
        ],
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a logger for the given module name.

    Usage:
        from jean_claude.logging import get_logger
        logger = get_logger(__name__)
        logger.info("operation started", operation="search", query="foo")
    """
    return structlog.get_logger(name)


class _ThirdPartyFilter(logging.Filter):
    """Filter out noisy third-party loggers from console output.

    These logs still go to the JSON file for debugging, but don't clutter
    the console with warnings about expected conditions (like 403 from
    People API when scope isn't granted).
    """

    THIRD_PARTY_PREFIXES = (
        "googleapiclient.",
        "google.auth.",
        "google_auth_httplib2",
        "urllib3",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        # Allow our own logs
        if record.name.startswith("jean_claude"):
            return True
        # Filter third-party logs from console
        for prefix in self.THIRD_PARTY_PREFIXES:
            if record.name.startswith(prefix):
                return False
        # Allow other logs (structlog, etc.)
        return True


class JeanClaudeError(Exception):
    """Expected error that should be shown to the user with a clean message.

    Raise this instead of click.ClickException or SystemExit for errors that:
    - Are expected (invalid input, API errors, etc.)
    - Should show a clean message without traceback
    - Should be logged for debugging

    The CLI entry point catches these, logs them, and exits with code 1.

    Usage:
        from jean_claude.logging import JeanClaudeError
        raise JeanClaudeError("Event not found: abc123")
    """

    pass


class LoggingHttp:
    """HTTP wrapper that logs all API requests.

    Wraps an AuthorizedHttp instance and logs all requests with structured
    metadata (API, resource, operation, body size, etc.) before delegating
    to the wrapped http object.

    Response/request bodies are not logged to avoid huge log files and
    sensitive data exposure (email content, message bodies, etc.).

    TODO: If metadata proves insufficient for debugging, consider adding
    opt-in body logging with redaction of sensitive fields.

    Usage:
        from jean_claude.logging import LoggingHttp
        http = LoggingHttp(google_auth_httplib2.AuthorizedHttp(creds))
        service = build("gmail", "v1", http=http)
    """

    # Precompiled patterns for URI metadata extraction
    _API_PATTERN = re.compile(r"(gmail|calendar|drive)")
    _RESOURCE_PATTERN = re.compile(
        r"/(messages|threads|drafts|labels|events|files)(?:/([^/]+)(?:/([^/]+))?)?"
    )

    def __init__(self, http: AuthorizedHttp) -> None:
        self._http = http
        self._logger = get_logger("jean_claude.api")

    def _parse_uri_metadata(self, uri: str, method: str) -> dict:
        """Extract structured metadata from Google API URIs.

        URIs look like:
        - https://gmail.googleapis.com/gmail/v1/users/me/messages/abc123
        - https://gmail.googleapis.com/gmail/v1/users/me/messages/batchModify
        - https://www.googleapis.com/calendar/v3/calendars/primary/events
        """
        extra: dict = {}
        path = uri.split("?")[0]

        if match := self._API_PATTERN.search(path):
            extra["api"] = match.group(1)

        if match := self._RESOURCE_PATTERN.search(path):
            extra["resource"] = match.group(1)
            if next_part := match.group(2):
                # Lowercase alphabetic = operation, otherwise = ID
                if next_part.islower() and next_part.isalpha():
                    extra["operation"] = next_part
                else:
                    extra["resource_id"] = next_part
                    if op := match.group(3):
                        extra["operation"] = op

        if "/batch" in path:
            extra["operation"] = "batch"

        return extra

    def request(
        self,
        uri: str,
        method: str = "GET",
        body: bytes | str | None = None,
        headers: dict | None = None,
        **kwargs,
    ):
        """Make an HTTP request, logging the operation."""
        extra = self._parse_uri_metadata(uri, method)

        # Include content-type for debugging
        if headers:
            content_type = headers.get("content-type") or headers.get("Content-Type")
            if content_type:
                # Just the type, not boundary params
                extra["content_type"] = content_type.split(";")[0]

        # Include body size for debugging
        if body:
            body_bytes = body if isinstance(body, bytes) else body.encode()
            extra["body_bytes"] = len(body_bytes)

            # For batch requests, count operations by counting MIME multipart boundaries
            # Google API uses boundaries like --===============<numbers>==
            # N requests produce N+1 boundaries (N separators + 1 final marker)
            if "/batch" in uri and b"--=====" in body_bytes:
                boundary_count = body_bytes.count(b"--=====")
                extra["batch_operations"] = max(1, boundary_count - 1)

        self._logger.debug(f"{method} {uri}", **extra)

        return self._http.request(
            uri, method=method, body=body, headers=headers, **kwargs
        )

    def __getattr__(self, name: str):
        """Delegate all other attributes to the wrapped http object."""
        return getattr(self._http, name)
