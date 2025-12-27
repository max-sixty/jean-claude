"""AppleScript utilities for macOS automation."""

from __future__ import annotations

import subprocess

from .logging import JeanClaudeError


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
        raise JeanClaudeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()
