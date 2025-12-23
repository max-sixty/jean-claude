"""Gmail CLI - search, draft, and send emails.

Rate Limits and Batching Strategy
==================================

Gmail API enforces per-user quota limits: 15,000 units/minute (≈250 units/second).

Quota Costs
-----------
- messages.batchModify: 50 units (up to 1000 messages)
- messages.modify: 5 units per message
- messages.get: 5 units per message
- messages.trash: 5 units per message
- messages.send: 100 units per message

jean-claude Batching Strategy
------------------------------

Label operations (star, archive, mark-read, mark-unread, unarchive):
    Uses messages.batchModify API
    - Processes up to 1000 messages per API call
    - Cost: 50 units per call regardless of message count
    - Chunk size: 1000 messages
    - Delay between chunks: 0.5 seconds (only for 1000+ messages)
    - Rate limits virtually impossible to hit with normal usage

    Examples:
        - Archive 50 messages = 50 units (one API call)
        - Archive 1000 messages = 50 units (one API call)
        - Archive 2500 messages = 150 units (three API calls)

Trash operations:
    Uses individual messages.trash calls (no batchTrash API)
    - Batch HTTP requests with 50 messages per batch
    - Cost: 5 units per message
    - Delay between batches: 0.3 seconds
    - Throughput: ~833 messages/minute
    - Note: Consider using archive instead for bulk inbox cleanup

Search operations:
    Fetches message details in batches of 15
    - Cost: 5 units per message
    - Delay between batches: 0.3 seconds
    - Throughput: ~50 messages/second

Error Handling
--------------
Rate limit errors (429) are automatically retried with exponential backoff:
    - Retry schedule: 2s, 4s, 8s (max 3 retries, total 14s wait)
    - User feedback during retry via stderr
    - If retries exhausted, provides actionable recovery guidance

Rate limit error output includes:
    - Progress tracking (how many messages succeeded)
    - Partial completion (remaining message IDs for retry)
    - Actionable guidance (specific commands to run)

Troubleshooting Rate Limits
----------------------------
If you encounter rate limits:
    1. Check concurrent clients: Other apps using Gmail API share your quota
    2. Wait between operations: Allow 5-10 seconds between large bulk operations
    3. Use query filters: For archive/trash, use --query to filter server-side
    4. Consider daily limits: Daily sending limits (500/2000) are separate from quota

References
----------
https://developers.google.com/gmail/api/reference/rest/v1/users.messages/batchModify
https://developers.google.com/workspace/gmail/api/reference/quota
"""

from __future__ import annotations

import base64
import json
import logging
import sys
import time
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parseaddr
from pathlib import Path

import click
from googleapiclient.discovery import build

from .auth import get_credentials

# Silence noisy HTTP logging by default
logging.getLogger("googleapiclient.discovery").setLevel(logging.WARNING)
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.WARNING)


def get_gmail():
    return build("gmail", "v1", credentials=get_credentials())


def _batch_callback(responses: dict):
    """Create a batch callback that stores responses by request_id."""

    def callback(request_id, response, exception):
        if exception:
            raise exception
        responses[request_id] = response

    return callback


def _raise_on_error(_request_id, _response, exception):
    """Batch callback that only raises exceptions (ignores responses)."""
    if exception:
        raise exception


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
    from googleapiclient.errors import HttpError

    if not message_ids:
        return

    # batchModify supports up to 1000 messages per call
    chunk_size = 1000
    max_retries = 3
    processed_count = 0

    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i : i + chunk_size]
        body = {"ids": chunk}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        # Retry loop with exponential backoff for rate limits
        for attempt in range(max_retries + 1):
            try:
                service.users().messages().batchModify(userId="me", body=body).execute()
                processed_count += len(chunk)
                break  # Success - exit retry loop
            except HttpError as e:
                if e.resp.status == 429:
                    if attempt < max_retries:
                        delay = 2 ** (attempt + 1)  # 2s, 4s, 8s
                        click.echo(
                            f"Rate limited, retrying in {delay}s... "
                            f"(attempt {attempt + 1}/{max_retries})",
                            err=True,
                        )
                        time.sleep(delay)
                        continue
                    # Exhausted retries
                    remaining = message_ids[i:]
                    raise click.ClickException(
                        f"Gmail API rate limit exceeded after {max_retries} retries.\n\n"
                        f"Progress: Successfully processed {processed_count} of {len(message_ids)} messages.\n"
                        f"Remaining: {len(remaining)} messages still need processing.\n\n"
                        f"Action required:\n"
                        f"1. Wait 30-60 seconds for rate limit to reset\n"
                        f"2. Retry with the remaining {len(remaining)} message IDs:\n"
                        f"   {' '.join(remaining[:10])}{'...' if len(remaining) > 10 else ''}\n\n"
                        f"Note: Using batchModify (50 units per 1000 messages). "
                        f"See module docstring (help jean_claude.gmail) for details."
                    )
                elif e.resp.status == 404:
                    remaining = message_ids[i:]
                    raise click.ClickException(
                        f"One or more message IDs not found.\n\n"
                        f"Progress: Successfully processed {processed_count} of {len(message_ids)} messages.\n"
                        f"Failed batch: {' '.join(chunk[:10])}{'...' if len(chunk) > 10 else ''}\n\n"
                        f"Possible causes:\n"
                        f"- Message(s) were deleted\n"
                        f"- Invalid message ID format\n"
                        f"- Message belongs to a different account\n\n"
                        f"Action required: Verify the message IDs and retry with valid IDs only."
                    )
                elif e.resp.status in (401, 403):
                    raise click.ClickException(
                        f"Gmail API authentication failed (HTTP {e.resp.status}).\n\n"
                        f"Possible causes:\n"
                        f"- Credentials expired or revoked\n"
                        f"- Insufficient permissions for this operation\n"
                        f"- API not enabled in Google Cloud project\n\n"
                        f"Action required:\n"
                        f"1. Check authentication: jean-claude status\n"
                        f"2. Re-authenticate if needed: jean-claude auth\n"
                        f"3. Verify required scopes are granted"
                    )
                raise

        # Add small delay between 1000-message chunks (only needed for 1000+ messages)
        if i + chunk_size < len(message_ids):
            time.sleep(0.5)


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


def decode_body(payload: dict) -> str:
    """Extract text body from message payload. Falls back to HTML if no plain text."""
    if "body" in payload and payload["body"].get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    if "parts" in payload:
        # First pass: look for text/plain
        for part in payload["parts"]:
            if part["mimeType"] == "text/plain" and part["body"].get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
            if part["mimeType"].startswith("multipart/"):
                if result := decode_body(part):
                    return result
        # Second pass: fall back to text/html
        for part in payload["parts"]:
            if part["mimeType"] == "text/html" and part["body"].get("data"):
                html = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                    "utf-8", errors="replace"
                )
                return _strip_html(html)
            if part["mimeType"].startswith("multipart/"):
                # Check nested parts for HTML
                if "parts" in part:
                    for subpart in part["parts"]:
                        if subpart["mimeType"] == "text/html" and subpart["body"].get(
                            "data"
                        ):
                            html = base64.urlsafe_b64decode(
                                subpart["body"]["data"]
                            ).decode("utf-8", errors="replace")
                            return _strip_html(html)
    return ""


def get_header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def extract_message_summary(msg: dict) -> dict:
    """Extract essential fields from a message for compact output.

    Writes full decoded message to .tmp/ and includes path in result.
    """
    headers = msg.get("payload", {}).get("headers", [])
    result = {
        "id": msg["id"],
        "threadId": msg.get("threadId"),
        "from": get_header(headers, "From"),
        "to": get_header(headers, "To"),
        "subject": get_header(headers, "Subject"),
        "date": get_header(headers, "Date"),
        "snippet": msg.get("snippet", ""),
        "labels": msg.get("labelIds", []),
    }
    if cc := get_header(headers, "Cc"):
        result["cc"] = cc

    body = decode_body(msg.get("payload", {}))
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(exist_ok=True)
    file_path = tmp_dir / f"email-{msg['id']}.txt"

    with open(file_path, "w") as f:
        f.write(f"From: {result['from']}\n")
        f.write(f"To: {result['to']}\n")
        f.write(f"Cc: {result.get('cc', '')}\n")
        f.write(f"Subject: {result['subject']}\n")
        f.write(f"Date: {result['date']}\n")
        f.write(f"\n{body}")

    result["file"] = str(file_path)

    return result


def extract_draft_summary(draft: dict) -> dict:
    """Extract essential fields from a draft for compact output."""
    msg = draft.get("message", {})
    headers = msg.get("payload", {}).get("headers", [])
    result = {
        "id": draft["id"],
        "messageId": msg.get("id"),
        "to": get_header(headers, "To"),
        "subject": get_header(headers, "Subject"),
        "snippet": msg.get("snippet", ""),
    }
    if cc := get_header(headers, "Cc"):
        result["cc"] = cc
    return result


def draft_url(draft_result: dict) -> str:
    """Get Gmail URL for a draft."""
    return f"https://mail.google.com/mail/u/0/#drafts/{draft_result['message']['id']}"


@click.group()
@click.option("--verbose", is_flag=True, help="Enable verbose logging")
def cli(verbose: bool):
    """Gmail CLI - search, draft, and send emails."""
    if verbose:
        logging.getLogger("googleapiclient.discovery").setLevel(logging.INFO)


@cli.command()
@click.option("-n", "--max-results", default=100, help="Maximum results")
@click.option("--unread", is_flag=True, help="Only show unread messages")
@click.option("--page-token", help="Token for next page of results")
def inbox(max_results: int, unread: bool, page_token: str | None):
    """List messages in inbox (shortcut for search "in:inbox")."""
    query = "in:inbox"
    if unread:
        query += " is:unread"
    _search_messages(query, max_results, page_token)


@cli.command()
@click.argument("query")
@click.option("-n", "--max-results", default=100, help="Maximum results")
@click.option("--page-token", help="Token for next page of results")
def search(query: str, max_results: int, page_token: str | None):
    """Search Gmail messages.

    QUERY: Gmail search query (e.g., 'is:unread', 'from:someone@example.com')
    """
    _search_messages(query, max_results, page_token)


def _search_messages(query: str, max_results: int, page_token: str | None = None):
    """Shared search implementation."""
    service = get_gmail()
    list_kwargs = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        list_kwargs["pageToken"] = page_token
    results = service.users().messages().list(**list_kwargs).execute()
    messages = results.get("messages", [])
    next_page_token = results.get("nextPageToken")

    if not messages:
        output: dict = {"messages": []}
        if next_page_token:
            output["nextPageToken"] = next_page_token
        click.echo(json.dumps(output, indent=2))
        return

    # Batch fetch messages in chunks to avoid rate limits
    # messages.get costs 5 quota units per operation
    # Strategy: 15 messages/chunk × 5 units = 75 units/chunk
    # With 0.3s delay: ~250 units/second (100% of limit, acceptable for search)
    # See module docstring for detailed analysis
    responses = {}
    chunk_size = 15

    for i in range(0, len(messages), chunk_size):
        chunk = messages[i : i + chunk_size]
        batch = service.new_batch_http_request(callback=_batch_callback(responses))
        for m in chunk:
            batch.add(
                service.users().messages().get(userId="me", id=m["id"], format="full"),
                request_id=m["id"],
            )
        batch.execute()
        if i + chunk_size < len(messages):
            time.sleep(0.3)

    detailed = [
        extract_message_summary(responses[m["id"]])
        for m in messages
        if m["id"] in responses
    ]
    output = {"messages": detailed}
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

    msg = MIMEText(data["body"])
    msg["to"] = data["to"]
    msg["subject"] = data["subject"]
    if data.get("cc"):
        msg["cc"] = data["cc"]
    if data.get("bcc"):
        msg["bcc"] = data["bcc"]

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        get_gmail()
        .users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    click.echo(f"Draft created: {result['id']}", err=True)
    click.echo(f"View: {draft_url(result)}", err=True)


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
    click.echo(f"Sent: {result['id']}", err=True)


def _create_reply_draft(
    message_id: str, body: str, *, include_cc: bool
) -> tuple[str, str]:
    """Create a reply draft, returning (draft_id, draft_url).

    Args:
        message_id: ID of the message to reply to
        body: Reply body text
        include_cc: If True, include CC recipients (reply-all behavior)
    """
    service = get_gmail()
    original = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="metadata")
        .execute()
    )
    my_email = service.users().getProfile(userId="me").execute()["emailAddress"]

    headers = original.get("payload", {}).get("headers", [])
    subject = get_header(headers, "Subject") or ""
    message_id_header = get_header(headers, "Message-ID")
    orig_refs = get_header(headers, "References")
    thread_id = original.get("threadId")

    reply_to = get_header(headers, "Reply-To")
    from_addr = get_header(headers, "From")
    orig_to = get_header(headers, "To")
    orig_cc = get_header(headers, "Cc")

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

    msg = MIMEText(body)
    msg["to"] = to_addr
    if include_cc and cc_addr:
        msg["cc"] = cc_addr
    msg["subject"] = subject
    if message_id_header:
        msg["In-Reply-To"] = message_id_header
        # Append to existing References chain for proper threading
        msg["References"] = (
            f"{orig_refs} {message_id_header}" if orig_refs else message_id_header
        )

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

    Preserves threading with the original message.

    JSON fields: body (required)

    Example:
        echo '{"body": "Thanks!"}' | jean-claude gmail draft reply MSG_ID
    """
    data = json.load(sys.stdin)
    if "body" not in data:
        raise click.UsageError("Missing required field: body")

    draft_id, url = _create_reply_draft(message_id, data["body"], include_cc=False)
    click.echo(f"Reply draft created: {draft_id}", err=True)
    click.echo(f"View: {url}", err=True)


@draft.command("reply-all")
@click.argument("message_id")
def draft_reply_all(message_id: str):
    """Create a reply-all draft from JSON stdin.

    Preserves threading and includes all original recipients.

    JSON fields: body (required)

    Example:
        echo '{"body": "Thanks!"}' | jean-claude gmail draft reply-all MSG_ID
    """
    data = json.load(sys.stdin)
    if "body" not in data:
        raise click.UsageError("Missing required field: body")

    draft_id, url = _create_reply_draft(message_id, data["body"], include_cc=True)
    click.echo(f"Reply-all draft created: {draft_id}", err=True)
    click.echo(f"View: {url}", err=True)


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

    headers = original.get("payload", {}).get("headers", [])
    subject = get_header(headers, "Subject") or ""
    from_addr = get_header(headers, "From")
    date = get_header(headers, "Date")
    original_body = decode_body(original.get("payload", {}))

    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"

    fwd_body = data.get("body", "")
    if fwd_body:
        fwd_body += "\n\n"
    fwd_body += "---------- Forwarded message ----------\n"
    fwd_body += f"From: {from_addr}\n"
    fwd_body += f"Date: {date}\n"
    fwd_body += f"Subject: {get_header(headers, 'Subject')}\n\n"
    fwd_body += original_body

    msg = MIMEText(fwd_body)
    msg["to"] = data["to"]
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    click.echo(f"Forward draft created: {result['id']}", err=True)
    click.echo(f"View: {draft_url(result)}", err=True)


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
    headers = msg.get("payload", {}).get("headers", [])

    body = decode_body(msg.get("payload", {}))
    tmp_dir = Path(".tmp")
    tmp_dir.mkdir(exist_ok=True)
    file_path = tmp_dir / f"draft-{draft_id}.txt"

    with open(file_path, "w") as f:
        f.write(f"From: {get_header(headers, 'From')}\n")
        f.write(f"To: {get_header(headers, 'To')}\n")
        f.write(f"Cc: {get_header(headers, 'Cc')}\n")
        f.write(f"Bcc: {get_header(headers, 'Bcc')}\n")
        f.write(f"Subject: {get_header(headers, 'Subject')}\n")
        f.write(f"Date: {get_header(headers, 'Date')}\n")
        f.write(f"\n{body}")

    click.echo(str(file_path))


@draft.command("delete")
@click.argument("draft_id")
def draft_delete(draft_id: str):
    """Permanently delete a draft.

    Example:
        jean-claude gmail draft delete r-123456789
    """
    get_gmail().users().drafts().delete(userId="me", id=draft_id).execute()
    click.echo(f"Deleted: {draft_id}", err=True)


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def star(message_ids: tuple[str, ...]):
    """Star messages."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), add_label_ids=["STARRED"])
    n = len(message_ids)
    click.echo(f"Starred {n} message{'s' if n != 1 else ''}", err=True)


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def unstar(message_ids: tuple[str, ...]):
    """Remove star from messages."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), remove_label_ids=["STARRED"])
    n = len(message_ids)
    click.echo(f"Unstarred {n} message{'s' if n != 1 else ''}", err=True)


@cli.command()
@click.argument("message_ids", nargs=-1)
@click.option(
    "--query",
    "-q",
    help="Archive all inbox messages matching query (e.g., 'from:example.com')",
)
@click.option(
    "-n",
    "--max-results",
    default=100,
    help="Max messages to archive when using --query",
)
def archive(message_ids: tuple[str, ...], query: str | None, max_results: int):
    """Archive messages (remove from inbox).

    Can archive by ID(s) or by query. Query automatically filters to inbox.

    Examples:
        jean-claude gmail archive MSG_ID1 MSG_ID2
        jean-claude gmail archive --query "from:newsletter@example.com"
    """
    if message_ids and query:
        raise click.UsageError("Provide message IDs or --query, not both")

    service = get_gmail()

    if query:
        full_query = f"in:inbox {query}"
        results = (
            service.users()
            .messages()
            .list(userId="me", q=full_query, maxResults=max_results)
            .execute()
        )
        ids_to_archive = (
            [m["id"] for m in results["messages"]] if "messages" in results else []
        )
    else:
        ids_to_archive = list(message_ids)

    if not ids_to_archive:
        click.echo("No messages to archive.", err=True)
        return

    _batch_modify_labels(service, ids_to_archive, remove_label_ids=["INBOX"])

    n = len(ids_to_archive)
    click.echo(f"Archived {n} message{'s' if n != 1 else ''}", err=True)


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def unarchive(message_ids: tuple[str, ...]):
    """Move messages back to inbox."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), add_label_ids=["INBOX"])
    n = len(message_ids)
    click.echo(f"Moved {n} message{'s' if n != 1 else ''} to inbox", err=True)


@cli.command("mark-read")
@click.argument("message_ids", nargs=-1, required=True)
def mark_read(message_ids: tuple[str, ...]):
    """Mark messages as read."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), remove_label_ids=["UNREAD"])
    n = len(message_ids)
    click.echo(f"Marked {n} message{'s' if n != 1 else ''} read", err=True)


@cli.command("mark-unread")
@click.argument("message_ids", nargs=-1, required=True)
def mark_unread(message_ids: tuple[str, ...]):
    """Mark messages as unread."""
    service = get_gmail()
    _batch_modify_labels(service, list(message_ids), add_label_ids=["UNREAD"])
    n = len(message_ids)
    click.echo(f"Marked {n} message{'s' if n != 1 else ''} unread", err=True)


@cli.command()
@click.argument("message_ids", nargs=-1, required=True)
def trash(message_ids: tuple[str, ...]):
    """Move messages to trash.

    Note: Uses individual trash operations (no batchTrash API exists).
    For bulk operations, consider using archive instead.
    """
    service = get_gmail()

    # No batchTrash API - use batch HTTP requests (5 units per trash operation)
    # Process in chunks to avoid overwhelming the API
    chunk_size = 50  # Conservative for individual operations
    for i in range(0, len(message_ids), chunk_size):
        chunk = message_ids[i : i + chunk_size]
        batch = service.new_batch_http_request(callback=_raise_on_error)
        for msg_id in chunk:
            batch.add(service.users().messages().trash(userId="me", id=msg_id))
        batch.execute()
        if i + chunk_size < len(message_ids):
            time.sleep(0.3)

    n = len(message_ids)
    click.echo(f"Trashed {n} message{'s' if n != 1 else ''}", err=True)


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
        click.echo("No attachments found.", err=True)
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
    click.echo(f"Downloaded: {output} ({len(data):,} bytes)", err=True)
