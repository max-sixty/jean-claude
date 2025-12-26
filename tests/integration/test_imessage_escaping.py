"""iMessage integration tests - verify shell escaping is handled correctly.

These tests send real iMessages to yourself, then verify the message content
was not corrupted by shell escaping.

Run with: uv run pytest -m integration tests/integration/test_imessage_escaping.py

Prerequisites:
- Messages.app configured with your phone number
- Terminal app has Full Disk Access (for reading message history)
"""

from __future__ import annotations

import json
import os
import time
import uuid

import pytest
from click.testing import CliRunner

from jean_claude.cli import cli

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def runner():
    """Shared CLI runner."""
    return CliRunner()


@pytest.fixture(scope="module")
def my_phone(runner):
    """Get a phone number that can receive iMessages to self.

    Uses the chat list to find a self-referencing chat (phone matches user).
    Falls back to requiring MY_PHONE_NUMBER env var if not auto-detectable.
    """
    # Allow override via environment
    if phone := os.environ.get("MY_PHONE_NUMBER"):
        return phone

    # Try to find self in recent chats by looking for "Maybe: " prefix
    # which indicates the contact is the user's own number
    result = runner.invoke(cli, ["imessage", "chats", "-n", "50"])
    if result.exit_code == 0:
        data = json.loads(result.stdout)
        for chat in data["chats"]:
            name = chat.get("name", "")
            if name.startswith("Maybe:") and chat["chat_id"].startswith("any;-;"):
                # Extract phone from chat_id: "any;-;+1234567890"
                return chat["chat_id"].split(";")[-1]

    pytest.skip(
        "Could not auto-detect phone number for iMessage self-test.\n"
        "Set MY_PHONE_NUMBER environment variable to your iMessage number."
    )


def poll_for_message(runner, phone: str, expected_text: str, timeout: float = 10.0):
    """Poll until a message with expected text appears in chat history."""
    start = time.time()
    while time.time() - start < timeout:
        result = runner.invoke(
            cli, ["imessage", "messages", "--chat", phone, "-n", "5"]
        )
        if result.exit_code == 0:
            messages = json.loads(result.stdout)
            for msg in messages:
                if msg.get("text") == expected_text and msg.get("is_from_me"):
                    return msg
        time.sleep(1)
    return None


class TestIMessageEscaping:
    """Test that special characters are not corrupted during send."""

    def test_apostrophe_and_exclamation(self, runner, my_phone):
        """Verify apostrophe + exclamation don't get escaped to backslash.

        This is the specific bug that motivated stdin-based message input:
        Claude Code's Bash tool was escaping ! to \\! when ' appeared in
        the command string.
        """
        # Use a unique marker to identify this test message
        marker = str(uuid.uuid4())[:8]
        test_body = f"It's great! Test {marker}"

        # Send via stdin (body as plain text, recipient as argument)
        result = runner.invoke(cli, ["imessage", "send", my_phone], input=test_body)
        assert result.exit_code == 0, f"Send failed: {result.output}"

        # Wait for message to appear in history
        msg = poll_for_message(runner, my_phone, test_body, timeout=10.0)
        assert msg is not None, "Message not found in history within timeout"

        # The critical assertion: no backslash escaping
        assert msg["text"] == test_body
        assert "\\!" not in msg["text"], "Exclamation was incorrectly escaped"
        assert "\\'" not in msg["text"], "Apostrophe was incorrectly escaped"

    def test_multiple_special_characters(self, runner, my_phone):
        """Test message with various special characters that could be escaped."""
        marker = str(uuid.uuid4())[:8]
        # Include characters that are special in shells: ! ' " $ ` \
        test_body = f"Test {marker}: Hello! It's me. $100 `code`"

        # Send via stdin (body as plain text, recipient as argument)
        result = runner.invoke(cli, ["imessage", "send", my_phone], input=test_body)
        assert result.exit_code == 0, f"Send failed: {result.output}"

        msg = poll_for_message(runner, my_phone, test_body, timeout=10.0)
        assert msg is not None, "Message not found in history"
        assert msg["text"] == test_body, f"Message corrupted: {msg['text']!r}"
