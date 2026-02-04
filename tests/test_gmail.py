"""Tests for gmail module helper functions."""

from __future__ import annotations

from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jean_claude.gmail import (
    GmailErrorHandlingGroup,
    _build_message_with_attachments,
    _create_attachment_part,
    _extract_attachments,
    _extract_inline_images,
    _get_part_header,
    _strip_html,
    decode_body,
    extract_attachments_from_payload,
    extract_draft_summary,
    extract_inline_images_from_payload,
    extract_message_summary,
    extract_thread_summary,
)


class TestStripHtml:
    """Tests for HTML stripping."""

    def test_basic_tags(self):
        """Test removing basic HTML tags."""
        html = "<p>Hello <b>world</b></p>"
        result = _strip_html(html)
        assert "Hello" in result
        assert "world" in result
        assert "<" not in result
        assert ">" not in result

    def test_script_removal(self):
        """Test that script tags are removed with content."""
        html = "<p>Before</p><script>alert('bad');</script><p>After</p>"
        result = _strip_html(html)
        assert "Before" in result
        assert "After" in result
        assert "alert" not in result
        assert "script" not in result.lower()

    def test_style_removal(self):
        """Test that style tags are removed with content."""
        html = "<p>Text</p><style>body { color: red; }</style>"
        result = _strip_html(html)
        assert "Text" in result
        assert "color" not in result
        assert "style" not in result.lower()

    def test_entity_decoding(self):
        """Test HTML entity decoding."""
        html = "Tom &amp; Jerry &lt;3 &gt; &quot;fun&quot;"
        result = _strip_html(html)
        assert "Tom & Jerry" in result
        assert "<3" in result
        assert '"fun"' in result

    def test_newlines_from_block_elements(self):
        """Test that block elements create newlines."""
        html = "<p>Para 1</p><p>Para 2</p>"
        result = _strip_html(html)
        assert "Para 1" in result
        assert "Para 2" in result


class TestDecodeBody:
    """Tests for email body decoding."""

    def test_empty_payload(self):
        """Test with empty payload."""
        assert decode_body({}) == ""

    def test_simple_body(self):
        """Test with simple body data."""
        import base64

        content = "Hello, World!"
        encoded = base64.urlsafe_b64encode(content.encode()).decode()
        payload = {"body": {"data": encoded}}
        assert decode_body(payload) == content

    def test_multipart_plain(self):
        """Test multipart with plain text part."""
        import base64

        content = "Plain text content"
        encoded = base64.urlsafe_b64encode(content.encode()).decode()
        payload = {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": encoded}},
                {"mimeType": "text/html", "body": {"data": "ignored"}},
            ]
        }
        assert decode_body(payload) == content

    def test_multipart_html_fallback(self):
        """Test multipart falls back to HTML when no plain text."""
        import base64

        html_content = "<p>HTML content</p>"
        encoded = base64.urlsafe_b64encode(html_content.encode()).decode()
        payload = {
            "parts": [
                {"mimeType": "text/html", "body": {"data": encoded}},
            ]
        }
        result = decode_body(payload)
        assert "HTML content" in result
        assert "<p>" not in result  # Tags should be stripped


class TestExtractSummary:
    """Tests for message/thread/draft summary extraction.

    These tests verify RFC 2822 compliance: email header names are case-insensitive.
    Different mail servers use different casings (e.g., Microsoft Exchange uses "CC"
    while Gmail uses "Cc"), so we must normalize them.
    """

    def _make_message(self, headers: list[tuple[str, str]]) -> dict:
        """Create a minimal Gmail API message object with given headers."""
        return {
            "id": "msg123",
            "threadId": "thread456",
            "payload": {
                "headers": [{"name": k, "value": v} for k, v in headers],
                "body": {"data": ""},
            },
            "snippet": "Test snippet",
            "labelIds": ["INBOX"],
        }

    def test_extract_message_cc_uppercase(self, tmp_path, monkeypatch):
        """Test CC header detection with Microsoft Exchange casing (all caps)."""
        monkeypatch.setattr("jean_claude.gmail.EMAIL_CACHE_DIR", tmp_path)

        msg = self._make_message(
            [
                ("From", "sender@example.com"),
                ("To", "recipient@example.com"),
                ("Subject", "Test"),
                ("Date", "Mon, 1 Jan 2024 12:00:00 +0000"),
                ("CC", "cc@example.com"),  # Microsoft Exchange uses all caps
            ]
        )

        result = extract_message_summary(msg)
        assert result["cc"] == "cc@example.com"
        assert result["from"] == "sender@example.com"
        assert result["to"] == "recipient@example.com"
        assert result["subject"] == "Test"

    def test_extract_message_cc_mixedcase(self, tmp_path, monkeypatch):
        """Test CC header detection with Gmail casing (mixed case)."""
        monkeypatch.setattr("jean_claude.gmail.EMAIL_CACHE_DIR", tmp_path)

        msg = self._make_message(
            [
                ("From", "sender@example.com"),
                ("To", "recipient@example.com"),
                ("Subject", "Test"),
                ("Date", "Mon, 1 Jan 2024 12:00:00 +0000"),
                ("Cc", "cc@example.com"),  # Gmail uses mixed case
            ]
        )

        result = extract_message_summary(msg)
        assert result["cc"] == "cc@example.com"

    def test_extract_thread_cc_uppercase(self, tmp_path, monkeypatch):
        """Test thread summary extracts CC regardless of casing."""
        monkeypatch.setattr("jean_claude.gmail.EMAIL_CACHE_DIR", tmp_path)

        thread = {
            "id": "thread456",
            "messages": [
                self._make_message(
                    [
                        ("From", "sender@example.com"),
                        ("To", "recipient@example.com"),
                        ("Subject", "Test thread"),
                        ("Date", "Mon, 1 Jan 2024 12:00:00 +0000"),
                        ("CC", "cc@example.com"),  # All caps
                    ]
                ),
            ],
        }

        result = extract_thread_summary(thread)
        assert result["messages"][0]["cc"] == "cc@example.com"
        assert result["subject"] == "Test thread"

    def test_extract_draft_cc_uppercase(self):
        """Test draft summary extracts CC regardless of casing."""
        draft = {
            "id": "draft789",
            "message": {
                "id": "msg123",
                "payload": {
                    "headers": [
                        {"name": "To", "value": "recipient@example.com"},
                        {"name": "Subject", "value": "Draft test"},
                        {"name": "CC", "value": "cc@example.com"},  # All caps
                    ],
                },
                "snippet": "Draft content",
            },
        }

        result = extract_draft_summary(draft)
        assert result["cc"] == "cc@example.com"
        assert result["to"] == "recipient@example.com"
        assert result["subject"] == "Draft test"


class TestExtractAttachments:
    """Tests for attachment extraction."""

    def test_no_attachments(self):
        """Test parts with no attachments."""
        parts = [
            {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
        ]
        attachments: list = []
        _extract_attachments(parts, attachments)
        assert attachments == []

    def test_single_attachment(self):
        """Test extracting a single attachment."""
        parts = [
            {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
            {
                "filename": "report.pdf",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "ANGjdJ8xyz", "size": 12345},
            },
        ]
        attachments: list = []
        _extract_attachments(parts, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"
        assert attachments[0]["mimeType"] == "application/pdf"
        assert attachments[0]["size"] == 12345
        assert attachments[0]["attachmentId"] == "ANGjdJ8xyz"

    def test_multiple_attachments(self):
        """Test extracting multiple attachments."""
        parts = [
            {
                "filename": "doc1.pdf",
                "mimeType": "application/pdf",
                "body": {"attachmentId": "id1", "size": 100},
            },
            {
                "filename": "image.png",
                "mimeType": "image/png",
                "body": {"attachmentId": "id2", "size": 200},
            },
        ]
        attachments: list = []
        _extract_attachments(parts, attachments)
        assert len(attachments) == 2
        assert attachments[0]["filename"] == "doc1.pdf"
        assert attachments[1]["filename"] == "image.png"

    def test_nested_attachments(self):
        """Test extracting attachments from nested multipart."""
        parts = [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
                    {
                        "filename": "nested.pdf",
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "nested_id", "size": 500},
                    },
                ],
            },
        ]
        attachments: list = []
        _extract_attachments(parts, attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "nested.pdf"

    def test_filename_without_attachment_id(self):
        """Test that parts with filename but no attachmentId are skipped."""
        parts = [
            {
                "filename": "inline.gif",
                "mimeType": "image/gif",
                "body": {"data": "R0lGODlh"},  # inline, no attachmentId
            },
        ]
        attachments: list = []
        _extract_attachments(parts, attachments)
        assert attachments == []

    def test_payload_is_attachment(self):
        """Test extracting attachment when payload itself is the attachment.

        This happens with emails like DMARC reports where the entire payload
        is a single attachment (e.g., a zip file) without nested parts.
        """
        # Payload structure when email body IS the attachment
        payload = {
            "partId": "",
            "mimeType": "application/zip",
            "filename": "dmarc-report.zip",
            "headers": [{"name": "Content-Type", "value": "application/zip"}],
            "body": {"attachmentId": "ANGjdJ9PBPzOfITU", "size": 726},
        }
        attachments: list = []
        _extract_attachments([payload], attachments)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "dmarc-report.zip"
        assert attachments[0]["mimeType"] == "application/zip"
        assert attachments[0]["attachmentId"] == "ANGjdJ9PBPzOfITU"


class TestExtractAttachmentsFromPayload:
    """Tests for extract_attachments_from_payload function."""

    def test_payload_without_parts_is_attachment(self):
        """Test that payload-level attachments are found (no parts field).

        When an email's payload IS the attachment (like DMARC reports),
        there are no nested parts - the payload itself has the attachment info.
        """
        # Payload structure when email body IS the attachment (no parts)
        payload = {
            "partId": "",
            "mimeType": "application/zip",
            "filename": "dmarc-report.zip",
            "headers": [{"name": "Content-Type", "value": "application/zip"}],
            "body": {"attachmentId": "ANGjdJ9PBPzOfITU", "size": 726},
        }
        attachments = extract_attachments_from_payload(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "dmarc-report.zip"

    def test_payload_with_parts(self):
        """Test normal multipart message with attachments in parts."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": "SGVsbG8="}},
                {
                    "filename": "report.pdf",
                    "mimeType": "application/pdf",
                    "body": {"attachmentId": "abc123", "size": 1234},
                },
            ],
        }
        attachments = extract_attachments_from_payload(payload)
        assert len(attachments) == 1
        assert attachments[0]["filename"] == "report.pdf"

    def test_empty_payload(self):
        """Test payload with no attachments."""
        payload = {
            "mimeType": "text/plain",
            "body": {"data": "SGVsbG8="},
        }
        attachments = extract_attachments_from_payload(payload)
        assert attachments == []


class TestGmailErrorHandling:
    """Tests for Gmail-specific HTTP error handling."""

    @pytest.fixture
    def handler(self):
        """Create a GmailErrorHandlingGroup instance for testing."""
        return GmailErrorHandlingGroup()

    def test_message_not_found(self, handler):
        """404 for message shows message ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/messages/19abc123"

        msg = handler._http_error_message(error)
        assert "Message not found: 19abc123" in msg
        assert "jean-claude gmail search" in msg

    def test_thread_not_found(self, handler):
        """404 for thread shows thread ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/threads/19xyz789"

        msg = handler._http_error_message(error)
        assert "Thread not found: 19xyz789" in msg
        assert "jean-claude gmail inbox" in msg

    def test_draft_not_found(self, handler):
        """404 for draft shows draft ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/drafts/r-123456"

        msg = handler._http_error_message(error)
        assert "Draft not found: r-123456" in msg
        assert "jean-claude gmail draft list" in msg

    def test_filter_not_found(self, handler):
        """404 for filter shows filter ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = (
            "https://gmail.googleapis.com/gmail/v1/users/me/settings/filters/ANe1BmjXYZ"
        )

        msg = handler._http_error_message(error)
        assert "Filter not found: ANe1BmjXYZ" in msg
        assert "jean-claude gmail filter list" in msg

    def test_attachment_not_found(self, handler):
        """404 for attachment shows attachment ID, message ID, and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/messages/19abc123/attachments/ANGjdJ8xyz"

        msg = handler._http_error_message(error)
        assert "Attachment not found: ANGjdJ8xyz" in msg
        assert "Message: 19abc123" in msg
        assert "jean-claude gmail attachments 19abc123" in msg

    def test_label_not_found(self, handler):
        """404 for label shows label ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/labels/Label_123"

        msg = handler._http_error_message(error)
        assert "Label not found: Label_123" in msg
        assert "jean-claude gmail labels" in msg

    def test_non_404_falls_through(self, handler):
        """Non-404 errors use base class handling."""
        error = MagicMock()
        error.resp.status = 403
        error._get_reason.return_value = "Forbidden"
        error.__str__ = lambda self: "403 Forbidden"
        error.uri = "https://gmail.googleapis.com/gmail/v1/users/me/messages"

        msg = handler._http_error_message(error)
        assert "Permission denied" in msg

    def test_404_without_uri_falls_through(self, handler):
        """404 without URI uses base class handling."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        del error.uri

        msg = handler._http_error_message(error)
        assert msg == "Not found: Not Found"


class TestCreateAttachmentPart:
    """Tests for _create_attachment_part."""

    def test_creates_valid_mime_part(self, tmp_path: Path):
        """Test that attachment part has correct structure."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        part = _create_attachment_part(test_file)

        assert part.get_content_type() == "text/plain"
        assert part.get_filename() == "test.txt"
        assert "attachment" in part["Content-Disposition"]

    def test_pdf_mime_type(self, tmp_path: Path):
        """Test PDF files get correct MIME type."""
        test_file = tmp_path / "document.pdf"
        test_file.write_bytes(b"%PDF-1.4 fake pdf content")

        part = _create_attachment_part(test_file)

        assert part.get_content_type() == "application/pdf"
        assert part.get_filename() == "document.pdf"

    def test_unknown_extension_uses_octet_stream(self, tmp_path: Path):
        """Test unknown extensions fall back to application/octet-stream."""
        test_file = tmp_path / "mystery.xyz123"
        test_file.write_bytes(b"binary data")

        part = _create_attachment_part(test_file)

        assert part.get_content_type() == "application/octet-stream"

    def test_binary_content_preserved(self, tmp_path: Path):
        """Test that binary content is correctly encoded."""
        test_file = tmp_path / "binary.bin"
        binary_content = bytes(range(256))  # All possible byte values
        test_file.write_bytes(binary_content)

        part = _create_attachment_part(test_file)

        # Use get_payload(decode=True) to decode base64 content
        decoded = part.get_payload(decode=True)
        assert decoded == binary_content


class TestBuildMessageWithAttachments:
    """Tests for _build_message_with_attachments."""

    def test_plain_text_only_returns_mimetext(self):
        """Test plain text without attachments returns MIMEText."""
        msg = _build_message_with_attachments("Hello", None, [])

        assert isinstance(msg, MIMEText)
        assert msg.get_content_type() == "text/plain"

    def test_html_returns_multipart_alternative(self):
        """Test text+html without attachments returns multipart/alternative."""
        msg = _build_message_with_attachments("Plain", "<p>HTML</p>", [])

        assert isinstance(msg, MIMEMultipart)
        assert msg.get_content_type() == "multipart/alternative"
        parts = msg.get_payload()
        assert len(parts) == 2
        assert parts[0].get_content_type() == "text/plain"  # type: ignore[union-attr]
        assert parts[1].get_content_type() == "text/html"  # type: ignore[union-attr]

    def test_with_attachments_returns_multipart_mixed(self, tmp_path: Path):
        """Test message with attachments returns multipart/mixed."""
        test_file = tmp_path / "attach.txt"
        test_file.write_text("attachment content")

        msg = _build_message_with_attachments("Body text", None, [test_file])

        assert isinstance(msg, MIMEMultipart)
        assert msg.get_content_type() == "multipart/mixed"
        parts = msg.get_payload()
        assert len(parts) == 2
        # First part is body
        assert parts[0].get_content_type() == "text/plain"  # type: ignore[union-attr]
        # Second part is attachment
        assert parts[1].get_filename() == "attach.txt"  # type: ignore[union-attr]

    def test_html_with_attachments_has_nested_alternative(self, tmp_path: Path):
        """Test HTML message with attachments has nested multipart/alternative."""
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"%PDF-1.4")

        msg = _build_message_with_attachments("Plain", "<p>HTML</p>", [test_file])

        assert msg.get_content_type() == "multipart/mixed"
        parts = msg.get_payload()
        assert len(parts) == 2

        # First part should be multipart/alternative with text and html
        body_part = parts[0]  # type: ignore[index]
        assert body_part.get_content_type() == "multipart/alternative"  # type: ignore[union-attr]
        body_parts = body_part.get_payload()  # type: ignore[union-attr]
        assert body_parts[0].get_content_type() == "text/plain"  # type: ignore[union-attr]
        assert body_parts[1].get_content_type() == "text/html"  # type: ignore[union-attr]

        # Second part is attachment
        assert parts[1].get_filename() == "doc.pdf"  # type: ignore[union-attr]

    def test_multiple_attachments(self, tmp_path: Path):
        """Test message with multiple attachments."""
        file1 = tmp_path / "one.txt"
        file2 = tmp_path / "two.pdf"
        file1.write_text("first")
        file2.write_bytes(b"%PDF")

        msg = _build_message_with_attachments("Body", None, [file1, file2])

        parts = msg.get_payload()
        assert len(parts) == 3  # body + 2 attachments
        assert parts[1].get_filename() == "one.txt"  # type: ignore[union-attr]
        assert parts[2].get_filename() == "two.pdf"  # type: ignore[union-attr]

    def test_message_can_be_serialized(self, tmp_path: Path):
        """Test that the message can be converted to bytes for sending."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        msg = _build_message_with_attachments("Body", "<p>HTML</p>", [test_file])
        msg["from"] = "sender@example.com"
        msg["to"] = "recipient@example.com"
        msg["subject"] = "Test"

        # Should not raise
        raw_bytes = msg.as_bytes()
        assert b"sender@example.com" in raw_bytes
        assert b"recipient@example.com" in raw_bytes
        assert b"Test" in raw_bytes

    def test_with_inline_images_returns_multipart_related(self):
        """Test message with inline images uses multipart/related."""
        # Create a mock inline image part
        inline_img = MIMEBase("image", "png")
        inline_img.set_payload(b"fake png data")
        encoders.encode_base64(inline_img)
        inline_img.add_header("Content-ID", "<image001>")
        inline_img.add_header("Content-Disposition", "inline")

        msg = _build_message_with_attachments(
            "Plain", "<p><img src='cid:image001'></p>", [], [inline_img]
        )

        # Should be multipart/related containing alternative + inline image
        assert msg.get_content_type() == "multipart/related"
        parts = msg.get_payload()
        assert len(parts) == 2
        # First is the alternative body
        assert parts[0].get_content_type() == "multipart/alternative"  # type: ignore[union-attr]
        # Second is the inline image
        assert parts[1].get_content_type() == "image/png"  # type: ignore[union-attr]
        assert parts[1]["Content-ID"] == "<image001>"  # type: ignore[index]

    def test_inline_images_with_attachments(self, tmp_path: Path):
        """Test message with both inline images and attachments."""
        # Create inline image
        inline_img = MIMEBase("image", "jpeg")
        inline_img.set_payload(b"fake jpeg")
        encoders.encode_base64(inline_img)
        inline_img.add_header("Content-ID", "<logo>")
        inline_img.add_header("Content-Disposition", "inline")

        # Create attachment file
        attach_file = tmp_path / "doc.pdf"
        attach_file.write_bytes(b"%PDF-1.4")

        msg = _build_message_with_attachments(
            "Plain",
            "<p><img src='cid:logo'></p>",
            [attach_file],
            [inline_img],
        )

        # Structure: multipart/mixed containing related + attachment
        assert msg.get_content_type() == "multipart/mixed"
        parts = msg.get_payload()
        assert len(parts) == 2

        # First part is multipart/related (body + inline images)
        related = parts[0]  # type: ignore[index]
        assert related.get_content_type() == "multipart/related"  # type: ignore[union-attr]
        related_parts = related.get_payload()  # type: ignore[union-attr]
        assert related_parts[0].get_content_type() == "multipart/alternative"  # type: ignore[union-attr]
        assert related_parts[1]["Content-ID"] == "<logo>"  # type: ignore[index]

        # Second part is the attachment
        assert parts[1].get_filename() == "doc.pdf"  # type: ignore[union-attr]


class TestExtractInlineImages:
    """Tests for inline image extraction."""

    def test_extracts_parts_with_content_id(self):
        """Test that parts with Content-ID are identified as inline images."""
        parts = [
            {
                "mimeType": "image/png",
                "headers": [{"name": "Content-ID", "value": "<image001>"}],
                "body": {"attachmentId": "attach123"},
            }
        ]
        inline_images: list = []
        _extract_inline_images(parts, inline_images)

        assert len(inline_images) == 1
        assert inline_images[0]["contentId"] == "<image001>"
        assert inline_images[0]["attachmentId"] == "attach123"
        assert inline_images[0]["mimeType"] == "image/png"

    def test_ignores_parts_without_content_id(self):
        """Test that parts without Content-ID are not inline images."""
        parts = [
            {
                "mimeType": "image/jpeg",
                "filename": "photo.jpg",
                "headers": [],
                "body": {"attachmentId": "attach456"},
            }
        ]
        inline_images: list = []
        _extract_inline_images(parts, inline_images)

        assert len(inline_images) == 0

    def test_extracts_nested_inline_images(self):
        """Test extraction from nested multipart structure."""
        parts = [
            {
                "mimeType": "multipart/related",
                "parts": [
                    {"mimeType": "text/html", "body": {"data": "aGVsbG8="}},
                    {
                        "mimeType": "image/gif",
                        "headers": [{"name": "Content-ID", "value": "<logo>"}],
                        "body": {"attachmentId": "gif123"},
                    },
                ],
            }
        ]
        inline_images: list = []
        _extract_inline_images(parts, inline_images)

        assert len(inline_images) == 1
        assert inline_images[0]["contentId"] == "<logo>"

    def test_extract_inline_images_from_payload(self):
        """Test the top-level extraction function."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/png",
                    "headers": [{"name": "Content-ID", "value": "<img1>"}],
                    "body": {"attachmentId": "a1"},
                },
                {
                    "mimeType": "image/jpeg",
                    "headers": [{"name": "Content-ID", "value": "<img2>"}],
                    "body": {"attachmentId": "a2"},
                },
            ],
        }
        inline_images = extract_inline_images_from_payload(payload)

        assert len(inline_images) == 2
        assert inline_images[0]["contentId"] == "<img1>"
        assert inline_images[1]["contentId"] == "<img2>"


class TestGetPartHeader:
    """Tests for _get_part_header helper."""

    def test_finds_header_case_insensitive(self):
        """Test header lookup is case-insensitive."""
        part = {"headers": [{"name": "Content-ID", "value": "<test>"}]}
        assert _get_part_header(part, "content-id") == "<test>"
        assert _get_part_header(part, "Content-ID") == "<test>"
        assert _get_part_header(part, "CONTENT-ID") == "<test>"

    def test_returns_none_for_missing_header(self):
        """Test returns None when header not found."""
        part = {"headers": [{"name": "Content-Type", "value": "text/plain"}]}
        assert _get_part_header(part, "Content-ID") is None

    def test_handles_empty_headers(self):
        """Test handles part with no headers."""
        part = {"headers": []}
        assert _get_part_header(part, "Content-ID") is None

    def test_handles_missing_headers_key(self):
        """Test handles part without headers key."""
        part = {"mimeType": "text/plain"}
        assert _get_part_header(part, "Content-ID") is None
