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
import functools
import html
import json
import mimetypes
import re
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import unquote

import click
from googleapiclient.errors import HttpError

from .auth import build_service
from .errors import ErrorHandlingGroup
from .input import read_body_stdin, read_stdin_optional
from .logging import JeanClaudeError, get_logger
from .pagination import paginated_output
from .paths import ATTACHMENT_CACHE_DIR, DRAFT_CACHE_DIR, EMAIL_CACHE_DIR
from .timezone import LOCAL_TZ

logger = get_logger(__name__)


class GmailErrorHandlingGroup(ErrorHandlingGroup):
    """Error handling with Gmail-specific context for 404s."""

    def _http_error_message(self, e: HttpError) -> str:
        """Add Gmail-specific context to 404 errors."""
        if e.resp.status == 404:
            url = e.uri if hasattr(e, "uri") else ""
            if url:
                # Attachment: /users/me/messages/{messageId}/attachments/{attachmentId}
                if match := re.search(
                    r"/users/me/messages/([^/]+)/attachments/([^/?]+)", url
                ):
                    msg_id = unquote(match.group(1))
                    attach_id = unquote(match.group(2))
                    return (
                        f"Attachment not found: {attach_id}\n"
                        f"  Message: {msg_id}\n"
                        f"  Tip: Use 'jean-claude gmail attachments {msg_id}' to list attachments"
                    )
                # Message: /users/me/messages/{messageId}
                if match := re.search(r"/users/me/messages/([^/?]+)", url):
                    msg_id = unquote(match.group(1))
                    return (
                        f"Message not found: {msg_id}\n"
                        f"  Tip: Use 'jean-claude gmail search' to find valid message IDs"
                    )
                # Thread: /users/me/threads/{threadId}
                if match := re.search(r"/users/me/threads/([^/?]+)", url):
                    thread_id = unquote(match.group(1))
                    return (
                        f"Thread not found: {thread_id}\n"
                        f"  Tip: Use 'jean-claude gmail inbox' to find valid thread IDs"
                    )
                # Draft: /users/me/drafts/{draftId}
                if match := re.search(r"/users/me/drafts/([^/?]+)", url):
                    draft_id = unquote(match.group(1))
                    return (
                        f"Draft not found: {draft_id}\n"
                        f"  Tip: Use 'jean-claude gmail draft list' to see available drafts"
                    )
                # Filter: /users/me/settings/filters/{filterId}
                if match := re.search(r"/users/me/settings/filters/([^/?]+)", url):
                    filter_id = unquote(match.group(1))
                    return (
                        f"Filter not found: {filter_id}\n"
                        f"  Tip: Use 'jean-claude gmail filter list' to see available filters"
                    )
                # Label: /users/me/labels/{labelId}
                if match := re.search(r"/users/me/labels/([^/?]+)", url):
                    label_id = unquote(match.group(1))
                    return (
                        f"Label not found: {label_id}\n"
                        f"  Tip: Use 'jean-claude gmail labels' to see available labels"
                    )
        return super()._http_error_message(e)


def _convert_to_local_time(date_str: str) -> str:
    """Convert RFC 2822 date string to local time ISO format.

    Input:  "Sun, 28 Dec 2025 07:01:08 +0000"
    Output: "2025-12-27T23:01:08-08:00" (in user's local timezone)
    """
    if not date_str:
        return date_str
    dt = parsedate_to_datetime(date_str)
    return dt.astimezone(LOCAL_TZ).isoformat()


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
    for alias in send_as["sendAs"]:
        if alias["isPrimary"]:
            email = alias["sendAsEmail"]
            display_name = alias.get("displayName", "")
            if display_name:
                return formataddr((display_name, email))
            break

    if not email:
        email = service.users().getProfile(userId="me").execute()["emailAddress"]

    # Fallback: try Google Account profile name via People API
    # This mirrors Gmail's behavior when send-as displayName is empty
    display_name = _get_profile_display_name()
    if display_name:
        return formataddr((display_name, email))
    return email


_scope_warnings_shown: set[str] = set()


def _warn_scope_error(e: HttpError, scope: str, feature: str) -> None:
    """Warn once per scope about missing permissions."""
    if scope in _scope_warnings_shown:
        return
    if e.resp.status == 403:
        _scope_warnings_shown.add(scope)
        logger.warning(
            "Missing scope for feature",
            feature=feature,
            scope=scope,
            hint="Run 'jean-claude auth' to re-authenticate",
        )
    else:
        logger.warning("Failed to look up feature", feature=feature, error=str(e))


def _get_profile_display_name() -> str | None:
    """Get display name from Google Account profile. Returns None on any failure."""
    try:
        profile = (
            get_people()
            .people()
            .get(resourceName="people/me", personFields="names")
            .execute()
        )
    except HttpError as e:
        _warn_scope_error(e, "userinfo.profile", "your display name")
        return None

    names = profile.get("names", [])
    for name in names:
        if name.get("metadata", {}).get("primary", False):
            if display_name := name.get("displayName"):
                return display_name
    if names:
        return names[0].get("displayName")
    return None


@functools.cache
def _lookup_contact_name(email: str) -> str | None:
    """Look up display name for an email address from Google Contacts.

    Uses searchContacts API for efficient per-email lookup. Results are cached
    for the CLI session to avoid duplicate lookups.

    Requires contacts scope. Shows warning if scope not granted.
    """
    email = email.lower()

    try:
        result = (
            get_people()
            .people()
            .searchContacts(query=email, readMask="names,emailAddresses")
            .execute()
        )
    except HttpError as e:
        _warn_scope_error(e, "contacts", "recipient names")
        return None

    # Find exact email match (searchContacts does prefix matching)
    for person in result.get("results", []):
        person_data = person.get("person", {})
        for email_addr in person_data.get("emailAddresses", []):
            if email_addr.get("value", "").lower() == email:
                names = person_data.get("names", [])
                if names:
                    return names[0].get("displayName")

    return None


def _format_recipients(addresses: str) -> str:
    """Format email addresses with display names from Google Contacts.

    Parses a comma-separated list of email addresses, looks up each in contacts,
    and returns addresses formatted as "Display Name <email>" where available.

    Addresses that already have a display name are kept as-is.
    """
    if not addresses:
        return addresses

    parsed = getaddresses([addresses])
    formatted = []
    for name, email in parsed:
        if name:
            # Already has a name, keep it
            formatted.append(formataddr((name, email)))
        else:
            # Look up display name from contacts
            contact_name = _lookup_contact_name(email)
            if contact_name:
                formatted.append(formataddr((contact_name, email)))
            else:
                formatted.append(email)
    return ", ".join(formatted)


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


def _get_headers(msg: dict) -> dict[str, str]:
    """Extract headers from a message payload as a name->value dict."""
    return {h["name"]: h["value"] for h in msg["payload"]["headers"]}


def _get_headers_lower(msg: dict) -> dict[str, str]:
    """Extract headers from a message payload as a lowercase name->value dict."""
    return {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}


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


def _sanitize_id(id_: str) -> str:
    """Sanitize ID for use in filenames, preventing path traversal."""
    return id_.replace("/", "_").replace("..", "__")


def _write_email_cache(
    prefix: str, id_: str, summary: dict, body: str, html_body: str | None
) -> str:
    """Write email data to cache as separate files, return JSON file path.

    Creates three files in ~/.cache/jean-claude/emails/ (XDG cache):
    - {prefix}-{id}.json: Metadata (queryable with jq)
    - {prefix}-{id}.txt: Plain text body (readable with cat/less)
    - {prefix}-{id}.html: HTML body if present (viewable in browser)

    The JSON includes body_file and html_file paths for reference.
    """
    EMAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    base_name = f"{prefix}-{_sanitize_id(id_)}"
    json_path = EMAIL_CACHE_DIR / f"{base_name}.json"
    txt_path = EMAIL_CACHE_DIR / f"{base_name}.txt"

    # Write plain text body
    txt_path.write_text(body, encoding="utf-8")

    # Build metadata (without snippet, without inline body)
    file_data = {k: v for k, v in summary.items() if k != "snippet"}
    file_data["body_file"] = str(txt_path)

    # Write HTML body if present
    if html_body:
        html_path = EMAIL_CACHE_DIR / f"{base_name}.html"
        html_path.write_text(html_body, encoding="utf-8")
        file_data["html_file"] = str(html_path)

    json_path.write_text(json.dumps(file_data, indent=2), encoding="utf-8")
    return str(json_path)


def extract_message_summary(msg: dict, include_headers: bool = False) -> dict:
    """Extract essential fields from a message for compact output.

    Writes cache files: JSON metadata, .txt body, and .html body when present.

    Args:
        msg: Gmail API message object
        include_headers: If True, include all email headers in the output
    """
    headers = _get_headers(msg)
    result = {
        "id": msg["id"],
        "threadId": msg["threadId"],
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "date": _convert_to_local_time(headers.get("Date", "")),
        "snippet": html.unescape(msg.get("snippet", "")),
        "labels": msg.get("labelIds", []),
    }
    if cc := headers.get("Cc"):
        result["cc"] = cc

    body, html_body = extract_body(msg["payload"])
    result["file"] = _write_email_cache("email", msg["id"], result, body, html_body)

    # Add full headers to output (not to cache file, to keep cache consistent)
    if include_headers:
        result["headers"] = headers
    return result


def _write_thread_metadata(thread_id: str, metadata: dict) -> str:
    """Write thread metadata to cache as JSON only (no body files).

    Thread files contain metadata about the conversation (message IDs, counts,
    labels) but NOT message bodies. Use `gmail message` to fetch bodies.
    """
    EMAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    json_path = EMAIL_CACHE_DIR / f"thread-{_sanitize_id(thread_id)}.json"
    json_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return str(json_path)


def extract_thread_summary(thread: dict) -> dict:
    """Extract essential fields from a thread for compact output.

    Returns info about the thread with all message IDs exposed. The cached
    thread file contains metadata only - use `get MESSAGE_ID` or
    `get --thread THREAD_ID` to fetch message bodies.
    """
    messages = thread["messages"]
    if not messages:
        result = {"threadId": thread["id"], "messageCount": 0, "messages": []}
        result["file"] = _write_thread_metadata(thread["id"], result)
        return result

    # Get the latest message for display
    latest_msg = messages[-1]
    headers = _get_headers(latest_msg)

    # Aggregate labels and build message summaries (oldest to newest)
    all_labels = set()
    unread_count = 0
    message_summaries = []
    for msg in messages:
        labels = msg.get("labelIds", [])
        all_labels.update(labels)
        is_unread = "UNREAD" in labels
        if is_unread:
            unread_count += 1
        msg_headers = _get_headers(msg)
        msg_summary = {
            "id": msg["id"],
            "date": _convert_to_local_time(msg_headers.get("Date", "")),
            "from": msg_headers.get("From", ""),
            "to": msg_headers.get("To", ""),
            "labels": labels,
            "unread": is_unread,
        }
        if cc := msg_headers.get("Cc"):
            msg_summary["cc"] = cc
        message_summaries.append(msg_summary)

    result = {
        "threadId": thread["id"],
        "messageCount": len(messages),
        "unreadCount": unread_count,
        "subject": headers.get("Subject", ""),
        "labels": sorted(all_labels),
        "messages": message_summaries,
        "latest": {
            "id": latest_msg["id"],
            "date": _convert_to_local_time(headers.get("Date", "")),
            "from": headers.get("From", ""),
            "snippet": html.unescape(latest_msg.get("snippet", "")),
        },
    }

    # Write metadata-only cache file (no body)
    result["file"] = _write_thread_metadata(thread["id"], result)

    return result


def extract_draft_summary(draft: dict) -> dict:
    """Extract essential fields from a draft for compact output."""
    msg = draft["message"]
    headers = _get_headers(msg)
    result = {
        "id": draft["id"],
        "messageId": msg["id"],
        "to": headers.get("To", ""),
        "subject": headers.get("Subject", ""),
        "snippet": html.unescape(msg.get("snippet", "")),
    }
    if cc := headers.get("Cc"):
        result["cc"] = cc
    return result


def draft_url(draft_result: dict) -> str:
    """Get Gmail URL for a draft."""
    return f"https://mail.google.com/mail/u/0/#drafts/{draft_result['message']['id']}"


@click.group(cls=GmailErrorHandlingGroup)
def cli():
    """Gmail CLI - search, draft, and send emails."""


def _parse_date(date_str: str) -> str:
    """Parse a date string (human-readable or explicit) to YYYY/MM/DD format.

    Accepts:
        - Human-readable: "yesterday", "last week", "3 days ago", "Monday"
        - Explicit: "2026-01-21", "Jan 21", "January 21 2026"

    Returns date in Gmail's after: format (YYYY/MM/DD).
    Raises JeanClaudeError if parsing fails.
    """
    from datetime import datetime

    import dateparser

    parsed = dateparser.parse(
        date_str,
        settings={
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": datetime.now(tz=LOCAL_TZ).replace(tzinfo=None),
        },
    )
    if parsed is None:
        raise JeanClaudeError(f"Could not parse date: {date_str!r}")
    return parsed.strftime("%Y/%m/%d")


@cli.command()
@click.option("-n", "--max-results", default=100, help="Maximum results")
@click.option("--unread", is_flag=True, help="Only show unread threads")
@click.option(
    "--since", help="Only show emails from this date (e.g., 'yesterday', '3 days ago')"
)
@click.option("--page-token", help="Token for next page of results")
def inbox(max_results: int, unread: bool, since: str | None, page_token: str | None):
    """List threads in inbox.

    Returns threads (conversations) matching Gmail UI behavior.
    A thread shows as unread if ANY message in it is unread.

    Use --since to filter by date (recommended over -n for complete results).
    Accepts human-readable dates like "yesterday", "last week", "3 days ago".

    Note: Gmail has a known bug where previously-snoozed threads may appear in
    --unread results even after being read. The snooze state isn't exposed via
    the API, so we can't filter these client-side. See:
    https://issuetracker.google.com/issues/151714665
    """
    query = "in:inbox"
    if unread:
        query += " is:unread"
    if since:
        query += f" after:{_parse_date(since)}"
    _search_threads(query, max_results, page_token, include_inbox_counts=True)


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
@click.argument("message_ids", nargs=-1, required=True)
@click.option(
    "--headers",
    is_flag=True,
    help="Include all email headers (Delivered-To, X-Original-To, etc.)",
)
def message(message_ids: tuple[str, ...], headers: bool):
    """Fetch messages by ID.

    Fetches full message content and writes to ~/.cache/jean-claude/emails/.
    Returns message summaries as JSON to stdout.

    \b
    Examples:
        jean-claude gmail message 19b51f93fcf3f8ca
        jean-claude gmail message id1 id2 id3
        jean-claude gmail message --headers 19b51f93fcf3f8ca
    """
    service = get_gmail()
    summaries = []
    for message_id in message_ids:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        summaries.append(extract_message_summary(msg, include_headers=headers))
    click.echo(json.dumps(summaries, indent=2))


@cli.command()
@click.argument("thread_ids", nargs=-1, required=True)
@click.option(
    "--headers",
    is_flag=True,
    help="Include all email headers (Delivered-To, X-Original-To, etc.)",
)
def thread(thread_ids: tuple[str, ...], headers: bool):
    """Fetch all messages in a thread.

    Fetches full content of all messages in the thread and writes to
    ~/.cache/jean-claude/emails/. Returns message summaries as JSON to stdout.

    \b
    Examples:
        jean-claude gmail thread 19b51f93fcf3f8ca
        jean-claude gmail thread id1 id2 id3
    """
    service = get_gmail()
    summaries = []
    for thread_id in thread_ids:
        thread_data = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
        for msg in thread_data.get("messages", []):
            summaries.append(extract_message_summary(msg, include_headers=headers))
    click.echo(json.dumps(summaries, indent=2))


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
        click.echo(
            json.dumps(paginated_output("messages", [], next_page_token), indent=2)
        )
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
    click.echo(
        json.dumps(paginated_output("messages", detailed, next_page_token), indent=2)
    )


def _get_inbox_counts(service) -> dict:
    """Get total thread and unread counts for inbox.

    Uses Gmail's labels.get API which provides accurate counts without
    needing to paginate through all messages.
    """
    label = service.users().labels().get(userId="me", id="INBOX").execute()
    return {
        "total_threads": label.get("threadsTotal", 0),
        "total_unread": label.get("threadsUnread", 0),
    }


def _search_threads(
    query: str,
    max_results: int,
    page_token: str | None = None,
    include_inbox_counts: bool = False,
):
    """Search for threads, returning thread-level summaries.

    This matches Gmail UI behavior where conversations (threads) are shown,
    not individual messages. A thread appears in inbox if ANY message has
    INBOX label, and shows as unread if ANY message is unread.

    Args:
        query: Gmail search query
        max_results: Maximum threads to return
        page_token: Pagination token
        include_inbox_counts: If True, include total_threads and total_unread
            from the INBOX label (useful for inbox command to show accurate counts)
    """
    logger.info(f"Searching threads: {query}", max_results=max_results)
    service = get_gmail()

    # Fetch inbox counts first if requested (single API call)
    inbox_counts = _get_inbox_counts(service) if include_inbox_counts else None

    list_kwargs = {"userId": "me", "q": query, "maxResults": max_results}
    if page_token:
        list_kwargs["pageToken"] = page_token
    results = service.users().threads().list(**list_kwargs).execute()
    threads = results.get("threads", [])
    next_page_token = results.get("nextPageToken")
    logger.debug(f"Found {len(threads)} threads")

    if not threads:
        output = paginated_output("threads", [], next_page_token)
        if inbox_counts:
            output.update(inbox_counts)
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
    output = paginated_output("threads", detailed, next_page_token)
    if inbox_counts:
        output.update(inbox_counts)
    click.echo(json.dumps(output, indent=2))


# Draft command group
@cli.group()
def draft():
    """Manage email drafts."""
    pass


@draft.command("create")
@click.option("--to", "to_addr", required=True, help="Recipient(s) (comma-separated)")
@click.option("--subject", required=True, help="Email subject line")
@click.option("--cc", help="CC recipients (comma-separated)")
@click.option("--bcc", help="BCC recipients (comma-separated)")
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File to attach (can be repeated)",
)
def draft_create(
    to_addr: str,
    subject: str,
    cc: str | None,
    bcc: str | None,
    attachments: tuple[Path, ...],
):
    """Create a new email draft with body from stdin.

    \b
    Examples:
        echo "Hello!" | jean-claude gmail draft create --to "x@y.com" --subject "Hi!"
        echo "See attached" | jean-claude gmail draft create --to "x@y.com" --subject "Report" \\
            --attach report.pdf --attach data.csv
    """
    body = read_body_stdin()

    service = get_gmail()
    msg = _build_message_with_attachments(body, None, list(attachments))
    msg["from"] = get_my_from_address(service)
    msg["to"] = _format_recipients(to_addr)
    msg["subject"] = subject
    if cc:
        msg["cc"] = _format_recipients(cc)
    if bcc:
        msg["bcc"] = _format_recipients(bcc)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    url = draft_url(result)
    logger.info(f"Draft created: {result['id']}", url=url)
    click.echo(json.dumps({"id": result["id"], "url": url}, indent=2))


@draft.command("send")
@click.argument("draft_id")
def draft_send(draft_id: str):
    """Send an existing draft.

    \b
    Example:
        jean-claude gmail draft send r-123456789
    """
    result = (
        get_gmail().users().drafts().send(userId="me", body={"id": draft_id}).execute()
    )
    logger.info(f"Sent: {result['id']}")
    click.echo(
        json.dumps({"id": result["id"], "threadId": result["threadId"]}, indent=2)
    )


def _format_gmail_date(date_str: str) -> str:
    """Format date string to Gmail's reply format: 'Mon, 22 Dec 2025 at 02:50'."""
    dt = parsedate_to_datetime(date_str)
    return dt.strftime("%a, %d %b %Y at %H:%M")


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
    message_id: str,
    body: str,
    *,
    include_cc: bool,
    custom_cc: str | None = None,
    attachments: list[Path] | None = None,
) -> tuple[str, str]:
    """Create a reply draft, returning (draft_id, draft_url).

    Args:
        message_id: ID of the message to reply to
        body: Reply body text
        include_cc: If True, include CC recipients (reply-all behavior)
        custom_cc: Optional user-specified CC addresses (overrides auto-CC)
        attachments: Optional list of file paths to attach
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

    headers = _get_headers_lower(original)
    thread_id = original["threadId"]

    subject = headers.get("subject", "")
    date = headers.get("date", "")
    from_addr = headers.get("from", "")
    reply_to = headers.get("reply-to", "")
    orig_to = headers.get("to", "")
    orig_cc = headers.get("cc", "")
    message_id_header = headers.get("message-id", "")
    orig_refs = headers.get("references", "")

    # Get original body for quoting (both plain text and HTML)
    original_body, original_html = extract_body(original["payload"])

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

    # Auto-include inline images from original (they're part of the quoted body)
    inline_image_parts, _ = _fetch_inline_image_parts(
        service, message_id, original["payload"], html_body
    )

    # Build message with attachments and inline images
    msg = _build_message_with_attachments(
        plain_body, html_body, attachments or [], inline_image_parts or None
    )
    msg["from"] = my_from_addr
    msg["to"] = to_addr
    # Use custom CC if provided, otherwise use auto-detected CC for reply-all
    if custom_cc:
        msg["cc"] = _format_recipients(custom_cc)
    elif include_cc and cc_addr:
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
@click.option("--cc", help="Additional CC recipients (comma-separated)")
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File to attach (can be repeated)",
)
def draft_reply(message_id: str, cc: str | None, attachments: tuple[Path, ...]):
    """Create a reply draft with body from stdin.

    Preserves threading with the original message. Includes quoted original
    message in Gmail format.

    MESSAGE_ID: The message to reply to.

    Body is read from stdin.

    \b
    Examples:
        echo "Thanks!" | jean-claude gmail draft reply MSG_ID
        echo "See attached" | jean-claude gmail draft reply MSG_ID --attach response.pdf
    """
    body = read_body_stdin()

    draft_id, url = _create_reply_draft(
        message_id, body, include_cc=False, custom_cc=cc, attachments=list(attachments)
    )
    logger.info(f"Reply draft created: {draft_id}", url=url)
    click.echo(json.dumps({"id": draft_id, "url": url}, indent=2))


@draft.command("reply-all")
@click.argument("message_id")
@click.option("--cc", help="Override CC recipients (comma-separated)")
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File to attach (can be repeated)",
)
def draft_reply_all(message_id: str, cc: str | None, attachments: tuple[Path, ...]):
    """Create a reply-all draft with body from stdin.

    Preserves threading and includes all original recipients. Includes quoted
    original message in Gmail format.

    MESSAGE_ID: The message to reply to.

    Body is read from stdin.

    \b
    Examples:
        echo "Thanks everyone!" | jean-claude gmail draft reply-all MSG_ID
        echo "See attached" | jean-claude gmail draft reply-all MSG_ID --attach notes.pdf
    """
    body = read_body_stdin()

    draft_id, url = _create_reply_draft(
        message_id, body, include_cc=True, custom_cc=cc, attachments=list(attachments)
    )
    logger.info(f"Reply-all draft created: {draft_id}", url=url)
    click.echo(json.dumps({"id": draft_id, "url": url}, indent=2))


@draft.command("forward")
@click.argument("message_id")
@click.argument("to")
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File to attach (can be repeated)",
)
def draft_forward(
    message_id: str,
    to: str,
    attachments: tuple[Path, ...],
):
    """Create a forward draft with body from stdin.

    MESSAGE_ID: The message to forward.
    TO: Recipient email address.

    Body is read from stdin (can be empty for forwarding without adding text).
    Original attachments are included automatically (can be removed via draft update).

    \b
    Examples:
        echo "FYI" | jean-claude gmail draft forward MSG_ID someone@example.com
        echo "FYI" | jean-claude gmail draft forward MSG_ID x@y.com --attach extra.pdf
        # Forward without adding text
        jean-claude gmail draft forward MSG_ID someone@example.com < /dev/null
    """
    body = read_body_stdin(allow_empty=True)

    service = get_gmail()
    original = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )

    headers = _get_headers_lower(original)
    orig_subject = headers.get("subject", "")
    from_addr = headers.get("from", "")
    date = headers.get("date", "")

    # Get both plain text and HTML for proper forwarding
    original_text, original_html = extract_body(original["payload"])

    # Build forward subject
    if orig_subject.lower().startswith("fwd:"):
        subject = orig_subject
    else:
        subject = f"Fwd: {orig_subject}"

    # Build both plain text and HTML versions (like replies)
    plain_body = _build_forward_text(body, original_text, from_addr, date, orig_subject)
    html_body = _build_forward_html(
        body, original_html, original_text, from_addr, date, orig_subject
    )

    # Auto-include inline images (they're part of the message body display)
    inline_image_parts, inline_attachment_ids = _fetch_inline_image_parts(
        service, message_id, original["payload"], html_body
    )

    # Collect regular attachments (new files + original if requested)
    all_attachment_parts: list[MIMEBase] = []

    # Add new file attachments
    for file_path in attachments:
        all_attachment_parts.append(_create_attachment_part(file_path))

    # Add original attachments (skip those already included as inline images)
    orig_attachments = extract_attachments_from_payload(original["payload"])
    for att in orig_attachments:
        if att["attachmentId"] in inline_attachment_ids:
            continue
        data = (
            service.users()
            .messages()
            .attachments()
            .get(userId="me", messageId=message_id, id=att["attachmentId"])
            .execute()
        )
        decoded_data = base64.urlsafe_b64decode(data["data"])

        mime_type = att.get("mimeType", "application/octet-stream")
        if "/" in mime_type:
            main_type, sub_type = mime_type.split("/", 1)
        else:
            main_type, sub_type = "application", "octet-stream"
        part = MIMEBase(main_type, sub_type)
        part.set_payload(decoded_data)
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=att["filename"],
        )
        all_attachment_parts.append(part)

    # Build message structure
    if html_body:
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(plain_body, "plain"))
        alt_part.attach(MIMEText(html_body, "html"))
        body_part = alt_part
    else:
        body_part = MIMEText(plain_body, "plain")

    # Wrap in multipart/related if we have inline images
    if inline_image_parts:
        related_part = MIMEMultipart("related")
        related_part.attach(body_part)
        for img_part in inline_image_parts:
            related_part.attach(img_part)
        body_part = related_part

    if all_attachment_parts:
        msg = MIMEMultipart("mixed")
        msg.attach(body_part)
        for part in all_attachment_parts:
            msg.attach(part)
    else:
        msg = body_part

    msg["from"] = get_my_from_address(service)
    msg["to"] = _format_recipients(to)
    msg["subject"] = subject

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    result = (
        service.users()
        .drafts()
        .create(userId="me", body={"message": {"raw": raw}})
        .execute()
    )
    url = draft_url(result)
    logger.info(f"Forward draft created: {result['id']}", url=url)
    click.echo(json.dumps({"id": result["id"], "url": url}, indent=2))


@draft.command("list")
@click.option("-n", "--max-results", default=20, help="Maximum results")
@click.option("--page-token", help="Token for next page of results")
def draft_list(max_results: int, page_token: str | None):
    """List drafts.

    \b
    Example:
        jean-claude gmail draft list
    """
    service = get_gmail()
    list_kwargs: dict = {"userId": "me", "maxResults": max_results}
    if page_token:
        list_kwargs["pageToken"] = page_token

    results = service.users().drafts().list(**list_kwargs).execute()
    drafts = results.get("drafts", [])
    next_page_token = results.get("nextPageToken")

    if not drafts:
        click.echo(
            json.dumps(paginated_output("drafts", [], next_page_token), indent=2)
        )
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
    click.echo(
        json.dumps(paginated_output("drafts", detailed, next_page_token), indent=2)
    )


@draft.command("get")
@click.argument("draft_id")
def draft_get(draft_id: str):
    """Get a draft, written to separate metadata and body files.

    Creates two files in ~/.cache/jean-claude/drafts/:
    - draft-{id}.json: Metadata (to, cc, subject, attachments, etc.)
    - draft-{id}.txt: Body text (editable with any text editor)

    \b
    Workflow:
        jean-claude gmail draft get DRAFT_ID
        # Edit the body file
        vim ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt
        # Update from edited body
        cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt | jean-claude gmail draft update DRAFT_ID

    \b
    Example:
        jean-claude gmail draft get r-123456789
    """
    service = get_gmail()
    draft = (
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    )
    msg = draft["message"]
    headers = _get_headers_lower(msg)
    body = decode_body(msg["payload"])

    DRAFT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Write body to plain text file (sanitize ID for safe filenames)
    safe_id = _sanitize_id(draft_id)
    body_path = DRAFT_CACHE_DIR / f"draft-{safe_id}.txt"
    body_path.write_text(body, encoding="utf-8")

    # Build metadata JSON (without inline body)
    draft_data = {
        "id": draft_id,
        "from": headers.get("from", ""),
        "to": headers.get("to", ""),
        "subject": headers.get("subject", ""),
        "body_file": str(body_path),
    }
    # Only include optional fields if present
    if cc := headers.get("cc"):
        draft_data["cc"] = cc
    if bcc := headers.get("bcc"):
        draft_data["bcc"] = bcc

    # Include attachments list
    attachments = extract_attachments_from_payload(msg["payload"])
    if attachments:
        draft_data["attachments"] = [
            {"filename": att["filename"], "mimeType": att["mimeType"]}
            for att in attachments
        ]

    json_path = DRAFT_CACHE_DIR / f"draft-{safe_id}.json"
    json_path.write_text(json.dumps(draft_data, indent=2), encoding="utf-8")

    click.echo(json.dumps({"id": draft_id, "file": str(json_path)}, indent=2))


@draft.command("update")
@click.argument("draft_id")
@click.option("--to", "to_addr", help="Update To recipients (comma-separated)")
@click.option("--cc", help="Update CC recipients (comma-separated)")
@click.option("--bcc", help="Update BCC recipients (comma-separated)")
@click.option("--subject", help="Update subject line")
@click.option(
    "--attach",
    "attachments",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="File to attach (can be repeated, replaces existing attachments)",
)
@click.option(
    "--clear-attachments",
    is_flag=True,
    help="Remove all attachments from the draft",
)
def draft_update(
    draft_id: str,
    to_addr: str | None,
    cc: str | None,
    bcc: str | None,
    subject: str | None,
    attachments: tuple[Path, ...],
    clear_attachments: bool,
):
    """Update an existing draft.

    Body is read from stdin (plain text). Metadata is updated via flags.
    Only provided fields are updated; others remain unchanged.

    Preserves threading headers (In-Reply-To, References) from the original draft.
    When --attach is used, new attachments replace any existing attachments.
    Use --clear-attachments to remove all attachments.

    \b
    Workflow for editing drafts:
        jean-claude gmail draft get DRAFT_ID
        # Edit the body file
        vim ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt
        # Update from edited body
        cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt | jean-claude gmail draft update DRAFT_ID

    \b
    Examples:
        # Update body only
        cat body.txt | jean-claude gmail draft update DRAFT_ID

        # Update metadata only (no stdin)
        jean-claude gmail draft update DRAFT_ID --subject "New subject"

        # Add attachments to draft
        jean-claude gmail draft update DRAFT_ID --attach report.pdf < /dev/null

        # Remove all attachments
        jean-claude gmail draft update DRAFT_ID --clear-attachments < /dev/null
    """
    # Read body from stdin if provided
    new_body = read_stdin_optional()

    # Validate options
    if clear_attachments and attachments:
        raise click.UsageError("Cannot use --attach with --clear-attachments")

    # Check that at least one update is provided
    if new_body is None and not any(
        (to_addr, cc, bcc, subject, attachments, clear_attachments)
    ):
        raise click.UsageError(
            "Nothing to update. Provide body on stdin or use --to/--cc/--bcc/--subject/--attach/--clear-attachments"
        )

    service = get_gmail()

    # Fetch existing draft to preserve threading headers and unchanged fields
    existing = (
        service.users().drafts().get(userId="me", id=draft_id, format="full").execute()
    )
    existing_msg = existing["message"]
    thread_id = existing_msg.get("threadId")

    # Start with existing headers
    headers = _get_headers_lower(existing_msg)
    existing_body = decode_body(existing_msg["payload"])

    # Apply updates (only for provided values)
    if to_addr is not None:
        headers["to"] = _format_recipients(to_addr)
    if cc is not None:
        headers["cc"] = _format_recipients(cc)
    if bcc is not None:
        headers["bcc"] = _format_recipients(bcc)
    if subject is not None:
        headers["subject"] = subject

    # Use new body if provided, otherwise keep existing
    final_body = new_body if new_body is not None else existing_body

    # Handle attachments:
    # --clear-attachments: remove all attachments
    # --attach: use new attachments (replaces existing)
    # neither: preserve existing attachments
    attachment_parts: list[MIMEBase] = []
    if clear_attachments:
        # User wants to remove all attachments - leave attachment_parts empty
        pass
    elif attachments:
        # User provided new attachments - use those (replaces existing)
        for file_path in attachments:
            attachment_parts.append(_create_attachment_part(file_path))
    else:
        # No --attach provided - preserve existing attachments from draft
        existing_attachments = extract_attachments_from_payload(existing_msg["payload"])
        for att in existing_attachments:
            data = (
                service.users()
                .messages()
                .attachments()
                .get(
                    userId="me",
                    messageId=existing_msg["id"],
                    id=att["attachmentId"],
                )
                .execute()
            )
            decoded_data = base64.urlsafe_b64decode(data["data"])

            mime_type = att.get("mimeType", "application/octet-stream")
            if "/" in mime_type:
                main_type, sub_type = mime_type.split("/", 1)
            else:
                main_type, sub_type = "application", "octet-stream"
            part = MIMEBase(main_type, sub_type)
            part.set_payload(decoded_data)
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                "attachment",
                filename=att["filename"],
            )
            attachment_parts.append(part)

    # Build message structure
    body_part = MIMEText(final_body, "plain")
    if attachment_parts:
        msg = MIMEMultipart("mixed")
        msg.attach(body_part)
        for part in attachment_parts:
            msg.attach(part)
    else:
        msg = body_part
    # Preserve original From header
    msg["from"] = headers.get("from") or get_my_from_address(service)
    for field in ["to", "cc", "bcc", "subject"]:
        if value := headers.get(field):
            msg[field] = value
    if in_reply_to := headers.get("in-reply-to"):
        msg["In-Reply-To"] = in_reply_to
    if references := headers.get("references"):
        msg["References"] = references

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    api_body = {"message": {"raw": raw}}
    if thread_id:
        api_body["message"]["threadId"] = thread_id

    result = (
        service.users()
        .drafts()
        .update(userId="me", id=draft_id, body=api_body)
        .execute()
    )
    url = draft_url(result)
    logger.info(f"Updated draft: {result['id']}", url=url)
    click.echo(json.dumps({"id": result["id"], "url": url}, indent=2))


@draft.command("delete")
@click.argument("draft_id")
def draft_delete(draft_id: str):
    """Permanently delete a draft.

    \b
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

    \b
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


def extract_attachments_from_payload(payload: dict) -> list[dict]:
    """Extract all attachments from a message payload.

    Handles two cases:
    1. Multipart messages with attachments in nested parts
    2. Single-part messages where the payload itself is an attachment
       (e.g., DMARC reports sent as a raw zip file)
    """
    attachments: list[dict] = []

    if "parts" in payload:
        _extract_attachments(payload["parts"], attachments)
    else:
        # Check if payload itself is an attachment (single-part message)
        _extract_attachments([payload], attachments)

    return attachments


def _get_part_header(part: dict, name: str) -> str | None:
    """Get a header value from a MIME part (case-insensitive)."""
    headers = part.get("headers", [])
    name_lower = name.lower()
    for h in headers:
        if h["name"].lower() == name_lower:
            return h["value"]
    return None


def _extract_inline_images(parts: list, inline_images: list) -> None:
    """Recursively extract inline image info from message parts.

    Inline images have a Content-ID header and are referenced in HTML via cid: URLs.
    """
    for part in parts:
        content_id = _get_part_header(part, "Content-ID")
        body = part.get("body", {})
        attachment_id = body.get("attachmentId")

        # Inline images have Content-ID and attachmentId (for fetching data)
        if content_id and attachment_id:
            inline_images.append(
                {
                    "contentId": content_id,
                    "mimeType": part.get("mimeType", "application/octet-stream"),
                    "attachmentId": attachment_id,
                }
            )

        # Recurse into nested parts
        if "parts" in part:
            _extract_inline_images(part["parts"], inline_images)


def extract_inline_images_from_payload(payload: dict) -> list[dict]:
    """Extract inline images from a message payload.

    Inline images are parts with Content-ID headers, referenced in HTML via cid: URLs.
    These should be included automatically when forwarding/replying to preserve
    the message's visual appearance.

    Handles both multipart and single-part payloads (mirrors extract_attachments_from_payload).
    """
    inline_images: list[dict] = []

    if "parts" in payload:
        _extract_inline_images(payload["parts"], inline_images)
    else:
        # Check if payload itself is an inline image (single-part message)
        _extract_inline_images([payload], inline_images)

    return inline_images


def _fetch_inline_image_parts(
    service, message_id: str, payload: dict, html_body: str | None = None
) -> tuple[list[MIMEBase], set[str]]:
    """Fetch and build MIME parts for inline images from a message payload.

    Extracts inline images (parts with Content-ID headers) and fetches their data
    from the Gmail API. Returns MIMEBase parts with Content-ID preserved for use
    in multipart/related messages.

    If html_body is provided, only fetches images that are actually referenced
    via cid: URLs in the HTML. This avoids unnecessary API calls for unused images.

    Returns:
        A tuple of (inline_image_parts, fetched_attachment_ids) where:
        - inline_image_parts: List of MIMEBase parts for inline images
        - fetched_attachment_ids: Set of attachment IDs that were fetched as inline
          (used to avoid double-fetching original attachments in draft forward)
    """
    # Short-circuit if HTML doesn't contain any cid: references
    if html_body is not None and "cid:" not in html_body:
        return [], set()

    inline_image_parts: list[MIMEBase] = []
    fetched_attachment_ids: set[str] = set()
    inline_images = extract_inline_images_from_payload(payload)

    for img in inline_images:
        try:
            attachment_id = img["attachmentId"]
            data = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            decoded_data = base64.urlsafe_b64decode(data["data"])

            mime_type = img.get("mimeType", "application/octet-stream")
            if "/" in mime_type:
                main_type, sub_type = mime_type.split("/", 1)
            else:
                main_type, sub_type = "application", "octet-stream"
            part = MIMEBase(main_type, sub_type)
            part.set_payload(decoded_data)
            encoders.encode_base64(part)
            # Preserve Content-ID for cid: URL references in HTML
            part.add_header("Content-ID", img["contentId"])
            part.add_header("Content-Disposition", "inline")
            inline_image_parts.append(part)
            fetched_attachment_ids.add(attachment_id)
        except Exception as e:
            # Log and skip failed inline images rather than breaking the draft
            logger.warning(
                "Failed to fetch inline image",
                content_id=img.get("contentId"),
                attachment_id=attachment_id,
                error=str(e),
            )

    return inline_image_parts, fetched_attachment_ids


def _create_attachment_part(file_path: Path) -> MIMEBase:
    """Create a MIME attachment part from a file path."""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    if mime_type is None:
        mime_type = "application/octet-stream"

    main_type, sub_type = mime_type.split("/", 1)
    with file_path.open("rb") as f:
        part = MIMEBase(main_type, sub_type)
        part.set_payload(f.read())

    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        "attachment",
        filename=file_path.name,
    )
    return part


def _build_message_with_attachments(
    text_body: str,
    html_body: str | None,
    attachments: list[Path],
    inline_images: list[MIMEBase] | None = None,
) -> MIMEMultipart | MIMEText:
    """Build a multipart email message with optional attachments and inline images.

    Structure with inline images and attachments:
        multipart/mixed
        ├── multipart/related
        │   ├── multipart/alternative
        │   │   ├── text/plain
        │   │   └── text/html (with cid: references)
        │   ├── image/png (Content-ID: <image001>)
        │   └── image/jpeg (Content-ID: <image002>)
        └── attachment.pdf

    Structure with only attachments (no inline images):
        multipart/mixed
        ├── multipart/alternative (or text/plain if no html)
        │   ├── text/plain
        │   └── text/html
        └── attachment1
        └── ...

    Structure with no attachments or inline images:
        multipart/alternative (or text/plain if no html)
    """
    inline_images = inline_images or []

    if html_body:
        # Create alternative part with both text and html
        alt_part = MIMEMultipart("alternative")
        alt_part.attach(MIMEText(text_body, "plain"))
        alt_part.attach(MIMEText(html_body, "html"))
        body_part = alt_part
    else:
        body_part = MIMEText(text_body, "plain")

    # If we have inline images, wrap body in multipart/related
    if inline_images:
        related_part = MIMEMultipart("related")
        related_part.attach(body_part)
        for img in inline_images:
            related_part.attach(img)
        body_part = related_part

    if attachments:
        # Wrap in mixed for attachments
        msg = MIMEMultipart("mixed")
        msg.attach(body_part)
        for file_path in attachments:
            msg.attach(_create_attachment_part(file_path))
        return msg
    elif inline_images:
        # Return related part (contains body + inline images)
        return body_part
    else:
        # Return the body part directly (alternative for html, text for plain)
        return body_part


# Filter command group
@cli.group()
def filter():
    """Manage Gmail filters."""
    pass


@filter.command("list")
def filter_list():
    """List all Gmail filters.

    Returns all filters with their criteria and actions.

    \b
    Example:
        jean-claude gmail filter list
    """
    service = get_gmail()
    results = service.users().settings().filters().list(userId="me").execute()
    filters = results.get("filter", [])

    # Transform to consistent output format
    output = [
        {"id": f["id"], "criteria": f["criteria"], "action": f["action"]}
        for f in filters
    ]

    click.echo(json.dumps({"filters": output}, indent=2))


@filter.command("get")
@click.argument("filter_id")
def filter_get(filter_id: str):
    """Get a specific filter by ID.

    \b
    Example:
        jean-claude gmail filter get ANe1BmjXYZ123
    """
    service = get_gmail()
    f = service.users().settings().filters().get(userId="me", id=filter_id).execute()

    output = {"id": f["id"], "criteria": f["criteria"], "action": f["action"]}
    click.echo(json.dumps(output, indent=2))


@filter.command("create")
@click.argument("query")
@click.option("--add-label", "-a", multiple=True, help="Label to add (can repeat)")
@click.option(
    "--remove-label", "-r", multiple=True, help="Label to remove (can repeat)"
)
@click.option("--forward", "-f", help="Email address to forward to (must be verified)")
def filter_create(
    query: str,
    add_label: tuple[str, ...],
    remove_label: tuple[str, ...],
    forward: str | None,
):
    """Create a new Gmail filter.

    QUERY uses Gmail search syntax. Actions are label operations or forwarding.

    \b
    Common labels:
        INBOX, UNREAD, STARRED, IMPORTANT, TRASH, SPAM
        CATEGORY_PERSONAL, CATEGORY_SOCIAL, CATEGORY_PROMOTIONS, CATEGORY_UPDATES

    \b
    Examples:
        # Archive (remove from inbox)
        jean-claude gmail filter create "to:reports@company.com" -r INBOX

        # Star and mark important
        jean-claude gmail filter create "from:boss@company.com" -a STARRED -a IMPORTANT

        # Mark as read (remove UNREAD label)
        jean-claude gmail filter create "from:notifications@github.com" -r UNREAD

        # Apply custom label (use 'gmail labels' to get IDs)
        jean-claude gmail filter create "from:client@example.com" -a Label_123

        # Forward
        jean-claude gmail filter create "from:vip@example.com" -f backup@example.com
    """
    action: dict = {}
    if add_label:
        action["addLabelIds"] = list(add_label)
    if remove_label:
        action["removeLabelIds"] = list(remove_label)
    if forward:
        action["forward"] = forward

    if not action:
        raise JeanClaudeError(
            "At least one action required: --add-label, --remove-label, or --forward"
        )

    service = get_gmail()
    criteria = {"query": query}
    body = {"criteria": criteria, "action": action}
    result = (
        service.users().settings().filters().create(userId="me", body=body).execute()
    )

    logger.info("Created filter", id=result["id"])
    click.echo(
        json.dumps(
            {"id": result["id"], "criteria": criteria, "action": action}, indent=2
        )
    )


@filter.command("delete")
@click.argument("filter_id")
def filter_delete(filter_id: str):
    """Delete a Gmail filter.

    \b
    Example:
        jean-claude gmail filter delete ANe1BmjXYZ123
    """
    service = get_gmail()
    service.users().settings().filters().delete(userId="me", id=filter_id).execute()
    logger.info("Deleted filter", id=filter_id)


@cli.command()
def labels():
    """List all Gmail labels.

    Returns label IDs and names. Use label IDs with filter --add-label/--remove-label.

    System labels have fixed IDs like INBOX, SENT, TRASH, SPAM, STARRED, UNREAD.
    Custom labels have generated IDs like Label_123456789.

    \b
    Example:
        jean-claude gmail labels
    """
    service = get_gmail()
    results = service.users().labels().list(userId="me").execute()
    labels_list = results.get("labels", [])

    # Sort: system labels first, then custom labels alphabetically
    def sort_key(label):
        # System labels (INBOX, SENT, etc.) typically don't have 'Label_' prefix
        is_custom = label["id"].startswith("Label_")
        return (is_custom, label["name"])

    labels_list.sort(key=sort_key)

    output = [
        {"id": label["id"], "name": label["name"], "type": label["type"]}
        for label in labels_list
    ]

    click.echo(json.dumps({"labels": output}, indent=2))


@cli.command()
@click.argument("message_id")
def attachments(message_id: str):
    """List attachments for a message.

    \b
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

    payload = msg.get("payload", {})
    attachment_list = extract_attachments_from_payload(payload)
    click.echo(json.dumps(attachment_list, indent=2))


@cli.command("attachment-download")
@click.argument("message_id")
@click.argument("attachment_id")
@click.argument("filename")
@click.option(
    "--output",
    "-o",
    type=click.Path(file_okay=False),
    help="Directory to save to (default: ~/.cache/jean-claude/attachments/)",
)
def attachment_download(
    message_id: str, attachment_id: str, filename: str, output: str | None
):
    """Download an attachment from a message.

    Use 'jean-claude gmail attachments MSG_ID' to get attachment IDs and filenames.

    By default, saves to ~/.cache/jean-claude/attachments/. Use --output to
    save to a different directory.

    \b
    Example:
        jean-claude gmail attachments MSG_ID
        jean-claude gmail attachment-download MSG_ID ATTACH_ID report.pdf
        jean-claude gmail attachment-download MSG_ID ATTACH_ID report.pdf -o ./
    """
    service = get_gmail()
    attachment = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )

    if output:
        output_dir = Path(output)
    else:
        output_dir = ATTACHMENT_CACHE_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / filename
    data = base64.urlsafe_b64decode(attachment["data"])
    output_path.write_bytes(data)

    result = {"file": str(output_path), "bytes": len(data)}
    click.echo(json.dumps(result, indent=2))
