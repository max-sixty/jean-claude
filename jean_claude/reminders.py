"""Apple Reminders CLI - create, list, and manage reminders via AppleScript."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime

import click

from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)

# AppleScript priority: 0 = none, 1 = high, 5 = medium, 9 = low
PRIORITY_TO_APPLESCRIPT = {"high": 1, "medium": 5, "low": 9}
APPLESCRIPT_TO_PRIORITY = {1: "high", 5: "medium", 9: "low"}


def run_applescript(script: str, *args: str) -> str:
    """Run AppleScript with optional arguments passed via 'on run argv'."""
    result = subprocess.run(
        ["osascript", "-e", script, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise JeanClaudeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def parse_datetime(dt_str: str) -> datetime:
    """Parse datetime string in various formats.

    Supports:
        - YYYY-MM-DD HH:MM (e.g., "2025-12-27 09:00")
        - YYYY-MM-DD (date only, defaults to 9:00 AM)
    """
    formats = [
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(dt_str, fmt)
            # If no time specified, default to 9:00 AM
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=9, minute=0)
            return dt
        except ValueError:
            continue
    raise JeanClaudeError(
        f"Invalid date format: {dt_str}\n"
        "Use: YYYY-MM-DD HH:MM (e.g., 2025-12-27 09:00) or YYYY-MM-DD"
    )


def format_applescript_date(dt: datetime) -> str:
    """Format datetime for AppleScript date constructor."""
    # AppleScript date format: "Friday, December 27, 2025 at 9:00:00 AM"
    return dt.strftime("%A, %B %d, %Y at %I:%M:%S %p")


@click.group()
def cli():
    """Apple Reminders CLI - create, list, and manage reminders.

    Uses AppleScript to interact with Reminders.app. Reminders sync across
    all your Apple devices via iCloud.
    """


@cli.command()
@click.argument("title")
@click.option("--due", help="Due date/time (YYYY-MM-DD HH:MM or YYYY-MM-DD)")
@click.option("--notes", help="Notes/description for the reminder")
@click.option("--list", "list_name", help="Reminder list name (default: first list)")
@click.option(
    "--priority",
    type=click.Choice(["low", "medium", "high"]),
    help="Priority level",
)
def create(
    title: str,
    due: str | None,
    notes: str | None,
    list_name: str | None,
    priority: str | None,
):
    """Create a new reminder.

    TITLE: The reminder title/name

    Examples:
        jean-claude reminders create "Buy groceries"
        jean-claude reminders create "Call doctor" --due "2025-12-27 14:00"
        jean-claude reminders create "Submit report" --due 2025-12-30 --list Work
        jean-claude reminders create "Important task" --priority high --notes "Don't forget!"
    """
    # Build arguments for safe AppleScript execution
    # Arguments: title, dueDate (or empty), notes (or empty), listName (or empty), priority (or 0)
    due_arg = ""
    if due:
        dt = parse_datetime(due)
        due_arg = format_applescript_date(dt)

    notes_arg = notes or ""
    list_arg = list_name or ""
    priority_arg = str(PRIORITY_TO_APPLESCRIPT.get(priority, 0)) if priority else "0"

    # Use argument passing to avoid injection
    script = """on run argv
set reminderTitle to item 1 of argv
set dueArg to item 2 of argv
set notesArg to item 3 of argv
set listArg to item 4 of argv
set priorityArg to (item 5 of argv) as integer

-- Parse date outside of Reminders context to avoid scope issues
if dueArg is not "" then
    set dueDate to date dueArg
else
    set dueDate to missing value
end if

tell application "Reminders"
    if listArg is not "" then
        set targetList to list listArg
    else
        set targetList to default list
    end if

    tell targetList
        set newReminder to make new reminder with properties {name:reminderTitle}

        if dueDate is not missing value then
            set remind me date of newReminder to dueDate
        end if

        if notesArg is not "" then
            set body of newReminder to notesArg
        end if

        if priorityArg > 0 then
            set priority of newReminder to priorityArg
        end if

        return id of newReminder
    end tell
end tell
end run"""

    reminder_id = run_applescript(
        script, title, due_arg, notes_arg, list_arg, priority_arg
    )

    output = {"id": reminder_id, "title": title}
    if due:
        output["due"] = due
    if list_name:
        output["list"] = list_name
    if notes:
        output["notes"] = notes
    if priority:
        output["priority"] = priority

    click.echo(json.dumps(output))
    logger.info(f"Created reminder: {title}", id=reminder_id)


@cli.command("list")
@click.option("--list", "list_name", help="Reminder list to show (default: all lists)")
@click.option("-n", "--max-results", default=50, help="Maximum reminders to show")
@click.option("--completed", is_flag=True, help="Show completed reminders instead")
def list_reminders(list_name: str | None, max_results: int, completed: bool):
    """List reminders.

    Shows incomplete reminders by default. Use --completed to show completed ones.

    Examples:
        jean-claude reminders list
        jean-claude reminders list --list Work
        jean-claude reminders list --completed -n 10
    """
    completed_filter = "true" if completed else "false"
    list_arg = list_name or ""

    # Use argument passing for list name
    script = f"""use framework "Foundation"
on run argv
set listArg to item 1 of argv

tell application "Reminders"
    set reminderList to current application's NSMutableArray's new()

    if listArg is not "" then
        set targetLists to {{list listArg}}
    else
        set targetLists to lists
    end if

    repeat with lst in targetLists
        repeat with r in (reminders of lst whose completed is {completed_filter})
            set reminderDict to current application's NSMutableDictionary's new()
            reminderDict's setValue:(id of r) forKey:"id"
            reminderDict's setValue:(name of r) forKey:"name"

            if listArg is "" then
                reminderDict's setValue:(name of lst) forKey:"list"
            end if

            try
                set dueDate to remind me date of r
                if dueDate is not missing value then
                    set dateFormatter to current application's NSDateFormatter's new()
                    dateFormatter's setDateFormat:"yyyy-MM-dd HH:mm"
                    set dueDateStr to (dateFormatter's stringFromDate:dueDate) as text
                    reminderDict's setValue:dueDateStr forKey:"due"
                end if
            end try

            try
                set noteText to body of r
                if noteText is not missing value then
                    reminderDict's setValue:noteText forKey:"notes"
                end if
            end try

            try
                set pri to priority of r
                if pri is 1 then
                    reminderDict's setValue:"high" forKey:"priority"
                else if pri is 5 then
                    reminderDict's setValue:"medium" forKey:"priority"
                else if pri is 9 then
                    reminderDict's setValue:"low" forKey:"priority"
                end if
            end try

            reminderList's addObject:reminderDict

            if (reminderList's |count|()) >= {max_results} then exit repeat
        end repeat
        if (reminderList's |count|()) >= {max_results} then exit repeat
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:reminderList options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text
end run"""

    output = run_applescript(script, list_arg)

    if not output or output == "[]":
        status = "completed" if completed else "incomplete"
        logger.info(f"No {status} reminders found")
        click.echo("[]")
        return

    click.echo(output)


@cli.command()
def lists():
    """List all reminder lists.

    Example:
        jean-claude reminders lists
    """
    script = """use framework "Foundation"
on run
tell application "Reminders"
    set listArray to current application's NSMutableArray's new()

    repeat with lst in lists
        set listDict to current application's NSMutableDictionary's new()
        listDict's setValue:(id of lst) forKey:"id"
        listDict's setValue:(name of lst) forKey:"name"

        -- Count incomplete reminders
        set incompleteCount to count of (reminders of lst whose completed is false)
        listDict's setValue:incompleteCount forKey:"count"

        listArray's addObject:listDict
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:listArray options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text
end run"""

    output = run_applescript(script)
    click.echo(output)


@cli.command()
@click.argument("reminder_id")
def complete(reminder_id: str):
    """Mark a reminder as completed.

    REMINDER_ID: The reminder ID (from 'reminders list' output)

    Example:
        jean-claude reminders complete "x-apple-reminder://..."
    """
    # Validate reminder ID format
    if not reminder_id.startswith("x-apple-reminder://"):
        raise JeanClaudeError(
            f"Invalid reminder ID format: {reminder_id}\n"
            "Expected: x-apple-reminder://..."
        )

    # Use argument passing to avoid injection
    script = """on run argv
set reminderId to item 1 of argv
tell application "Reminders"
    set targetReminder to reminder id reminderId
    set completed of targetReminder to true
    return name of targetReminder
end tell
end run"""

    name = run_applescript(script, reminder_id)
    click.echo(json.dumps({"id": reminder_id, "name": name, "completed": True}))
    logger.info(f"Completed: {name}")


@cli.command()
@click.argument("reminder_id")
def delete(reminder_id: str):
    """Delete a reminder.

    REMINDER_ID: The reminder ID (from 'reminders list' output)

    Example:
        jean-claude reminders delete "x-apple-reminder://..."
    """
    # Validate reminder ID format
    if not reminder_id.startswith("x-apple-reminder://"):
        raise JeanClaudeError(
            f"Invalid reminder ID format: {reminder_id}\n"
            "Expected: x-apple-reminder://..."
        )

    # Use argument passing to avoid injection
    script = """on run argv
set reminderId to item 1 of argv
tell application "Reminders"
    set targetReminder to reminder id reminderId
    set reminderName to name of targetReminder
    delete targetReminder
    return reminderName
end tell
end run"""

    name = run_applescript(script, reminder_id)
    click.echo(json.dumps({"id": reminder_id, "name": name, "deleted": True}))
    logger.info(f"Deleted: {name}")


@cli.command()
@click.argument("query")
@click.option("-n", "--max-results", default=20, help="Maximum results to return")
def search(query: str, max_results: int):
    """Search reminders by name.

    QUERY: Search term (searches reminder titles)

    Example:
        jean-claude reminders search "groceries"
    """
    # Use argument passing for query to avoid injection
    script = f"""use framework "Foundation"
on run argv
set searchQuery to item 1 of argv

tell application "Reminders"
    set reminderList to current application's NSMutableArray's new()

    repeat with lst in lists
        repeat with r in reminders of lst
            if (name of r) contains searchQuery then
                set reminderDict to current application's NSMutableDictionary's new()
                reminderDict's setValue:(id of r) forKey:"id"
                reminderDict's setValue:(name of r) forKey:"name"
                reminderDict's setValue:(name of lst) forKey:"list"
                reminderDict's setValue:(completed of r) forKey:"completed"

                try
                    set dueDate to remind me date of r
                    if dueDate is not missing value then
                        set dateFormatter to current application's NSDateFormatter's new()
                        dateFormatter's setDateFormat:"yyyy-MM-dd HH:mm"
                        set dueDateStr to (dateFormatter's stringFromDate:dueDate) as text
                        reminderDict's setValue:dueDateStr forKey:"due"
                    end if
                end try

                reminderList's addObject:reminderDict

                if (reminderList's |count|()) >= {max_results} then exit repeat
            end if
        end repeat
        if (reminderList's |count|()) >= {max_results} then exit repeat
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:reminderList options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text
end run"""

    output = run_applescript(script, query)

    if not output or output == "[]":
        logger.info(f"No reminders found matching '{query}'")
        click.echo("[]")
        return

    click.echo(output)
