"""Tests for gmail module helper functions."""

from __future__ import annotations

from jean_claude.gmail import _strip_html, decode_body, get_header


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


class TestGetHeader:
    """Tests for header extraction."""

    def test_existing_header(self):
        """Test extracting an existing header."""
        headers = [
            {"name": "From", "value": "alice@example.com"},
            {"name": "To", "value": "bob@example.com"},
            {"name": "Subject", "value": "Test"},
        ]
        assert get_header(headers, "From") == "alice@example.com"
        assert get_header(headers, "Subject") == "Test"

    def test_case_insensitive(self):
        """Test that header lookup is case-insensitive."""
        headers = [{"name": "Content-Type", "value": "text/plain"}]
        assert get_header(headers, "content-type") == "text/plain"
        assert get_header(headers, "CONTENT-TYPE") == "text/plain"

    def test_missing_header(self):
        """Test that missing headers return empty string."""
        headers = [{"name": "From", "value": "alice@example.com"}]
        assert get_header(headers, "Cc") == ""

    def test_empty_headers(self):
        """Test with empty headers list."""
        assert get_header([], "From") == ""


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
