#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "click",
# ]
# ///
"""iMessage CLI - send messages and list chats via AppleScript."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import click

DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
# Apple's Cocoa epoch (2001-01-01) offset from Unix epoch (1970-01-01)
APPLE_EPOCH_OFFSET = 978307200
TEXT_TRUNCATE_LENGTH = 200


def run_applescript(script: str, *args: str) -> str:
    """Run AppleScript with optional arguments passed via 'on run argv'."""
    result = subprocess.run(
        ["osascript", "-e", script, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise click.ClickException(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def get_db_connection() -> sqlite3.Connection:
    """Get a read-only connection to the Messages database."""
    if not DB_PATH.exists():
        raise click.ClickException(f"Messages database not found at {DB_PATH}")
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        return conn
    except sqlite3.OperationalError as e:
        if "unable to open" in str(e):
            raise click.ClickException(
                "Cannot access Messages database. Grant Full Disk Access to your terminal:\n"
                "  System Preferences > Privacy & Security > Full Disk Access\n"
                "  Then add and enable your terminal app (Terminal, iTerm2, Ghostty, etc.)"
            )
        raise


def truncate_text(text: str | None) -> str:
    """Truncate text for display."""
    if not text:
        return "(no text)"
    if len(text) > TEXT_TRUNCATE_LENGTH:
        return text[:TEXT_TRUNCATE_LENGTH] + "..."
    return text


def format_message_row(date: str, sender: str, text: str, is_from_me: bool = False) -> None:
    """Format and print a message row."""
    if is_from_me:
        click.echo(f"{click.style(date, dim=True)} {click.style('me', fg='blue', bold=True)}")
    else:
        click.echo(f"{click.style(date, dim=True)} {click.style(sender, bold=True)}")
    click.echo(f"  {truncate_text(text)}")
    click.echo()


def _extract_identifier(chat_id: str) -> str:
    """Extract human-readable identifier from chat ID."""
    parts = chat_id.split(";")
    if len(parts) >= 3:
        return parts[-1]
    return chat_id


@click.group()
def cli():
    """iMessage CLI - send messages and list chats.

    Send via AppleScript (always works). Reading message history requires
    Full Disk Access for the terminal app to query ~/Library/Messages/chat.db.
    """


@cli.command()
@click.argument("recipient")
@click.argument("message")
def send(recipient: str, message: str):
    """Send an iMessage to a phone number or chat ID.

    RECIPIENT: Phone number (+1234567890) or chat ID (any;+;chat123...)
    MESSAGE: The message text to send

    Examples:
        imessage.py send "+12025551234" "Hello!"
        imessage.py send "any;+;chat123456789" "Hello group!"
    """
    if recipient.startswith("any;"):
        # Chat ID - send directly to chat
        script = '''on run {chatId, msg}
  tell application "Messages"
    set targetChat to chat id chatId
    send msg to targetChat
  end tell
end run'''
    else:
        # Phone number - use buddy
        script = '''on run {phoneNumber, msg}
  tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy phoneNumber of targetService
    send msg to targetBuddy
  end tell
end run'''

    run_applescript(script, recipient, message)
    click.echo(f"Sent to {recipient}")


@cli.command()
@click.argument("recipient")
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
def send_file(recipient: str, file_path: Path):
    """Send a file attachment via iMessage.

    RECIPIENT: Phone number (+1234567890) or chat ID (any;+;chat123...)
    FILE_PATH: Path to file to send

    Examples:
        imessage.py send-file "+12025551234" ./document.pdf
        imessage.py send-file "any;+;chat123456789" ./photo.jpg
    """
    abs_path = str(file_path.resolve())

    if recipient.startswith("any;"):
        script = '''on run {chatId, filePath}
  tell application "Messages"
    set targetChat to chat id chatId
    set theFile to POSIX file filePath
    send theFile to targetChat
  end tell
end run'''
    else:
        script = '''on run {phoneNumber, filePath}
  tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy phoneNumber of targetService
    set theFile to POSIX file filePath
    send theFile to targetBuddy
  end tell
end run'''

    run_applescript(script, recipient, abs_path)
    click.echo(f"Sent {file_path.name} to {recipient}")


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to list")
def chats(max_results: int):
    """List available iMessage chats.

    Shows chat name (if any) and chat ID. Use chat ID to send to groups.

    Example:
        imessage.py chats
    """
    script = '''tell application "Messages"
  set chatInfo to {}
  repeat with c in chats
    try
      set chatName to name of c
      if chatName is missing value then
        set chatName to ""
      end if
      set end of chatInfo to chatName & "||" & (id of c as text)
    end try
  end repeat
  return chatInfo
end tell'''

    output = run_applescript(script)
    if not output:
        click.echo("No chats found.", err=True)
        return

    items = output.split(", ")
    displayed = 0

    for item in items:
        if displayed >= max_results:
            break
        if "||" not in item:
            continue

        name, chat_id = item.split("||", 1)
        name = name.strip()
        chat_id = chat_id.strip()

        if name:
            click.echo(f"{click.style(name, bold=True)}")
            click.echo(f"  {click.style(chat_id, dim=True)}")
        else:
            identifier = _extract_identifier(chat_id)
            click.echo(f"{identifier}")
            click.echo(f"  {click.style(chat_id, dim=True)}")

        displayed += 1
        click.echo()


@cli.command()
@click.argument("chat_id")
def participants(chat_id: str):
    """List participants of a group chat.

    CHAT_ID: The chat ID (e.g., any;+;chat123456789)

    Example:
        imessage.py participants "any;+;chat123456789"
    """
    script = '''on run {chatId}
  tell application "Messages"
    set c to chat id chatId
    set pList to {}
    repeat with p in participants of c
      try
        set pName to full name of p
        set pHandle to handle of p
        if pName is missing value then
          set end of pList to pHandle
        else
          set end of pList to pName & " (" & pHandle & ")"
        end if
      on error
        try
          set end of pList to handle of p
        end try
      end try
    end repeat
    return pList
  end tell
end run'''

    output = run_applescript(script, chat_id)
    if not output:
        click.echo("No participants found or not a group chat.", err=True)
        return

    for item in output.split(", "):
        item = item.strip()
        if item:
            click.echo(item)


@cli.command("open")
@click.argument("chat_id")
def open_chat(chat_id: str):
    """Open a chat in Messages.app (marks messages as read).

    CHAT_ID: The chat ID (e.g., any;-;+12025551234 or any;+;chat123...)

    Example:
        imessage.py open "any;-;+12025551234"
        imessage.py open "any;+;chat123456789"
    """
    script = '''on run {chatId}
  tell application "Messages"
    activate
    set targetChat to chat id chatId
  end tell
end run'''

    run_applescript(script, chat_id)
    click.echo(f"Opened chat: {chat_id}")


@cli.command()
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
def unread(max_results: int):
    """List unread messages (requires Full Disk Access).

    Shows messages that haven't been read yet, excluding messages you sent.

    Example:
        imessage.py unread
        imessage.py unread -n 50
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT
            datetime(m.date/1000000000 + {APPLE_EPOCH_OFFSET}, 'unixepoch', 'localtime') as date,
            COALESCE(h.id, c.chat_identifier, 'unknown') as sender,
            m.text,
            c.display_name
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE m.is_read = 0
          AND m.is_from_me = 0
          AND m.text IS NOT NULL
          AND m.text != ''
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (max_results,),
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        click.echo("No unread messages.", err=True)
        return

    for date, sender, text, display_name in rows:
        if display_name:
            click.echo(
                f"{click.style(date, dim=True)} {click.style(sender, bold=True)} "
                f"in {click.style(display_name, fg='cyan')}"
            )
        else:
            click.echo(f"{click.style(date, dim=True)} {click.style(sender, bold=True)}")
        click.echo(f"  {truncate_text(text)}")
        click.echo()


@cli.command()
@click.argument("query", required=False)
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
def search(query: str | None, max_results: int):
    """Search message history (requires Full Disk Access).

    Searches the local Messages database. Your terminal app must have
    Full Disk Access in System Preferences > Privacy & Security.

    QUERY: Search term (searches message text)

    Examples:
        imessage.py search "dinner plans"
        imessage.py search -n 50
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    base_query = f"""
        SELECT
            datetime(m.date/1000000000 + {APPLE_EPOCH_OFFSET}, 'unixepoch', 'localtime') as date,
            COALESCE(h.id, c.chat_identifier, 'unknown') as sender,
            m.text
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE m.text IS NOT NULL AND m.text != ''
    """

    if query:
        cursor.execute(
            base_query + " AND m.text LIKE ? ORDER BY m.date DESC LIMIT ?",
            (f"%{query}%", max_results),
        )
    else:
        cursor.execute(
            base_query + " ORDER BY m.date DESC LIMIT ?",
            (max_results,),
        )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        click.echo("No messages found.", err=True)
        return

    for date, sender, text in rows:
        format_message_row(date, sender, text)


@cli.command()
@click.argument("chat_id")
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
def history(chat_id: str, max_results: int):
    """Get message history for a specific chat (requires Full Disk Access).

    CHAT_ID: The chat ID (e.g., any;-;+12025551234 or any;+;chat123...)

    Example:
        imessage.py history "any;-;+12025551234" -n 10
    """
    # Extract chat identifier for database lookup
    # Chat IDs from AppleScript look like "any;-;+16467194457" or "any;+;chat123..."
    chat_identifier = chat_id.split(";")[-1] if ";" in chat_id else chat_id

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT
            datetime(m.date/1000000000 + {APPLE_EPOCH_OFFSET}, 'unixepoch', 'localtime') as date,
            CASE WHEN m.is_from_me = 1 THEN 'me' ELSE COALESCE(h.id, 'unknown') END as sender,
            m.text,
            m.is_from_me
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE (c.chat_identifier = ? OR h.id = ?)
          AND m.text IS NOT NULL AND m.text != ''
        ORDER BY m.date DESC
        LIMIT ?
        """,
        (chat_identifier, chat_identifier, max_results),
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        click.echo("No messages found for this chat.", err=True)
        return

    # Reverse to show oldest first
    for date, sender, text, is_from_me in reversed(rows):
        format_message_row(date, sender, text, is_from_me)


if __name__ == "__main__":
    cli()
