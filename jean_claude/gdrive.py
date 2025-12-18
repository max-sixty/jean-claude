"""Google Drive CLI - list, search, and manage files."""

from __future__ import annotations

import io
import json
from pathlib import Path

import click
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from .auth import get_credentials


def get_drive():
    return build("drive", "v3", credentials=get_credentials())


def format_file(f: dict) -> str:
    """Format a file for display."""
    is_folder = f.get("mimeType") == "application/vnd.google-apps.folder"
    size = f.get("size", "")
    if size:
        size = f" ({int(size):,} bytes)"
    modified = f.get("modifiedTime", "")[:10] if f.get("modifiedTime") else ""

    name = f['name']
    if is_folder:
        name += "/"

    lines = [
        click.style(f"ID: {f['id']}", dim=True),
        click.style(name, bold=True) + size,
    ]
    if modified:
        lines.append(f"Modified: {modified}")
    if f.get("webViewLink"):
        lines.append(f"Link: {f['webViewLink']}")

    return "\n".join(lines)


@click.group()
def cli():
    """Google Drive CLI - list, search, and manage files."""
    pass


@cli.command("list")
@click.option("--folder", help="Folder ID to list (default: root)")
@click.option("-n", "--max-results", default=20, help="Maximum results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_files(folder: str | None, max_results: int, as_json: bool):
    """List files in a folder."""
    parent = folder or "root"
    query = f"'{parent}' in parents and trashed = false"

    result = get_drive().files().list(
        q=query,
        pageSize=max_results,
        fields="files(id, name, mimeType, size, modifiedTime, webViewLink)",
        orderBy="folder, name",
    ).execute()

    files = result.get("files", [])

    if not files:
        click.echo("No files found.", err=True)
        return

    if as_json:
        click.echo(json.dumps(files, indent=2))
    else:
        for i, f in enumerate(files):
            if i > 0:
                click.echo("\n" + "─" * 40 + "\n")
            click.echo(format_file(f))


@cli.command()
@click.argument("query")
@click.option("-n", "--max-results", default=20, help="Maximum results")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def search(query: str, max_results: int, as_json: bool):
    """Search for files.

    QUERY: Search query (e.g., 'name contains "report"', 'mimeType = "application/pdf"')
    """
    # If query doesn't look like a Drive query, treat it as a name search
    if "=" not in query and "contains" not in query:
        query = f"name contains '{query}' and trashed = false"
    elif "trashed" not in query.lower():
        query = f"({query}) and trashed = false"

    result = get_drive().files().list(
        q=query,
        pageSize=max_results,
        fields="files(id, name, mimeType, size, modifiedTime, webViewLink)",
    ).execute()

    files = result.get("files", [])

    if not files:
        click.echo("No files found.", err=True)
        return

    if as_json:
        click.echo(json.dumps(files, indent=2))
    else:
        for i, f in enumerate(files):
            if i > 0:
                click.echo("\n" + "─" * 40 + "\n")
            click.echo(format_file(f))


@cli.command()
@click.argument("file_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def get(file_id: str, as_json: bool):
    """Get file metadata."""
    f = get_drive().files().get(
        fileId=file_id,
        fields="id, name, mimeType, size, createdTime, modifiedTime, webViewLink, parents, owners",
    ).execute()

    if as_json:
        click.echo(json.dumps(f, indent=2))
    else:
        click.echo(format_file(f))
        if f.get("parents"):
            click.echo(f"Parents: {', '.join(f['parents'])}")
        if f.get("owners"):
            click.echo(f"Owner: {f['owners'][0].get('emailAddress', 'unknown')}")


@cli.command()
@click.argument("file_id")
@click.argument("output", type=click.Path())
def download(file_id: str, output: str):
    """Download a file.

    FILE_ID: The file ID to download
    OUTPUT: Local path to save the file
    """
    service = get_drive()

    # Get file metadata first
    f = service.files().get(fileId=file_id, fields="name, mimeType").execute()
    mime_type = f.get("mimeType", "")

    # Handle Google Docs formats (export instead of download)
    export_types = {
        "application/vnd.google-apps.document": "application/pdf",
        "application/vnd.google-apps.spreadsheet": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.google-apps.presentation": "application/pdf",
    }

    if mime_type in export_types:
        request = service.files().export_media(fileId=file_id, mimeType=export_types[mime_type])
    else:
        request = service.files().get_media(fileId=file_id)

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    Path(output).write_bytes(fh.getvalue())
    click.echo(f"Downloaded: {output}")


@cli.command()
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--folder", help="Parent folder ID (default: root)")
@click.option("--name", help="Name in Drive (default: local filename)")
def upload(file_path: str, folder: str | None, name: str | None):
    """Upload a file.

    FILE_PATH: Local file to upload
    """
    path = Path(file_path)
    file_name = name or path.name

    file_metadata = {"name": file_name}
    if folder:
        file_metadata["parents"] = [folder]

    media = MediaFileUpload(file_path)
    f = get_drive().files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink",
    ).execute()

    click.echo(f"Uploaded: {f['name']}")
    click.echo(f"ID: {f['id']}")
    if f.get("webViewLink"):
        click.echo(f"Link: {f['webViewLink']}")


@cli.command()
@click.argument("name")
@click.option("--folder", help="Parent folder ID (default: root)")
def mkdir(name: str, folder: str | None):
    """Create a folder.

    NAME: Folder name
    """
    file_metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if folder:
        file_metadata["parents"] = [folder]

    f = get_drive().files().create(
        body=file_metadata,
        fields="id, name, webViewLink",
    ).execute()

    click.echo(f"Created folder: {f['name']}")
    click.echo(f"ID: {f['id']}")
    if f.get("webViewLink"):
        click.echo(f"Link: {f['webViewLink']}")


@cli.command()
@click.argument("file_id")
@click.argument("email")
@click.option("--role", type=click.Choice(["reader", "commenter", "writer"]), default="reader", help="Permission level")
@click.option("--notify", is_flag=True, help="Send notification email")
def share(file_id: str, email: str, role: str, notify: bool):
    """Share a file or folder.

    FILE_ID: The file/folder ID to share
    EMAIL: Email address to share with
    """
    permission = {
        "type": "user",
        "role": role,
        "emailAddress": email,
    }

    get_drive().permissions().create(
        fileId=file_id,
        body=permission,
        sendNotificationEmail=notify,
    ).execute()

    click.echo(f"Shared with {email} as {role}")


@cli.command()
@click.argument("file_id")
def trash(file_id: str):
    """Move a file to trash."""
    get_drive().files().update(
        fileId=file_id,
        body={"trashed": True},
    ).execute()
    click.echo(f"Trashed: {file_id}")


@cli.command()
@click.argument("file_id")
def untrash(file_id: str):
    """Restore a file from trash."""
    get_drive().files().update(
        fileId=file_id,
        body={"trashed": False},
    ).execute()
    click.echo(f"Restored: {file_id}")
