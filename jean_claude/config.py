"""Configuration and feature flags for jean-claude.

Feature flags control which messaging services are enabled. Services are
disabled by default for safety - enable explicitly in config or env vars.

Configuration sources (in priority order):
1. Environment variables (JEAN_CLAUDE_ENABLE_WHATSAPP, JEAN_CLAUDE_ENABLE_SIGNAL)
2. Config file (~/.config/jean-claude/config.json)
3. Defaults (all messaging services disabled)
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from .paths import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"


def _parse_bool(value: str | None) -> bool | None:
    """Parse a boolean from environment variable string."""
    if value is None:
        return None
    return value.lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def _load_config() -> dict:
    """Load configuration from config file."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _get_flag(name: str, default: bool = False) -> bool:
    """Get a feature flag value.

    Checks environment variable first, then config file, then default.
    """
    # Environment variable takes priority
    env_var = f"JEAN_CLAUDE_{name.upper()}"
    env_value = _parse_bool(os.environ.get(env_var))
    if env_value is not None:
        return env_value

    # Check config file
    config = _load_config()
    if name in config:
        return bool(config[name])

    return default


def is_whatsapp_enabled() -> bool:
    """Check if WhatsApp messaging is enabled.

    Enable via:
    - Environment: JEAN_CLAUDE_ENABLE_WHATSAPP=1
    - Config: {"enable_whatsapp": true} in ~/.config/jean-claude/config.json
    """
    return _get_flag("enable_whatsapp", default=False)


def is_signal_enabled() -> bool:
    """Check if Signal messaging is enabled.

    Enable via:
    - Environment: JEAN_CLAUDE_ENABLE_SIGNAL=1
    - Config: {"enable_signal": true} in ~/.config/jean-claude/config.json
    """
    return _get_flag("enable_signal", default=False)


def clear_config_cache() -> None:
    """Clear the cached config (useful for testing)."""
    _load_config.cache_clear()
