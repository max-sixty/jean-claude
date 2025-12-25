"""Tests for reminders module helper functions."""

from __future__ import annotations

import pytest

from jean_claude.logging import JeanClaudeError
from jean_claude.reminders import (
    APPLESCRIPT_TO_PRIORITY,
    PRIORITY_TO_APPLESCRIPT,
    format_applescript_date,
    parse_datetime,
)


class TestParseDateTime:
    """Tests for datetime parsing."""

    def test_date_with_time(self):
        """Test YYYY-MM-DD HH:MM format."""
        result = parse_datetime("2025-12-27 14:30")
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 27
        assert result.hour == 14
        assert result.minute == 30

    def test_date_only_defaults_to_9am(self):
        """Test YYYY-MM-DD format defaults to 9:00 AM."""
        result = parse_datetime("2025-12-27")
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 27
        assert result.hour == 9
        assert result.minute == 0

    def test_midnight(self):
        """Test parsing midnight time."""
        result = parse_datetime("2025-01-01 00:00")
        assert result.hour == 0
        assert result.minute == 0

    def test_end_of_day(self):
        """Test parsing 23:59."""
        result = parse_datetime("2025-01-01 23:59")
        assert result.hour == 23
        assert result.minute == 59

    def test_invalid_format_raises_error(self):
        """Test that invalid formats raise JeanClaudeError."""
        with pytest.raises(JeanClaudeError) as exc_info:
            parse_datetime("December 27, 2025")
        assert "Invalid date format" in str(exc_info.value)
        assert "YYYY-MM-DD" in str(exc_info.value)

    def test_invalid_date_raises_error(self):
        """Test that invalid dates raise JeanClaudeError."""
        with pytest.raises(JeanClaudeError):
            parse_datetime("2025-13-01")  # Invalid month

    def test_slash_format_not_supported(self):
        """Test that slash format is not supported (unlike gcal)."""
        with pytest.raises(JeanClaudeError):
            parse_datetime("2025/12/27")


class TestFormatApplescriptDate:
    """Tests for AppleScript date formatting."""

    def test_format_date(self):
        """Test formatting a datetime for AppleScript."""
        from datetime import datetime

        dt = datetime(2025, 12, 27, 14, 30, 0)
        result = format_applescript_date(dt)
        # Format: "Saturday, December 27, 2025 at 02:30:00 PM"
        assert "December" in result
        assert "27" in result
        assert "2025" in result
        assert "02:30:00 PM" in result

    def test_format_morning_time(self):
        """Test formatting AM time."""
        from datetime import datetime

        dt = datetime(2025, 1, 15, 9, 0, 0)
        result = format_applescript_date(dt)
        assert "09:00:00 AM" in result
        assert "January" in result
        assert "15" in result

    def test_format_midnight(self):
        """Test formatting midnight."""
        from datetime import datetime

        dt = datetime(2025, 1, 1, 0, 0, 0)
        result = format_applescript_date(dt)
        assert "12:00:00 AM" in result

    def test_format_noon(self):
        """Test formatting noon."""
        from datetime import datetime

        dt = datetime(2025, 1, 1, 12, 0, 0)
        result = format_applescript_date(dt)
        assert "12:00:00 PM" in result


class TestPriorityMappings:
    """Tests for priority constant mappings."""

    def test_priority_to_applescript(self):
        """Test priority name to AppleScript value mapping."""
        assert PRIORITY_TO_APPLESCRIPT["high"] == 1
        assert PRIORITY_TO_APPLESCRIPT["medium"] == 5
        assert PRIORITY_TO_APPLESCRIPT["low"] == 9

    def test_applescript_to_priority(self):
        """Test AppleScript value to priority name mapping."""
        assert APPLESCRIPT_TO_PRIORITY[1] == "high"
        assert APPLESCRIPT_TO_PRIORITY[5] == "medium"
        assert APPLESCRIPT_TO_PRIORITY[9] == "low"

    def test_mappings_are_inverse(self):
        """Test that the mappings are inverses of each other."""
        for name, value in PRIORITY_TO_APPLESCRIPT.items():
            assert APPLESCRIPT_TO_PRIORITY[value] == name
