"""Main CLI entry point for jean-claude."""

from __future__ import annotations

import json
import sys

import click

from .auth import SCOPES_FULL, SCOPES_READONLY, TOKEN_FILE, run_auth
from .gcal import cli as gcal_cli
from .gdrive import cli as gdrive_cli
from .gmail import cli as gmail_cli
from .gsheets import cli as gsheets_cli
from .imessage import cli as imessage_cli
from .logging import JeanClaudeError, configure_logging, get_logger

logger = get_logger(__name__)


class ErrorHandlingGroup(click.Group):
    """Click group that handles JeanClaudeError with clean output."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except JeanClaudeError as e:
            logger.error(str(e))
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


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
    """jean-claude: Gmail, Calendar, Drive, and iMessage integration."""
    # Allow "none" to disable file logging
    log_file = None if json_log == "none" else json_log
    configure_logging(verbose=verbose, json_log=log_file)


cli.add_command(gmail_cli, name="gmail")
cli.add_command(gcal_cli, name="gcal")
cli.add_command(gdrive_cli, name="gdrive")
cli.add_command(gsheets_cli, name="gsheets")
cli.add_command(imessage_cli, name="imessage")


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
def status():
    """Show authentication status and API availability."""
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
            # Determine scope level
            if scopes == set(SCOPES_FULL):
                scope_level = "full access"
            elif scopes == set(SCOPES_READONLY):
                scope_level = "read-only"
            else:
                scope_level = "custom"

            click.echo(
                "Google: " + click.style(f"Authenticated ({scope_level})", fg="green")
            )

            # Check API availability
            try:
                _check_google_apis()
            except Exception as e:
                click.echo(f"  Error checking APIs: {e}")

    # iMessage status (doesn't require Google auth)
    click.echo()
    _check_imessage_status()


def _check_google_apis() -> None:
    """Check Google API availability."""
    from googleapiclient.errors import HttpError

    from .auth import build_service

    # Check Gmail
    try:
        gmail = build_service("gmail", "v1")
        gmail.users().getProfile(userId="me").execute()
        click.echo("  Gmail: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Gmail", e)

    # Check Calendar
    try:
        cal = build_service("calendar", "v3")
        cal.calendarList().list(maxResults=1).execute()
        click.echo("  Calendar: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Calendar", e)

    # Check Drive
    try:
        drive = build_service("drive", "v3")
        drive.about().get(fields="user").execute()
        click.echo("  Drive: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Drive", e)

    # Check Sheets - test with Google's public sample spreadsheet
    # (Sample Spreadsheet from Google Sheets API quickstart)
    try:
        sheets = build_service("sheets", "v4")
        sheets.spreadsheets().get(
            spreadsheetId="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
            fields="spreadsheetId",
        ).execute()
        click.echo("  Sheets: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Sheets", e)


def _check_imessage_status() -> None:
    """Check iMessage availability (send and read capabilities)."""
    import sqlite3
    import subprocess
    from pathlib import Path

    click.echo("iMessage:")

    # Check send capability (AppleScript/Automation permission)
    # This script just checks if Messages.app is accessible, doesn't send anything
    test_script = 'tell application "Messages" to get name'
    result = subprocess.run(
        ["osascript", "-e", test_script],
        capture_output=True,
        text=True,
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
            conn.close()
            click.echo("  Read: " + click.style("OK", fg="green"))
        except sqlite3.OperationalError as e:
            if "unable to open" in str(e):
                click.echo("  Read: " + click.style("No Full Disk Access", fg="yellow"))
                click.echo(
                    "    System Preferences > Privacy & Security > Full Disk Access"
                )
                click.echo("    Add and enable your terminal app")
            else:
                click.echo("  Read: " + click.style(f"Error - {e}", fg="red"))


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
