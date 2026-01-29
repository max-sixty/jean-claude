"""Tests for gcal module helper functions."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import click
import pytest

from jean_claude.gcal import (
    CalendarErrorHandlingGroup,
    _events_overlap,
    _parse_event_times,
    parse_datetime,
    resolve_calendar_ids,
)
from jean_claude.logging import JeanClaudeError


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
            parse_datetime("not a date at all xyz")
        assert "Cannot parse datetime" in str(exc_info.value)

    def test_relative_today(self):
        """Test 'today' parses to current date."""
        result = parse_datetime("today")
        today = datetime.now()
        assert result.year == today.year
        assert result.month == today.month
        assert result.day == today.day

    def test_relative_tomorrow(self):
        """Test 'tomorrow' parses to next day."""
        result = parse_datetime("tomorrow")
        tomorrow = datetime.now() + timedelta(days=1)
        assert result.year == tomorrow.year
        assert result.month == tomorrow.month
        assert result.day == tomorrow.day

    def test_relative_days(self):
        """Test 'in 3 days' parses correctly."""
        result = parse_datetime("in 3 days")
        expected = datetime.now() + timedelta(days=3)
        assert result.year == expected.year
        assert result.month == expected.month
        assert result.day == expected.day

    def test_relative_week(self):
        """Test '1 week' parses correctly."""
        result = parse_datetime("in 1 week")
        expected = datetime.now() + timedelta(weeks=1)
        # Allow 1 day tolerance for edge cases around midnight
        assert abs((result - expected).days) <= 1

    def test_named_day(self):
        """Test named days like 'monday' parse to future dates."""
        result = parse_datetime("monday")
        # Should be a valid datetime in the future
        assert result >= datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        assert result.weekday() == 0  # Monday is 0


class TestResolveCalendarIds:
    """Tests for calendar ID resolution."""

    @pytest.fixture
    def mock_calendars(self):
        """Mock calendar list response."""
        return {
            "items": [
                {"id": "m@example.com", "summary": "Max @ Personal", "primary": True},
                {"id": "work@example.com", "summary": "Work Calendar"},
                {"id": "family@example.com", "summary": "Family"},
            ]
        }

    @pytest.fixture
    def mock_service(self, mock_calendars):
        """Create a mock calendar service."""
        service = MagicMock()
        service.calendarList().list().execute.return_value = mock_calendars
        return service

    def test_resolve_by_name_with_at_symbol(self, mock_service):
        """Calendar names containing @ should resolve by name, not as email."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("Max @ Personal",))
            assert result == [("m@example.com", "Max @ Personal")]

    def test_resolve_by_exact_id(self, mock_service):
        """Exact calendar ID should match directly."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("work@example.com",))
            assert result == [("work@example.com", "Work Calendar")]

    def test_resolve_by_name_substring(self, mock_service):
        """Name substring should match."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("Family",))
            assert result == [("family@example.com", "Family")]

    def test_resolve_primary(self, mock_service):
        """'primary' should resolve to the primary calendar."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("primary",))
            assert result == [("m@example.com", "Max @ Personal")]

    def test_resolve_empty_defaults_to_primary(self, mock_service):
        """Empty tuple should default to primary."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(())
            assert result == [("primary", "primary")]

    def test_not_found_lists_available(self, mock_service):
        """Not found error should list available calendars."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            with pytest.raises(JeanClaudeError) as exc_info:
                resolve_calendar_ids(("Nonexistent",))
            error_msg = str(exc_info.value)
            assert "No calendar found matching 'Nonexistent'" in error_msg
            assert "Max @ Personal" in error_msg
            assert "Work Calendar" in error_msg
            assert "Family" in error_msg

    def test_ambiguous_match_lists_options(self, mock_service):
        """Ambiguous match should list all matching options."""
        # Add another calendar that matches "a"
        mock_service.calendarList().list().execute.return_value = {
            "items": [
                {"id": "a@example.com", "summary": "Calendar A"},
                {"id": "b@example.com", "summary": "Calendar AB"},
            ]
        }
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            with pytest.raises(JeanClaudeError) as exc_info:
                resolve_calendar_ids(("Calendar A",))
            error_msg = str(exc_info.value)
            assert "Multiple calendars match" in error_msg
            assert "Calendar A" in error_msg
            assert "Calendar AB" in error_msg


class TestCalendarErrorHandling:
    """Tests for calendar-specific HTTP error handling."""

    @pytest.fixture
    def handler(self):
        """Create a CalendarErrorHandlingGroup instance for testing."""
        return CalendarErrorHandlingGroup()

    def test_event_not_found(self, handler):
        """404 for event shows event ID, calendar, and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = (
            "https://www.googleapis.com/calendar/v3/calendars/primary/events/abc123"
        )

        msg = handler._http_error_message(error)
        assert "Event not found: abc123" in msg
        assert "Calendar: primary" in msg
        assert "jean-claude gcal list" in msg

    def test_event_not_found_url_encoded(self, handler):
        """404 with URL-encoded calendar ID decodes properly."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://www.googleapis.com/calendar/v3/calendars/m%40example.com/events/xyz789"

        msg = handler._http_error_message(error)
        assert "Event not found: xyz789" in msg
        assert "Calendar: m@example.com" in msg

    def test_calendar_not_found(self, handler):
        """404 for calendar (events list) shows calendar ID and tip."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        error.uri = "https://www.googleapis.com/calendar/v3/calendars/nonexistent%40example.com/events?timeMin=..."

        msg = handler._http_error_message(error)
        assert "Calendar not found: nonexistent@example.com" in msg
        assert "jean-claude gcal calendars" in msg

    def test_non_404_falls_through(self, handler):
        """Non-404 errors use base class handling."""
        error = MagicMock()
        error.resp.status = 403
        error._get_reason.return_value = "Forbidden"
        error.__str__ = lambda self: "403 Forbidden"
        error.uri = "https://www.googleapis.com/calendar/v3/calendars/primary/events"

        msg = handler._http_error_message(error)
        assert "Permission denied" in msg

    def test_404_without_uri_falls_through(self, handler):
        """404 without URI uses base class handling."""
        error = MagicMock()
        error.resp.status = 404
        error._get_reason.return_value = "Not Found"
        # No uri attribute
        del error.uri

        msg = handler._http_error_message(error)
        assert msg == "Not found: Not Found"


class TestEventConflicts:
    """Tests for conflict detection helpers."""

    def test_parse_event_times_with_datetime(self):
        """Events with dateTime should parse correctly."""
        event = {
            "start": {"dateTime": "2024-01-15T10:00:00-08:00"},
            "end": {"dateTime": "2024-01-15T11:00:00-08:00"},
        }
        start, end = _parse_event_times(event)
        assert start is not None
        assert end is not None
        assert start.hour == 10 or start.hour == 18  # Depends on TZ handling
        assert end > start

    def test_parse_event_times_all_day(self):
        """All-day events should return None."""
        event = {
            "start": {"date": "2024-01-15"},
            "end": {"date": "2024-01-16"},
        }
        start, end = _parse_event_times(event)
        assert start is None
        assert end is None

    def test_events_overlap_true(self):
        """Overlapping events should be detected."""
        event1 = {
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"},
        }
        event2 = {
            "start": {"dateTime": "2024-01-15T10:30:00Z"},
            "end": {"dateTime": "2024-01-15T11:30:00Z"},
        }
        assert _events_overlap(event1, event2) is True

    def test_events_overlap_false(self):
        """Non-overlapping events should not be flagged."""
        event1 = {
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"},
        }
        event2 = {
            "start": {"dateTime": "2024-01-15T11:00:00Z"},
            "end": {"dateTime": "2024-01-15T12:00:00Z"},
        }
        assert _events_overlap(event1, event2) is False

    def test_events_overlap_one_contains_other(self):
        """Event fully contained in another should overlap."""
        event1 = {
            "start": {"dateTime": "2024-01-15T09:00:00Z"},
            "end": {"dateTime": "2024-01-15T17:00:00Z"},
        }
        event2 = {
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"},
        }
        assert _events_overlap(event1, event2) is True

    def test_events_overlap_all_day_skipped(self):
        """All-day events should not report conflicts."""
        event1 = {
            "start": {"date": "2024-01-15"},
            "end": {"date": "2024-01-16"},
        }
        event2 = {
            "start": {"dateTime": "2024-01-15T10:00:00Z"},
            "end": {"dateTime": "2024-01-15T11:00:00Z"},
        }
        assert _events_overlap(event1, event2) is False
