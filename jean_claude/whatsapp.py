"""WhatsApp CLI - send messages and list chats via whatsapp-cli Go binary."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from pathlib import Path

import click

from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def _get_whatsapp_cli_path() -> Path:
    """Find the whatsapp-cli binary for the current platform.

    Looks in jean_claude/bin/ for platform-specific binaries.
    Falls back to whatsapp/whatsapp-cli for development.
    """
    # Map Python platform info to Go naming conventions
    os_name = {"darwin": "darwin", "linux": "linux", "win32": "windows"}.get(
        sys.platform, sys.platform
    )
    machine = platform.machine().lower()
    arch = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }.get(machine, machine)

    # Look for bundled binary first
    bin_dir = Path(__file__).parent / "bin"
    bundled = bin_dir / f"whatsapp-cli-{os_name}-{arch}"
    if bundled.exists():
        return bundled

    # Fall back to development location
    dev_binary = Path(__file__).parent.parent / "whatsapp" / "whatsapp-cli"
    if dev_binary.exists():
        return dev_binary

    raise JeanClaudeError(
        f"WhatsApp CLI not found for {os_name}/{arch}.\n"
        f"Looked in:\n"
        f"  - {bundled}\n"
        f"  - {dev_binary}\n"
        "Build with: cd whatsapp && ./build.sh"
    )


def _run_whatsapp_cli(*args: str, capture: bool = True) -> dict | list | None:
    """Run the whatsapp-cli binary and return parsed JSON output.

    Args:
        *args: Command line arguments to pass to whatsapp-cli
        capture: If True, capture and parse JSON output. If False, let output flow to terminal.

    Returns:
        Parsed JSON output, or None if capture=False
    """
    cli_path = _get_whatsapp_cli_path()
    cmd = [str(cli_path), *args]
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
        # Log unexpected non-JSON output for debugging
        logger.warning(
            "whatsapp-cli returned non-JSON output",
            args=args,
            stdout_preview=stdout[:200] if len(stdout) > 200 else stdout,
        )
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
@click.argument("recipient", required=False)
@click.argument("message", required=False)
@click.option(
    "--name", help="Look up recipient by contact name instead of phone number"
)
@click.option("--reply-to", help="Message ID to reply to (creates quoted reply)")
def send(
    recipient: str | None, message: str | None, name: str | None, reply_to: str | None
):
    """Send a WhatsApp message.

    RECIPIENT: Phone number with country code (e.g., +12025551234)
    MESSAGE: The message text to send

    Use --name to send by contact name instead of phone number.
    Use --reply-to to create a quoted reply to a specific message.

    Examples:
        jean-claude whatsapp send "+12025551234" "Hello!"
        jean-claude whatsapp send --name "John Doe" "Hello!"
        jean-claude whatsapp send "+12025551234" --reply-to "MSG_ID" "Reply text"
    """
    args = ["send"]
    if name:
        args.append(f"--name={name}")
        if not message and recipient:
            # If --name is used, recipient is actually the message
            message = recipient
            recipient = None
    if reply_to:
        args.append(f"--reply-to={reply_to}")
    if recipient:
        args.append(recipient)
    if message:
        args.append(message)
    else:
        raise click.UsageError("Message is required")

    result = _run_whatsapp_cli(*args)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command("send-file")
@click.argument("recipient", required=False)
@click.argument("file_path", required=False, type=click.Path(exists=True))
@click.option(
    "--name", help="Look up recipient by contact name instead of phone number"
)
def send_file(recipient: str | None, file_path: str | None, name: str | None):
    """Send a file attachment via WhatsApp.

    RECIPIENT: Phone number with country code (e.g., +12025551234)
    FILE_PATH: Path to the file to send

    Supports images, videos, audio, and documents.
    Use --name to send by contact name instead of phone number.

    Examples:
        jean-claude whatsapp send-file "+12025551234" ./photo.jpg
        jean-claude whatsapp send-file --name "John Doe" ./document.pdf
    """
    args = ["send-file"]
    if name:
        args.append(f"--name={name}")
        if not file_path and recipient:
            # If --name is used, recipient is actually the file path
            file_path = recipient
            recipient = None
    if recipient:
        args.append(recipient)
    if file_path:
        args.append(file_path)
    else:
        raise click.UsageError("File path is required")

    result = _run_whatsapp_cli(*args)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to return")
@click.option("--unread", is_flag=True, help="Show only chats with unread messages")
def chats(max_results: int, unread: bool):
    """List WhatsApp chats.

    Shows recent chats with names (for groups and contacts) and last
    message timestamps. Use --unread to show only chats with unread messages.
    """
    args = ["chats"]
    if unread:
        args.append("--unread")
    result = _run_whatsapp_cli(*args)
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
    args = ["messages", f"--max-results={max_results}"]
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
@click.argument("query")
@click.option("-n", "--max-results", default=50, help="Maximum results to return")
def search(query: str, max_results: int):
    """Search message history.

    QUERY: Search term (searches message text)

    Examples:
        jean-claude whatsapp search "dinner plans"
        jean-claude whatsapp search "meeting" -n 20
    """
    result = _run_whatsapp_cli("search", query, f"--max-results={max_results}")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("group_jid")
def participants(group_jid: str):
    """List participants of a group chat.

    GROUP_JID: The group JID (e.g., "120363277025153496@g.us")

    Examples:
        jean-claude whatsapp participants "120363277025153496@g.us"
    """
    result = _run_whatsapp_cli("participants", group_jid)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def refresh():
    """Fetch chat and group names from WhatsApp.

    Updates names for chats that don't have them. This is normally done
    automatically during sync, but can be run manually if needed.
    """
    _run_whatsapp_cli("refresh", capture=False)


@cli.command("mark-read")
@click.argument("chat_jid")
def mark_read(chat_jid: str):
    """Mark all messages in a chat as read.

    CHAT_JID: The chat JID (e.g., "120363277025153496@g.us")

    Examples:
        jean-claude whatsapp mark-read "120363277025153496@g.us"
    """
    result = _run_whatsapp_cli("mark-read", chat_jid)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("message_id")
@click.option(
    "--output", type=click.Path(), help="Output file path (defaults to XDG data dir)"
)
def download(message_id: str, output: str | None):
    """Download media from a message.

    MESSAGE_ID: The message ID

    Downloads media to ~/.local/share/jean-claude/whatsapp/media/ by default.
    Uses content hash as filename for deduplication.

    Examples:
        jean-claude whatsapp download "3EB0ABC123..."
        jean-claude whatsapp download "3EB0ABC123..." --output ./photo.jpg
    """
    args = ["download", message_id]
    if output:
        args.append(f"--output={output}")

    result = _run_whatsapp_cli(*args)
    if result:
        click.echo(json.dumps(result, indent=2))


# Image MIME types for auto-download (excludes Apple-specific formats like HEIC)
IMAGE_MIME_TYPES = frozenset(
    [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
    ]
)


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum messages to return")
def unread(max_results: int):
    """List unread messages and auto-download images.

    Shows unread messages with sender, timestamp, and text content.
    Images are automatically downloaded to ~/.local/share/jean-claude/whatsapp/media/
    and their file paths are included in the output.

    Examples:
        jean-claude whatsapp unread
        jean-claude whatsapp unread -n 20
    """
    # Get unread messages
    result = _run_whatsapp_cli("messages", f"--max-results={max_results}", "--unread")
    if not result or not isinstance(result, list):
        click.echo(json.dumps([], indent=2))
        return

    # Auto-download images
    for msg in result:
        media_type = msg.get("media_type")
        mime_type = msg.get("mime_type_full", "")
        msg_id = msg.get("id")

        # Only auto-download images
        if media_type == "image" or mime_type in IMAGE_MIME_TYPES:
            # Check if already has a file path
            if msg.get("media_file_path"):
                msg["file"] = msg["media_file_path"]
                continue

            # Try to download
            try:
                download_result = _run_whatsapp_cli("download", msg_id)
                if download_result and download_result.get("file"):
                    msg["file"] = download_result["file"]
            except JeanClaudeError as e:
                logger.debug("Download failed", message_id=msg_id, error=str(e))

    click.echo(json.dumps(result, indent=2))
