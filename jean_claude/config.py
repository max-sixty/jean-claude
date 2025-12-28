"""Configuration and feature flags for jean-claude.

Feature flags control which messaging services are enabled. Services are
disabled by default for safety - enable explicitly via config file or CLI.

Use `jean-claude config set <key> <value>` to configure, or edit
~/.config/jean-claude/config.json directly.
"""

from __future__ import annotations

import json

from .paths import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"

# Default configuration values
DEFAULT_CONFIG = {
    "enable_whatsapp": False,
    "enable_signal": False,
    "setup_completed": False,
}


def _load_config() -> dict:
    """Load configuration from config file."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def is_whatsapp_enabled() -> bool:
    """Check if WhatsApp messaging is enabled.

    Enable via: jean-claude config set enable_whatsapp true
    """
    config = _load_config()
    return bool(config.get("enable_whatsapp", False))


def is_signal_enabled() -> bool:
    """Check if Signal messaging is enabled.

    Enable via: jean-claude config set enable_signal true
    """
    config = _load_config()
    return bool(config.get("enable_signal", False))


def is_setup_completed() -> bool:
    """Check if first-run setup has been completed."""
    config = _load_config()
    return bool(config.get("setup_completed", False))


def get_config() -> dict:
    """Get the full configuration with defaults applied.

    Returns a dict with all config keys, using file values where present
    and defaults otherwise.
    """
    config = _load_config()
    return {**DEFAULT_CONFIG, **config}


def set_config_value(key: str, value: bool | str) -> None:
    """Set a configuration value and persist to file.

    Args:
        key: Configuration key (e.g., "enable_whatsapp", "setup_completed")
        value: Value to set (bool or str)
    """
    # Load existing config
    config = _load_config()

    # Update the value
    config[key] = value

    # Ensure config directory exists
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")
