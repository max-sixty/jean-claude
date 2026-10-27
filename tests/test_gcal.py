"""Tests for gcal module helper functions."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from jean_claude.gcal import (
    CalendarErrorHandlingGroup,
    _events_overlap,
    _parse_event_times,
    calendar_display_name,
    cli,
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

    def test_iso_with_timezone_offset(self):
        """ISO datetime with explicit offset preserves timezone info."""
        result = parse_datetime("2026-04-04T17:30:00-06:00")
        assert result.year == 2026
        assert result.month == 4
        assert result.day == 4
        assert result.hour == 17
        assert result.minute == 30
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(hours=-6)

    def test_iso_with_utc_offset(self):
        """ISO datetime with +00:00 offset is timezone-aware."""
        result = parse_datetime("2026-04-04T17:30:00+00:00")
        assert result.tzinfo is not None
        assert result.utcoffset() == timedelta(0)

    def test_iso_without_offset_is_naive(self):
        """ISO datetime without offset returns naive datetime."""
        result = parse_datetime("2026-04-04T17:30:00")
        assert result.tzinfo is None
        assert result.hour == 17
        assert result.minute == 30


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
                {
                    "id": "urs@example.com",
                    "summary": "urs@example.com",
                    "summaryOverride": "Ursula Gwynn",
                },
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

    def test_resolve_by_summary_override(self, mock_service):
        """A calendar the user renamed resolves by the name they see."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("Ursula Gwynn",))
            assert result == [("urs@example.com", "Ursula Gwynn")]

    def test_exact_id_reports_override_name(self, mock_service):
        """Resolving by ID still reports the renamed display name."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            result = resolve_calendar_ids(("urs@example.com",))
            assert result == [("urs@example.com", "Ursula Gwynn")]

    def test_not_found_lists_override_names(self, mock_service):
        """The available-calendars list shows renamed names, not owner names."""
        with patch("jean_claude.gcal.get_calendar", return_value=mock_service):
            with pytest.raises(JeanClaudeError) as exc_info:
                resolve_calendar_ids(("Nonexistent",))
            assert "Ursula Gwynn" in str(exc_info.value)

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


class TestUpdateDurationPreservation:
    """Tests for update command preserving event duration."""

    def test_update_start_preserves_original_duration(self):
        """Changing only --start should keep the original event duration."""
        existing_event = {
            "summary": "Existing Meeting",
            "start": {"dateTime": "2024-01-15T10:00:00+00:00"},
            "end": {"dateTime": "2024-01-15T11:30:00+00:00"},
        }
        updated_event = {**existing_event, "id": "evt123"}

        service = MagicMock()
        service.events().get().execute.return_value = existing_event
        service.events().update().execute.return_value = updated_event

        with (
            patch("jean_claude.gcal.get_calendar", return_value=service),
            patch("jean_claude.gcal.resolve_calendar_id", return_value="primary"),
        ):
            runner = CliRunner()
            result = runner.invoke(
                cli, ["update", "evt123", "--start", "2024-01-20T14:00:00"]
            )

        assert result.exit_code == 0, result.output

        # Check the body passed to update() preserved 90-minute duration
        call_kwargs = service.events().update.call_args[1]
        body = call_kwargs["body"]
        new_start = datetime.fromisoformat(body["start"]["dateTime"])
        new_end = datetime.fromisoformat(body["end"]["dateTime"])
        assert new_end - new_start == timedelta(minutes=90)


ORGANIZER = {
    "email": "max@example.com",
    "organizer": True,
    "self": True,
    "responseStatus": "accepted",
}
GUEST = {"email": "alice@example.com", "responseStatus": "accepted"}
OPTIONAL_GUEST = {
    "email": "bob@example.com",
    "optional": True,
    "responseStatus": "tentative",
}


def _run_update(args, attendees):
    """Run `gcal update` against an event with these attendees.

    Returns the CliRunner result and the attendee list sent to the API.
    """
    service = MagicMock()
    service.events().get().execute.return_value = {
        "summary": "Dinner",
        "attendees": [dict(a) for a in attendees],
    }
    service.events().update().execute.return_value = {"id": "evt123"}

    with (
        patch("jean_claude.gcal.get_calendar", return_value=service),
        patch("jean_claude.gcal.resolve_calendar_id", return_value="primary"),
    ):
        result = CliRunner().invoke(cli, ["update", "evt123", *args])

    body = service.events().update.call_args.kwargs.get("body")
    return result, body and body.get("attendees")


class TestUpdateAttendees:
    """Attendees who stay on an event keep their response and flags."""

    def test_add_keeps_existing_entries(self):
        result, sent = _run_update(
            ["--add-attendees", "family@group.calendar.google.com"],
            [ORGANIZER, GUEST, OPTIONAL_GUEST],
        )
        assert result.exit_code == 0, result.output
        assert sent == [
            ORGANIZER,
            GUEST,
            OPTIONAL_GUEST,
            {"email": "family@group.calendar.google.com"},
        ]

    def test_add_existing_attendee_is_a_no_op(self):
        result, sent = _run_update(
            ["--add-attendees", "Bob@Example.com"], [ORGANIZER, OPTIONAL_GUEST]
        )
        assert result.exit_code == 0, result.output
        assert sent == [ORGANIZER, OPTIONAL_GUEST]

    def test_remove_keeps_the_others(self):
        result, sent = _run_update(
            ["--remove-attendees", "alice@example.com"],
            [ORGANIZER, GUEST, OPTIONAL_GUEST],
        )
        assert result.exit_code == 0, result.output
        assert sent == [ORGANIZER, OPTIONAL_GUEST]

    def test_remove_non_attendee_fails(self):
        result, sent = _run_update(
            ["--remove-attendees", "carol@example.com"], [ORGANIZER, GUEST]
        )
        assert result.exit_code != 0
        assert sent is None
        assert "carol@example.com" in result.output
        assert "alice@example.com" in result.output

    def test_replace_keeps_entries_for_people_who_stay(self):
        result, sent = _run_update(
            ["--attendees", "max@example.com, bob@example.com,dan@example.com"],
            [ORGANIZER, GUEST, OPTIONAL_GUEST],
        )
        assert result.exit_code == 0, result.output
        assert sent == [ORGANIZER, OPTIONAL_GUEST, {"email": "dan@example.com"}]

    def test_replace_with_add_is_rejected(self):
        result, sent = _run_update(
            ["--attendees", "a@example.com", "--add-attendees", "b@example.com"],
            [ORGANIZER],
        )
        assert result.exit_code != 0
        assert sent is None


class FakeCalendarService:
    """``events().list`` serving fixed pages per calendar, tokens like the API's.

    A page's token is its index, so a resumed request gets the same page back.
    """

    def __init__(self, pages_by_calendar):
        self.pages_by_calendar = pages_by_calendar

    def events(self):
        return self

    def list(self, calendarId, pageToken=None, **params):
        pages = self.pages_by_calendar[calendarId]
        index = int(pageToken or 0)
        response = {"items": pages[index]}
        if index + 1 < len(pages):
            response["nextPageToken"] = str(index + 1)
        return MagicMock(execute=MagicMock(return_value=response))


def _event(summary: str, start: str) -> dict:
    return {"id": summary, "summary": summary, "start": {"dateTime": start}}


def _invoke(pages_by_calendar, args) -> dict:
    calendar_ids = [(calendar_id, calendar_id) for calendar_id in pages_by_calendar]
    with (
        patch(
            "jean_claude.gcal.get_calendar",
            return_value=FakeCalendarService(pages_by_calendar),
        ),
        patch("jean_claude.gcal.resolve_calendar_ids", return_value=calendar_ids),
    ):
        result = CliRunner().invoke(cli, args)

    assert result.exit_code == 0, result.output
    return json.loads(result.output)


class TestListAcrossCalendars:
    """`gcal list` over several calendars returns every event, in start order."""

    # "work" is on Eastern time, so its 10:00 is "home"'s 07:00 Pacific.
    PAGES = {
        "home": [
            [_event("h1", "2025-01-01T06:00:00-08:00")],
            [_event("h2", "2025-01-01T08:00:00-08:00")],
            [_event("h3", "2025-01-01T12:00:00-08:00")],
        ],
        "work": [
            [
                _event("w1", "2025-01-01T10:00:00-05:00"),
                _event("w2", "2025-01-01T14:00:00-05:00"),
            ],
            [],
            [_event("w3", "2025-01-01T16:00:00-05:00")],
        ],
    }
    ORDER = ["h1", "w1", "h2", "w2", "h3", "w3"]

    def test_unbounded_list_fetches_every_page(self):
        output = _invoke(self.PAGES, ["list", "--from", "2025-01-01"])

        assert [e["id"] for e in output["events"]] == self.ORDER
        assert "nextPageToken" not in output

    def test_pages_resume_where_the_last_one_stopped(self):
        seen = []
        token = None
        while True:
            args = ["list", "--from", "2025-01-01", "-n", "4"]
            output = _invoke(
                self.PAGES, args + (["--page-token", token] if token else [])
            )
            assert len(output["events"]) <= 4
            seen += [e["id"] for e in output["events"]]
            token = output.get("nextPageToken")
            if not token:
                break

        assert seen == self.ORDER

    def test_resume_survives_events_leaving_the_page(self):
        """Search's window starts at "now", so an event can drop off page one."""
        pages = {
            "home": [
                [
                    _event("a", "2025-01-01T06:00:00-08:00"),
                    _event("b", "2025-01-01T07:00:00-08:00"),
                    _event("c", "2025-01-01T08:00:00-08:00"),
                ]
            ]
        }
        first = _invoke(pages, ["list", "--from", "2025-01-01", "-n", "2"])
        del pages["home"][0][0]
        rest = _invoke(
            pages,
            ["list", "--from", "2025-01-01", "--page-token", first["nextPageToken"]],
        )

        assert [e["id"] for e in first["events"] + rest["events"]] == ["a", "b", "c"]

    def test_events_name_their_calendar(self):
        output = _invoke(self.PAGES, ["list", "--from", "2025-01-01"])

        assert {e["id"]: e["calendar_id"] for e in output["events"]}["w3"] == "work"

    def test_token_for_other_calendars_is_rejected(self):
        token = _invoke(self.PAGES, ["list", "--from", "2025-01-01", "-n", "1"])[
            "nextPageToken"
        ]
        with (
            patch(
                "jean_claude.gcal.get_calendar",
                return_value=FakeCalendarService(self.PAGES),
            ),
            patch(
                "jean_claude.gcal.resolve_calendar_ids",
                return_value=[("home", "home")],
            ),
        ):
            result = CliRunner().invoke(
                cli, ["list", "--from", "2025-01-01", "--page-token", token]
            )

        assert result.exit_code != 0
        assert "different calendars" in result.output


class TestSearchEmptyHint:
    """Empty search results carry a hint, so they aren't read as absence."""

    def test_no_results_explains_whole_word_matching(self):
        output = _invoke({"primary": [[]]}, ["search", "irthday", "--days", "400"])

        assert output["events"] == []
        hint = output["hint"]
        assert "irthday" in hint
        assert "whole words" in hint
        assert "400 days" in hint
        assert "primary" in hint

    def test_results_carry_no_hint(self):
        event = _event("Alice Birthday", "2027-07-04T10:00:00-07:00")
        output = _invoke({"primary": [[event]]}, ["search", "birthday"])

        assert len(output["events"]) == 1
        assert "hint" not in output

    def test_empty_page_is_skipped_not_reported(self):
        """The API can return an empty page with a token; the match is on a later page."""
        event = _event("Alice Birthday", "2027-07-04T10:00:00-07:00")
        output = _invoke({"primary": [[], [event]]}, ["search", "birthday"])

        assert [e["id"] for e in output["events"]] == ["Alice Birthday"]
        assert "hint" not in output


class TestCalendarDisplayName:
    """Tests for the calendarList display-name fallback chain."""

    def test_prefers_override(self):
        cal = {"id": "x@e.com", "summary": "Owner Name", "summaryOverride": "My Name"}
        assert calendar_display_name(cal) == "My Name"

    def test_falls_back_to_summary(self):
        assert (
            calendar_display_name({"id": "x@e.com", "summary": "Owner Name"})
            == "Owner Name"
        )

    def test_falls_back_to_id(self):
        assert calendar_display_name({"id": "x@e.com"}) == "x@e.com"
