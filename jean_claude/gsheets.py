"""Google Sheets CLI - read and write spreadsheet data."""

from __future__ import annotations

import json
import re
import sys

import click

from .auth import build_service
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def get_sheets():
    return build_service("sheets", "v4")


def _read_rows_from_stdin() -> list:
    """Read and validate JSON array of rows from stdin."""
    try:
        rows = json.load(sys.stdin)
    except json.JSONDecodeError as e:
        raise JeanClaudeError(f"Invalid JSON: {e}")
    if not isinstance(rows, list):
        raise JeanClaudeError("Input must be a JSON array of rows")
    return rows


def _get_sheet_id(service, spreadsheet_id: str, sheet_name: str) -> int:
    """Get sheet ID from sheet name."""
    meta = (
        service.spreadsheets()
        .get(spreadsheetId=spreadsheet_id, fields="sheets.properties")
        .execute()
    )
    for sheet in meta["sheets"]:
        if sheet["properties"]["title"] == sheet_name:
            return sheet["properties"]["sheetId"]
    raise JeanClaudeError(f"Sheet not found: {sheet_name}")


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
def read(spreadsheet_id: str, range_: str, sheet: str | None):
    """Read data from a spreadsheet. Returns JSON array of rows.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)

    Examples:
        jean-claude gsheets read 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
        jean-claude gsheets read 1BxiM... --range 'Sheet1!A1:D10'
        jean-claude gsheets read 1BxiM... --sheet 'Data'
    """
    service = get_sheets()

    # Build the range
    if range_:
        full_range = _normalize_range(range_)
    elif sheet:
        full_range = sheet
    else:
        # Get first sheet name
        meta = (
            service.spreadsheets()
            .get(spreadsheetId=spreadsheet_id, fields="sheets.properties.title")
            .execute()
        )
        if not meta["sheets"]:
            raise JeanClaudeError("Spreadsheet has no sheets")
        full_range = meta["sheets"][0]["properties"]["title"]

    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=spreadsheet_id,
            range=full_range,
        )
        .execute()
    )

    click.echo(json.dumps(result.get("values", []), indent=2))


@cli.command()
@click.argument("spreadsheet_id")
def info(spreadsheet_id: str):
    """Get spreadsheet metadata. Returns JSON.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    """
    service = get_sheets()
    result = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            fields="spreadsheetId,properties.title,sheets.properties",
        )
        .execute()
    )

    click.echo(json.dumps(result, indent=2))


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

    updates = result["updates"]
    logger.info(
        f"Appended {updates['updatedRows']} rows", range=updates["updatedRange"]
    )
    click.echo(
        json.dumps(
            {
                "updatedRows": updates["updatedRows"],
                "updatedRange": updates["updatedRange"],
            }
        )
    )


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

    logger.info(
        f"Updated {result['updatedCells']} cells",
        rows=result["updatedRows"],
        cols=result["updatedColumns"],
    )
    click.echo(
        json.dumps(
            {
                "updatedRange": result["updatedRange"],
                "updatedRows": result["updatedRows"],
                "updatedColumns": result["updatedColumns"],
                "updatedCells": result["updatedCells"],
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

    logger.info("Cleared range", range=result["clearedRange"])
    click.echo(json.dumps({"clearedRange": result["clearedRange"]}))


@cli.command("add-sheet")
@click.argument("spreadsheet_id")
@click.argument("sheet_name")
@click.option("--index", type=int, help="Position to insert (0 = first)")
def add_sheet(spreadsheet_id: str, sheet_name: str, index: int | None):
    """Add a new sheet to a spreadsheet.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    SHEET_NAME: Name for the new sheet

    Examples:
        jean-claude gsheets add-sheet SPREADSHEET_ID "February"
        jean-claude gsheets add-sheet SPREADSHEET_ID "Summary" --index 0
    """
    service = get_sheets()

    properties: dict = {"title": sheet_name}
    if index is not None:
        properties["index"] = index

    result = (
        service.spreadsheets()
        .batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": properties}}]},
        )
        .execute()
    )

    reply = result["replies"][0]["addSheet"]["properties"]
    logger.info("Added sheet", title=reply["title"], sheetId=reply["sheetId"])
    click.echo(
        json.dumps(
            {
                "sheetId": reply["sheetId"],
                "title": reply["title"],
                "index": reply["index"],
            }
        )
    )


@cli.command("delete-sheet")
@click.argument("spreadsheet_id")
@click.argument("sheet_name")
def delete_sheet(spreadsheet_id: str, sheet_name: str):
    """Delete a sheet from a spreadsheet.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    SHEET_NAME: Name of the sheet to delete

    Examples:
        jean-claude gsheets delete-sheet SPREADSHEET_ID "Old Data"
    """
    service = get_sheets()
    sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_name)

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
    ).execute()

    logger.info("Deleted sheet", title=sheet_name)
    click.echo(json.dumps({"deleted": sheet_name, "sheetId": sheet_id}))


def _column_to_index(col: str) -> int:
    """Convert column letter(s) to 0-based index. A=0, B=1, Z=25, AA=26."""
    if not col or not col.isalpha():
        raise JeanClaudeError(f"Invalid column: {col} (must be letters only)")
    result = 0
    for char in col.upper():
        result = result * 26 + (ord(char) - ord("A") + 1)
    return result - 1


@cli.command()
@click.argument("spreadsheet_id")
@click.argument("range_")
@click.option(
    "--by",
    "columns",
    multiple=True,
    required=True,
    help="Column to sort by (e.g., 'A', 'B desc'). Can be repeated.",
)
@click.option("--header", is_flag=True, help="First row is header (exclude from sort)")
def sort(spreadsheet_id: str, range_: str, columns: tuple[str, ...], header: bool):
    """Sort data in a range by one or more columns.

    SPREADSHEET_ID: The spreadsheet ID (from the URL)
    RANGE: A1 notation range (e.g., 'Sheet1!A1:D100')

    Examples:
        jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A
        jean-claude gsheets sort SPREADSHEET_ID 'Data!A1:Z50' --by B --by 'C desc'
        jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A --header
    """
    normalized_range = _normalize_range(range_)

    # Parse range to get sheet name and cell range
    if "!" in normalized_range:
        sheet_name, cell_range = normalized_range.split("!", 1)
    else:
        raise JeanClaudeError("Range must include sheet name (e.g., 'Sheet1!A1:D100')")

    # Parse cell range to get start/end columns and rows
    # Handle formats: A1:D100, A:D, A1:D
    match = re.match(r"([A-Z]+)(\d*):([A-Z]+)(\d*)", cell_range.upper())
    if not match:
        raise JeanClaudeError(f"Invalid range format: {cell_range}")

    start_col, start_row, end_col, end_row = match.groups()
    start_col_idx = _column_to_index(start_col)
    end_col_idx = _column_to_index(end_col) + 1  # Exclusive

    # Build sort specs
    sort_specs = []
    for col_spec in columns:
        parts = col_spec.split()
        col_letter = parts[0].upper()
        order = (
            "DESCENDING"
            if len(parts) > 1 and parts[1].lower() == "desc"
            else "ASCENDING"
        )
        col_idx = _column_to_index(col_letter)
        if col_idx < start_col_idx or col_idx >= end_col_idx:
            raise JeanClaudeError(
                f"Sort column {col_letter} is outside range {start_col}:{end_col}"
            )
        sort_specs.append({"dimensionIndex": col_idx, "sortOrder": order})

    service = get_sheets()
    sheet_id = _get_sheet_id(service, spreadsheet_id, sheet_name)

    # Build range spec
    range_spec: dict = {
        "sheetId": sheet_id,
        "startColumnIndex": start_col_idx,
        "endColumnIndex": end_col_idx,
    }

    if start_row:
        range_spec["startRowIndex"] = int(start_row) - 1  # 0-based
        if header:
            range_spec["startRowIndex"] += 1  # Skip header row
    if end_row:
        range_spec["endRowIndex"] = int(end_row)  # Exclusive

    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "requests": [{"sortRange": {"range": range_spec, "sortSpecs": sort_specs}}]
        },
    ).execute()

    sort_desc = ", ".join(columns)
    logger.info(f"Sorted range by {sort_desc}", range=normalized_range)
    click.echo(json.dumps({"sortedRange": normalized_range, "sortedBy": list(columns)}))
