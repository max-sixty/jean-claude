"""Tests for gcal module helper functions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import click
import pytest

from jean_claude.gcal import (
    CalendarErrorHandlingGroup,
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
            parse_datetime("January 15, 2024")
        assert "Cannot parse datetime" in str(exc_info.value)


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
