"""Tests for imessage module helper functions."""

from __future__ import annotations

from jean_claude.imessage import _extract_identifier, truncate_text


class TestTruncateText:
    """Tests for text truncation."""

    def test_short_text(self):
        """Test that short text is not truncated."""
        text = "Hello, World!"
        result = truncate_text(text)
        assert result == text

    def test_exact_limit(self):
        """Test text exactly at truncate limit."""
        text = "x" * 200  # TEXT_TRUNCATE_LENGTH is 200
        result = truncate_text(text)
        assert result == text
        assert "..." not in result

    def test_long_text(self):
        """Test that long text is truncated."""
        text = "x" * 250
        result = truncate_text(text)
        assert len(result) == 203  # 200 + "..."
        assert result.endswith("...")

    def test_none_text(self):
        """Test that None returns placeholder."""
        result = truncate_text(None)
        assert result == "(no text)"

    def test_empty_text(self):
        """Test that empty string returns placeholder."""
        result = truncate_text("")
        assert result == "(no text)"


class TestExtractIdentifier:
    """Tests for chat ID identifier extraction."""

    def test_phone_number_chat(self):
        """Test extracting phone number from chat ID."""
        chat_id = "any;-;+12025551234"
        result = _extract_identifier(chat_id)
        assert result == "+12025551234"

    def test_group_chat(self):
        """Test extracting group chat identifier."""
        chat_id = "any;+;chat123456789"
        result = _extract_identifier(chat_id)
        assert result == "chat123456789"

    def test_email_chat(self):
        """Test extracting email from chat ID."""
        chat_id = "any;-;user@example.com"
        result = _extract_identifier(chat_id)
        assert result == "user@example.com"

    def test_short_format(self):
        """Test chat ID with fewer than 3 parts returns original."""
        chat_id = "iMessage;+12025551234"
        result = _extract_identifier(chat_id)
        # With only 2 parts, returns original (needs 3+ parts to extract)
        assert result == chat_id

    def test_no_semicolons(self):
        """Test chat ID without semicolons."""
        chat_id = "+12025551234"
        result = _extract_identifier(chat_id)
        # Returns original if no semicolons
        assert result == "+12025551234"

    def test_many_parts(self):
        """Test chat ID with many semicolon-separated parts."""
        chat_id = "a;b;c;d;e;final_part"
        result = _extract_identifier(chat_id)
        assert result == "final_part"
