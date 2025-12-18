"""Tests for gcal module helper functions."""

from __future__ import annotations

import click
import pytest

from jean_claude.gcal import format_event, parse_datetime


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


class TestFormatEvent:
    """Tests for event formatting."""

    def test_basic_event(self):
        """Test formatting a basic event."""
        event = {
            "id": "event123",
            "summary": "Team Meeting",
            "start": {"dateTime": "2024-01-15T14:00:00-08:00"},
            "end": {"dateTime": "2024-01-15T15:00:00-08:00"},
        }
        result = format_event(event)
        assert "event123" in result
        assert "Team Meeting" in result
        assert "2024-01-15 14:00" in result
        assert "15:00" in result

    def test_all_day_event(self):
        """Test formatting an all-day event."""
        event = {
            "id": "event456",
            "summary": "Holiday",
            "start": {"date": "2024-01-15"},
            "end": {"date": "2024-01-16"},
        }
        result = format_event(event)
        assert "event456" in result
        assert "Holiday" in result
        assert "2024-01-15" in result

    def test_event_with_location(self):
        """Test formatting event with location."""
        event = {
            "id": "event789",
            "summary": "Offsite",
            "start": {"dateTime": "2024-01-15T09:00:00Z"},
            "end": {"dateTime": "2024-01-15T17:00:00Z"},
            "location": "Conference Center, 123 Main St",
        }
        result = format_event(event)
        assert "Conference Center" in result
        assert "Location:" in result

    def test_event_with_attendees(self):
        """Test formatting event with attendees."""
        event = {
            "id": "event101",
            "summary": "1:1",
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T10:30:00Z"},
            "attendees": [
                {"email": "alice@example.com"},
                {"email": "bob@example.com"},
            ],
        }
        result = format_event(event)
        assert "alice@example.com" in result
        assert "bob@example.com" in result
        assert "Attendees:" in result

    def test_event_with_many_attendees(self):
        """Test that many attendees are truncated."""
        event = {
            "id": "event102",
            "summary": "All-Hands",
            "start": {"dateTime": "2024-01-15T14:00:00Z"},
            "end": {"dateTime": "2024-01-15T15:00:00Z"},
            "attendees": [{"email": f"user{i}@example.com"} for i in range(10)],
        }
        result = format_event(event)
        assert "...and 5 more" in result

    def test_event_with_description(self):
        """Test formatting event with description."""
        event = {
            "id": "event103",
            "summary": "Planning",
            "start": {"dateTime": "2024-01-15T11:00:00Z"},
            "end": {"dateTime": "2024-01-15T12:00:00Z"},
            "description": "Quarterly planning session",
        }
        result = format_event(event)
        assert "Description:" in result
        assert "planning session" in result

    def test_event_with_long_description(self):
        """Test that long descriptions are truncated."""
        event = {
            "id": "event104",
            "summary": "Review",
            "start": {"dateTime": "2024-01-15T13:00:00Z"},
            "end": {"dateTime": "2024-01-15T14:00:00Z"},
            "description": "x" * 200,
        }
        result = format_event(event)
        assert "..." in result

    def test_event_no_title(self):
        """Test formatting event without title."""
        event = {
            "id": "event105",
            "start": {"dateTime": "2024-01-15T15:00:00Z"},
            "end": {"dateTime": "2024-01-15T16:00:00Z"},
        }
        result = format_event(event)
        assert "(no title)" in result
