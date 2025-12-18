"""Tests for gdrive module helper functions."""

from __future__ import annotations

from jean_claude.gdrive import format_file


class TestFormatFile:
    """Tests for file formatting."""

    def test_basic_file(self):
        """Test formatting a basic file."""
        f = {
            "id": "file123abc",
            "name": "document.pdf",
            "mimeType": "application/pdf",
        }
        result = format_file(f)
        assert "file123abc" in result
        assert "document.pdf" in result

    def test_folder(self):
        """Test formatting a folder (adds trailing slash)."""
        f = {
            "id": "folder456",
            "name": "My Folder",
            "mimeType": "application/vnd.google-apps.folder",
        }
        result = format_file(f)
        assert "My Folder/" in result

    def test_file_with_size(self):
        """Test formatting file with size."""
        f = {
            "id": "file789",
            "name": "image.png",
            "mimeType": "image/png",
            "size": "1048576",
        }
        result = format_file(f)
        assert "1,048,576 bytes" in result

    def test_file_with_modified_time(self):
        """Test formatting file with modified time."""
        f = {
            "id": "file101",
            "name": "report.docx",
            "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "modifiedTime": "2024-01-15T10:30:00.000Z",
        }
        result = format_file(f)
        assert "Modified: 2024-01-15" in result

    def test_file_with_link(self):
        """Test formatting file with web view link."""
        f = {
            "id": "file102",
            "name": "spreadsheet.xlsx",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "webViewLink": "https://docs.google.com/spreadsheets/d/file102/edit",
        }
        result = format_file(f)
        assert "Link:" in result
        assert "https://docs.google.com" in result

    def test_file_all_fields(self):
        """Test formatting file with all optional fields."""
        f = {
            "id": "file103",
            "name": "complete.txt",
            "mimeType": "text/plain",
            "size": "256",
            "modifiedTime": "2024-06-20T15:45:00.000Z",
            "webViewLink": "https://drive.google.com/file/d/file103/view",
        }
        result = format_file(f)
        assert "file103" in result
        assert "complete.txt" in result
        assert "256 bytes" in result
        assert "2024-06-20" in result
        assert "https://drive.google.com" in result

    def test_google_doc(self):
        """Test formatting a Google Doc (no size)."""
        f = {
            "id": "doc123",
            "name": "Meeting Notes",
            "mimeType": "application/vnd.google-apps.document",
        }
        result = format_file(f)
        assert "Meeting Notes" in result
        # Google Docs don't have a size, so no "bytes" should appear
        assert "bytes" not in result
