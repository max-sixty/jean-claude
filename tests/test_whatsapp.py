"""Tests for WhatsApp CLI wrapper functions."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from jean_claude.logging import JeanClaudeError
from jean_claude.whatsapp import find_chat_by_name, resolve_recipient
from tests.fixtures.whatsapp_cli import SAMPLE_CHATS


def _fail_if_called():
    """Sentinel function that fails if invoked - for testing code paths that shouldn't call a function."""
    raise AssertionError("_get_all_chats should not be called")


def _extract_data(output: dict | list, key: str) -> list:
    """Extract data list from CLI output, handling both formats.

    The CLI returns either:
    - A plain list when everything is OK
    - An object with '_status' and the data key when there are warnings (auth/staleness)

    Args:
        output: Parsed JSON output from CLI
        key: Key to extract when output is wrapped (e.g., 'chats', 'messages')

    Returns:
        The data list, regardless of wrapper format
    """
    if isinstance(output, list):
        return output
    if isinstance(output, dict) and key in output:
        data = output[key]
        assert isinstance(data, list)
        return data
    # Fallback - shouldn't happen in normal use
    assert isinstance(output, list)
    return output


def _get_whatsapp_cli_binary() -> Path | None:
    """Find or build the whatsapp-cli binary for testing.

    Returns the path to the binary, or None if not available.
    """
    # Check for pre-built binary in jean_claude/bin/
    bin_dir = Path(__file__).parent.parent / "jean_claude" / "bin"
    os_name = {"darwin": "darwin", "linux": "linux"}.get(sys.platform)
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine)

    if os_name and arch:
        binary = bin_dir / f"whatsapp-cli-{os_name}-{arch}"
        if binary.exists():
            return binary

    # Try to build from source if Go is available
    if shutil.which("go"):
        whatsapp_dir = Path(__file__).parent.parent / "whatsapp"
        if (whatsapp_dir / "main.go").exists():
            with tempfile.NamedTemporaryFile(delete=False, suffix="-whatsapp-cli") as f:
                output = Path(f.name)
            try:
                subprocess.run(
                    ["go", "build", "-o", str(output), "."],
                    cwd=whatsapp_dir,
                    check=True,
                    capture_output=True,
                    env={**os.environ, "CGO_ENABLED": "0"},
                )
                return output
            except subprocess.CalledProcessError:
                output.unlink(missing_ok=True)
                return None

    return None


class TestFindChatByName:
    """Tests for find_chat_by_name lookup function."""

    def test_returns_jid_for_exact_match(self, monkeypatch):
        """Test that exact name match returns the JID."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        result = find_chat_by_name("Bob Johnson")
        assert result == "12025555678@s.whatsapp.net"

    def test_returns_none_for_no_match(self, monkeypatch):
        """Test that no match returns None."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        result = find_chat_by_name("Charlie Brown")
        assert result is None

    def test_raises_for_multiple_matches(self, monkeypatch):
        """Test that multiple matches raise an error with details."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        with pytest.raises(JeanClaudeError) as exc_info:
            find_chat_by_name("Alice Smith")

        # Error message should list both matches
        error_msg = str(exc_info.value)
        assert "Multiple chats match" in error_msg
        assert "12025551234@s.whatsapp.net" in error_msg
        assert "12025559999@s.whatsapp.net" in error_msg

    def test_returns_group_chat_jid(self, monkeypatch):
        """Test that group chats are found by name."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        result = find_chat_by_name("Project Team")
        assert result == "120363277025153496@g.us"

    def test_empty_chats_returns_none(self, monkeypatch):
        """Test that empty chat list returns None."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: [],
        )
        result = find_chat_by_name("Anyone")
        assert result is None

    def test_name_matching_is_case_sensitive(self, monkeypatch):
        """Verify name matching is case-sensitive."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        # "bob johnson" (lowercase) should not match "Bob Johnson"
        result = find_chat_by_name("bob johnson")
        assert result is None


class TestResolveRecipient:
    """Tests for resolve_recipient function."""

    def test_jid_passes_through_directly(self, monkeypatch):
        """Test that JIDs are returned unchanged."""
        monkeypatch.setattr("jean_claude.whatsapp._get_all_chats", _fail_if_called)
        result = resolve_recipient("12025551234@s.whatsapp.net")
        assert result == "12025551234@s.whatsapp.net"

    def test_group_jid_passes_through(self, monkeypatch):
        """Test that group JIDs are returned unchanged."""
        monkeypatch.setattr("jean_claude.whatsapp._get_all_chats", _fail_if_called)
        result = resolve_recipient("120363277025153496@g.us")
        assert result == "120363277025153496@g.us"

    def test_phone_number_passes_through(self, monkeypatch):
        """Test that phone numbers are returned unchanged."""
        monkeypatch.setattr("jean_claude.whatsapp._get_all_chats", _fail_if_called)
        result = resolve_recipient("+12025551234")
        assert result == "+12025551234"

    def test_phone_with_formatting_passes_through(self, monkeypatch):
        """Test that formatted phone numbers pass through."""
        monkeypatch.setattr("jean_claude.whatsapp._get_all_chats", _fail_if_called)
        result = resolve_recipient("+1 (202) 555-1234")
        assert result == "+1 (202) 555-1234"

    def test_name_resolves_to_jid(self, monkeypatch):
        """Test that chat names are resolved to JIDs."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        result = resolve_recipient("Bob Johnson")
        assert result == "12025555678@s.whatsapp.net"

    def test_group_name_resolves_to_jid(self, monkeypatch):
        """Test that group names are resolved to group JIDs."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        result = resolve_recipient("Family Group")
        assert result == "120363299999999999@g.us"

    def test_raises_for_ambiguous_name(self, monkeypatch):
        """Test that ambiguous names raise an error."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        with pytest.raises(JeanClaudeError) as exc_info:
            resolve_recipient("Alice Smith")

        assert "Multiple chats match" in str(exc_info.value)

    def test_raises_for_unknown_name(self, monkeypatch):
        """Test that unknown names raise an error with suggestions."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: SAMPLE_CHATS,
        )
        with pytest.raises(JeanClaudeError) as exc_info:
            resolve_recipient("Unknown Person")

        error_msg = str(exc_info.value)
        assert "Could not resolve" in error_msg
        assert "Unknown Person" in error_msg
        # Should suggest alternatives
        assert "phone number" in error_msg or "ID" in error_msg


class TestPhoneNumberDetection:
    """Tests for phone number pattern detection in resolve_recipient."""

    def test_international_format(self, monkeypatch):
        """Test +country code format."""
        monkeypatch.setattr("jean_claude.whatsapp._get_all_chats", _fail_if_called)
        assert resolve_recipient("+442071234567") == "+442071234567"
        assert resolve_recipient("+81312345678") == "+81312345678"

    def test_short_numbers_not_detected_as_phone(self, monkeypatch):
        """Test that short strings are not detected as phone numbers."""
        monkeypatch.setattr(
            "jean_claude.whatsapp._get_all_chats",
            lambda: [],  # Return empty so name lookup fails
        )
        # "123" is too short to be a phone, should try name lookup
        with pytest.raises(JeanClaudeError):
            resolve_recipient("123")


# =============================================================================
# Integration Tests - Go CLI with SQLite Database
# =============================================================================


@pytest.fixture
def whatsapp_cli(tmp_path):
    """Provide the WhatsApp CLI binary and environment for testing.

    Returns a function that runs the CLI with test database directories.
    Skips tests if Go binary is not available.
    """
    binary = _get_whatsapp_cli_binary()
    if binary is None:
        pytest.skip("WhatsApp CLI binary not available (Go not installed)")

    config_dir = tmp_path / "config"
    config_dir.mkdir()

    def run_cli(*args, data_dir: Path | None = None) -> subprocess.CompletedProcess:
        env = {
            **os.environ,
            "WHATSAPP_CONFIG_DIR": str(config_dir),
        }
        if data_dir:
            env["WHATSAPP_DATA_DIR"] = str(data_dir)

        return subprocess.run(
            [str(binary), *args],
            capture_output=True,
            text=True,
            env=env,
        )

    return run_cli


class TestWhatsAppCLIChats:
    """Integration tests for 'whatsapp-cli chats' command."""

    def test_chats_returns_json_list(self, whatsapp_cli, whatsapp_data_dir):
        """Test that chats command returns valid JSON list."""
        result = whatsapp_cli("chats", data_dir=whatsapp_data_dir)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        output = json.loads(result.stdout)
        chats = _extract_data(output, "chats")
        assert isinstance(chats, list)
        assert len(chats) > 0

    def test_chats_sorted_by_last_message_time(self, whatsapp_cli, whatsapp_data_dir):
        """Test that chats are sorted by last_message_time descending."""
        result = whatsapp_cli("chats", data_dir=whatsapp_data_dir)
        output = json.loads(result.stdout)
        chats = _extract_data(output, "chats")

        # Extract timestamps (may be None for some chats)
        timestamps = [c.get("last_message_time") or 0 for c in chats]
        assert timestamps == sorted(timestamps, reverse=True)

    def test_chats_have_expected_fields(self, whatsapp_cli, whatsapp_data_dir):
        """Test that each chat has the expected fields."""
        result = whatsapp_cli("chats", data_dir=whatsapp_data_dir)
        output = json.loads(result.stdout)
        chats = _extract_data(output, "chats")

        for chat in chats:
            assert "jid" in chat
            assert "name" in chat
            assert "is_group" in chat
            assert isinstance(chat["is_group"], bool)

    def test_chats_unread_filter(self, whatsapp_cli, whatsapp_data_dir):
        """Test --unread filter returns only chats with unread messages or marked_as_unread."""
        result = whatsapp_cli("chats", "--unread", data_dir=whatsapp_data_dir)
        assert result.returncode == 0

        output = json.loads(result.stdout)
        chats = _extract_data(output, "chats")
        # Sample data has chats with unread messages and one marked_as_unread
        assert len(chats) > 0, "Should have at least one unread chat"

        # Verify it's filtering - get all chats and compare
        all_result = whatsapp_cli("chats", data_dir=whatsapp_data_dir)
        all_output = json.loads(all_result.stdout)
        all_chats = _extract_data(all_output, "chats")
        assert len(chats) < len(all_chats), "Unread filter should return fewer chats"


class TestWhatsAppCLIMessages:
    """Integration tests for 'whatsapp-cli messages' command."""

    def test_messages_returns_json_list(self, whatsapp_cli, whatsapp_data_dir):
        """Test that messages command returns valid JSON list."""
        result = whatsapp_cli("messages", data_dir=whatsapp_data_dir)
        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")
        assert isinstance(messages, list)
        assert len(messages) > 0

    def test_messages_have_expected_fields(self, whatsapp_cli, whatsapp_data_dir):
        """Test that each message has the expected fields."""
        result = whatsapp_cli("messages", data_dir=whatsapp_data_dir)
        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")

        for msg in messages:
            assert "id" in msg
            assert "chat_jid" in msg
            assert "sender_jid" in msg
            assert "timestamp" in msg
            assert "is_from_me" in msg

    def test_messages_sorted_by_timestamp_desc(self, whatsapp_cli, whatsapp_data_dir):
        """Test that messages are sorted by timestamp descending (newest first)."""
        result = whatsapp_cli("messages", data_dir=whatsapp_data_dir)
        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")

        timestamps = [m["timestamp"] for m in messages]
        assert len(timestamps) >= 2, "Need multiple messages to verify sort order"
        assert timestamps == sorted(timestamps, reverse=True)

    def test_messages_filter_by_chat(self, whatsapp_cli, whatsapp_data_dir):
        """Test filtering messages by chat JID."""
        # First get all chats to find a valid JID
        chats_result = whatsapp_cli("chats", data_dir=whatsapp_data_dir)
        chats_output = json.loads(chats_result.stdout)
        chats = _extract_data(chats_output, "chats")
        chat_jid = chats[0]["jid"]

        # Filter messages to that chat
        result = whatsapp_cli(
            "messages", f"--chat={chat_jid}", data_dir=whatsapp_data_dir
        )
        assert result.returncode == 0

        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")
        for msg in messages:
            assert msg["chat_jid"] == chat_jid

    def test_messages_include_reactions(self, whatsapp_cli, whatsapp_data_dir):
        """Test that messages include reactions when present."""
        result = whatsapp_cli("messages", data_dir=whatsapp_data_dir)
        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")

        # Find a message with reactions (from sample data)
        messages_with_reactions = [m for m in messages if m.get("reactions")]
        assert len(messages_with_reactions) > 0

        # Verify reaction structure
        reaction = messages_with_reactions[0]["reactions"][0]
        assert "emoji" in reaction
        assert "sender_jid" in reaction


class TestWhatsAppCLISearch:
    """Integration tests for 'whatsapp-cli search' command."""

    def test_search_finds_matching_messages(self, whatsapp_cli, whatsapp_data_dir):
        """Test that search finds messages containing the query."""
        result = whatsapp_cli("search", "lunch", data_dir=whatsapp_data_dir)
        assert result.returncode == 0

        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")
        assert len(messages) > 0
        for msg in messages:
            assert "lunch" in msg.get("text", "").lower()

    def test_search_respects_max_results(self, whatsapp_cli, whatsapp_data_dir):
        """Test that --max-results limits output."""
        result = whatsapp_cli(
            "search", "a", "--max-results=2", data_dir=whatsapp_data_dir
        )
        assert result.returncode == 0

        output = json.loads(result.stdout)
        messages = _extract_data(output, "messages")
        assert len(messages) <= 2


class TestWhatsAppCLIContacts:
    """Integration tests for 'whatsapp-cli contacts' command."""

    def test_contacts_returns_json_list(self, whatsapp_cli, whatsapp_data_dir):
        """Test that contacts command returns valid JSON list."""
        result = whatsapp_cli("contacts", data_dir=whatsapp_data_dir)
        assert result.returncode == 0

        contacts = json.loads(result.stdout)
        assert isinstance(contacts, list)

    def test_contacts_have_expected_fields(self, whatsapp_cli, whatsapp_data_dir):
        """Test that each contact has the expected fields."""
        result = whatsapp_cli("contacts", data_dir=whatsapp_data_dir)
        contacts = json.loads(result.stdout)

        for contact in contacts:
            assert "jid" in contact
            # name and push_name may be null
