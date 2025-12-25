"""Google Docs CLI - read and write document content."""

from __future__ import annotations

import json
import sys

import click

from .auth import build_service
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def get_docs():
    return build_service("docs", "v1")


def _extract_text(doc: dict) -> str:
    """Extract plain text from a Google Docs document structure."""
    text_parts = []
    for element in doc.get("body", {}).get("content", []):
        if "paragraph" in element:
            for elem in element["paragraph"].get("elements", []):
                if "textRun" in elem:
                    text_parts.append(elem["textRun"]["content"])
    return "".join(text_parts)


def _get_end_index(doc: dict) -> int:
    """Get the end index for appending text (insert point at end of document)."""
    content = doc.get("body", {}).get("content", [])
    if not content:
        return 1
    # Scan backward for last element with endIndex (handles unusual structures)
    for elem in reversed(content):
        if "endIndex" in elem:
            return max(1, elem["endIndex"] - 1)
    return 1


@click.group()
def cli():
    """Google Docs CLI - read and write document content."""
    pass


@cli.command()
@click.argument("document_id")
@click.option("--json", "as_json", is_flag=True, help="Output full document structure")
def read(document_id: str, as_json: bool):
    """Read document content.

    DOCUMENT_ID: The document ID (from the URL)

    Examples:
        jean-claude gdocs read 1abc...xyz
        jean-claude gdocs read 1abc...xyz --json
    """
    service = get_docs()
    doc = service.documents().get(documentId=document_id).execute()

    if as_json:
        click.echo(json.dumps(doc, indent=2))
    else:
        text = _extract_text(doc)
        click.echo(text)


@cli.command()
@click.argument("title")
def create(title: str):
    """Create a new document.

    TITLE: Title of the new document

    Examples:
        jean-claude gdocs create "My Document"
        jean-claude gdocs create "Meeting Notes 2025-01-15"
    """
    service = get_docs()
    result = service.documents().create(body={"title": title}).execute()

    doc_id = result["documentId"]
    url = f"https://docs.google.com/document/d/{doc_id}/edit"

    logger.info("Created document", id=doc_id)
    click.echo(json.dumps({"documentId": doc_id, "documentUrl": url, "title": title}))


@cli.command()
@click.argument("document_id")
@click.option("--text", help="Text to append (otherwise reads from stdin)")
def append(document_id: str, text: str | None):
    """Append text to the end of a document.

    DOCUMENT_ID: The document ID (from the URL)

    Examples:
        jean-claude gdocs append 1abc...xyz --text "New paragraph"
        echo "Content from stdin" | jean-claude gdocs append 1abc...xyz
    """
    if text is None:
        if sys.stdin.isatty():
            raise JeanClaudeError("No text provided (use --text or pipe input)")
        text = sys.stdin.read()

    if not text:
        raise JeanClaudeError("No text provided (use --text or stdin)")

    service = get_docs()

    # Get minimal document data to find end index
    doc = (
        service.documents()
        .get(
            documentId=document_id,
            fields="body.content.endIndex",
        )
        .execute()
    )
    end_index = _get_end_index(doc)

    service.documents().batchUpdate(
        documentId=document_id,
        body={
            "requests": [
                {"insertText": {"location": {"index": end_index}, "text": text}}
            ]
        },
    ).execute()

    logger.info("Appended text", chars=len(text))
    click.echo(json.dumps({"appended": len(text), "documentId": document_id}))


@cli.command()
@click.argument("document_id")
@click.option("--find", "find_text", required=True, help="Text to find")
@click.option("--replace-with", "replace_text", required=True, help="Replacement text")
@click.option("--match-case", is_flag=True, help="Case-sensitive matching")
def replace(document_id: str, find_text: str, replace_text: str, match_case: bool):
    """Find and replace text in a document.

    DOCUMENT_ID: The document ID (from the URL)

    Examples:
        jean-claude gdocs replace 1abc...xyz --find "old text" --replace-with "new text"
        jean-claude gdocs replace 1abc...xyz --find "TODO" --replace-with "DONE" --match-case
    """
    service = get_docs()
    result = (
        service.documents()
        .batchUpdate(
            documentId=document_id,
            body={
                "requests": [
                    {
                        "replaceAllText": {
                            "containsText": {
                                "text": find_text,
                                "matchCase": match_case,
                            },
                            "replaceText": replace_text,
                        }
                    }
                ]
            },
        )
        .execute()
    )

    occurrences = (
        result.get("replies", [{}])[0]
        .get("replaceAllText", {})
        .get("occurrencesChanged", 0)
    )
    logger.info("Replaced occurrences", count=occurrences, document_id=document_id)
    click.echo(
        json.dumps({"occurrencesChanged": occurrences, "documentId": document_id})
    )


@cli.command()
@click.argument("document_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def info(document_id: str, as_json: bool):
    """Get document metadata.

    DOCUMENT_ID: The document ID (from the URL)
    """
    service = get_docs()
    doc = (
        service.documents()
        .get(
            documentId=document_id,
            fields="documentId,title,revisionId",
        )
        .execute()
    )

    if as_json:
        click.echo(
            json.dumps(
                {
                    "documentId": doc["documentId"],
                    "title": doc["title"],
                    "revisionId": doc.get("revisionId"),
                },
                indent=2,
            )
        )
    else:
        click.echo(f"Title: {doc['title']}")
        click.echo(f"ID: {doc['documentId']}")
        if doc.get("revisionId"):
            click.echo(f"Revision: {doc['revisionId']}")
