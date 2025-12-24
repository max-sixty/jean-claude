"""Google Calendar CLI - list, create, and search events."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import click

from .auth import build_service


# Auto-detect timezone from system
def _get_local_timezone() -> str:
    """Get local timezone in IANA format."""
    # Try reading from macOS symlink (most reliable)
    try:
        tz_link = Path("/etc/localtime")
        if tz_link.is_symlink():
            target = str(tz_link.readlink())  # readlink, not resolve
            parts = target.split("/")
            if "zoneinfo" in parts:
                idx = parts.index("zoneinfo")
                return "/".join(parts[idx + 1 :])
    except Exception:
        pass
    # Fallback with warning
    click.echo(
        "Warning: Could not detect timezone, using America/Los_Angeles", err=True
    )
    return "America/Los_Angeles"


TIMEZONE = _get_local_timezone()
LOCAL_TZ = ZoneInfo(TIMEZONE)


def get_calendar():
    return build_service("calendar", "v3")


def parse_datetime(s: str) -> datetime:
    """Parse datetime from various formats."""
    for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M", "%Y/%m/%d"]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise click.BadParameter(f"Cannot parse datetime: {s}")


@click.group()
def cli():
    """Google Calendar CLI - list, create, and search events."""
    pass


@cli.command("list")
@click.option("--days", default=1, help="Number of days to show (default: 1)")
@click.option("--from", "from_date", help="Start date (YYYY-MM-DD)")
@click.option("--to", "to_date", help="End date (YYYY-MM-DD)")
def list_events(days: int, from_date: str, to_date: str):
    """List calendar events. Returns JSON array of events."""
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
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    click.echo(json.dumps(result.get("items", []), indent=2))


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
    start_dt = parse_datetime(start)

    if all_day:
        # All-day events use date strings, not datetime
        start_date = start_dt.strftime("%Y-%m-%d")
        if end:
            end_dt = parse_datetime(end)
            # All-day end date is exclusive, so add 1 day
            end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        elif duration:
            end_date = (start_dt + timedelta(days=duration)).strftime("%Y-%m-%d")
        else:
            # Default: 1-day event (end is exclusive)
            end_date = (start_dt + timedelta(days=1)).strftime("%Y-%m-%d")

        event_body = {
            "summary": summary,
            "start": {"date": start_date},
            "end": {"date": end_date},
        }
    else:
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
    click.echo(f"Event created: {result['id']}")
    click.echo(f"View: {result.get('htmlLink', '')}")


@cli.command()
@click.argument("query")
@click.option("--days", default=30, help="Days to search (default: 30)")
def search(query: str, days: int):
    """Search calendar events. Returns JSON array of matching events.

    QUERY: Text to search for in event titles/descriptions
    """
    time_min = datetime.now(LOCAL_TZ)
    time_max = time_min + timedelta(days=days)

    service = get_calendar()
    result = (
        service.events()
        .list(
            calendarId="primary",
            timeMin=time_min.isoformat(),
            timeMax=time_max.isoformat(),
            q=query,
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    click.echo(json.dumps(result.get("items", []), indent=2))


@cli.command()
@click.option(
    "--days", type=int, help="Limit to events within N days (default: no limit)"
)
def invitations(days: int | None):
    """List pending calendar invitations. Returns JSON array.

    Shows all future events where you are an attendee and haven't responded yet.
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

    click.echo(json.dumps(pending, indent=2))


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
    from googleapiclient.errors import HttpError

    if not response:
        raise click.UsageError("Must specify --accept, --decline, or --tentative")

    service = get_calendar()

    # Get the event
    try:
        event = service.events().get(calendarId="primary", eventId=event_id).execute()
    except HttpError as e:
        if e.resp.status == 404:
            click.echo(f"Error: Event not found: {event_id}", err=True)
        else:
            click.echo(f"Error: {e.reason}", err=True)
        raise SystemExit(1)

    # Find the user's attendee entry and update their response
    attendees = event.get("attendees", [])
    if not attendees:
        click.echo(
            "Error: This event has no attendees. You can only respond to invitations.",
            err=True,
        )
        raise SystemExit(1)

    user_found = False
    for attendee in attendees:
        if attendee.get("self"):
            attendee["responseStatus"] = response
            user_found = True
            break

    if not user_found:
        click.echo("Error: You are not an attendee of this event.", err=True)
        raise SystemExit(1)

    # Update the event with new response status
    send_updates = "all" if notify else "none"
    try:
        service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body={"attendees": attendees},
            sendUpdates=send_updates,
        ).execute()
    except HttpError as e:
        click.echo(f"Error updating response: {e.reason}", err=True)
        raise SystemExit(1)

    response_text = {
        "accepted": "accepted",
        "declined": "declined",
        "tentative": "tentatively accepted",
    }
    click.echo(
        f"Invitation {response_text[response]}: {event.get('summary', '(no title)')}"
    )
    if notify:
        click.echo("Organizer has been notified.")


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
    click.echo(f"Event deleted: {event_id}")
    if notify:
        click.echo("Cancellation notifications sent to attendees.")


@cli.command()
@click.argument("event_id")
@click.option("--summary", help="New event title")
@click.option("--start", help="New start time (YYYY-MM-DD HH:MM)")
@click.option("--end", help="New end time (YYYY-MM-DD HH:MM)")
@click.option(
    "--duration", type=int, help="New duration in minutes (alternative to --end)"
)
@click.option("--location", help="New location")
@click.option("--description", help="New description")
@click.option("--notify", is_flag=True, help="Send update emails to attendees")
def update(
    event_id: str,
    summary: str,
    start: str,
    end: str,
    duration: int,
    location: str,
    description: str,
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

    if start:
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

    click.echo(f"Event updated: {result['id']}")
    click.echo(f"View: {result.get('htmlLink', '')}")
    if notify:
        click.echo("Update notifications sent to attendees.")
