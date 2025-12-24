"""Tests for gsheets module."""

import io
import json
import sys

import pytest

from jean_claude.gsheets import _normalize_range, _read_rows_from_stdin
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
