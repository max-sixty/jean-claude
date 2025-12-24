"""Tests for gcal module helper functions."""

from __future__ import annotations

import click
import pytest

from jean_claude.gcal import parse_datetime


class TestParseDateTime:
    """Tests for datetime parsing."""

    def test_date_with_time(self):
        """Test YYYY-MM-DD HH:MM format."""
        result = parse_datetime("2024-01-15 14:30")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_date_only(self):
        """Test YYYY-MM-DD format."""
        result = parse_datetime("2024-01-15")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 0
        assert result.minute == 0

    def test_slash_date_with_time(self):
        """Test YYYY/MM/DD HH:MM format."""
        result = parse_datetime("2024/01/15 09:00")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15
        assert result.hour == 9
        assert result.minute == 0

    def test_slash_date_only(self):
        """Test YYYY/MM/DD format."""
        result = parse_datetime("2024/01/15")
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_invalid_format(self):
        """Test that invalid formats raise BadParameter."""
        with pytest.raises(click.BadParameter) as exc_info:
            parse_datetime("January 15, 2024")
        assert "Cannot parse datetime" in str(exc_info.value)
