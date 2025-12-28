"""Signal CLI - send messages and list chats via signal-cli Rust binary."""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import click

from .config import is_signal_enabled
from .input import read_body_stdin
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def _get_platform_info() -> tuple[str, str]:
    """Get OS and architecture names in Rust target conventions."""
    os_name = {
        "darwin": "apple-darwin",
        "linux": "unknown-linux-gnu",
        "win32": "pc-windows-msvc",
    }.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    arch = {
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "aarch64",
        "aarch64": "aarch64",
    }.get(machine, machine)
    return os_name, arch


def _try_compile_binary(bin_dir: Path, os_name: str, arch: str) -> Path | None:
    """Try to compile the Rust binary from source.

    Returns the path to the compiled binary, or None if compilation fails.
    """
    signal_dir = Path(__file__).parent.parent / "signal"
    if not (signal_dir / "Cargo.toml").exists():
        return None

    # Check if Cargo is available
    if not shutil.which("cargo"):
        logger.debug("Cargo not installed, skipping compilation")
        return None

    bin_dir.mkdir(parents=True, exist_ok=True)
    output = bin_dir / f"signal-cli-{arch}-{os_name}"

    logger.info("Compiling signal-cli from source...")
    try:
        subprocess.run(
            ["cargo", "build", "--release"],
            cwd=signal_dir,
            check=True,
            capture_output=True,
        )
        # Copy the binary to our bin directory
        built_binary = signal_dir / "target" / "release" / "signal-cli"
        if built_binary.exists():
            shutil.copy(built_binary, output)
            output.chmod(0o755)
            logger.info("Compiled signal-cli successfully", path=str(output))
            return output
    except subprocess.CalledProcessError as e:
        logger.warning(
            "Failed to compile signal-cli",
            stderr=e.stderr.decode() if e.stderr else None,
        )
    return None


def _get_signal_cli_path() -> Path:
    """Find or provision the signal-cli binary for the current platform.

    Lookup order:
    1. Existing binary in jean_claude/bin/
    2. Compile from source (if Cargo is installed)
    """
    os_name, arch = _get_platform_info()
    bin_dir = Path(__file__).parent / "bin"
    binary_path = bin_dir / f"signal-cli-{arch}-{os_name}"

    # 1. Check for existing binary (bundled or previously compiled)
    if binary_path.exists():
        return binary_path

    # 2. Try to compile from source
    compiled = _try_compile_binary(bin_dir, os_name, arch)
    if compiled:
        return compiled

    raise JeanClaudeError(
        f"Signal CLI not found for {os_name}/{arch}.\n"
        f"Tried:\n"
        f"  - Binary: {binary_path}\n"
        f"  - Compile from source: Cargo not installed or compilation failed\n"
        "Install Rust and run: cd signal && cargo build --release"
    )


def _run_signal_cli(*args: str, capture: bool = True) -> dict | list | None:
    """Run the signal-cli binary and return parsed JSON output.

    Args:
        *args: Command line arguments to pass to signal-cli
        capture: If True, capture and parse JSON output. If False, let output flow to terminal.

    Returns:
        Parsed JSON output, or None if capture=False
    """
    if not is_signal_enabled():
        raise JeanClaudeError(
            "Signal is disabled. Enable via:\n"
            "  jean-claude config set enable_signal true"
        )
    cli_path = _get_signal_cli_path()
    cmd = [str(cli_path), *args]
    logger.debug("Running signal-cli", args=args)

    if not capture:
        # Let output flow directly (for link command with QR code)
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise JeanClaudeError(
                f"signal-cli failed with exit code {result.returncode}"
            )
        return None

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = (
            result.stderr.strip() if result.stderr else f"Exit code {result.returncode}"
        )
        raise JeanClaudeError(f"Signal error: {error_msg}")

    # Parse JSON from stdout
    stdout = result.stdout.strip()
    if not stdout:
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # Log unexpected non-JSON output for debugging
        logger.warning(
            "signal-cli returned non-JSON output",
            args=args,
            stdout_preview=stdout[:200] if len(stdout) > 200 else stdout,
        )
        return None


def _run_signal_cli_with_stdin(*args: str, stdin_data: str) -> dict | list | None:
    """Run signal-cli with stdin input and return parsed JSON output.

    Args:
        *args: Command line arguments to pass to signal-cli
        stdin_data: Data to pass to stdin

    Returns:
        Parsed JSON output
    """
    if not is_signal_enabled():
        raise JeanClaudeError(
            "Signal is disabled. Enable via:\n"
            "  jean-claude config set enable_signal true"
        )
    cli_path = _get_signal_cli_path()
    cmd = [str(cli_path), *args]
    logger.debug("Running signal-cli with stdin", args=args)

    result = subprocess.run(cmd, input=stdin_data, capture_output=True, text=True)

    if result.returncode != 0:
        error_msg = (
            result.stderr.strip() if result.stderr else f"Exit code {result.returncode}"
        )
        raise JeanClaudeError(f"Signal error: {error_msg}")

    # Parse JSON from stdout
    stdout = result.stdout.strip()
    if not stdout:
        return None

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        logger.warning(
            "signal-cli returned non-JSON output",
            args=args,
            stdout_preview=stdout[:200] if len(stdout) > 200 else stdout,
        )
        return None


@click.group()
def cli():
    """Signal CLI - send messages and list chats.

    Requires linking via QR code scan. Uses the Signal protocol for
    end-to-end encrypted messaging.
    """


@cli.command()
@click.option(
    "-d", "--device-name", default="jean-claude", help="Device name shown in Signal"
)
def link(device_name: str):
    """Link as a secondary device by scanning QR code.

    Opens a QR code in the terminal. Scan with Signal on your phone:
    Settings > Linked Devices > Link New Device.
    """
    _run_signal_cli("link", "--device-name", device_name, capture=False)


@cli.command()
def status():
    """Show Signal connection status."""
    result = _run_signal_cli("status")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def whoami():
    """Show account information."""
    result = _run_signal_cli("whoami")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to return")
def chats(max_results: int):
    """List Signal chats (contacts and groups).

    Shows contacts and groups with names and IDs.
    """
    result = _run_signal_cli("chats", "--max-results", str(max_results))
    if result and isinstance(result, list):
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("recipient")
def send(recipient: str):
    """Send a Signal message.

    RECIPIENT: UUID of the contact to send to.

    Message body is read from stdin.

    \b
    Examples:
        echo "Hello!" | jean-claude signal send "abc123-uuid"
    """
    body = read_body_stdin()
    result = _run_signal_cli_with_stdin("send", recipient, stdin_data=body)
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
def receive():
    """Receive pending messages.

    Downloads and displays any pending messages from Signal.
    """
    result = _run_signal_cli("receive")
    if result:
        click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("chat_id")
@click.option("-n", "--max-results", default=50, help="Maximum messages to return")
def messages(chat_id: str, max_results: int):
    """Read stored messages from a chat.

    CHAT_ID: UUID of the contact or hex group ID.

    Messages are stored locally after running 'receive'.

    \b
    Examples:
        jean-claude signal messages "abc123-def456-..."
        jean-claude signal messages "abc123-def456-..." -n 20
    """
    result = _run_signal_cli("messages", chat_id, "-n", str(max_results))
    if result:
        click.echo(json.dumps(result, indent=2))
