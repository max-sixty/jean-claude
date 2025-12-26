"""WhatsApp CLI - send messages and list chats via whatsapp-cli Go binary."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import click

from .input import read_body_stdin
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def _get_platform_info() -> tuple[str, str]:
    """Get OS and architecture names in Go naming conventions."""
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
    return os_name, arch


def _try_compile_binary(bin_dir: Path, os_name: str, arch: str) -> Path | None:
    """Try to compile the Go binary from source.

    Returns the path to the compiled binary, or None if compilation fails.
    """
    whatsapp_dir = Path(__file__).parent.parent / "whatsapp"
    if not (whatsapp_dir / "main.go").exists():
        return None

    # Check if Go is available
    if not shutil.which("go"):
        logger.debug("Go not installed, skipping compilation")
        return None

    bin_dir.mkdir(parents=True, exist_ok=True)
    output = bin_dir / f"whatsapp-cli-{os_name}-{arch}"

    logger.info("Compiling whatsapp-cli from source...")
    try:
        subprocess.run(
            [
                "go",
                "build",
                "-ldflags=-s -w",
                "-o",
                str(output),
                ".",
            ],
            cwd=whatsapp_dir,
            check=True,
            capture_output=True,
            env={**os.environ, "CGO_ENABLED": "0"},
        )
        logger.info("Compiled whatsapp-cli successfully", path=str(output))
        return output
    except subprocess.CalledProcessError as e:
        logger.warning(
            "Failed to compile whatsapp-cli",
            stderr=e.stderr.decode() if e.stderr else None,
        )
        return None


def _try_download_from_pypi(bin_dir: Path, os_name: str, arch: str) -> Path | None:
    """Try to download the binary from PyPI wheel.

    Downloads the platform-specific wheel and extracts the binary.
    Returns the path to the extracted binary, or None if download fails.
    """
    # Map to PyPI platform tags (substring match in wheel filename)
    platform_tags = {
        ("darwin", "arm64"): "macosx_11_0_arm64",
        ("darwin", "amd64"): "macosx_10_9_x86_64",
        ("linux", "amd64"): "manylinux2014_x86_64",
        ("linux", "arm64"): "manylinux2014_aarch64",
    }
    platform_tag = platform_tags.get((os_name, arch))
    if not platform_tag:
        logger.debug("No PyPI wheel available for platform", os=os_name, arch=arch)
        return None

    wheel_path: Path | None = None
    try:
        # Get package info from PyPI JSON API
        with urllib.request.urlopen(
            "https://pypi.org/pypi/jean-claude-code/json", timeout=30
        ) as response:
            package_info = json.loads(response.read())

        # Find wheel URL and hash for our platform
        wheel_url = None
        expected_sha256 = None
        for file_info in package_info["urls"]:
            filename = file_info["filename"]
            if filename.endswith(".whl") and platform_tag in filename:
                wheel_url = file_info["url"]
                expected_sha256 = file_info["digests"]["sha256"]
                break

        if not wheel_url:
            logger.debug("No wheel found for platform on PyPI", platform=platform_tag)
            return None

        logger.info("Downloading whatsapp-cli from PyPI...", url=wheel_url)

        # Download the wheel to a temp file
        with tempfile.NamedTemporaryFile(suffix=".whl", delete=False) as tmp:
            wheel_path = Path(tmp.name)
            with urllib.request.urlopen(wheel_url, timeout=60) as response:
                wheel_data = response.read()
                tmp.write(wheel_data)

        # Verify SHA256 hash
        actual_sha256 = hashlib.sha256(wheel_data).hexdigest()
        if actual_sha256 != expected_sha256:
            logger.warning(
                "Wheel hash mismatch",
                expected=expected_sha256,
                actual=actual_sha256,
            )
            return None

        # Extract the binary from the wheel
        binary_name = f"whatsapp-cli-{os_name}-{arch}"
        binary_in_wheel = f"jean_claude/bin/{binary_name}"

        with zipfile.ZipFile(wheel_path) as zf:
            if binary_in_wheel not in zf.namelist():
                logger.debug("Binary not found in wheel", expected=binary_in_wheel)
                return None

            bin_dir.mkdir(parents=True, exist_ok=True)
            output = bin_dir / binary_name

            # Write to temp file first, then atomic rename
            with tempfile.NamedTemporaryFile(
                dir=bin_dir, delete=False, prefix=".tmp-"
            ) as tmp_out:
                with zf.open(binary_in_wheel) as src:
                    tmp_out.write(src.read())
                tmp_binary = Path(tmp_out.name)

            tmp_binary.chmod(0o755)
            tmp_binary.rename(output)  # atomic on same filesystem

            logger.info("Extracted whatsapp-cli from PyPI", path=str(output))
            return output

    except (
        urllib.error.URLError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        KeyError,
        OSError,
    ) as e:
        logger.warning("Failed to download from PyPI", error=str(e))
        return None
    finally:
        if wheel_path:
            wheel_path.unlink(missing_ok=True)


def _get_whatsapp_cli_path() -> Path:
    """Find or provision the whatsapp-cli binary for the current platform.

    Lookup order:
    1. Existing binary in jean_claude/bin/
    2. Compile from source (if Go is installed)
    3. Download from PyPI wheel
    """
    os_name, arch = _get_platform_info()
    bin_dir = Path(__file__).parent / "bin"
    binary_path = bin_dir / f"whatsapp-cli-{os_name}-{arch}"

    # 1. Check for existing binary (bundled or previously compiled/downloaded)
    if binary_path.exists():
        return binary_path

    # 2. Try to compile from source
    compiled = _try_compile_binary(bin_dir, os_name, arch)
    if compiled:
        return compiled

    # 3. Try to download from PyPI
    downloaded = _try_download_from_pypi(bin_dir, os_name, arch)
    if downloaded:
        return downloaded

    raise JeanClaudeError(
        f"WhatsApp CLI not found for {os_name}/{arch}.\n"
        f"Tried:\n"
        f"  - Binary: {binary_path}\n"
        f"  - Compile from source: Go not installed or compilation failed\n"
        f"  - Download from PyPI: Failed\n"
        "Install Go and run: cd whatsapp && ./build.sh"
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
@click.argument("recipient")
@click.option("--reply-to", help="Message ID to reply to")
def send(recipient: str, reply_to: str | None):
    """Send a WhatsApp message.

    RECIPIENT: Phone number with country code (e.g., +12025551234).

    Message body is read from stdin.

    \b
    Examples:
        echo "Hello!" | jean-claude whatsapp send "+12025551234"
        cat << 'EOF' | jean-claude whatsapp send "+12025551234"
        It's great to hear from you!
        EOF
    """
    body = read_body_stdin()

    args = ["send", recipient, body]
    if reply_to:
        args.insert(1, f"--reply-to={reply_to}")

    result = _run_whatsapp_cli(*args)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command("send-file")
@click.argument("recipient")
@click.argument("file_path", type=click.Path(exists=True))
def send_file(recipient: str, file_path: str):
    """Send a file attachment via WhatsApp.

    RECIPIENT: Phone number with country code (e.g., +12025551234)
    FILE_PATH: Path to the file to send

    Supports images, videos, audio, and documents.

    \b
    Examples:
        jean-claude whatsapp send-file "+12025551234" ./photo.jpg
        jean-claude whatsapp send-file "+12025551234" ./document.pdf
    """
    result = _run_whatsapp_cli("send-file", recipient, file_path)
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

    \b
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

    \b
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

    \b
    Examples:
        jean-claude whatsapp participants "120363277025153496@g.us"
    """
    result = _run_whatsapp_cli("participants", group_jid)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command("mark-read")
@click.argument("chat_jid")
def mark_read(chat_jid: str):
    """Mark all messages in a chat as read.

    CHAT_JID: The chat JID (e.g., "120363277025153496@g.us")

    \b
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

    \b
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
