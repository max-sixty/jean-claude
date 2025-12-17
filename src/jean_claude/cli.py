"""Main CLI entry point for jean-claude."""

from __future__ import annotations

import click

from .auth import main as auth_main
from .gcal import cli as gcal_cli
from .gdrive import cli as gdrive_cli
from .gmail import cli as gmail_cli
from .imessage import cli as imessage_cli


@click.group()
def cli():
    """jean-claude: Gmail, Calendar, Drive, and iMessage integration."""
    pass


cli.add_command(gmail_cli, name="gmail")
cli.add_command(gcal_cli, name="gcal")
cli.add_command(gdrive_cli, name="gdrive")
cli.add_command(imessage_cli, name="imessage")


@cli.command()
def auth():
    """Authenticate with Google APIs."""
    auth_main()


if __name__ == "__main__":
    cli()
