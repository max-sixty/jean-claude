"""Tests for gsheets module."""

import io
import json
import sys
from unittest.mock import MagicMock

import pytest

from jean_claude.gsheets import (
    SheetsErrorHandlingGroup,
    _column_to_index,
    _normalize_range,
    _read_rows_from_stdin,
)
from jean_claude.logging import JeanClaudeError


class TestNormalizeRange:
    """Tests for _normalize_range function."""

    def test_no_escaping_needed(self):
        """Range without escaping passes through unchanged."""
        assert _normalize_range("Sheet1!A1:B10") == "Sheet1!A1:B10"

    def test_unescape_exclamation(self):
        """Escaped exclamation mark is unescaped."""
        assert _normalize_range("Sheet1\\!A1:B10") == "Sheet1!A1:B10"

    def test_multiple_escapes(self):
        """Multiple escaped exclamation marks are all unescaped."""
        assert _normalize_range("A\\!B\\!C") == "A!B!C"

    def test_sheet_name_with_spaces(self):
        """Sheet name with spaces and escaped ! works."""
        assert _normalize_range("My Sheet\\!A1:Z100") == "My Sheet!A1:Z100"

    def test_empty_string(self):
        """Empty string returns empty string."""
        assert _normalize_range("") == ""


class TestReadRowsFromStdin:
    """Tests for _read_rows_from_stdin function."""

    def test_valid_array(self, monkeypatch):
        """Valid JSON array is parsed correctly."""
        monkeypatch.setattr(sys, "stdin", io.StringIO('[["a", 1], ["b", 2]]'))
        result = _read_rows_from_stdin()
        assert result == [["a", 1], ["b", 2]]

    def test_empty_array(self, monkeypatch):
        """Empty array is valid input."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))
        result = _read_rows_from_stdin()
        assert result == []

    def test_invalid_json(self, monkeypatch):
        """Invalid JSON raises JeanClaudeError."""
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
        with pytest.raises(JeanClaudeError, match="Invalid JSON"):
            _read_rows_from_stdin()

    def test_not_array(self, monkeypatch):
        """Non-array JSON raises JeanClaudeError."""
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"key": "value"}'))
        with pytest.raises(JeanClaudeError, match="must be a JSON array"):
            _read_rows_from_stdin()

    def test_string_instead_of_array(self, monkeypatch):
        """String JSON raises JeanClaudeError."""
        monkeypatch.setattr(sys, "stdin", io.StringIO('"just a string"'))
        with pytest.raises(JeanClaudeError, match="must be a JSON array"):
            _read_rows_from_stdin()

    def test_nested_arrays(self, monkeypatch):
        """Nested arrays work correctly."""
        data = [[["nested", "data"]], [1, 2, 3]]
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(data)))
        result = _read_rows_from_stdin()
        assert result == data


class TestColumnToIndex:
    """Tests for _column_to_index function."""

    def test_single_letter_a(self):
        """Column A is index 0."""
        assert _column_to_index("A") == 0

    def test_single_letter_b(self):
        """Column B is index 1."""
        assert _column_to_index("B") == 1

    def test_single_letter_z(self):
        """Column Z is index 25."""
        assert _column_to_index("Z") == 25

    def test_double_letter_aa(self):
        """Column AA is index 26."""
        assert _column_to_index("AA") == 26

    def test_double_letter_az(self):
        """Column AZ is index 51."""
        assert _column_to_index("AZ") == 51

    def test_double_letter_ba(self):
        """Column BA is index 52."""
        assert _column_to_index("BA") == 52

    def test_lowercase(self):
        """Lowercase letters work."""
        assert _column_to_index("a") == 0
        assert _column_to_index("aa") == 26

    def test_triple_letter_zz(self):
        """Column ZZ is index 701."""
        assert _column_to_index("ZZ") == 701

    def test_empty_string_raises(self):
        """Empty string raises JeanClaudeError."""
        with pytest.raises(JeanClaudeError, match="must be letters only"):
            _column_to_index("")

    def test_invalid_chars_raises(self):
        """Non-letter characters raise JeanClaudeError."""
        with pytest.raises(JeanClaudeError, match="must be letters only"):
            _column_to_index("A1")

    def test_special_chars_raises(self):
        """Special characters raise JeanClaudeError."""
        with pytest.raises(JeanClaudeError, match="must be letters only"):
            _column_to_index("!@#")


class TestSheetsErrorHandling:
    """Tests for Sheets-specific HTTP error handling."""

    @pytest.fixture
    def handler(self):
        """Create a SheetsErrorHandlingGroup instance for testing."""
        return SheetsErrorHandlingGroup()

    def test_spreadsheet_not_found(self, handler):
        """404 for spreadsheet shows spreadsheet ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://sheets.googleapis.com/v4/spreadsheets/1abc123xyz"

        msg = handler._http_error_message(error)
        assert "Spreadsheet not found: 1abc123xyz" in msg
        assert "jean-claude gdrive search" in msg

    def test_spreadsheet_not_found_with_range(self, handler):
        """404 for spreadsheet values shows spreadsheet ID."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = (
            "https://sheets.googleapis.com/v4/spreadsheets/1abc123xyz:batchUpdate"
        )

        msg = handler._http_error_message(error)
        assert "Spreadsheet not found: 1abc123xyz" in msg

    def test_non_404_falls_through(self, handler):
        """Non-404 errors use base class handling."""
        error = MagicMock()
        error.resp.status = 403
        error._get_reason.return_value = "Forbidden"
        error.__str__ = lambda self: "403 Forbidden"
        error.uri = "https://sheets.googleapis.com/v4/spreadsheets"

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
