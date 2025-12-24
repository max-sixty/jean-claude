"""Google Sheets CLI - read and write spreadsheet data."""

from __future__ import annotations

import json
import sys

import click
from googleapiclient.errors import HttpError

from .auth import build_service
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def get_sheets():
    return build_service("sheets", "v4")


def _handle_sheets_error(e: HttpError, spreadsheet_id: str | None = None) -> None:
    """Convert HttpError to JeanClaudeError with helpful message."""
    status = e.resp.status
    if status == 404:
        if spreadsheet_id:
            raise JeanClaudeError(f"Spreadsheet not found: {spreadsheet_id}")
        raise JeanClaudeError("Spreadsheet or range not found")
    if status == 403:
        raise JeanClaudeError("Permission denied. Check spreadsheet sharing settings.")
    if status == 400:
        # Usually invalid range format
        raise JeanClaudeError(f"Invalid request: {e._get_reason()}")
    raise JeanClaudeError(f"Sheets API error: {e._get_reason()}")


def _read_rows_from_stdin() -> list:
    """Read and validate JSON array of rows from stdin."""
    try:
        rows = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        raise JeanClaudeError(f"Invalid JSON: {e}")
    if not isinstance(rows, list):
        raise JeanClaudeError("Input must be a JSON array of rows")
    return rows


@click.group()
def cli():
    """Google Sheets CLI - read and write spreadsheet data."""
    pass


def _normalize_range(range_str: str) -> str:
    r"""Normalize a range string by removing shell escape sequences.

    Some shells/tools escape ! to \! which breaks A1 notation.
    Since \! is never valid in Google Sheets ranges, we can safely unescape it.
    """
    return range_str.replace("\\!", "!")


@cli.command()
@click.argument("spreadsheet_id")
@click.option(
    "--range", "range_", default="", help="A1 notation range (e.g., 'Sheet1!A1:D10')"
)
@click.option("--sheet", help="Sheet name (default: first sheet)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def read(spreadsheet_id: str, range_: str, sheet: str | None, as_json: bool):
    """Read data from a spreadsheet.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)

    Examples:
        jean-claude gsheets read 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
        jean-claude gsheets read 1BxiM... --range 'Sheet1!A1:D10'
        jean-claude gsheets read 1BxiM... --sheet 'Data' --json
    """
    service = get_sheets()

    # Build the range
    if range_:
        full_range = _normalize_range(range_)
    elif sheet:
        full_range = sheet
    else:
        # Get first sheet name
        try:
            meta = (
                service.spreadsheets()
                .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
                .execute()
            )
        except HttpError as e:
            _handle_sheets_error(e, spreadsheet_id)
        sheets = meta.get("sheets", [])
        if not sheets:
            raise JeanClaudeError("Spreadsheet has no sheets")
        full_range = sheets[0]["properties"]["title"]

    try:
        result = (
            service.spreadsheets()
            .values()
            .get(
                spreadsheetId=spreadsheet_id,
                range=full_range,
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e, spreadsheet_id)

    values = result.get("values", [])

    if not values:
        if as_json:
            click.echo(json.dumps([]))
        else:
            logger.info("No data found")
        return

    if as_json:
        click.echo(json.dumps(values, indent=2))
    else:
        # Simple table output
        for row in values:
            click.echo("\t".join(str(cell) for cell in row))


@cli.command()
@click.argument("spreadsheet_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def info(spreadsheet_id: str, as_json: bool):
    """Get spreadsheet metadata.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    """
    service = get_sheets()

    try:
        result = (
            service.spreadsheets()
            .get(
                spreadsheetId=spreadsheet_id,
                fields="spreadsheetId,properties.title,sheets.properties",
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e, spreadsheet_id)

    if as_json:
        click.echo(json.dumps(result, indent=2))
    else:
        click.echo(click.style(f"Title: {result['properties']['title']}", bold=True))
        click.echo(click.style(f"ID: {result['spreadsheetId']}", dim=True))
        click.echo()
        click.echo("Sheets:")
        for sheet in result.get("sheets", []):
            props = sheet["properties"]
            rows = props.get("gridProperties", {}).get("rowCount", "?")
            cols = props.get("gridProperties", {}).get("columnCount", "?")
            click.echo(f"  • {props['title']} ({rows} rows × {cols} cols)")


@cli.command()
@click.argument("title")
@click.option("--sheet", default="Sheet1", help="Initial sheet name (default: Sheet1)")
def create(title: str, sheet: str):
    """Create a new spreadsheet.

    TITLE: Title of the new spreadsheet

    Examples:
        jean-claude gsheets create "My New Spreadsheet"
        jean-claude gsheets create "Budget 2025" --sheet "January"
    """
    service = get_sheets()

    try:
        result = (
            service.spreadsheets()
            .create(
                body={
                    "properties": {"title": title},
                    "sheets": [{"properties": {"title": sheet}}],
                }
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e)

    spreadsheet_id = result["spreadsheetId"]
    url = result["spreadsheetUrl"]

    logger.info("Created spreadsheet", id=spreadsheet_id)
    click.echo(
        json.dumps(
            {
                "spreadsheetId": spreadsheet_id,
                "spreadsheetUrl": url,
                "title": title,
            }
        )
    )


@cli.command()
@click.argument("spreadsheet_id")
@click.option("--sheet", default="Sheet1", help="Sheet name (default: Sheet1)")
def append(spreadsheet_id: str, sheet: str):
    """Append rows to a spreadsheet. Reads JSON array from stdin.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)

    Input format: JSON array of rows, where each row is an array of cell values.

    Examples:
        echo '[["Alice", 100], ["Bob", 200]]' | jean-claude gsheets append SPREADSHEET_ID
        cat data.json | jean-claude gsheets append SPREADSHEET_ID --sheet 'Data'
    """
    rows = _read_rows_from_stdin()

    service = get_sheets()
    try:
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=sheet,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": rows},
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e, spreadsheet_id)

    updates = result.get("updates", {})
    updated_rows = updates.get("updatedRows", 0)
    updated_range = updates.get("updatedRange", "")

    logger.info(f"Appended {updated_rows} rows", range=updated_range)
    click.echo(json.dumps({"updatedRows": updated_rows, "updatedRange": updated_range}))


@cli.command()
@click.argument("spreadsheet_id")
@click.argument("range_")
def write(spreadsheet_id: str, range_: str):
    """Write data to a specific range. Reads JSON array from stdin.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    RANGE: A1 notation range (e.g., 'Sheet1!A1:C3')

    Input format: JSON array of rows, where each row is an array of cell values.

    Examples:
        echo '[["Name", "Score"], ["Alice", 100]]' | jean-claude gsheets write SPREADSHEET_ID 'Sheet1!A1:B2'
        cat data.json | jean-claude gsheets write SPREADSHEET_ID 'Data!A1'
    """
    rows = _read_rows_from_stdin()
    normalized_range = _normalize_range(range_)

    service = get_sheets()
    try:
        result = (
            service.spreadsheets()
            .values()
            .update(
                spreadsheetId=spreadsheet_id,
                range=normalized_range,
                valueInputOption="USER_ENTERED",
                body={"values": rows},
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e, spreadsheet_id)

    updated_rows = result.get("updatedRows", 0)
    updated_cols = result.get("updatedColumns", 0)
    updated_cells = result.get("updatedCells", 0)

    logger.info(f"Updated {updated_cells} cells", rows=updated_rows, cols=updated_cols)
    click.echo(
        json.dumps(
            {
                "updatedRange": result.get("updatedRange", ""),
                "updatedRows": updated_rows,
                "updatedColumns": updated_cols,
                "updatedCells": updated_cells,
            }
        )
    )


@cli.command()
@click.argument("spreadsheet_id")
@click.argument("range_")
def clear(spreadsheet_id: str, range_: str):
    """Clear data from a range (keeps formatting).

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    RANGE: A1 notation range (e.g., 'Sheet1!A1:C10')

    Examples:
        jean-claude gsheets clear SPREADSHEET_ID 'Sheet1!A2:Z1000'
        jean-claude gsheets clear SPREADSHEET_ID 'Data!A:Z'
    """
    normalized_range = _normalize_range(range_)

    service = get_sheets()
    try:
        result = (
            service.spreadsheets()
            .values()
            .clear(
                spreadsheetId=spreadsheet_id,
                range=normalized_range,
                body={},
            )
            .execute()
        )
    except HttpError as e:
        _handle_sheets_error(e, spreadsheet_id)

    cleared_range = result.get("clearedRange", normalized_range)
    logger.info("Cleared range", range=cleared_range)
    click.echo(json.dumps({"clearedRange": cleared_range}))
