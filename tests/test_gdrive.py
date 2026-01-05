"""Tests for gdrive module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jean_claude.gdrive import DriveErrorHandlingGroup


class TestDriveErrorHandling:
    """Tests for Drive-specific HTTP error handling."""

    @pytest.fixture
    def handler(self):
        """Create a DriveErrorHandlingGroup instance for testing."""
        return DriveErrorHandlingGroup()

    def test_file_not_found(self, handler):
        """404 for file shows file ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://www.googleapis.com/drive/v3/files/1abc123xyz"

        msg = handler._http_error_message(error)
        assert "File not found: 1abc123xyz" in msg
        assert "jean-claude gdrive search" in msg

    def test_non_404_falls_through(self, handler):
        """Non-404 errors use base class handling."""
        error = MagicMock()
        error.resp.status = 403
        error._get_reason.return_value = "Forbidden"
        error.__str__ = lambda self: "403 Forbidden"
        error.uri = "https://www.googleapis.com/drive/v3/files"

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
