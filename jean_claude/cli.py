"""Main CLI entry point for jean-claude."""

from __future__ import annotations

import json
import sys

import click

from googleapiclient.errors import HttpError

from .auth import SCOPES_FULL, SCOPES_READONLY, TOKEN_FILE, run_auth
from .config import (
    CONFIG_FILE,
    get_config,
    is_setup_completed,
    is_signal_enabled,
    is_whatsapp_enabled,
    set_config_value,
)
from .errors import ErrorHandlingGroup
from .gcal import cli as gcal_cli
from .gdocs import cli as gdocs_cli
from .gdrive import cli as gdrive_cli
from .gmail import cli as gmail_cli
from .gsheets import cli as gsheets_cli
from .imessage import cli as imessage_cli
from .reminders import cli as reminders_cli
from .signal import cli as signal_cli
from .logging import JeanClaudeError, configure_logging, get_logger
from .whatsapp import cli as whatsapp_cli

logger = get_logger(__name__)


@click.group(cls=ErrorHandlingGroup)
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging to stderr")
@click.option(
    "--json-log",
    metavar="FILE",
    envvar="JEAN_CLAUDE_LOG",
    default="auto",
    help='JSON log file path (default: auto, "-" for stdout, "none" to disable)',
)
def cli(verbose: bool, json_log: str):
    """jean-claude: Gmail, Calendar, Drive, iMessage, WhatsApp, and Signal integration."""
    # Allow "none" to disable file logging
    log_file = None if json_log == "none" else json_log
    configure_logging(verbose=verbose, json_log=log_file)


cli.add_command(gmail_cli, name="gmail")
cli.add_command(gcal_cli, name="gcal")
cli.add_command(gdocs_cli, name="gdocs")
cli.add_command(gdrive_cli, name="gdrive")
cli.add_command(gsheets_cli, name="gsheets")
cli.add_command(imessage_cli, name="imessage")
cli.add_command(reminders_cli, name="reminders")
cli.add_command(signal_cli, name="signal")
cli.add_command(whatsapp_cli, name="whatsapp")


@cli.command()
@click.option(
    "--readonly", is_flag=True, help="Request read-only access (no send/modify)"
)
@click.option("--logout", is_flag=True, help="Remove stored credentials and log out")
def auth(readonly: bool, logout: bool):
    """Authenticate with Google APIs.

    By default, requests full access (read, send, modify). Use --readonly
    to request only read access to Gmail, Calendar, and Drive.

    Use --logout to remove stored credentials.
    """
    if logout:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()
            click.echo("Logged out. Credentials removed.")
        else:
            click.echo("Not logged in (no credentials found).")
        return
    run_auth(readonly=readonly)


@cli.command()
@click.option(
    "--json", "as_json", is_flag=True, help="Output as JSON for programmatic use"
)
def status(as_json: bool):
    """Show authentication status and API availability."""
    if as_json:
        _status_json()
    else:
        _status_human()


def _status_json():
    """Output status as JSON for programmatic use."""
    services: dict[str, dict] = {}

    # Google status
    services["google"] = _get_google_status()

    # iMessage status (macOS only)
    if sys.platform == "darwin":
        services["imessage"] = _get_imessage_status()
        services["reminders"] = _get_reminders_status()

    # WhatsApp status
    services["whatsapp"] = _get_whatsapp_status()

    # Signal status
    services["signal"] = _get_signal_status()

    result = {
        "platform": sys.platform,
        "config_exists": CONFIG_FILE.exists(),
        "setup_completed": is_setup_completed(),
        "services": services,
    }

    click.echo(json.dumps(result, indent=2))


def _status_human():
    """Output status as human-readable text."""
    # Google Workspace status
    if not TOKEN_FILE.exists():
        click.echo("Google: " + click.style("Not authenticated", fg="yellow"))
        click.echo("  Run 'jean-claude auth' to authenticate.")
    else:
        try:
            token_data = json.loads(TOKEN_FILE.read_text())
            scopes = set(token_data.get("scopes", []))
        except (json.JSONDecodeError, KeyError):
            click.echo("Google: " + click.style("Token file corrupted", fg="red"))
            click.echo(
                "  Run 'jean-claude auth --logout' then 'jean-claude auth' to fix."
            )
            scopes = None

        if scopes is not None:
            # Determine scope level and missing scopes
            missing_scopes = set(SCOPES_FULL) - scopes
            if scopes == set(SCOPES_FULL):
                scope_level = "full access"
            elif scopes == set(SCOPES_READONLY):
                scope_level = "read-only"
            elif missing_scopes:
                scope_level = "missing scopes"
            else:
                scope_level = "full access"  # Has all required + extras

            if missing_scopes:
                click.echo(
                    "Google: "
                    + click.style(f"Authenticated ({scope_level})", fg="yellow")
                )
                click.echo(
                    "  Run 'jean-claude auth' to re-authenticate with full access."
                )
                click.echo("  Missing scopes:")
                for scope in sorted(missing_scopes):
                    click.echo(f"    - {scope}")
            else:
                click.echo(
                    "Google: "
                    + click.style(f"Authenticated ({scope_level})", fg="green")
                )

            # Check API availability
            try:
                _check_google_apis()
            except Exception as e:
                click.echo(f"  Error checking APIs: {e}")

    # iMessage status (doesn't require Google auth)
    click.echo()
    _check_imessage_status()

    # Reminders status
    click.echo()
    _check_reminders_status()

    # WhatsApp status
    click.echo()
    _check_whatsapp_status()

    # Signal status
    click.echo()
    _check_signal_status()


def _get_google_status() -> dict:
    """Get Google service status as a dict."""
    if not TOKEN_FILE.exists():
        return {"enabled": True, "authenticated": False}

    try:
        token_data = json.loads(TOKEN_FILE.read_text())
        scopes = set(token_data.get("scopes", []))
    except (json.JSONDecodeError, KeyError):
        return {
            "enabled": True,
            "authenticated": False,
            "error": "Token file corrupted",
        }

    # Determine scope level and missing scopes
    missing_scopes = set(SCOPES_FULL) - scopes
    if scopes == set(SCOPES_FULL):
        scope_level = "full"
    elif scopes == set(SCOPES_READONLY):
        scope_level = "readonly"
    elif missing_scopes:
        scope_level = "missing_scopes"
    else:
        scope_level = "full"  # Has all required + extras

    result = {
        "enabled": True,
        "authenticated": True,
        "scopes": scope_level,
    }
    if missing_scopes:
        result["missing_scopes"] = sorted(missing_scopes)

    # Try to get user email
    user_email = None
    try:
        from .auth import build_service

        gmail = build_service("gmail", "v1")
        profile = gmail.users().getProfile(userId="me").execute()
        user_email = profile.get("emailAddress")
        result["user"] = user_email or "unknown"
    except Exception as e:
        result["user_fetch_error"] = str(e)

    # Get calendars list with stats
    try:
        from .auth import build_service

        cal = build_service("calendar", "v3")
        result["calendars"] = _get_calendars_list(
            cal, with_stats=True, user_email=user_email
        )
    except Exception as e:
        result["calendars_fetch_error"] = str(e)

    return result


def _get_imessage_status() -> dict:
    """Get iMessage status as a dict."""
    import sqlite3
    import subprocess
    from pathlib import Path

    result = {"enabled": True}

    # Check send capability (AppleScript/Automation permission)
    test_script = 'tell application "Messages" to get name'
    proc = subprocess.run(
        ["osascript", "-e", test_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    result["send_permission"] = proc.returncode == 0
    if not result["send_permission"]:
        error = proc.stderr.strip()
        if "not allowed" in error.lower() or "assistive" in error.lower():
            result["send_error"] = "Automation permission required"
        else:
            result["send_error"] = error

    # Check read capability (Full Disk Access to Messages database)
    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        result["read_permission"] = False
        result["read_error"] = "Messages database not found"
    else:
        try:
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                conn.execute("SELECT 1 FROM message LIMIT 1")
                result["read_permission"] = True
        except sqlite3.OperationalError as e:
            result["read_permission"] = False
            if "unable to open" in str(e):
                result["read_error"] = "Full Disk Access required"
            else:
                result["read_error"] = str(e)

    return result


def _get_reminders_status() -> dict:
    """Get Apple Reminders status as a dict."""
    import subprocess

    result = {"enabled": True}

    test_script = 'tell application "Reminders" to get name of default list'
    proc = subprocess.run(
        ["osascript", "-e", test_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    result["authenticated"] = proc.returncode == 0
    if not result["authenticated"]:
        error = proc.stderr.strip()
        if "not allowed" in error.lower() or "assistive" in error.lower():
            result["error"] = "Automation permission required"
        else:
            result["error"] = error

    return result


def _get_whatsapp_status() -> dict:
    """Get WhatsApp status as a dict."""
    from .logging import JeanClaudeError
    from .whatsapp import _get_whatsapp_cli_path, _run_whatsapp_cli

    if not is_whatsapp_enabled():
        return {"enabled": False}

    result = {"enabled": True}

    # Check if CLI binary exists
    try:
        _get_whatsapp_cli_path()
        result["cli_available"] = True
    except JeanClaudeError:
        result["cli_available"] = False
        result["error"] = "CLI binary not built"
        return result

    # Check authentication status
    try:
        status = _run_whatsapp_cli("status")
        if status and isinstance(status, dict) and status.get("authenticated"):
            result["authenticated"] = True
            result["phone"] = status.get("phone", "unknown")
        else:
            result["authenticated"] = False
    except Exception as e:
        result["authenticated"] = False
        result["error"] = str(e)

    return result


def _get_signal_status() -> dict:
    """Get Signal status as a dict."""
    from .logging import JeanClaudeError
    from .signal import _get_signal_cli_path, _run_signal_cli

    if not is_signal_enabled():
        return {"enabled": False}

    result = {"enabled": True}

    # Check if CLI binary exists
    try:
        _get_signal_cli_path()
        result["cli_available"] = True
    except JeanClaudeError:
        result["cli_available"] = False
        result["error"] = "CLI binary not built"
        return result

    # Check authentication status
    try:
        status = _run_signal_cli("status")
        if status and isinstance(status, dict) and status.get("linked"):
            result["authenticated"] = True
            result["phone"] = status.get("phone", "unknown")
        else:
            result["authenticated"] = False
    except Exception as e:
        result["authenticated"] = False
        result["error"] = str(e)

    return result


def _check_google_apis() -> None:
    """Check Google API availability and show message counts."""
    from .auth import build_service

    gmail = build_service("gmail", "v1")

    # Check Gmail API and show counts
    user_email = None
    try:
        profile = gmail.users().getProfile(userId="me").execute()
        user_email = profile.get("emailAddress")
    except HttpError as e:
        _print_api_error("Gmail", e)
    else:
        click.echo("  Gmail: " + click.style("OK", fg="green"))
        try:
            _show_gmail_counts(gmail)
        except Exception as e:
            logger.warning("Failed to fetch Gmail counts", error=str(e))

    def check_api(name: str, test_call):
        try:
            test_call()
            click.echo(f"  {name}: " + click.style("OK", fg="green"))
        except HttpError as e:
            _print_api_error(name, e)

    cal = build_service("calendar", "v3")
    try:
        cal.calendarList().list(maxResults=1).execute()
    except HttpError as e:
        _print_api_error("Calendar", e)
    else:
        click.echo("  Calendar: " + click.style("OK", fg="green"))
        try:
            _show_calendar_counts(cal)
            _show_calendars_list(cal, user_email=user_email)
        except Exception as e:
            logger.warning("Failed to fetch calendar info", error=str(e))

    drive = build_service("drive", "v3")
    check_api("Drive", lambda: drive.about().get(fields="user").execute())

    # Docs: test by attempting to get a non-existent doc (404/400 = API works, 403 = disabled)
    docs = build_service("docs", "v1")

    def check_docs_api():
        try:
            docs.documents().get(documentId="test-api-access").execute()
        except HttpError as e:
            if e.resp.status in (404, 400):
                return  # API works, doc just doesn't exist or invalid ID format
            raise

    check_api("Docs", check_docs_api)

    # Sheets: test with Google's public sample spreadsheet
    sheets = build_service("sheets", "v4")
    check_api(
        "Sheets",
        lambda: sheets.spreadsheets()
        .get(
            spreadsheetId="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
            fields="spreadsheetId",
        )
        .execute(),
    )


def _check_imessage_status() -> None:
    """Check iMessage availability (send and read capabilities)."""
    import sqlite3
    import subprocess
    import sys
    from pathlib import Path

    click.echo("iMessage:")

    # iMessage only available on macOS
    if sys.platform != "darwin":
        click.echo("  " + click.style("Not available (macOS only)", fg="yellow"))
        return

    # Check send capability (AppleScript/Automation permission)
    # This script just checks if Messages.app is accessible, doesn't send anything
    test_script = 'tell application "Messages" to get name'
    result = subprocess.run(
        ["osascript", "-e", test_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        click.echo("  Send: " + click.style("OK", fg="green"))
    else:
        error = result.stderr.strip()
        if "not allowed" in error.lower() or "assistive" in error.lower():
            click.echo(
                "  Send: " + click.style("No Automation permission", fg="yellow")
            )
            click.echo("    Grant when prompted on first send, or enable in:")
            click.echo("    System Preferences > Privacy & Security > Automation")
        else:
            click.echo("  Send: " + click.style(f"Error - {error}", fg="red"))

    # Check read capability (Full Disk Access to Messages database)
    db_path = Path.home() / "Library" / "Messages" / "chat.db"
    if not db_path.exists():
        click.echo("  Read: " + click.style("Messages database not found", fg="yellow"))
    else:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.execute("SELECT 1 FROM message LIMIT 1")
        except sqlite3.OperationalError as e:
            if "unable to open" in str(e):
                click.echo("  Read: " + click.style("No Full Disk Access", fg="yellow"))
                click.echo(
                    "    System Preferences > Privacy & Security > Full Disk Access"
                )
                click.echo("    Add and enable your terminal app")
            else:
                click.echo("  Read: " + click.style(f"Error - {e}", fg="red"))
        else:
            click.echo("  Read: " + click.style("OK", fg="green"))
            try:
                _show_imessage_counts(conn)
            except Exception as e:
                logger.warning("Failed to fetch iMessage counts", error=str(e))
            finally:
                conn.close()


def _check_reminders_status() -> None:
    """Check Apple Reminders availability."""
    import subprocess
    import sys

    click.echo("Reminders:")

    # Reminders only available on macOS
    if sys.platform != "darwin":
        click.echo("  " + click.style("Not available (macOS only)", fg="yellow"))
        return

    # Test AppleScript access to Reminders.app
    test_script = 'tell application "Reminders" to get name of default list'
    result = subprocess.run(
        ["osascript", "-e", test_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0:
        click.echo("  Access: " + click.style("OK", fg="green"))
        try:
            _show_reminders_counts()
        except Exception as e:
            logger.warning("Failed to fetch reminders counts", error=str(e))
    else:
        error = result.stderr.strip()
        if "not allowed" in error.lower() or "assistive" in error.lower():
            click.echo(
                "  Access: " + click.style("No Automation permission", fg="yellow")
            )
            click.echo("    Grant when prompted on first use, or enable in:")
            click.echo("    System Preferences > Privacy & Security > Automation")
        else:
            click.echo("  Access: " + click.style(f"Error - {error}", fg="red"))


def _check_whatsapp_status() -> None:
    """Check WhatsApp CLI availability and authentication."""
    from .logging import JeanClaudeError
    from .whatsapp import _get_whatsapp_cli_path, _run_whatsapp_cli

    click.echo("WhatsApp:")

    # Check if feature is enabled
    if not is_whatsapp_enabled():
        click.echo("  " + click.style("Disabled", fg="yellow"))
        click.echo("    Enable: jean-claude config set enable_whatsapp true")
        return

    # Check if CLI binary exists
    try:
        _get_whatsapp_cli_path()
    except JeanClaudeError:
        click.echo("  CLI: " + click.style("Not built", fg="yellow"))
        click.echo("    Build with: cd whatsapp && ./build.sh")
        return

    click.echo("  CLI: " + click.style("OK", fg="green"))

    # Check authentication status
    try:
        result = _run_whatsapp_cli("status")
        if result and isinstance(result, dict) and result.get("authenticated"):
            phone = result.get("phone", "unknown")
            click.echo("  Auth: " + click.style(f"Authenticated ({phone})", fg="green"))
            try:
                _show_whatsapp_counts()
            except Exception as e:
                logger.warning("Failed to fetch WhatsApp counts", error=str(e))
        else:
            click.echo("  Auth: " + click.style("Not authenticated", fg="yellow"))
            click.echo("    Run 'jean-claude whatsapp auth' to authenticate")
    except Exception as e:
        click.echo("  Auth: " + click.style(f"Error - {e}", fg="red"))


def _check_signal_status() -> None:
    """Check Signal CLI availability and authentication."""
    from .logging import JeanClaudeError
    from .signal import _get_signal_cli_path, _run_signal_cli

    click.echo("Signal:")

    # Check if feature is enabled
    if not is_signal_enabled():
        click.echo("  " + click.style("Disabled", fg="yellow"))
        click.echo("    Enable: jean-claude config set enable_signal true")
        return

    # Check if CLI binary exists
    try:
        _get_signal_cli_path()
    except JeanClaudeError:
        click.echo("  CLI: " + click.style("Not built", fg="yellow"))
        click.echo("    Build with: cd signal && cargo build --release")
        return

    click.echo("  CLI: " + click.style("OK", fg="green"))

    # Check authentication status
    try:
        result = _run_signal_cli("status")
        if result and isinstance(result, dict) and result.get("linked"):
            phone = result.get("phone", "unknown")
            click.echo("  Auth: " + click.style(f"Linked ({phone})", fg="green"))
        else:
            click.echo("  Auth: " + click.style("Not linked", fg="yellow"))
            click.echo("    Run 'jean-claude signal link' to link device")
    except Exception as e:
        click.echo("  Auth: " + click.style(f"Error - {e}", fg="red"))


def _print_api_error(api_name: str, error: Exception) -> None:
    """Print formatted API error with actionable guidance."""
    error_str = str(error)
    if "403" in error_str and "not been used" in error_str.lower():
        click.echo(f"  {api_name}: " + click.style("API not enabled", fg="red"))
        click.echo("    Enable at: https://console.cloud.google.com/apis/library")
    elif "403" in error_str:
        click.echo(f"  {api_name}: " + click.style("Access denied", fg="red"))
    else:
        click.echo(f"  {api_name}: " + click.style(f"Error - {error}", fg="red"))


def _show_gmail_counts(gmail) -> None:
    """Show Gmail inbox/unread/draft counts."""
    inbox = gmail.users().labels().get(userId="me", id="INBOX").execute()
    inbox_threads = inbox["threadsTotal"]
    inbox_unread = inbox["threadsUnread"]

    draft = gmail.users().labels().get(userId="me", id="DRAFT").execute()
    draft_count = draft["threadsTotal"]

    parts = [f"{inbox_threads} inbox"]
    if inbox_unread > 0:
        parts.append(click.style(f"{inbox_unread} unread", fg="yellow"))
    if draft_count > 0:
        parts.append(f"{draft_count} drafts")

    click.echo(f"    {', '.join(parts)}")


def _show_imessage_counts(conn) -> None:
    """Show iMessage unread counts.

    Uses Apple's unread criteria from their database index:
    is_read=0, is_from_me=0, item_type=0, is_finished=1, is_system_message=0
    """
    cursor = conn.execute("""
        SELECT COUNT(*), COUNT(DISTINCT cmj.chat_id)
        FROM message m
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        WHERE m.is_read = 0 AND m.is_from_me = 0 AND m.item_type = 0
          AND m.is_finished = 1 AND m.is_system_message = 0
    """)
    total_unread, chats_with_unread = cursor.fetchone()

    if total_unread > 0:
        click.echo(
            f"    {click.style(f'{total_unread} unread', fg='yellow')} "
            f"across {chats_with_unread} chats"
        )


def _show_whatsapp_counts() -> None:
    """Show WhatsApp unread counts."""
    from .whatsapp import _run_whatsapp_cli

    result = _run_whatsapp_cli("chats", "--unread")
    if result and isinstance(result, list):
        total_unread = sum(chat["unread_count"] for chat in result)
        chats_with_unread = len(result)

        if total_unread > 0:
            click.echo(
                f"    {click.style(f'{total_unread} unread', fg='yellow')} "
                f"across {chats_with_unread} chats"
            )


def _get_calendars_list(
    cal, with_stats: bool = False, user_email: str | None = None
) -> list[dict]:
    """Get list of calendars with access info and optional event stats.

    When with_stats=True, also fetches:
    - upcoming: total events in next 30 days
    - organized: events where user is organizer (actively created)
    - invited: events where user is an attendee (invited by someone else)

    The organized/invited split helps distinguish active calendars from "block"
    calendars that just show someone else's events.
    """
    from datetime import datetime, timedelta

    from .gcal import LOCAL_TZ

    result = cal.calendarList().list().execute()
    calendars = []

    # Time range for stats: next 30 days
    now = datetime.now(LOCAL_TZ)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=30)).isoformat()

    for item in result.get("items", []):
        calendar_data = {
            "id": item["id"],
            "name": item.get("summary", "(no name)"),
            "primary": item.get("primary", False),
            "accessRole": item.get("accessRole"),
        }

        if with_stats:
            # Fetch upcoming events for this calendar
            try:
                events_result = (
                    cal.events()
                    .list(
                        calendarId=item["id"],
                        timeMin=time_min,
                        timeMax=time_max,
                        singleEvents=True,
                        maxResults=100,
                    )
                    .execute()
                )
                events = events_result.get("items", [])
                calendar_data["upcoming"] = len(events)

                # Count organized vs invited events (requires user email)
                if user_email:
                    user_email_lower = user_email.lower()
                    organized = 0
                    invited = 0
                    for event in events:
                        organizer_email = event.get("organizer", {}).get("email", "")

                        if organizer_email.lower() == user_email_lower:
                            organized += 1
                        elif any(
                            a.get("email", "").lower() == user_email_lower
                            for a in event.get("attendees", [])
                        ):
                            invited += 1

                    calendar_data["organized"] = organized
                    calendar_data["invited"] = invited
            except Exception:
                # Some calendars might not allow event listing (e.g., freeBusyReader)
                calendar_data["upcoming"] = None

        calendars.append(calendar_data)

    # Sort: primary first, then by relevance (organized + invited), then total upcoming
    def sort_key(c):
        # Primary always first (0), others after (1)
        primary_order = 0 if c["primary"] else 1
        # Higher relevance first (events user is involved in)
        relevance = -((c.get("organized") or 0) + (c.get("invited") or 0))
        # Higher event counts first (negative for descending)
        event_order = -(c.get("upcoming") or 0)
        return (primary_order, relevance, event_order, c["name"].lower())

    calendars.sort(key=sort_key)
    return calendars


def _show_calendar_counts(cal) -> None:
    """Show calendar event counts for today and this week."""
    from datetime import datetime, timedelta

    from .gcal import LOCAL_TZ, get_event_start

    now = datetime.now(LOCAL_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_end = today_start + timedelta(days=7)

    # Single API call for the week, filter locally for today
    week_result = (
        cal.events()
        .list(
            calendarId="primary",
            timeMin=today_start.isoformat(),
            timeMax=week_end.isoformat(),
            singleEvents=True,
        )
        .execute()
    )
    week_events = week_result.get("items", [])
    week_count = len(week_events)

    # Filter for today: event starts before end of today
    today_end_iso = today_end.isoformat()
    today_count = sum(1 for e in week_events if get_event_start(e) < today_end_iso)

    if today_count > 0 or week_count > 0:
        parts = []
        if today_count > 0:
            parts.append(f"{today_count} today")
        if week_count > today_count:
            parts.append(f"{week_count} this week")
        click.echo(f"    {', '.join(parts)}")


def _show_calendars_list(cal, user_email: str | None = None) -> None:
    """Show list of available calendars with access roles and event counts."""
    calendars = _get_calendars_list(cal, with_stats=True, user_email=user_email)
    if not calendars:
        return

    click.echo("    Calendars:")
    for c in calendars:
        name = c["name"]
        role = c["accessRole"]
        upcoming = c.get("upcoming")
        organized = c.get("organized")
        invited = c.get("invited")

        # Build the line
        parts = [f"      {name}"]
        if c["primary"]:
            parts.append(click.style("(primary)", fg="cyan"))

        # Show upcoming count with organized/invited breakdown if available
        if upcoming is not None:
            if upcoming > 0:
                # Build stats string
                if organized is not None and invited is not None:
                    # Show breakdown: "24 upcoming: 15 yours, 5 invited"
                    involved = organized + invited
                    if involved > 0:
                        stat_parts = []
                        if organized > 0:
                            stat_parts.append(f"{organized} yours")
                        if invited > 0:
                            stat_parts.append(f"{invited} invited")
                        stats_str = f"({upcoming} upcoming: {', '.join(stat_parts)})"
                        parts.append(click.style(stats_str, fg="green"))
                    else:
                        # No events user is involved in - dim to show it's just a "block" calendar
                        parts.append(
                            click.style(f"({upcoming} upcoming, 0 yours)", dim=True)
                        )
                else:
                    parts.append(click.style(f"({upcoming} upcoming)", fg="green"))
            else:
                parts.append(click.style("(no upcoming)", dim=True))

        parts.append(f"[{role}]")
        click.echo(" ".join(parts))


def _show_reminders_counts() -> None:
    """Show incomplete reminders count."""
    from .applescript import run_applescript

    script = """tell application "Reminders"
    set totalCount to 0
    repeat with lst in lists
        set totalCount to totalCount + (count of (reminders of lst whose completed is false))
    end repeat
    return totalCount
end tell"""
    count = int(run_applescript(script))
    if count > 0:
        click.echo(f"    {count} incomplete")


@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completions(shell: str):
    """Generate shell completion script.

    Output the completion script for the specified shell. Add to your shell
    config to enable tab completion.

    \b
    Bash (~/.bashrc):
        eval "$(jean-claude completions bash)"

    \b
    Zsh (~/.zshrc):
        eval "$(jean-claude completions zsh)"

    \b
    Fish (~/.config/fish/config.fish):
        jean-claude completions fish | source
    """
    from click.shell_completion import get_completion_class

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise JeanClaudeError(f"Unsupported shell: {shell}")

    comp = comp_cls(cli, {}, "jean-claude", "_JEAN_CLAUDE_COMPLETE")
    click.echo(comp.source())


@cli.group()
def config():
    """Manage jean-claude configuration."""
    pass


@config.command("show")
def config_show():
    """Show current configuration."""
    current = get_config()
    click.echo(json.dumps(current, indent=2))


@config.command("set")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str):
    """Set a configuration value.

    \b
    Examples:
        jean-claude config set enable_whatsapp true
        jean-claude config set enable_signal true
        jean-claude config set setup_completed true
    """
    from .config import DEFAULT_CONFIG

    # Validate key is known
    if key not in DEFAULT_CONFIG:
        valid_keys = ", ".join(sorted(DEFAULT_CONFIG.keys()))
        raise JeanClaudeError(f"Unknown config key: {key}. Valid keys: {valid_keys}")

    # Parse boolean values
    if value.lower() in ("true", "1", "yes", "on"):
        parsed_value: bool | str = True
    elif value.lower() in ("false", "0", "no", "off"):
        parsed_value = False
    else:
        parsed_value = value

    # Reject non-boolean values for boolean keys
    if isinstance(DEFAULT_CONFIG[key], bool) and not isinstance(parsed_value, bool):
        raise JeanClaudeError(
            f"Config key '{key}' requires a boolean value (true/false), got: {value}"
        )

    set_config_value(key, parsed_value)
    logger.info(f"Set {key}={parsed_value}")
