"""Gmail CLI - search, draft, and send emails.

Rate Limits and Batching Strategy
==================================

Gmail API enforces per-user quota limits: 15,000 units/minute (≈250 units/second).

Quota Costs
-----------
- threads.modify: 5 units per thread
- threads.trash: 5 units per thread
- messages.batchModify: 50 units (up to 1000 messages)
- messages.get: 5 units per message
- messages.send: 100 units per message

jean-claude Batching Strategy
------------------------------

Thread operations (archive, mark-read, mark-unread, unarchive, trash):
    Uses threads.modify or threads.trash API
    - Cost: 5 units per thread
    - Processes threads individually with rate limit retry
    - Matches Gmail UI behavior (operates on entire conversations)

Message operations (star, unstar):
    Uses messages.batchModify API
    - Processes up to 1000 messages per API call
    - Cost: 50 units per call regardless of message count

Search operations:
    Fetches message details in batches of 15
    - Cost: 5 units per message
    - Delay between batches: 0.3 seconds

Error Handling
--------------
Rate limit errors (429) are automatically retried with exponential backoff:
    - Retry schedule: 2s, 4s, 8s (max 3 retries, total 14s wait)
    - User feedback during retry via stderr

Troubleshooting Rate Limits
----------------------------
If you encounter rate limits:
    1. Check concurrent clients: Other apps using Gmail API share your quota
    2. Wait between operations: Allow 5-10 seconds between large bulk operations
    3. Use query filters: For archive, use --query to filter server-side

References
----------
https://developers.google.com/gmail/api/reference/rest/v1/users.threads/modify
https://developers.google.com/workspace/gmail/api/reference/quota
"""

from __future__ import annotations

import base64
import html
import json
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path
from typing import NoReturn

import click
from googleapiclient.errors import HttpError

from .auth import build_service
from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)


def get_gmail():
    return build_service("gmail", "v1")


def get_people():
    return build_service("people", "v1")


def get_my_from_address(service=None) -> str:
    """Get the user's From address with display name.

    Checks sources in order of preference:
    1. Gmail send-as displayName (explicit user configuration)
    2. Google Account profile name via People API (requires userinfo.profile scope)
    3. Just the email address (fallback)

    Returns formatted as "Name <email>" or just "email" if no name found.
    """
    if service is None:
        service = get_gmail()

    # Get primary email and any configured display name from send-as settings
    send_as = service.users().settings().sendAs().list(userId="me").execute()
    email = ""
    for alias in send_as.get("sendAs", []):
        if alias.get("isPrimary"):
            email = alias.get("sendAsEmail", "")
            display_name = alias.get("displayName", "")
            if display_name:
                return formataddr((display_name, email))
            break

    if not email:
        email = service.users().getProfile(userId="me").execute()["emailAddress"]

    # Fallback: try Google Account profile name via People API
    # This mirrors Gmail's behavior when send-as displayName is empty
    try:
        people = get_people()
        profile = (
            people.people()
            .get(resourceName="people/me", personFields="names")
            .execute()
        )
        names = profile.get("names", [])
        for name in names:
            if name.get("metadata", {}).get("primary", False):
                if display_name := name.get("displayName"):
                    return formataddr((display_name, email))
        if names and (display_name := names[0].get("displayName")):
            return formataddr((display_name, email))
    except Exception as e:
        # People API requires userinfo.profile scope - user may need to re-auth
        logger.debug("Could not retrieve display name from People API", error=str(e))

    return email


def _wrap_batch_error(request_id: str, exception: Exception) -> NoReturn:
    """Raise a wrapped exception with context about which ID failed."""
    if isinstance(exception, HttpError) and exception.resp.status == 404:
        raise JeanClaudeError(f"Not found: {request_id}") from exception
    raise JeanClaudeError(f"Error processing {request_id}: {exception}") from exception


def _batch_callback(responses: dict):
    """Create a batch callback that stores responses by request_id."""

    def callback(request_id, response, exception):
        if exception:
            _wrap_batch_error(request_id, exception)
        responses[request_id] = response

    return callback


def _raise_on_error(request_id, _response, exception):
    """Batch callback that only raises exceptions (ignores responses)."""
    if exception:
        _wrap_batch_error(request_id, exception)


def _retry_on_rate_limit(func, max_retries: int = 3):
    """Execute a function with exponential backoff retry on rate limits.

    Args:
        func: Callable that executes a Gmail API request (must call .execute())
        max_retries: Maximum retry attempts (default 3, giving 2s, 4s, 8s delays)

    Returns:
        The result of func() on success

    Raises:
        JeanClaudeError: If rate limit persists after all retries
        HttpError: For non-rate-limit errors
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except HttpError as e:
            if e.resp.status == 429:
                if attempt < max_retries:
                    delay = 2 ** (attempt + 1)  # 2s, 4s, 8s
                    logger.warning(
                        f"Rate limited, retrying in {delay}s",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    time.sleep(delay)
                    continue
                raise JeanClaudeError(
                    f"Gmail API rate limit exceeded after {max_retries} retries."
                )
            raise


def _batch_modify_labels(
    service,
    message_ids: list[str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
):
    """Modify labels on messages using Gmail's batchModify API.

    Gmail API quota: messages.batchModify costs 50 units for up to 1000 messages
    Example: 1000 messages = 50 units (single API call)
    See module docstring for detailed analysis

    Rate limit handling: Automatically retries with exponential backoff (2s, 4s, 8s)
    before failing. Most rate limits resolve within a few seconds.

    Args:
        service: Gmail API service instance
        message_ids: List of message IDs to process (up to 1000 per call)
        add_label_ids: Label IDs to add (e.g., ["STARRED", "INBOX"])
        remove_label_ids: Label IDs to remove (e.g., ["UNREAD"])
    """
    if not message_ids:
        return

    logger.info(
        f"Modifying labels on {len(message_ids)} messages",
        add_labels=add_label_ids,
        remove_labels=remove_label_ids,
    )

    # batchModify supports up to 1000 messages per call
    chunk_size = 1000

    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i : i + chunk_size]
        body = {"ids": chunk}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        _retry_on_rate_limit(
            lambda b=body: service.users()
            .messages()
            .batchModify(userId="me", body=b)
            .execute()
        )
        logger.debug(
            f"Processed {i + len(chunk)}/{len(message_ids)} messages",
            chunk_size=len(chunk),
        )

        # Add small delay between 1000-message chunks (only needed for 1000+ messages)
        if i + chunk_size < len(message_ids):
            time.sleep(0.5)


def _modify_thread_labels(
    service,
    thread_ids: list[str],
    add_label_ids: list[str] | None = None,
    remove_label_ids: list[str] | None = None,
):
    """Modify labels on entire threads using Gmail's threads.modify API.

    This modifies all messages in each thread atomically, matching Gmail UI behavior.
    When you archive a thread in Gmail's UI, all messages in that thread are archived.

    Args:
        service: Gmail API service instance
        thread_ids: List of thread IDs to process
        add_label_ids: Label IDs to add (e.g., ["STARRED", "INBOX"])
        remove_label_ids: Label IDs to remove (e.g., ["INBOX"])
    """
    if not thread_ids:
        return

    logger.info(
        f"Modifying labels on {len(thread_ids)} threads",
        add_labels=add_label_ids,
        remove_labels=remove_label_ids,
    )

    for thread_id in thread_ids:
        body = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        _retry_on_rate_limit(
            lambda tid=thread_id, b=body: service.users()
            .threads()
            .modify(userId="me", id=tid, body=b)
            .execute()
        )
        logger.debug(f"Modified thread {thread_id}")


def _batch_fetch(
    service, items: list[dict], build_request, chunk_size: int = 15
) -> dict:
    """Batch fetch full details for a list of items.

    Args:
        service: Gmail API service instance
        items: List of dicts with 'id' keys (from list API response)
        build_request: Callable(service, item_id) -> request object
        chunk_size: Items per batch (15 for messages, 10 for threads)

    Returns:
        Dict mapping item ID to full response
    """
    responses = {}
    for i in range(0, len(items), chunk_size):
        chunk = items[i : i + chunk_size]
        batch = service.new_batch_http_request(callback=_batch_callback(responses))
        for item in chunk:
            batch.add(build_request(service, item["id"]), request_id=item["id"])
        batch.execute()
        if i + chunk_size < len(items):
            time.sleep(0.3)
    return responses


def _strip_html(html: str) -> str:
    """Strip HTML tags for basic text extraction."""
    import re

    # Remove script and style elements
    html = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE
    )
    # Replace common block elements with newlines
    html = re.sub(r"<(br|p|div|tr|li)[^>]*>", "\n", html, flags=re.IGNORECASE)
    # Remove remaining tags
    html = re.sub(r"<[^>]+>", "", html)
    # Decode common HTML entities
    html = (
        html.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    # Collapse multiple newlines
    html = re.sub(r"\n\s*\n+", "\n\n", html)
    return html.strip()


def _decode_part(part: dict) -> str:
    """Decode base64 body data from a MIME part."""
    return base64.urlsafe_b64decode(part["body"]["data"]).decode(
        "utf-8", errors="replace"
    )


def _find_body_parts(payload: dict) -> tuple[dict | None, dict | None]:
    """Find text/plain and text/html parts in a single traversal.

    Returns (text_part, html_part) - the raw part dicts, not decoded.
    """
    text_part: dict | None = None
    html_part: dict | None = None

    def traverse(part: dict) -> bool:
        """Traverse MIME tree. Returns True to stop (found both)."""
        nonlocal text_part, html_part
        mime = part.get("mimeType", "")

        if part.get("body", {}).get("data"):
            # Treat missing/empty mimeType as text/plain (simple emails without parts)
            if mime in ("text/plain", "") and text_part is None:
                text_part = part
            elif mime == "text/html" and html_part is None:
                html_part = part

            if text_part is not None and html_part is not None:
                return True

        for subpart in part.get("parts", []):
            if traverse(subpart):
                return True
        return False

    traverse(payload)
    return text_part, html_part


def decode_body(payload: dict) -> str:
    """Extract text body from message payload. Falls back to HTML if no plain text."""
    text_part, html_part = _find_body_parts(payload)
    if text_part is not None:
        return _decode_part(text_part)
    if html_part is not None:
        return _strip_html(_decode_part(html_part))
    return ""


def extract_html_body(payload: dict) -> str | None:
    """Extract raw HTML body from message payload, if present."""
    _, html_part = _find_body_parts(payload)
    if html_part is not None:
        return _decode_part(html_part)
    return None


def extract_body(payload: dict) -> tuple[str, str | None]:
    """Extract text and HTML body from message payload in a single traversal.

    Returns (text_body, html_body) where:
    - text_body: Plain text for display (falls back to stripped HTML if no plain text)
    - html_body: Raw HTML for reply quoting (None if no HTML part)
    """
    text_part, html_part = _find_body_parts(payload)

    # Decode HTML first (needed for both html_body and potential fallback)
    html = _decode_part(html_part) if html_part else None

    # Build display text: prefer plain text, fall back to stripped HTML
    if text_part is not None:
        display_text = _decode_part(text_part)
    elif html is not None:
        display_text = _strip_html(html)
    else:
        display_text = ""

    return display_text, html


def _write_email_json(
    prefix: str, id_: str, summary: dict, body: str, html_body: str | None
) -> str:
    """Write email data to .tmp/ as JSON, return file path.

    Creates a self-contained JSON file with the full body instead of snippet.
    """
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(exist_ok=True)

    file_data = {k: v for k, v in summary.items() if k != "snippet"}
    file_data["body"] = body
    if html_body:
        file_data["html_body"] = html_body

    file_path = tmp_dir / f"{prefix}-{id_}.json"
    file_path.write_text(json.dumps(file_data, indent=2))
    return str(file_path)


def extract_message_summary(msg: dict) -> dict:
    """Extract essential fields from a message for compact output.

    Writes self-contained JSON to .tmp/ with full body (and html_body when present).
    """
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    result = {
        "id": msg["id"],
        "threadId": msg.get("threadId"),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
    }
    if cc := headers.get("Cc"):
        result["cc"] = cc

    payload = msg.get("payload", {})
    body, html_body = extract_body(payload)
    result["file"] = _write_email_json("email", msg["id"], result, body, html_body)
    return result


def extract_thread_summary(thread: dict) -> dict:
    """Extract essential fields from a thread for compact output.

    Returns info about the thread with the latest message's details.
    Gmail UI shows threads, not individual messages, so this matches that view.
    """
    messages = thread["messages"]
    if not messages:
        return {"threadId": thread["id"], "messageCount": 0}

    # Get the latest message for display
    latest_msg = messages[-1]
    headers = {
        h["name"]: h["value"] for h in latest_msg.get("payload", {}).get("headers", [])
    }

    # Aggregate labels across all messages in thread
    all_labels = set()
    unread_count = 0
    for msg in messages:
        labels = msg.get("labelIds", [])
        all_labels.update(labels)
        if "UNREAD" in labels:
            unread_count += 1

    result = {
        "threadId": thread["id"],
        "messageCount": len(messages),
        "unreadCount": unread_count,
        "latestMessageId": latest_msg["id"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": headers.get("Date", ""),
        "snippet": latest_msg.get("snippet", ""),
        "labels": sorted(all_labels),
    }
    if cc := headers.get("Cc"):
        result["cc"] = cc

    payload = latest_msg.get("payload", {})
    body, html_body = extract_body(payload)
    result["file"] = _write_email_json("thread", thread["id"], result, body, html_body)

    return result


def extract_draft_summary(draft: dict) -> dict:
    """Extract essential fields from a draft for compact output."""
    msg = draft.get("message", {})
    headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
    result = {
        "id": draft["id"],
        "messageId": msg.get("id"),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "snippet": msg.get("snippet", ""),
    }
    if cc := headers.get("Cc"):
        result["cc"] = cc
    return result


def draft_url(draft_result: dict) -> str:
    """Get Gmail URL for a draft."""
    return f"https://mail.google.com/mail/u/0/#drafts/{draft_result['message']['id']}"


@click.group()
def cli():
    """Gmail CLI - search, draft, and send emails."""


@cli.command()
@click.option("-n", "--max-results", default=100, help="Maximum results")
@click.option("--unread", is_flag=True, help="Only show unread threads")
@click.option("--page-token", help="Token for next page of results")
def inbox(max_results: int, unread: bool, page_token: str | None):
    """List threads in inbox.

    Returns threads (conversations) matching Gmail UI behavior.
    A thread shows as unread if ANY message in it is unread.
    """
    query = "in:inbox"
    if unread:
        query += " is:unread"
    _search_threads(query, max_results, page_token)


@cli.command()
@click.argument("query")
@click.option("-n", "--max-results", default=100, help="Maximum results")
@click.option("--page-token", help="Token for next page of results")
def search(query: str, max_results: int, page_token: str | None):
    """Search Gmail messages.

    QUERY: Gmail search query (e.g., 'is:unread', 'from:someone@example.com')
    """
    _search_messages(query, max_results, page_token)


@cli.command()
@click.argument("message_id")
def get(message_id: str):
    """Get a single message by ID, written to file.

    Fetches the full message content and writes it to .tmp/email-ID.json.
    Returns the message summary JSON to stdout.

    Example:
        jean-claude gmail get 19b51f93fcf3f8ca
    """
    service = get_gmail()
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    summary = extract_message_summary(msg)
    click.echo(json.dumps(summary, indent=2))


def _search_messages(query: str, max_results: int, page_token: str | None = None):
    """Shared search implementation."""
    logger.info(f"Searching messages: {query}", max_results=max_results)
    service = get_gmail()
    list_kwargs = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        list_kwargs["pageToken"] = page_token
    results = service.users().messages().list(**list_kwargs).execute()
    messages = results.get("messages", [])
    next_page_token = results.get("nextPageToken")
    logger.debug(f"Found {len(messages)} messages")

    if not messages:
        output: dict = {"messages": []}
        if next_page_token:
            output["nextPageToken"] = next_page_token
        click.echo(json.dumps(output, indent=2))
        return

    # Batch fetch messages (15/chunk × 5 units = 75 units, 0.3s delay)
    responses = _batch_fetch(
        service,
        messages,
        lambda svc, mid: svc.users().messages().get(userId="me", id=mid, format="full"),
        chunk_size=15,
    )
    detailed = [
        extract_message_summary(responses[m["id"]])
        for m in messages
        if m["id"] in responses
    ]
    output = {"messages": detailed}
    if next_page_token:
        output["nextPageToken"] = next_page_token
    click.echo(json.dumps(output, indent=2))


def _search_threads(query: str, max_results: int, page_token: str | None = None):
    """Search for threads, returning thread-level summaries.

    This matches Gmail UI behavior where conversations (threads) are shown,
    not individual messages. A thread appears in inbox if ANY message has
    INBOX label, and shows as unread if ANY message is unread.
    """
    logger.info(f"Searching threads: {query}", max_results=max_results)
    service = get_gmail()
    list_kwargs = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        list_kwargs["pageToken"] = page_token
    results = service.users().threads().list(**list_kwargs).execute()
    threads = results.get("threads", [])
    next_page_token = results.get("nextPageToken")
    logger.debug(f"Found {len(threads)} threads")

    if not threads:
        output: dict = {"threads": []}
        if next_page_token:
            output["nextPageToken"] = next_page_token
        click.echo(json.dumps(output, indent=2))
        return

    # Batch fetch threads (10/chunk - threads.get is heavier than messages.get)
    responses = _batch_fetch(
        service,
        threads,
        lambda svc, tid: svc.users().threads().get(userId="me", id=tid, format="full"),
        chunk_size=10,
    )
    detailed = [
        extract_thread_summary(responses[t["id"]])
        for t in threads
        if t["id"] in responses
    ]
    output = {"threads": detailed}
    if next_page_token:
        output["nextPageToken"] = next_page_token
    click.echo(json.dumps(output, indent=2))


# Draft command group
@cli.group()
def draft():
    """Manage email drafts."""
    pass


@draft.command("create")
def draft_create():
    """Create a new email draft from JSON stdin.

    JSON fields: to (required), subject (required), body (required), cc, bcc

    Example:
        echo '{"to": "x@y.com", "subject": "Hi!", "body": "Hello!"}' | jean-claude gmail draft create
    """
    data = json.load(sys.stdin)
    for field in ("to", "subject", "body"):
        if field not in data:
            raise click.UsageError(f"Missing required field: {field}")

    service = get_gmail()
    msg = MIMEText(data["body"])
    msg["from"] = get_my_from_address(service)
    msg["to"] = data["to"]
    msg["subject"] = data["subject"]
    if data.get("cc"):
        msg["cc"] = data["cc"]
    if data.get("bcc"):
        msg["bcc"] = data["bcc"]

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    logger.info(f"Draft created: {result['id']}", url=draft_url(result))


@draft.command("send")
@click.argument("draft_id")
def draft_send(draft_id: str):
    """Send an existing draft.

    Example:
        jean-claude gmail draft send r-123456789
    """
    result = (
        get_gmail().users().drafts().send(userId="me", body={"id": draft_id}).execute()
    )
    logger.info(f"Sent: {result['id']}")


def _format_gmail_date(date_str: str) -> str:
    """Format date string to Gmail's reply format: 'Mon, 22 Dec 2025 at 02:50'."""
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(date_str)
        # Gmail format: "Mon, 22 Dec 2025 at 02:50"
        return dt.strftime("%a, %d %b %Y at %H:%M")
    except ValueError as e:
        # Malformed date header - log for debugging, fall back to original
        logger.warning("Could not parse date header", date=date_str, error=str(e))
        return date_str


def _build_quoted_reply(
    body: str, original_body: str, from_addr: str, date: str
) -> str:
    """Build plain text reply body with Gmail-style quoted original message.

    Format:
        [user's reply]

        On Mon, 22 Dec 2025 at 02:50, Sender Name <sender@example.com> wrote:

        > quoted line 1
        > quoted line 2
    """
    quoted_lines = [f"> {line}" for line in original_body.splitlines()]
    quoted_text = "\n".join(quoted_lines)

    formatted_date = _format_gmail_date(date)
    return f"{body}\n\nOn {formatted_date}, {from_addr} wrote:\n\n{quoted_text}\n"


def _text_to_html(text: str) -> str:
    """Convert plain text to HTML, preserving line breaks."""
    escaped = html.escape(text)
    return escaped.replace("\n", "<br>\n")


def _build_html_quoted_reply(
    body: str, original_html: str | None, original_text: str, from_addr: str, date: str
) -> str:
    """Build HTML reply body with Gmail-style blockquote.

    Format matches Gmail's HTML replies with proper blockquote styling.
    If original was plain text, converts it to HTML.

    Note: When original_html is provided, it's embedded as-is. Gmail sanitizes
    HTML on send/display, so we trust the original content from Gmail's API.
    """
    formatted_date = _format_gmail_date(date)

    # Convert reply body to HTML
    reply_html = _text_to_html(body)

    # Use original HTML if available, otherwise convert plain text
    if original_html:
        quoted_content = original_html
    else:
        quoted_content = _text_to_html(original_text)

    # Escape from_addr since it may contain < > characters
    safe_from = html.escape(from_addr)
    safe_date = html.escape(formatted_date)

    return f"""<div dir="ltr">{reply_html}</div>
<br>
<div class="gmail_quote gmail_quote_container">
<div dir="ltr" class="gmail_attr">On {safe_date}, {safe_from} wrote:<br></div>
<blockquote class="gmail_quote" style="margin:0px 0px 0px 0.8ex;border-left:1px solid rgb(204,204,204);padding-left:1ex">
{quoted_content}
</blockquote>
</div>"""


def _build_forward_text(
    body: str, original_body: str, from_addr: str, date: str, subject: str
) -> str:
    """Build plain text forward body with Gmail-style header.

    Format:
        [user's message]

        ---------- Forwarded message ----------
        From: Sender Name <sender@example.com>
        Date: Mon, 22 Dec 2025 at 02:50
        Subject: Original subject

        [original message content]
    """
    formatted_date = _format_gmail_date(date)

    fwd_body = body
    if fwd_body:
        fwd_body += "\n\n"
    fwd_body += "---------- Forwarded message ----------\n"
    fwd_body += f"From: {from_addr}\n"
    fwd_body += f"Date: {formatted_date}\n"
    fwd_body += f"Subject: {subject}\n\n"
    fwd_body += original_body
    return fwd_body


def _build_forward_html(
    body: str,
    original_html: str | None,
    original_text: str,
    from_addr: str,
    date: str,
    subject: str,
) -> str:
    """Build HTML forward body with Gmail-style formatting.

    Format matches Gmail's HTML forwards.
    If original was plain text, converts it to HTML.
    """
    formatted_date = _format_gmail_date(date)

    # Convert forward body to HTML
    body_html = _text_to_html(body) if body else ""

    # Use original HTML if available, otherwise convert plain text
    if original_html:
        quoted_content = original_html
    else:
        quoted_content = _text_to_html(original_text)

    # Escape header values for HTML safety
    safe_from = html.escape(from_addr)
    safe_date = html.escape(formatted_date)
    safe_subject = html.escape(subject)

    return f"""<div dir="ltr">{body_html}</div>
<br>
<div class="gmail_quote gmail_quote_container">
<div dir="ltr">---------- Forwarded message ----------<br>
From: {safe_from}<br>
Date: {safe_date}<br>
Subject: {safe_subject}<br><br>
</div>
{quoted_content}
</div>"""


def _create_reply_draft(
    message_id: str, body: str, *, include_cc: bool, custom_cc: str | None = None
) -> tuple[str, str]:
    """Create a reply draft, returning (draft_id, draft_url).

    Args:
        message_id: ID of the message to reply to
        body: Reply body text
        include_cc: If True, include CC recipients (reply-all behavior)
        custom_cc: Optional user-specified CC addresses (overrides auto-CC)
    """
    service = get_gmail()
    # Use format="full" to get the message body for quoting
    original = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    my_from_addr = get_my_from_address(service)
    _, my_email = parseaddr(my_from_addr)

    headers = {
        h["name"].lower(): h["value"]
        for h in original.get("payload", {}).get("headers", [])
    }
    thread_id = original.get("threadId")

    subject = headers.get("subject", "")
    date = headers.get("date", "")
    from_addr = headers.get("from", "")
    reply_to = headers.get("reply-to", "")
    orig_to = headers.get("to", "")
    orig_cc = headers.get("cc", "")
    message_id_header = headers.get("message-id", "")
    orig_refs = headers.get("references", "")

    # Get original body for quoting (both plain text and HTML)
    payload = original.get("payload", {})
    original_body, original_html = extract_body(payload)

    # Use SENT label to detect own messages (handles send-as aliases)
    labels = original.get("labelIds", [])
    is_own_message = "SENT" in labels
    _, from_email = parseaddr(from_addr)

    # Build recipient list, excluding self (uses RFC 5322 parsing)
    def filter_addrs(addrs: str, also_exclude: str = "") -> str:
        """Filter addresses, removing self and optionally another email."""
        if not addrs:
            return ""
        exclude_lower = {my_email.lower()}
        if also_exclude:
            exclude_lower.add(also_exclude.lower())
        # Parse properly (handles quoted commas in display names)
        parsed = getaddresses([addrs])
        filtered = [
            (name, addr)
            for name, addr in parsed
            if addr and addr.lower() not in exclude_lower
        ]
        return ", ".join(formataddr(pair) for pair in filtered)

    # Determine recipients
    if reply_to:
        to_addr = reply_to
        # Exclude Reply-To addresses from CC to avoid duplicates
        _, reply_to_email = parseaddr(reply_to)
        cc_addr = filter_addrs(
            f"{orig_to}, {orig_cc}" if orig_cc else orig_to,
            also_exclude=reply_to_email,
        )
    elif is_own_message:
        to_addr = orig_to
        if not to_addr:
            raise click.UsageError(
                "Cannot reply to own message: original has no To header"
            )
        # Filter CC to remove self
        cc_addr = filter_addrs(orig_cc) if orig_cc else ""
    else:
        to_addr = from_addr
        all_others = f"{orig_to}, {orig_cc}" if orig_cc else orig_to
        cc_addr = filter_addrs(all_others, also_exclude=from_email)

    # Validate we have a recipient
    if not to_addr:
        raise click.UsageError("Cannot determine reply recipient: no From/To header")

    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    # Build both plain text and HTML versions
    plain_body = _build_quoted_reply(body, original_body, from_addr, date)
    html_body = _build_html_quoted_reply(
        body, original_html, original_body, from_addr, date
    )

    # Create multipart/alternative with both versions
    msg = MIMEMultipart("alternative")
    msg["from"] = my_from_addr
    msg["to"] = to_addr
    # Use custom CC if provided, otherwise use auto-detected CC for reply-all
    if custom_cc:
        msg["cc"] = custom_cc
    elif include_cc and cc_addr:
        msg["cc"] = cc_addr
    msg["subject"] = subject
    if message_id_header:
        msg["In-Reply-To"] = message_id_header
        # Append to existing References chain for proper threading
        msg["References"] = (
            f"{orig_refs} {message_id_header}" if orig_refs else message_id_header
        )

    # Attach plain text first, then HTML (email clients prefer later parts)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw, "threadId": thread_id}})
        .execute()
    )
    return result["id"], draft_url(result)


@draft.command("reply")
@click.argument("message_id")
def draft_reply(message_id: str):
    """Create a reply draft from JSON stdin.

    Preserves threading with the original message. Includes quoted original
    message in Gmail format.

    JSON fields: body (required), cc (optional)

    Example:
        echo '{"body": "Thanks!"}' | jean-claude gmail draft reply MSG_ID
        echo '{"body": "FYI", "cc": "a@x.com, b@y.com"}' | jean-claude gmail draft reply MSG_ID
    """
    data = json.load(sys.stdin)
    if "body" not in data:
        raise click.UsageError("Missing required field: body")

    draft_id, url = _create_reply_draft(
        message_id, data["body"], include_cc=False, custom_cc=data.get("cc")
    )
    logger.info(f"Reply draft created: {draft_id}", url=url)


@draft.command("reply-all")
@click.argument("message_id")
def draft_reply_all(message_id: str):
    """Create a reply-all draft from JSON stdin.

    Preserves threading and includes all original recipients. Includes quoted
    original message in Gmail format.

    JSON fields: body (required), cc (optional, overrides auto-detected CC)

    Example:
        echo '{"body": "Thanks!"}' | jean-claude gmail draft reply-all MSG_ID
    """
    data = json.load(sys.stdin)
    if "body" not in data:
        raise click.UsageError("Missing required field: body")

    draft_id, url = _create_reply_draft(
        message_id, data["body"], include_cc=True, custom_cc=data.get("cc")
    )
    logger.info(f"Reply-all draft created: {draft_id}", url=url)


@draft.command("forward")
@click.argument("message_id")
def draft_forward(message_id: str):
    """Create a forward draft from JSON stdin.

    JSON fields: to (required), body (optional, prepended to forwarded message)

    Example:
        echo '{"to": "x@y.com", "body": "FYI"}' | jean-claude gmail draft forward MSG_ID
    """
    data = json.load(sys.stdin)
    if "to" not in data:
        raise click.UsageError("Missing required field: to")

    service = get_gmail()
    original = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    headers = {
        h["name"].lower(): h["value"]
        for h in original.get("payload", {}).get("headers", [])
    }
    orig_subject = headers.get("subject", "")
    from_addr = headers.get("from", "")
    date = headers.get("date", "")

    # Get both plain text and HTML for proper forwarding
    payload = original.get("payload", {})
    original_text, original_html = extract_body(payload)

    # Build forward subject
    if orig_subject.lower().startswith("fwd:"):
        subject = orig_subject
    else:
        subject = f"Fwd: {orig_subject}"

    user_body = data.get("body", "")

    # Build both plain text and HTML versions (like replies)
    plain_body = _build_forward_text(
        user_body, original_text, from_addr, date, orig_subject
    )
    html_body = _build_forward_html(
        user_body, original_html, original_text, from_addr, date, orig_subject
    )

    # Create multipart/alternative with both versions
    msg = MIMEMultipart("alternative")
    msg["from"] = get_my_from_address(service)
    msg["to"] = data["to"]
    msg["subject"] = subject

    # Attach plain text first, then HTML (email clients prefer later parts)
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    logger.info(f"Forward draft created: {result['id']}", url=draft_url(result))


@draft.command("list")
@click.option("-n", "--max-results", default=20, help="Maximum results")
def draft_list(max_results: int):
    """List drafts.

    Example:
        jean-claude gmail draft list
    """
    service = get_gmail()
    results = (
        service.users().drafts().list(userId="me", maxResults=max_results).execute()
    )
    drafts = results.get("drafts", [])

    if not drafts:
        click.echo(json.dumps([]))
        return

    # Batch fetch draft details
    responses = {}
    batch = service.new_batch_http_request(callback=_batch_callback(responses))
    for d in drafts:
        batch.add(
            service.users().drafts().get(userId="me", id=d["id"], format="metadata"),
            request_id=d["id"],
        )
    batch.execute()

    detailed = [
        extract_draft_summary(responses[d["id"]])
        for d in drafts
        if d["id"] in responses
    ]
    click.echo(json.dumps(detailed, indent=2))


@draft.command("get")
@click.argument("draft_id")
def draft_get(draft_id: str):
    """Get a draft with full body, written to file.

    Example:
        jean-claude gmail draft get r-123456789
    """
    service = get_gmail()
    draft = (
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    )
    msg = draft.get("message", {})
    headers = {
        h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])
    }
    body = decode_body(msg.get("payload", {}))

    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(exist_ok=True)
    file_path = tmp_dir / f"draft-{draft_id}.txt"

    with open(file_path, "w") as f:
        for field in ["from", "to", "cc", "bcc", "subject", "date"]:
            f.write(f"{field.title()}: {headers.get(field, '')}\n")
        f.write(f"\n{body}")

    click.echo(str(file_path))


@draft.command("update")
@click.argument("draft_id")
def draft_update(draft_id: str):
    """Update an existing draft from JSON stdin.

    Preserves threading headers (In-Reply-To, References) from the original draft.
    Only fields provided in the JSON are updated; others remain unchanged.

    JSON fields (all optional): to, cc, bcc, subject, body

    Workflow for iterating on long emails:
        1. jean-claude gmail draft get DRAFT_ID  # writes to .tmp/draft-ID.txt
        2. Edit the file with your changes
        3. cat .tmp/draft-ID.txt | jean-claude gmail draft update DRAFT_ID

    Example:
        echo '{"cc": "new@example.com"}' | jean-claude gmail draft update DRAFT_ID
        echo '{"subject": "New subject", "body": "New body"}' | jean-claude gmail draft update DRAFT_ID
    """
    data = json.load(sys.stdin)
    if not data:
        raise click.UsageError("No fields provided to update")

    service = get_gmail()

    # Fetch existing draft to preserve threading headers
    existing = (
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    )
    existing_msg = existing.get("message", {})
    existing_headers = existing_msg.get("payload", {}).get("headers", [])
    thread_id = existing_msg.get("threadId")

    # Copy all headers from original, then apply updates
    headers = {h["name"].lower(): h["value"] for h in existing_headers}
    headers["body"] = decode_body(existing_msg.get("payload", {}))
    headers |= {k.lower(): v for k, v in data.items()}

    # Build new message
    msg = MIMEText(headers.get("body", ""))
    # Preserve original From header unless explicitly updated
    msg["from"] = headers.get("from") or get_my_from_address(service)
    for field in ["to", "cc", "bcc", "subject"]:
        if value := headers.get(field):
            msg[field] = value
    if in_reply_to := headers.get("in-reply-to"):
        msg["In-Reply-To"] = in_reply_to
    if references := headers.get("references"):
        msg["References"] = references

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id

    result = (
        service.users().drafts().update(userId="me", id=draft_id, body=body).execute()
    )
    logger.info(f"Updated draft: {result['id']}", url=draft_url(result))


@draft.command("delete")
@click.argument("draft_id")
def draft_delete(draft_id: str):
    """Permanently delete a draft.

    Example:
        jean-claude gmail draft delete r-123456789
    """
    get_gmail().users().drafts().delete(userId="me", id=draft_id).execute()
    logger.info(f"Deleted draft: {draft_id}")


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def star(message_ids: tuple[str, ...]):
    """Star messages."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), add_label_ids=["STARRED"])
    logger.info(f"Starred {len(message_ids)} messages", count=len(message_ids))


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def unstar(message_ids: tuple[str, ...]):
    """Remove star from messages."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), remove_label_ids=["STARRED"])
    logger.info(f"Unstarred {len(message_ids)} messages", count=len(message_ids))


@cli.command()
@click.argument("thread_ids", nargs=-1)
@click.option(
    "--query",
    "-q",
    help="Archive all inbox messages matching query (e.g., 'from:example.com')",
)
@click.option(
    "-n",
    "--max-results",
    default=100,
    help="Max threads to archive when using --query",
)
def archive(thread_ids: tuple[str, ...], query: str | None, max_results: int):
    """Archive threads (remove from inbox).

    Archives entire threads, matching Gmail UI behavior. When you archive a
    conversation in Gmail, all messages in that thread are archived together.

    Accepts thread IDs (from inbox/search output) or a query.

    Examples:
        jean-claude gmail archive THREAD_ID1 THREAD_ID2
        jean-claude gmail archive --query "from:newsletter@example.com"
    """
    if thread_ids and query:
        raise click.UsageError("Provide thread IDs or --query, not both")

    service = get_gmail()

    if query:
        full_query = f"in:inbox {query}"
        results = (
            service.users()
            .threads()
            .list(userId="me", q=full_query, maxResults=max_results)
            .execute()
        )
        ids = [t["id"] for t in results["threads"]] if "threads" in results else []
    else:
        ids = list(thread_ids)

    if not ids:
        logger.info("No threads to archive")
        return

    _modify_thread_labels(service, ids, remove_label_ids=["INBOX"])
    logger.info(f"Archived {len(ids)} threads", count=len(ids))


@cli.command()
@click.argument("thread_ids", nargs=-1, required=True)
def unarchive(thread_ids: tuple[str, ...]):
    """Move threads back to inbox."""
    service = get_gmail()
    ids = list(thread_ids)
    if not ids:
        logger.info("No threads to unarchive")
        return
    _modify_thread_labels(service, ids, add_label_ids=["INBOX"])
    logger.info(f"Moved {len(ids)} threads to inbox", count=len(ids))


@cli.command("mark-read")
@click.argument("thread_ids", nargs=-1, required=True)
def mark_read(thread_ids: tuple[str, ...]):
    """Mark threads as read (all messages in thread)."""
    service = get_gmail()
    ids = list(thread_ids)
    _modify_thread_labels(service, ids, remove_label_ids=["UNREAD"])
    logger.info(f"Marked {len(ids)} threads read", count=len(ids))


@cli.command("mark-unread")
@click.argument("thread_ids", nargs=-1, required=True)
def mark_unread(thread_ids: tuple[str, ...]):
    """Mark threads as unread."""
    service = get_gmail()
    ids = list(thread_ids)
    _modify_thread_labels(service, ids, add_label_ids=["UNREAD"])
    logger.info(f"Marked {len(ids)} threads unread", count=len(ids))


@cli.command()
@click.argument("thread_ids", nargs=-1, required=True)
def trash(thread_ids: tuple[str, ...]):
    """Move threads to trash (all messages in thread)."""
    service = get_gmail()
    ids = list(thread_ids)

    # Use threads.trash API (5 units per thread)
    chunk_size = 50
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i : i + chunk_size]
        batch = service.new_batch_http_request(callback=_raise_on_error)
        for tid in chunk:
            batch.add(
                service.users().threads().trash(userId="me", id=tid),
                request_id=tid,
            )
        batch.execute()
        if i + chunk_size < len(ids):
            time.sleep(0.3)

    logger.info(f"Trashed {len(ids)} threads", count=len(ids))


def _extract_attachments(parts: list, attachments: list) -> None:
    """Recursively extract attachment info from message parts."""
    for part in parts:
        filename = part.get("filename", "")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")

        if filename and attachment_id:
            attachments.append(
                {
                    "filename": filename,
                    "mimeType": part.get("mimeType", "application/octet-stream"),
                    "size": body.get("size", 0),
                    "attachmentId": attachment_id,
                }
            )

        # Recurse into nested parts
        if "parts" in part:
            _extract_attachments(part["parts"], attachments)


@cli.command()
@click.argument("message_id")
def attachments(message_id: str):
    """List attachments for a message.

    Example:
        jean-claude gmail attachments MSG_ID
    """
    service = get_gmail()
    msg = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    attachment_list: list[dict] = []
    payload = msg.get("payload", {})
    if "parts" in payload:
        _extract_attachments(payload["parts"], attachment_list)

    if not attachment_list:
        logger.info("No attachments found")
        return

    click.echo(json.dumps(attachment_list, indent=2))


@cli.command("attachment-download")
@click.argument("message_id")
@click.argument("attachment_id")
@click.argument("output", type=click.Path())
def attachment_download(message_id: str, attachment_id: str, output: str):
    """Download an attachment from a message.

    Use 'jean-claude gmail attachments MSG_ID' to get attachment IDs.

    \b
    Example:
        jean-claude gmail attachments MSG_ID
        jean-claude gmail attachment-download MSG_ID ATTACH_ID ./file.pdf
    """
    service = get_gmail()
    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )

    data = base64.urlsafe_b64decode(attachment["data"])
    Path(output).write_bytes(data)
    logger.info(f"Downloaded: {output}", bytes=len(data))
