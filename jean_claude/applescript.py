"""AppleScript utilities for macOS automation."""

from __future__ import annotations

import re
import subprocess

from .logging import JeanClaudeError


def _parse_applescript_error(stderr: str) -> str:
    """Parse AppleScript error output into a user-friendly message.

    Common error patterns:
    - "execution error: App got an error: Can't get resource \"X\". (-1728)"
    - "execution error: App got an error: Can't make \"X\" into type reference. (-1700)"

    Args:
        stderr: The raw stderr from osascript

    Returns:
        A cleaned up, user-friendly error message.
    """
    stderr = stderr.strip()

    # Pattern: "execution error: App got an error: Message. (code)"
    # Extract the app name (may have spaces like "System Events"), message, and error code
    match = re.match(
        r"execution error: (.+?) got an error: (.+?)\.?\s*\((-?\d+)\)$",
        stderr,
        re.IGNORECASE,
    )
    if match:
        app_name, message, _error_code = match.groups()
        message = message.strip()

        # Parse common "Can't get X" patterns for specific resources
        # Handles both "Can't get list \"X\"" and "Can't get chat id \"X\""
        if cant_get := re.match(r"Can't get (\w+)(?: id)? \"([^\"]+)\"", message):
            resource_type, resource_id = cant_get.groups()
            return f"{app_name}: {resource_type.capitalize()} not found: {resource_id}"

        # "Can't make X into type Y" - usually type conversion errors
        if cant_make := re.match(r"Can't make \"([^\"]+)\" into type (.+)", message):
            value, target_type = cant_make.groups()
            return f"{app_name}: Invalid {target_type}: {value}"

        # Permission errors
        if "not allowed" in message.lower() or "assistive" in message.lower():
            return (
                f"{app_name}: Automation permission required.\n"
                f"  Grant access in System Settings > Privacy & Security > Automation"
            )

        # General error with cleaned message
        return f"{app_name}: {message}"

    # Simpler pattern without error code
    match = re.match(r"execution error: (.+)", stderr, re.IGNORECASE)
    if match:
        return f"AppleScript: {match.group(1).strip()}"

    # Fallback: return raw error
    return (
        f"AppleScript error: {stderr}" if stderr else "AppleScript error: Unknown error"
    )


def run_applescript(script: str, *args: str) -> str:
    """Run AppleScript with optional arguments passed via 'on run argv'.

    Args:
        script: The AppleScript source code to execute
        *args: Arguments passed to the script's 'on run argv' handler

    Returns:
        The script's stdout output, stripped of trailing whitespace

    Raises:
        JeanClaudeError: If the script exits with non-zero status
    """
    result = subprocess.run(
        ["osascript", "-e", script, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise JeanClaudeError(_parse_applescript_error(result.stderr))
    return result.stdout.strip()
