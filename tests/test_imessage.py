"""Tests for imessage module helper functions."""

from __future__ import annotations

from jean_claude.imessage import (
    _extract_identifier,
    extract_text_from_attributed_body,
    get_message_text,
    truncate_text,
)


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


class TestExtractTextFromAttributedBody:
    """Tests for extracting text from NSAttributedString binary."""

    # Real attributedBody format from macOS Messages database
    # Format: streamtyped header + NSAttributedString + NSObject + NSString + content
    SAMPLE_ATTRIBUTED_BODY = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12"
        b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92"
        b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x12"
        b"very cool though!!"
        b"\x86\x84\x02iI\x01\x12\x92"
    )

    def test_extracts_text_from_valid_body(self):
        """Test extracting text from valid attributedBody."""
        result = extract_text_from_attributed_body(self.SAMPLE_ATTRIBUTED_BODY)
        assert result == "very cool though!!"

    def test_returns_none_for_none_input(self):
        """Test that None input returns None."""
        result = extract_text_from_attributed_body(None)
        assert result is None

    def test_returns_none_for_empty_bytes(self):
        """Test that empty bytes returns None."""
        result = extract_text_from_attributed_body(b"")
        assert result is None

    def test_returns_none_for_missing_nsstring(self):
        """Test returns None when NSString marker missing."""
        result = extract_text_from_attributed_body(b"random data without marker")
        assert result is None

    def test_returns_none_for_missing_plus_marker(self):
        """Test returns None when + marker is too far from NSString."""
        # NSString present but + marker is too far away (>50 bytes)
        data = b"NSString" + b"x" * 60 + b"+\x05hello"
        result = extract_text_from_attributed_body(data)
        assert result is None

    def test_returns_none_for_truncated_data(self):
        """Test returns None when data is truncated."""
        # Length byte says 20 but only 5 bytes of text follow
        data = b"NSString\x00\x01+\x14hello"
        result = extract_text_from_attributed_body(data)
        assert result is None

    def test_handles_multibyte_length(self):
        """Test parsing messages > 127 chars with multi-byte length encoding."""
        # When length >= 128, format uses 0x81 followed by 2 bytes little-endian
        # Build: NSString + 5-byte preamble + 0x81 + 2-byte length + content
        long_text = "x" * 200
        length_bytes = len(long_text).to_bytes(2, "little")
        data = (
            b"NSString"
            + b"\x01\x94\x84\x01+"  # 5-byte preamble
            + b"\x81"  # multi-byte length indicator
            + length_bytes
            + long_text.encode("utf-8")
        )
        result = extract_text_from_attributed_body(data)
        assert result == long_text
        assert len(result) == 200


class TestGetMessageText:
    """Tests for get_message_text helper."""

    SAMPLE_ATTRIBUTED_BODY = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12"
        b"NSAttributedString\x00\x84\x84\x08NSObject\x00\x85\x92"
        b"\x84\x84\x84\x08NSString\x01\x94\x84\x01+\x0c"
        b"Hello world!"
        b"\x86"
    )

    def test_prefers_text_column(self):
        """Test that text column is preferred over attributedBody."""
        result = get_message_text("Direct text", self.SAMPLE_ATTRIBUTED_BODY)
        assert result == "Direct text"

    def test_falls_back_to_attributed_body(self):
        """Test fallback to attributedBody when text is None."""
        result = get_message_text(None, self.SAMPLE_ATTRIBUTED_BODY)
        assert result == "Hello world!"

    def test_returns_none_when_both_empty(self):
        """Test returns None when both sources are empty."""
        result = get_message_text(None, None)
        assert result is None

    def test_empty_string_text_returns_attributed(self):
        """Test that empty string text still uses text column (truthy check)."""
        # Empty string is falsy, so falls back to attributedBody
        result = get_message_text("", self.SAMPLE_ATTRIBUTED_BODY)
        assert result == "Hello world!"
