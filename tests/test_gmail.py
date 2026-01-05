"""Tests for gmail module helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jean_claude.gmail import (
    GmailErrorHandlingGroup,
    _extract_attachments,
    _strip_html,
    decode_body,
    extract_attachments_from_payload,
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
