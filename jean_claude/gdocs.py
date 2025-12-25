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
def read(document_id: str):
    """Read document structure. Returns JSON.

    DOCUMENT_ID: The document ID (from the URL)

    Examples:
        jean-claude gdocs read 1abc...xyz
    """
    service = get_docs()
    doc = service.documents().get(documentId=document_id).execute()
    click.echo(json.dumps(doc, indent=2))


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
def info(document_id: str):
    """Get document metadata. Returns JSON.

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

    click.echo(json.dumps(doc, indent=2))
