"""WhatsApp CLI - send messages and list chats via whatsapp-cli Go binary."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)

# Path to the Go binary (built from whatsapp/ directory)
WHATSAPP_CLI = Path(__file__).parent.parent / "whatsapp" / "whatsapp-cli"


def _run_whatsapp_cli(*args: str, capture: bool = True) -> dict | list | None:
    """Run the whatsapp-cli binary and return parsed JSON output.

    Args:
        *args: Command line arguments to pass to whatsapp-cli
        capture: If True, capture and parse JSON output. If False, let output flow to terminal.

    Returns:
        Parsed JSON output, or None if capture=False
    """
    if not WHATSAPP_CLI.exists():
        raise JeanClaudeError(
            f"WhatsApp CLI not found at {WHATSAPP_CLI}\n"
            "Build it with: cd whatsapp && go build -o whatsapp-cli ."
        )

    cmd = [str(WHATSAPP_CLI), *args]
    logger.debug("Running whatsapp-cli", args=args)

    if not capture:
        # Let output flow directly (for auth command with QR code)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise JeanClaudeError(
                f"whatsapp-cli failed with exit code {result.returncode}"
            )
        return None

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = (
            result.stderr.strip() if result.stderr else f"Exit code {result.returncode}"
        )
        raise JeanClaudeError(f"WhatsApp error: {error_msg}")

    # Parse JSON from stdout
    stdout = result.stdout.strip()
    if not stdout:
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Some commands output text, not JSON
        return None


@click.group()
def cli():
    """WhatsApp CLI - send messages and list chats.

    Requires authentication via QR code scan. Messages are synced to a local
    database for fast access.
    """


@cli.command()
def auth():
    """Authenticate with WhatsApp by scanning QR code.

    Opens a QR code image and displays it in the terminal. Scan with
    WhatsApp on your phone: Settings > Linked Devices > Link a Device.
    """
    _run_whatsapp_cli("auth", capture=False)


@cli.command()
def logout():
    """Log out and clear WhatsApp credentials."""
    _run_whatsapp_cli("logout", capture=False)


@cli.command()
def status():
    """Show WhatsApp connection status."""
    result = _run_whatsapp_cli("status")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def sync():
    """Sync messages from WhatsApp to local database.

    Downloads new messages and updates chat names. Run periodically to
    keep the local database current.
    """
    _run_whatsapp_cli("sync", capture=False)


@cli.command()
@click.argument("recipient")
@click.argument("message")
def send(recipient: str, message: str):
    """Send a WhatsApp message.

    RECIPIENT: Phone number with country code (e.g., +12025551234)
    MESSAGE: The message text to send

    Examples:
        jean-claude whatsapp send "+12025551234" "Hello!"
    """
    result = _run_whatsapp_cli("send", recipient, message)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to return")
def chats(max_results: int):
    """List WhatsApp chats.

    Shows recent chats with names (for groups and contacts) and last
    message timestamps.
    """
    result = _run_whatsapp_cli("chats")
    if result and isinstance(result, list):
        # Apply limit
        chats_list = result[:max_results]
        click.echo(json.dumps(chats_list, indent=2))


@cli.command()
@click.option("--chat", "chat_jid", help="Filter to specific chat JID")
@click.option("-n", "--max-results", default=50, help="Maximum messages to return")
@click.option("--unread", is_flag=True, help="Show only unread messages")
def messages(chat_jid: str | None, max_results: int, unread: bool):
    """List messages from local database.

    Shows messages with sender, timestamp, and text content.
    Use --chat to filter to a specific conversation.
    Use --unread to show only unread messages.

    Examples:
        jean-claude whatsapp messages -n 20
        jean-claude whatsapp messages --chat "120363277025153496@g.us"
        jean-claude whatsapp messages --unread
    """
    args = ["messages", f"--limit={max_results}"]
    if chat_jid:
        args.append(f"--chat={chat_jid}")
    if unread:
        args.append("--unread")

    result = _run_whatsapp_cli(*args)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def contacts():
    """List WhatsApp contacts from local database."""
    result = _run_whatsapp_cli("contacts")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def refresh():
    """Fetch chat and group names from WhatsApp.

    Updates names for chats that don't have them. This is normally done
    automatically during sync, but can be run manually if needed.
    """
    _run_whatsapp_cli("refresh", capture=False)
