"""Configuration and feature flags for jean-claude."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .logging import get_logger
from .paths import CONFIG_DIR

logger = get_logger(__name__)
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "enable_contacts": False,
    "enable_imessage": False,
    "enable_reminders": False,
    "enable_whatsapp": False,
    "enable_signal": False,
    "setup_completed": False,
}


def _load_config() -> dict:
    """Load config from file, returning empty dict on any error."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Config file unreadable, using defaults", error=str(e))
        return {}


def is_contacts_enabled() -> bool:
    return _load_config().get("enable_contacts", False)


def is_imessage_enabled() -> bool:
    return _load_config().get("enable_imessage", False)


def is_reminders_enabled() -> bool:
    return _load_config().get("enable_reminders", False)


def is_whatsapp_enabled() -> bool:
    return _load_config().get("enable_whatsapp", False)


def is_signal_enabled() -> bool:
    return _load_config().get("enable_signal", False)


def is_setup_completed() -> bool:
    return _load_config().get("setup_completed", False)


def get_config() -> dict:
    """Get full config with defaults applied."""
    return {**DEFAULT_CONFIG, **_load_config()}


def set_config_value(key: str, value: bool | str) -> None:
    """Set a config value and persist to file."""
    from .logging import JeanClaudeError

    if key in DEFAULT_CONFIG and isinstance(DEFAULT_CONFIG[key], bool):
        if not isinstance(value, bool):
            raise JeanClaudeError(f"'{key}' requires bool, got {type(value).__name__}")

    config = _load_config()
    config[key] = value

    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", dir=CONFIG_DIR, prefix=".config_", suffix=".tmp", delete=False
        ) as f:
            json.dump(config, f, indent=2)
            f.write("\n")
            tmp_path = Path(f.name)
        tmp_path.replace(CONFIG_FILE)
    except OSError as e:
        raise JeanClaudeError(f"Cannot write config: {e}")
