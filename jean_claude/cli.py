"""Main CLI entry point for jean-claude."""

from __future__ import annotations

import json

import click

from .auth import SCOPES_FULL, SCOPES_READONLY, TOKEN_FILE, get_credentials, run_auth
from .gcal import cli as gcal_cli
from .gdrive import cli as gdrive_cli
from .gmail import cli as gmail_cli
from .imessage import cli as imessage_cli


@click.group()
def cli():
    """jean-claude: Gmail, Calendar, Drive, and iMessage integration."""


cli.add_command(gmail_cli, name="gmail")
cli.add_command(gcal_cli, name="gcal")
cli.add_command(gdrive_cli, name="gdrive")
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
    if not TOKEN_FILE.exists():
        click.echo("Status: Not authenticated")
        click.echo("Run 'jean auth' to authenticate.")
        return

    try:
        token_data = json.loads(TOKEN_FILE.read_text())
        scopes = set(token_data.get("scopes", []))
    except (json.JSONDecodeError, KeyError):
        click.echo("Status: Token file corrupted")
        click.echo("Run 'jean auth --logout' then 'jean auth' to re-authenticate.")
        return

    # Determine scope level
    if scopes == set(SCOPES_FULL):
        scope_level = "full access"
    elif scopes == set(SCOPES_READONLY):
        scope_level = "read-only"
    else:
        scope_level = "custom"

    click.echo(f"Status: Authenticated ({scope_level})")

    # Check API availability
    click.echo("\nAPI Status:")
    try:
        creds = get_credentials()
    except Exception as e:
        click.echo(f"  Error getting credentials: {e}")
        return

    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    # Check Gmail
    try:
        gmail = build("gmail", "v1", credentials=creds)
        gmail.users().getProfile(userId="me").execute()
        click.echo("  Gmail: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Gmail", e)

    # Check Calendar
    try:
        cal = build("calendar", "v3", credentials=creds)
        cal.calendarList().list(maxResults=1).execute()
        click.echo("  Calendar: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Calendar", e)

    # Check Drive
    try:
        drive = build("drive", "v3", credentials=creds)
        drive.about().get(fields="user").execute()
        click.echo("  Drive: " + click.style("OK", fg="green"))
    except HttpError as e:
        _print_api_error("Drive", e)


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
        eval "$(jean completions bash)"

    \b
    Zsh (~/.zshrc):
        eval "$(jean completions zsh)"

    \b
    Fish (~/.config/fish/config.fish):
        jean completions fish | source
    """
    from click.shell_completion import get_completion_class

    comp_cls = get_completion_class(shell)
    if comp_cls is None:
        raise click.ClickException(f"Unsupported shell: {shell}")

    comp = comp_cls(cli, {}, "jean", "_JEAN_COMPLETE")
    click.echo(comp.source())
