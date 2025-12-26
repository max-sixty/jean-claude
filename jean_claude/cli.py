"""Main CLI entry point for jean-claude."""

from __future__ import annotations

import json
import sys

import click

from googleapiclient.errors import HttpError

from .auth import SCOPES_FULL, SCOPES_READONLY, TOKEN_FILE, run_auth
from .gcal import cli as gcal_cli
from .gdocs import cli as gdocs_cli
from .gdrive import cli as gdrive_cli
from .gmail import cli as gmail_cli
from .gsheets import cli as gsheets_cli
from .imessage import cli as imessage_cli
from .reminders import cli as reminders_cli
from .logging import JeanClaudeError, configure_logging, get_logger
from .whatsapp import cli as whatsapp_cli

logger = get_logger(__name__)


class ErrorHandlingGroup(click.Group):
    """Click group that handles errors with clean output."""

    def invoke(self, ctx: click.Context):
        try:
            return super().invoke(ctx)
        except HttpError as e:
            self._handle_error(self._http_error_message(e))
        except JeanClaudeError as e:
            self._handle_error(str(e))

    def _handle_error(self, message: str) -> None:
        """Log error and exit cleanly."""
        logger.error(message)
        click.echo(f"Error: {message}", err=True)
        sys.exit(1)

    def _http_error_message(self, e: HttpError) -> str:
        """Convert HttpError to user-friendly message."""
        status = e.resp.status
        reason = e._get_reason()

        if status == 404:
            return f"Not found: {reason}"
        if status == 403:
            # Check for specific API-not-enabled error
            error_str = str(e)
            if (
                "not been used" in error_str.lower()
                or "not enabled" in error_str.lower()
            ):
                return f"API not enabled: {reason}. Enable at https://console.cloud.google.com/apis/library"
            return f"Permission denied: {reason}"
        if status == 400:
            return f"Invalid request: {reason}"
        if status == 401:
            return f"Authentication failed: {reason}. Try 'jean-claude auth' to re-authenticate."
        if status == 429:
            return f"Rate limit exceeded: {reason}. Wait a moment and try again."
        return f"API error ({status}): {reason}"


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
    """jean-claude: Gmail, Calendar, Drive, iMessage, and WhatsApp integration."""
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

    # Reminders status
    click.echo()
    _check_reminders_status()

    # WhatsApp status
    click.echo()
    _check_whatsapp_status()


def _check_google_apis() -> None:
    """Check Google API availability."""
    from .auth import build_service

    def check_api(name: str, test_call):
        try:
            test_call()
            click.echo(f"  {name}: " + click.style("OK", fg="green"))
        except HttpError as e:
            _print_api_error(name, e)

    gmail = build_service("gmail", "v1")
    check_api("Gmail", lambda: gmail.users().getProfile(userId="me").execute())

    cal = build_service("calendar", "v3")
    check_api("Calendar", lambda: cal.calendarList().list(maxResults=1).execute())

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
    )
    if result.returncode == 0:
        click.echo("  Access: " + click.style("OK", fg="green"))
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
        if result and result.get("authenticated"):
            phone = result.get("phone", "unknown")
            click.echo("  Auth: " + click.style(f"Authenticated ({phone})", fg="green"))
        else:
            click.echo("  Auth: " + click.style("Not authenticated", fg="yellow"))
            click.echo("    Run 'jean-claude whatsapp auth' to authenticate")
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
