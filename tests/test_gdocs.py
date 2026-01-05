"""Tests for gdocs module."""

from unittest.mock import MagicMock

import pytest

from jean_claude.gdocs import DocsErrorHandlingGroup, _get_end_index


class TestGetEndIndex:
    """Tests for _get_end_index function."""

    def test_empty_document(self):
        """Empty document returns index 1."""
        doc = {"body": {"content": []}}
        assert _get_end_index(doc) == 1

    def test_single_element(self):
        """Single element returns its endIndex - 1."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}}
                ]
            }
        }
        assert _get_end_index(doc) == 49

    def test_multiple_elements(self):
        """Multiple elements returns last element's endIndex - 1."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}},
                    {"startIndex": 50, "endIndex": 100, "paragraph": {"elements": []}},
                ]
            }
        }
        assert _get_end_index(doc) == 99

    def test_missing_body(self):
        """Document without body returns 1."""
        doc = {}
        assert _get_end_index(doc) == 1

    def test_element_without_endindex(self):
        """Element without endIndex is skipped, uses previous element."""
        doc = {
            "body": {
                "content": [
                    {"startIndex": 1, "endIndex": 50, "paragraph": {"elements": []}},
                    {"sectionBreak": {"sectionStyle": {}}},  # No endIndex
                ]
            }
        }
        assert _get_end_index(doc) == 49

    def test_all_elements_missing_endindex(self):
        """All elements missing endIndex returns 1."""
        doc = {
            "body": {
                "content": [
                    {"sectionBreak": {"sectionStyle": {}}},
                ]
            }
        }
        assert _get_end_index(doc) == 1


class TestDocsErrorHandling:
    """Tests for Docs-specific HTTP error handling."""

    @pytest.fixture
    def handler(self):
        """Create a DocsErrorHandlingGroup instance for testing."""
        return DocsErrorHandlingGroup()

    def test_document_not_found(self, handler):
        """404 for document shows document ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://docs.googleapis.com/v1/documents/1abc123xyz"

        msg = handler._http_error_message(error)
        assert "Document not found: 1abc123xyz" in msg
        assert "jean-claude gdrive search" in msg

    def test_non_404_falls_through(self, handler):
        """Non-404 errors use base class handling."""
        error = MagicMock()
        error.resp.status = 403
        error._get_reason.return_value = "Forbidden"
        error.__str__ = lambda self: "403 Forbidden"
        error.uri = "https://docs.googleapis.com/v1/documents"

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
