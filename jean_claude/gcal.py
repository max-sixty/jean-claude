"""Google Calendar CLI - list, create, and search events."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import click

from .auth import build_service
from .logging import JeanClaudeError, get_logger
from .pagination import paginated_output
from .timezone import LOCAL_TZ, TIMEZONE

logger = get_logger(__name__)


def get_calendar():
    return build_service("calendar", "v3")


def get_event_start(event: dict) -> str:
    """Get the start time of an event as ISO string (dateTime or date)."""
    start = event.get("start", {})
    return start.get("dateTime", start.get("date", ""))


def parse_datetime(s: str) -> datetime:
    """Parse datetime from various formats."""
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise click.BadParameter(f"Cannot parse datetime: {s}")


def calculate_all_day_dates(
    start: str, end: str | None, duration: int | None
) -> tuple[str, str]:
    """Calculate start/end dates for all-day events.

    Returns (start_date, end_date) as YYYY-MM-DD strings.
    Note: end_date is exclusive per Google Calendar API.
    """
    start_dt = parse_datetime(start)
    start_date = start_dt.strftime("%Y-%m-%d")
    if end:
        end_dt = parse_datetime(end)
        end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    elif duration:
        end_date = (start_dt + timedelta(days=duration)).strftime("%Y-%m-%d")
    else:
        end_date = (start_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return start_date, end_date


@click.group()
def cli():
    """Google Calendar CLI - list, create, and search events."""
    pass


@cli.command("list")
@click.option("--days", default=1, help="Number of days to show (default: 1)")
@click.option("--from", "from_date", help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", help="End date (YYYY-MM-DD)")
@click.option("-n", "--max-results", type=int, help="Maximum events to return per page")
@click.option("--page-token", help="Token for next page of results")
def list_events(
    days: int, from_date: str, to_date: str, max_results: int, page_token: str
):
    """List calendar events. Returns JSON with events and optional nextPageToken."""
    if from_date:
        time_min = parse_datetime(from_date).replace(tzinfo=LOCAL_TZ)
    else:
        time_min = datetime.now(LOCAL_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    if to_date:
        time_max = parse_datetime(to_date).replace(
            hour=23, minute=59, second=59, tzinfo=LOCAL_TZ
        )
    else:
        time_max = time_min + timedelta(days=days)

    service = get_calendar()
    list_kwargs = {
        "calendarId": "primary",
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if max_results:
        list_kwargs["maxResults"] = max_results
    if page_token:
        list_kwargs["pageToken"] = page_token

    result = service.events().list(**list_kwargs).execute()
    output = paginated_output(
        "events", result.get("items", []), result.get("nextPageToken")
    )
    click.echo(json.dumps(output, indent=2))


@cli.command()
@click.argument("summary")
@click.option(
    "--start", required=True, help="Start time (YYYY-MM-DD HH:MM) or date (YYYY-MM-DD)"
)
@click.option("--end", help="End time (YYYY-MM-DD HH:MM) or date (YYYY-MM-DD)")
@click.option("--duration", type=int, help="Duration in minutes (or days if --all-day)")
@click.option(
    "--all-day", "all_day", is_flag=True, help="Create all-day event (uses date only)"
)
@click.option("--location", help="Event location")
@click.option("--description", help="Event description")
@click.option("--attendees", help="Comma-separated attendee emails")
def create(
    summary: str,
    start: str,
    end: str,
    duration: int,
    all_day: bool,
    location: str,
    description: str,
    attendees: str,
):
    """Create a calendar event.

    SUMMARY: Event title

    \b
    Examples:
        jean-claude gcal create "Meeting" --start "2024-01-15 14:00"
        jean-claude gcal create "Vacation" --start 2024-01-15 --end 2024-01-20 --all-day
    """
    if all_day:
        start_date, end_date = calculate_all_day_dates(start, end, duration)
        event_body = {
            "summary": summary,
            "start": {"date": start_date},
            "end": {"date": end_date},
        }
    else:
        start_dt = parse_datetime(start)
        if end:
            end_dt = parse_datetime(end)
        elif duration:
            end_dt = start_dt + timedelta(minutes=duration)
        else:
            end_dt = start_dt + timedelta(hours=1)

        event_body = {
            "summary": summary,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }

    if location:
        event_body["location"] = location
    if description:
        event_body["description"] = description
    if attendees:
        event_body["attendees"] = [{"email": e.strip()} for e in attendees.split(",")]

    result = (
        get_calendar().events().insert(calendarId="primary", body=event_body).execute()
    )
    logger.info("Event created", event_id=result["id"])
    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("query")
@click.option("--days", default=30, help="Days to search (default: 30)")
@click.option("-n", "--max-results", type=int, help="Maximum events to return per page")
@click.option("--page-token", help="Token for next page of results")
def search(query: str, days: int, max_results: int, page_token: str):
    """Search calendar events. Returns JSON with events and optional nextPageToken.

    QUERY: Text to search for in event titles/descriptions
    """
    time_min = datetime.now(LOCAL_TZ)
    time_max = time_min + timedelta(days=days)

    service = get_calendar()
    list_kwargs = {
        "calendarId": "primary",
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "q": query,
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if max_results:
        list_kwargs["maxResults"] = max_results
    if page_token:
        list_kwargs["pageToken"] = page_token

    result = service.events().list(**list_kwargs).execute()
    output = paginated_output(
        "events", result.get("items", []), result.get("nextPageToken")
    )
    click.echo(json.dumps(output, indent=2))


@cli.command()
@click.option(
    "--days", type=int, help="Limit to events within N days (default: no limit)"
)
@click.option(
    "--expand",
    is_flag=True,
    help="Show all instances instead of collapsing recurring events",
)
def invitations(days: int | None, expand: bool):
    """List pending calendar invitations. Returns JSON array.

    Shows all future events where you are an attendee and haven't responded yet.
    Recurring events are collapsed into a single entry with instanceCount.
    Use the parent ID to respond to all instances at once.
    Use --expand to see all individual instances.
    """
    time_min = datetime.now(LOCAL_TZ)

    service = get_calendar()
    params = {
        "calendarId": "primary",
        "timeMin": time_min.isoformat(),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if days is not None:
        params["timeMax"] = (time_min + timedelta(days=days)).isoformat()

    result = service.events().list(**params).execute()

    # Filter to events where user is attendee with needsAction status
    pending = []
    for event in result.get("items", []):
        attendees = event.get("attendees", [])
        for attendee in attendees:
            if attendee.get("self") and attendee.get("responseStatus") == "needsAction":
                pending.append(event)
                break

    # If --expand, return all instances without collapsing
    if expand:
        click.echo(json.dumps(pending, indent=2))
        return

    # Collapse recurring events into single entries
    recurring_groups: dict[str, list[dict]] = {}
    standalone = []

    for event in pending:
        parent_id = event.get("recurringEventId")
        if parent_id:
            if parent_id not in recurring_groups:
                recurring_groups[parent_id] = []
            recurring_groups[parent_id].append(event)
        else:
            standalone.append(event)

    # Build output: standalone events + collapsed recurring series
    output = []

    # Add standalone events (sorted by start time, which they already are)
    for event in standalone:
        modified = event.copy()
        modified["recurring"] = False
        output.append(modified)

    # Add collapsed recurring series
    for parent_id, instances in recurring_groups.items():
        # Use first instance as template, but replace ID with parent ID
        first = instances[0].copy()
        first["id"] = parent_id
        first["recurring"] = True
        first["instanceCount"] = len(instances)
        # Remove instance-specific fields
        first.pop("recurringEventId", None)
        first.pop("originalStartTime", None)
        output.append(first)

    # Sort by start time
    output.sort(key=get_event_start)

    click.echo(json.dumps(output, indent=2))


@cli.command()
@click.argument("event_id")
@click.option(
    "--accept", "response", flag_value="accepted", help="Accept the invitation"
)
@click.option(
    "--decline", "response", flag_value="declined", help="Decline the invitation"
)
@click.option(
    "--tentative", "response", flag_value="tentative", help="Tentatively accept"
)
@click.option(
    "--notify/--no-notify", default=True, help="Notify organizer (default: notify)"
)
def respond(event_id: str, response: str, notify: bool):
    """Respond to a calendar invitation.

    EVENT_ID: The event ID (from invitations or list output)

    \b
    Examples:
        jean-claude gcal respond EVENT_ID --accept
        jean-claude gcal respond EVENT_ID --decline --no-notify
        jean-claude gcal respond EVENT_ID --tentative
    """
    if not response:
        raise click.UsageError("Must specify --accept, --decline, or --tentative")

    service = get_calendar()

    # Get the event
    event = service.events().get(calendarId="primary", eventId=event_id).execute()

    # Find the user's attendee entry and update their response
    attendees = event.get("attendees", [])
    if not attendees:
        raise JeanClaudeError(
            "This event has no attendees. You can only respond to invitations."
        )

    user_found = False
    for attendee in attendees:
        if attendee.get("self"):
            attendee["responseStatus"] = response
            user_found = True
            break

    if not user_found:
        raise JeanClaudeError("You are not an attendee of this event.")

    # Update the event with new response status
    send_updates = "all" if notify else "none"
    service.events().patch(
        calendarId="primary",
        eventId=event_id,
        body={"attendees": attendees},
        sendUpdates=send_updates,
    ).execute()

    logger.info(
        "Invitation response sent",
        event_id=event_id,
        response=response,
        notified=notify,
    )
    click.echo(
        json.dumps(
            {"eventId": event_id, "response": response, "notified": notify}, indent=2
        )
    )


@cli.command()
@click.argument("event_id")
@click.option("--notify", is_flag=True, help="Send cancellation emails to attendees")
def delete(event_id: str, notify: bool):
    """Delete/cancel a calendar event.

    EVENT_ID: The event ID (from list or search output)
    """
    send_updates = "all" if notify else "none"
    get_calendar().events().delete(
        calendarId="primary", eventId=event_id, sendUpdates=send_updates
    ).execute()
    logger.info("Event deleted", event_id=event_id, notified=notify)
    click.echo(
        json.dumps({"eventId": event_id, "deleted": True, "notified": notify}, indent=2)
    )


@cli.command()
@click.argument("event_id")
@click.option("--summary", help="New event title")
@click.option("--start", help="New start time (YYYY-MM-DD HH:MM) or date (YYYY-MM-DD)")
@click.option("--end", help="New end time (YYYY-MM-DD HH:MM) or date (YYYY-MM-DD)")
@click.option(
    "--duration", type=int, help="New duration in minutes (or days if --all-day)"
)
@click.option(
    "--all-day",
    "all_day",
    is_flag=True,
    help="Make this an all-day event (uses date only)",
)
@click.option("--location", help="New location")
@click.option("--description", help="New description")
@click.option("--attendees", help="Comma-separated attendee emails (replaces existing)")
@click.option("--notify", is_flag=True, help="Send update emails to attendees")
def update(
    event_id: str,
    summary: str,
    start: str,
    end: str,
    duration: int,
    all_day: bool,
    location: str,
    description: str,
    attendees: str,
    notify: bool,
):
    """Update/modify an existing calendar event.

    EVENT_ID: The event ID (from list or search output)

    Only specified fields are updated; others remain unchanged.
    """
    service = get_calendar()

    # Get existing event
    event = service.events().get(calendarId="primary", eventId=event_id).execute()

    # Update only provided fields
    if summary:
        event["summary"] = summary
    if location:
        event["location"] = location
    if description:
        event["description"] = description
    if attendees:
        event["attendees"] = [{"email": e.strip()} for e in attendees.split(",")]

    if all_day:
        if not start:
            raise click.UsageError("--all-day requires --start to specify the date")
        start_date, end_date = calculate_all_day_dates(start, end, duration)
        event["start"] = {"date": start_date}
        event["end"] = {"date": end_date}
    elif start:
        start_dt = parse_datetime(start)
        event["start"] = {"dateTime": start_dt.isoformat(), "timeZone": TIMEZONE}

        if end:
            end_dt = parse_datetime(end)
        elif duration:
            end_dt = start_dt + timedelta(minutes=duration)
        else:
            # Keep same duration as before
            old_start = event.get("start", {}).get("dateTime", "")
            old_end = event.get("end", {}).get("dateTime", "")
            if old_start and old_end:
                old_duration = datetime.fromisoformat(
                    old_end.replace("Z", "+00:00")
                ) - datetime.fromisoformat(old_start.replace("Z", "+00:00"))
                end_dt = start_dt + old_duration
            else:
                end_dt = start_dt + timedelta(hours=1)

        event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE}
    elif end:
        end_dt = parse_datetime(end)
        event["end"] = {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE}

    send_updates = "all" if notify else "none"
    result = (
        service.events()
        .update(
            calendarId="primary", eventId=event_id, body=event, sendUpdates=send_updates
        )
        .execute()
    )

    logger.info("Event updated", event_id=result["id"], notified=notify)
    click.echo(json.dumps(result, indent=2))
