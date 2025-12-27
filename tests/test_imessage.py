"""Tests for imessage module helper functions and database queries."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from jean_claude.imessage import (
    MessageQuery,
    extract_text_from_attributed_body,
    fetch_messages,
    get_message_text,
)
from tests.fixtures.imessage_db import (
    IMESSAGE_GROUP_CHAT_STYLE,
    IMESSAGE_INDIVIDUAL_CHAT_STYLE,
)


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

    def test_empty_string_text_falls_back_to_attributed(self):
        """Test that empty string text falls back to attributedBody.

        Empty string is falsy, so get_message_text uses attributedBody instead.
        """
        result = get_message_text("", self.SAMPLE_ATTRIBUTED_BODY)
        assert result == "Hello world!"


# =============================================================================
# Database Query Tests
# =============================================================================


class TestFetchMessages:
    """Tests for fetch_messages SQL query against synthetic database."""

    def test_fetches_recent_messages(self, imessage_sample_db):
        """Test fetching recent messages returns results in date order."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(imessage_sample_db, query)

        assert len(messages) > 0
        # Most recent message should be first (default ORDER BY date DESC)
        assert "checking in" in messages[0]["text"]

    def test_respects_max_results(self, imessage_sample_db):
        """Test that max_results limit is honored."""
        query = MessageQuery(max_results=3)
        messages = fetch_messages(imessage_sample_db, query)

        assert len(messages) <= 3

    def test_includes_sender_for_received_messages(self, imessage_sample_db):
        """Test that received messages have sender populated."""
        query = MessageQuery(max_results=20, show_both_directions=True)
        messages = fetch_messages(imessage_sample_db, query)

        received = [m for m in messages if not m.get("is_from_me", False)]
        assert len(received) > 0
        for msg in received:
            assert msg["sender"] != "me"
            assert msg["sender"] != "unknown"

    def test_marks_self_messages_correctly(self, imessage_sample_db):
        """Test that messages from self have is_from_me=True when requested."""
        query = MessageQuery(max_results=20, show_both_directions=True)
        messages = fetch_messages(imessage_sample_db, query)

        from_me = [m for m in messages if m.get("is_from_me")]
        assert len(from_me) > 0
        for msg in from_me:
            assert msg["sender"] == "me"

    def test_group_chat_has_group_name(self, imessage_sample_db):
        """Test that group chat messages include group_name."""
        query = MessageQuery(max_results=50)
        messages = fetch_messages(imessage_sample_db, query)

        # Find messages from the named group
        project_msgs = [m for m in messages if m.get("group_name") == "Project Team"]
        assert len(project_msgs) > 0

    def test_message_with_attachment_marker(self, imessage_sample_db):
        """Test that messages with attachment marker are returned.

        Note: Actual attachment data requires files to exist on disk,
        which is tested in TestMessageAttachments. This test verifies
        the query runs without error for messages with cache_has_attachments=1.
        """
        query = MessageQuery(max_results=50)
        messages = fetch_messages(imessage_sample_db, query)

        # The message with "Check out this photo" should be returned
        photo_msg = next(
            (m for m in messages if m["text"] and "photo from lunch" in m["text"]),
            None,
        )
        assert photo_msg is not None

    def test_empty_database_returns_empty_list(self, imessage_db_builder):
        """Test that an empty database returns empty list."""
        conn = imessage_db_builder.build()
        query = MessageQuery()
        messages = fetch_messages(conn, query)

        assert messages == []


class TestFetchMessagesWithFilters:
    """Tests for fetch_messages with WHERE clause filters."""

    @pytest.fixture
    def db_with_specific_chat(self, imessage_db_builder):
        """Create a database with specific chat for filtering tests."""
        builder = imessage_db_builder

        alice = builder.add_handle("+15551111111")
        bob = builder.add_handle("+15552222222")

        chat_alice = builder.add_individual_chat(alice)
        chat_bob = builder.add_individual_chat(bob)

        base_time = datetime.now() - timedelta(days=1)

        # Messages in Alice's chat
        builder.add_message(
            chat_alice, "Message 1 to Alice", sender=None, date=base_time
        )
        builder.add_message(
            chat_alice,
            "Reply from Alice",
            sender=alice,
            date=base_time + timedelta(hours=1),
        )

        # Messages in Bob's chat
        builder.add_message(chat_bob, "Message to Bob", sender=None, date=base_time)
        builder.add_message(
            chat_bob, "Bob's response", sender=bob, date=base_time + timedelta(hours=2)
        )

        return builder.build(), chat_alice, chat_bob

    def test_filter_by_chat_identifier(self, db_with_specific_chat):
        """Test filtering messages by chat identifier."""
        conn, chat_alice, _ = db_with_specific_chat

        # Filter by Alice's chat identifier
        query = MessageQuery(
            chat_identifier="+15551111111",
            max_results=10,
        )
        messages = fetch_messages(conn, query)

        assert len(messages) == 2
        for msg in messages:
            assert "Alice" in msg["text"] or "Message 1" in msg["text"]


class TestFetchMessagesGroupChats:
    """Tests for group chat handling in fetch_messages."""

    @pytest.fixture
    def db_with_groups(self, imessage_db_builder):
        """Create a database with group chats."""
        builder = imessage_db_builder

        alice = builder.add_handle("+15551111111")
        bob = builder.add_handle("+15552222222")
        charlie = builder.add_handle("+15553333333")

        # Unnamed group - should show participant list
        unnamed_group = builder.add_group_chat([alice, bob, charlie])

        # Named group - should show display name
        named_group = builder.add_group_chat([alice, bob], display_name="Work Team")

        base_time = datetime.now() - timedelta(hours=1)

        builder.add_message(
            unnamed_group, "Hello everyone", sender=alice, date=base_time
        )
        builder.add_message(
            named_group,
            "Team update",
            sender=bob,
            date=base_time + timedelta(minutes=5),
        )

        return builder.build()

    def test_named_group_uses_display_name(self, db_with_groups):
        """Test that named groups use display_name for group_name."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_groups, query)

        team_msg = next(m for m in messages if "Team update" in m["text"])
        assert team_msg["group_name"] == "Work Team"

    def test_unnamed_group_lists_participants(self, db_with_groups):
        """Test that unnamed groups build group_name from participants."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_groups, query)

        hello_msg = next(m for m in messages if "Hello everyone" in m["text"])
        # Group name should contain participant phone numbers (or resolved names)
        assert hello_msg["group_name"] is not None
        # Should contain at least some participants - check for digits from the phone numbers
        # (resolve_phones_to_names returns empty dict in tests, so raw numbers pass through)
        assert "555" in hello_msg["group_name"]


class TestChatListQuery:
    """Tests for chat listing functionality."""

    def test_sample_db_has_expected_chats(self, imessage_sample_db):
        """Verify the sample database structure."""
        cursor = imessage_sample_db.cursor()

        # Count chats
        cursor.execute("SELECT COUNT(*) FROM chat")
        chat_count = cursor.fetchone()[0]
        assert chat_count >= 4  # At least 4 chats in sample data

        # Count individual vs group
        cursor.execute(
            "SELECT COUNT(*) FROM chat WHERE style = ?",
            (IMESSAGE_INDIVIDUAL_CHAT_STYLE,),
        )
        individual = cursor.fetchone()[0]
        cursor.execute(
            "SELECT COUNT(*) FROM chat WHERE style = ?", (IMESSAGE_GROUP_CHAT_STYLE,)
        )
        group = cursor.fetchone()[0]

        assert individual >= 2
        assert group >= 2

    def test_chat_has_messages(self, imessage_sample_db):
        """Verify chats have associated messages."""
        cursor = imessage_sample_db.cursor()

        cursor.execute("""
            SELECT c.ROWID, COUNT(cmj.message_id)
            FROM chat c
            LEFT JOIN chat_message_join cmj ON c.ROWID = cmj.chat_id
            GROUP BY c.ROWID
        """)
        results = cursor.fetchall()

        # All chats should have at least one message
        for chat_id, msg_count in results:
            assert msg_count > 0, f"Chat {chat_id} has no messages"


class TestMessageAttachments:
    """Tests for message attachment handling."""

    @pytest.fixture
    def db_with_attachments(self, imessage_db_builder, tmp_path):
        """Create a database with various attachment types.

        Creates real temp files since parse_attachments validates file existence.
        """
        builder = imessage_db_builder

        alice = builder.add_handle("+15551234567")
        chat = builder.add_individual_chat(alice)

        base_time = datetime.now() - timedelta(hours=1)

        # Create real temp files for attachments
        photo_path = tmp_path / "photo.jpeg"
        photo_path.write_bytes(b"fake jpeg data")

        doc_path = tmp_path / "report.pdf"
        doc_path.write_bytes(b"fake pdf data")

        file1_path = tmp_path / "file1.png"
        file1_path.write_bytes(b"fake png data")

        file2_path = tmp_path / "file2.png"
        file2_path.write_bytes(b"fake png data")

        # Message with image
        msg_photo = builder.add_message(
            chat, "Check this out", sender=alice, date=base_time
        )
        builder.add_attachment(msg_photo, str(photo_path), "image/jpeg", size=1024000)

        # Message with PDF (non-image, should be filtered out)
        msg_doc = builder.add_message(
            chat,
            "Here's the document",
            sender=alice,
            date=base_time + timedelta(minutes=5),
        )
        builder.add_attachment(msg_doc, str(doc_path), "application/pdf", size=512000)

        # Message with multiple image attachments
        msg_multi = builder.add_message(
            chat, "Some files", sender=alice, date=base_time + timedelta(minutes=10)
        )
        builder.add_attachment(msg_multi, str(file1_path), "image/png", size=1024)
        builder.add_attachment(msg_multi, str(file2_path), "image/png", size=2048)

        # Text-only message
        builder.add_message(
            chat,
            "Just text, no attachment",
            sender=alice,
            date=base_time + timedelta(minutes=15),
        )

        return builder.build()

    def test_attachment_metadata_in_message(self, db_with_attachments):
        """Test that attachment metadata is included in message."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_attachments, query)

        photo_msg = next(m for m in messages if "Check this out" in m["text"])
        assert len(photo_msg["attachments"]) == 1
        assert photo_msg["attachments"][0]["mimeType"] == "image/jpeg"
        assert photo_msg["attachments"][0]["size"] == 1024000

    def test_multiple_attachments_per_message(self, db_with_attachments):
        """Test messages with multiple attachments."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_attachments, query)

        multi_msg = next(m for m in messages if "Some files" in m["text"])
        assert len(multi_msg["attachments"]) == 2

    def test_text_only_message_has_empty_attachments(self, db_with_attachments):
        """Test that text-only messages have empty attachments list."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_attachments, query)

        text_msg = next(m for m in messages if "Just text" in m["text"])
        assert text_msg.get("attachments", []) == []

    def test_non_image_attachments_filtered(self, db_with_attachments):
        """Test that non-image attachments are filtered out."""
        query = MessageQuery(max_results=10)
        messages = fetch_messages(db_with_attachments, query)

        doc_msg = next(m for m in messages if "document" in m["text"])
        # PDF should be filtered out since parse_attachments only returns images
        assert doc_msg.get("attachments", []) == []
