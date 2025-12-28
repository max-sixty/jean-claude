"""Timezone utilities for converting to user's local time."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

from .logging import get_logger

logger = get_logger(__name__)


def _get_local_timezone() -> str:
    """Get local timezone in IANA format."""
    # Try reading from macOS symlink (most reliable)
    try:
        tz_link = Path("/etc/localtime")
        if tz_link.is_symlink():
            target = str(tz_link.readlink())  # readlink, not resolve
            parts = target.split("/")
            if "zoneinfo" in parts:
                idx = parts.index("zoneinfo")
                return "/".join(parts[idx + 1 :])
    except OSError as e:
        logger.debug("Could not read /etc/localtime", error=str(e))
    # Fallback with warning
    logger.warning("Could not detect timezone, using America/Los_Angeles")
    return "America/Los_Angeles"


TIMEZONE = _get_local_timezone()
LOCAL_TZ = ZoneInfo(TIMEZONE)
