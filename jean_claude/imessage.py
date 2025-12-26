"""iMessage CLI - send messages and list chats via AppleScript."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import click

from .logging import JeanClaudeError, get_logger

logger = get_logger(__name__)

DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
# Apple's Cocoa epoch (2001-01-01) offset from Unix epoch (1970-01-01)
APPLE_EPOCH_OFFSET = 978307200


def run_applescript(script: str, *args: str) -> str:
    """Run AppleScript with optional arguments passed via 'on run argv'."""
    result = subprocess.run(
        ["osascript", "-e", script, *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise JeanClaudeError(f"AppleScript error: {result.stderr.strip()}")
    return result.stdout.strip()


def get_db_connection() -> sqlite3.Connection:
    """Get a read-only connection to the Messages database."""
    if not DB_PATH.exists():
        raise JeanClaudeError(f"Messages database not found at {DB_PATH}")
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        return conn
    except sqlite3.OperationalError as e:
        if "unable to open" in str(e):
            raise JeanClaudeError(
                "Cannot access Messages database. Grant Full Disk Access to your terminal:\n"
                "  System Preferences > Privacy & Security > Full Disk Access\n"
                "  Then add and enable your terminal app (Terminal, iTerm2, Ghostty, etc.)"
            )
        raise


def build_message_dict(
    date: str,
    sender: str,
    text: str | None,
    is_from_me: bool = False,
    contact_name: str | None = None,
    group_name: str | None = None,
    attachments: list[dict] | None = None,
) -> dict:
    """Build a message dictionary for JSON output."""
    result = {
        "date": date,
        "sender": sender,
        "text": text,
        "is_from_me": is_from_me,
        "contact_name": contact_name,
        "group_name": group_name,
    }
    if attachments:
        result["attachments"] = attachments
    return result


# Image MIME types we expose (includes Apple-specific formats for iMessage)
IMAGE_MIME_TYPES = frozenset(
    [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/heic",
        "image/heif",
        "image/webp",
        "image/tiff",
    ]
)


def parse_attachments(attachments_json: str | None) -> list[dict]:
    """Parse attachment JSON from SQLite query into list of attachment dicts.

    Only returns image attachments with valid, existing file paths.
    The SQL query defines the JSON structure, so we trust the field names.
    """
    if not attachments_json:
        return []

    attachments_data = json.loads(attachments_json)

    result = []
    for att in attachments_data:
        mime_type = att["mime_type"]
        filename = att["filename"]

        # Only include images
        if mime_type not in IMAGE_MIME_TYPES:
            continue

        # Expand ~ to home directory and verify file exists
        file_path = Path(filename).expanduser()
        if not file_path.exists():
            continue

        result.append(
            {
                "type": mime_type.split("/")[0],
                "filename": file_path.name,
                "mimeType": mime_type,
                "size": att["size"] or 0,
                "file": str(file_path),
            }
        )

    return result


def extract_text_from_attributed_body(data: bytes | None) -> str | None:
    """Extract text from NSAttributedString streamtyped binary.

    Modern macOS stores iMessage text in attributedBody (binary plist) rather
    than the text column. The format is a streamtyped NSAttributedString where
    the actual string follows this structure:

        ... | b"NSString" | 5-byte preamble | length | content | ...

    The 5-byte preamble is always b"\\x01\\x94\\x84\\x01+".

    Length encoding:
    - If first byte is 0x81 (129): length is next 2 bytes, little-endian
    - Otherwise: length is just that single byte

    Based on LangChain's iMessage loader implementation.
    """
    if not data:
        return None

    try:
        # Find NSString marker and skip past it + 5-byte preamble
        parts = data.split(b"NSString")
        if len(parts) < 2:
            return None

        content = parts[1][5:]  # Skip 5-byte preamble after NSString
        if not content:
            return None

        # Parse variable-length encoding
        length = content[0]
        start = 1

        if length == 0x81:  # Multi-byte length indicator
            # Length is next 2 bytes in little-endian
            if len(content) < 3:
                return None
            length = int.from_bytes(content[1:3], "little")
            start = 3

        if len(content) < start + length:
            return None

        text = content[start : start + length].decode("utf-8", errors="replace")
        return text.strip() if text else None

    except (UnicodeDecodeError, IndexError, ValueError):
        # Expected failures from malformed binary data
        return None


def get_message_text(text: str | None, attributed_body: bytes | None) -> str | None:
    """Get message text from text column or attributedBody fallback."""
    if text:
        return text
    return extract_text_from_attributed_body(attributed_body)


def get_chat_id_for_phone(phone: str) -> str | None:
    """Get the Messages.app chat ID for a phone number.

    Uses AppleScript to let Messages.app handle phone number normalization.
    Returns the chat ID (e.g., "any;-;+16467194457") or None if not found.
    """
    script = """on run {phoneNumber}
tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    try
        set targetBuddy to buddy phoneNumber of targetService
        set chatList to every chat whose participants contains targetBuddy
        repeat with c in chatList
            return id of c
        end repeat
    end try
end tell
return ""
end run"""
    result = run_applescript(script, phone)
    return result if result else None


def resolve_phones_to_names(phones: list[str]) -> dict[str, str]:
    """Resolve phone numbers to contact names via Messages.app.

    Uses Messages.app's buddy lookup which has fast access to contact names.

    Args:
        phones: List of phone numbers to look up (e.g., ["+12025551234", ...])

    Returns:
        Dict mapping original phone string to contact name (only for matches).
    """
    if not phones:
        return {}

    # Filter to phone-like strings (contain digits, not chat IDs)
    valid_phones = [p for p in phones if any(c.isdigit() for c in p)]
    if not valid_phones:
        return {}

    # Pass JSON as argument to avoid AppleScript injection risks
    phones_json = json.dumps(valid_phones)
    script = """use framework "Foundation"

on run {phonesJsonArg}
    set phoneList to current application's NSJSONSerialization's JSONObjectWithData:((current application's NSString's stringWithString:phonesJsonArg)'s dataUsingEncoding:(current application's NSUTF8StringEncoding)) options:0 |error|:(missing value)
    set resultDict to current application's NSMutableDictionary's new()

    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        repeat with phoneNum in phoneList
            try
                set targetBuddy to buddy (phoneNum as text) of targetService
                set buddyName to full name of targetBuddy
                if buddyName is not missing value then
                    resultDict's setValue:buddyName forKey:phoneNum
                end if
            end try
        end repeat
    end tell

    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:resultDict options:0 |error|:(missing value)
    set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
    return jsonString as text
end run"""

    try:
        output = run_applescript(script, phones_json)
        if not output:
            return {}
        return json.loads(output)
    except json.JSONDecodeError:
        logger.debug("Failed to parse contact names from Messages.app")
        return {}
    except JeanClaudeError:
        logger.debug("Messages.app contact lookup failed")
        return {}


def search_contacts_by_name(name: str) -> list[tuple[str, list[str]]]:
    """Search Contacts.app for people matching the given name.

    Returns list of (full_name, [phone_numbers]) tuples.
    Only returns contacts that have at least one phone number.
    """
    script = """use framework "Foundation"

on run {searchName}
tell application "Contacts"
    set foundPeople to every person whose name contains searchName
    set contactList to current application's NSMutableArray's new()

    repeat with p in foundPeople
        try
            set pName to name of p
            set phoneValues to current application's NSMutableArray's new()
            repeat with ph in phones of p
                set phoneVal to value of ph as text
                phoneValues's addObject:phoneVal
            end repeat

            -- Only include contacts with at least one phone
            if (phoneValues's |count|()) > 0 then
                set contactDict to current application's NSMutableDictionary's new()
                contactDict's setValue:pName forKey:"name"
                contactDict's setValue:phoneValues forKey:"phones"
                contactList's addObject:contactDict
            end if
        end try
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:contactList options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text
end run"""

    output = run_applescript(script, name)
    if not output:
        return []

    contacts_data = json.loads(output)
    return [(c["name"], c["phones"]) for c in contacts_data]


def resolve_recipient(recipient: str | None, name: str | None) -> str:
    """Resolve recipient to phone/chat ID from direct value or contact name.

    Raises UsageError if neither is provided.
    Raises ClickException if contact name doesn't resolve.
    """
    if name:
        return resolve_contact_to_phone(name)

    if recipient:
        return recipient

    raise click.UsageError("Provide either RECIPIENT or --name")


def resolve_contact_to_phone(name: str) -> str:
    """Resolve a contact name to a phone number (raw format from Contacts.app).

    Raises ClickException if no match found or ambiguous.
    """
    contacts = search_contacts_by_name(name)

    if not contacts:
        raise JeanClaudeError(f"No contact found matching '{name}' with a phone number")

    # Build list of contacts with their valid phones
    # [(contact_name, [raw_phone, ...]), ...]
    contacts_with_phones: list[tuple[str, list[str]]] = []
    for contact_name, phones in contacts:
        # Filter to phones that have at least one digit
        valid_phones = [p for p in phones if any(c.isdigit() for c in p)]
        if valid_phones:
            contacts_with_phones.append((contact_name, valid_phones))

    if not contacts_with_phones:
        raise JeanClaudeError(f"No contact found matching '{name}' with a phone number")

    # Check for ambiguity: multiple contacts
    if len(contacts_with_phones) > 1:
        matches = "\n".join(f"  - {c[0]}: {c[1][0]}" for c in contacts_with_phones)
        raise JeanClaudeError(
            f"Multiple contacts match '{name}':\n{matches}\n"
            "Use a more specific name or send directly to the phone number."
        )

    # Single contact - check for multiple phones
    contact_name, valid_phones = contacts_with_phones[0]
    if len(valid_phones) > 1:
        phones_list = "\n".join(f"  - {p}" for p in valid_phones)
        raise JeanClaudeError(
            f"Contact '{contact_name}' has multiple phone numbers:\n{phones_list}\n"
            "Send directly to the phone number to avoid ambiguity."
        )

    # Exactly one contact with exactly one phone - return raw format
    raw_phone = valid_phones[0]
    logger.info(f"Found: {contact_name} ({raw_phone})")
    return raw_phone


# Group chat style constant (43 = iMessage group chat)
IMESSAGE_GROUP_CHAT_STYLE = 43


@dataclass
class MessageQuery:
    """Parameters for querying messages from the database."""

    where_clause: str = ""
    params: tuple = field(default_factory=tuple)
    include_is_from_me: bool = False
    reverse_order: bool = False
    max_results: int = 20


def fetch_messages(conn: sqlite3.Connection, query: MessageQuery) -> list[dict]:
    """Fetch messages with full enrichment (names, group participants).

    This is the central function for querying messages. It handles:
    - Executing the query with common SELECT columns
    - Identifying unnamed group chats and fetching their participants
    - Resolving phone numbers to contact names
    - Building consistent message dictionaries

    Args:
        conn: Database connection
        query: MessageQuery specifying filters and options

    Returns:
        List of message dictionaries ready for JSON output.

    Security Note:
        The `where_clause` is interpolated directly into SQL. Only use hardcoded
        SQL fragments with parameterized placeholders (?). User input must ONLY
        go in `params`, never in `where_clause`. Current callers are safe.
    """
    cursor = conn.cursor()

    # Build SELECT columns - include is_from_me only when needed
    is_from_me_col = "m.is_from_me," if query.include_is_from_me else ""
    sender_col = (
        "CASE WHEN m.is_from_me = 1 THEN 'me' ELSE COALESCE(h.id, c.chat_identifier, 'unknown') END"
        if query.include_is_from_me
        else "COALESCE(h.id, c.chat_identifier, 'unknown')"
    )

    # Use subqueries with GROUP_CONCAT/json_group_array for participants and attachments
    # This avoids N+1 queries
    sql = f"""
        SELECT
            datetime(m.date/1000000000 + {APPLE_EPOCH_OFFSET}, 'unixepoch', 'localtime') as date,
            {sender_col} as sender,
            m.text,
            m.attributedBody,
            c.display_name,
            c.style,
            (SELECT GROUP_CONCAT(h2.id, '|')
             FROM chat_handle_join chj2
             JOIN handle h2 ON chj2.handle_id = h2.ROWID
             WHERE chj2.chat_id = c.ROWID) as participants,
            (SELECT json_group_array(json_object(
                'filename', a.filename,
                'mime_type', a.mime_type,
                'size', a.total_bytes
             ))
             FROM message_attachment_join maj
             JOIN attachment a ON maj.attachment_id = a.ROWID
             WHERE maj.message_id = m.ROWID
               AND a.filename IS NOT NULL
               AND a.transfer_state IN (0, 5)
            ) as attachments_json
            {"," + is_from_me_col.rstrip(",") if is_from_me_col else ""}
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR m.cache_has_attachments = 1)
        {("AND " + query.where_clause) if query.where_clause else ""}
        ORDER BY m.date DESC
        LIMIT ?
    """

    cursor.execute(sql, (*query.params, query.max_results))
    rows = cursor.fetchall()

    if not rows:
        return []

    # Collect all phones to resolve: senders + group participants
    all_phones: set[str] = set()
    for row in rows:
        sender, participants_str = row[1], row[6]
        if sender and sender not in ("unknown", "me"):
            all_phones.add(sender)
        if participants_str:
            all_phones.update(participants_str.split("|"))
    phone_to_name = resolve_phones_to_names(list(all_phones))

    # Build message dictionaries
    messages = []
    for row in rows:
        (
            date,
            sender,
            text,
            attributed_body,
            display_name,
            style,
            participants_str,
            attachments_json,
        ) = row[:8]
        is_from_me = row[8] if query.include_is_from_me else False

        msg_text = get_message_text(text, attributed_body)
        attachments = parse_attachments(attachments_json)

        # Skip messages with no text and no attachments
        if not msg_text and not attachments:
            continue

        contact_name = phone_to_name.get(sender)

        # Determine group_name: use display_name, or build from participants for unnamed groups
        if display_name:
            group_name = display_name
        elif style == IMESSAGE_GROUP_CHAT_STYLE and participants_str:
            participants = participants_str.split("|")
            participant_names = [phone_to_name.get(p, p) for p in participants]
            group_name = ", ".join(participant_names)
        else:
            group_name = None

        messages.append(
            build_message_dict(
                date=date,
                sender=sender,
                text=msg_text,
                is_from_me=is_from_me,
                contact_name=contact_name,
                group_name=group_name,
                attachments=attachments,
            )
        )

    if query.reverse_order:
        messages.reverse()

    return messages


@click.group()
def cli():
    """iMessage CLI - send messages and list chats.

    Send via AppleScript (always works). Reading message history requires
    Full Disk Access for the terminal app to query ~/Library/Messages/chat.db.
    """


@cli.command()
@click.argument("recipient", required=False)
@click.argument("message", required=False)
@click.option("--name", help="Contact name to send to (instead of phone/chat ID)")
def send(recipient: str | None, message: str | None, name: str | None):
    """Send an iMessage to a phone number, chat ID, or contact name.

    RECIPIENT: Phone number (+1234567890) or chat ID (any;+;chat123...)
    MESSAGE: The message text to send

    Examples:
        jean-claude imessage send "+12025551234" "Hello!"
        jean-claude imessage send "any;+;chat123456789" "Hello group!"
        jean-claude imessage send --name "Kevin Seals" "Hello!"
    """
    # When --name is used, recipient slot contains the message
    if name and recipient and not message:
        message = recipient
        recipient = None

    if not message:
        raise click.UsageError("MESSAGE is required")

    recipient = resolve_recipient(recipient, name)

    if recipient.startswith("any;"):
        # Chat ID - send directly to chat
        script = """on run {chatId, msg}
  tell application "Messages"
    set targetChat to chat id chatId
    send msg to targetChat
  end tell
end run"""
    else:
        # Phone number - use buddy
        script = """on run {phoneNumber, msg}
  tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy phoneNumber of targetService
    send msg to targetBuddy
  end tell
end run"""

    run_applescript(script, recipient, message)
    click.echo(f"Sent to {recipient}")


@cli.command()
@click.argument("recipient", required=False)
@click.argument(
    "file_path", required=False, type=click.Path(exists=True, path_type=Path)
)
@click.option("--name", help="Contact name to send to (instead of phone/chat ID)")
def send_file(recipient: str | None, file_path: Path | None, name: str | None):
    """Send a file attachment via iMessage.

    RECIPIENT: Phone number (+1234567890) or chat ID (any;+;chat123...)
    FILE_PATH: Path to file to send

    Examples:
        jean-claude imessage send-file "+12025551234" ./document.pdf
        jean-claude imessage send-file "any;+;chat123456789" ./photo.jpg
        jean-claude imessage send-file --name "Kevin Seals" ./photo.jpg
    """
    # When --name is used, recipient slot contains the file path
    if name and recipient and not file_path:
        file_path = Path(recipient)
        if not file_path.exists():
            raise click.UsageError(f"File not found: {recipient}")
        recipient = None

    if not file_path:
        raise click.UsageError("FILE_PATH is required")

    recipient = resolve_recipient(recipient, name)
    abs_path = str(file_path.resolve())

    if recipient.startswith("any;"):
        script = """on run {chatId, filePath}
  tell application "Messages"
    set targetChat to chat id chatId
    set theFile to POSIX file filePath
    send theFile to targetChat
  end tell
end run"""
    else:
        script = """on run {phoneNumber, filePath}
  tell application "Messages"
    set targetService to 1st service whose service type = iMessage
    set targetBuddy to buddy phoneNumber of targetService
    set theFile to POSIX file filePath
    send theFile to targetBuddy
  end tell
end run"""

    run_applescript(script, recipient, abs_path)
    click.echo(f"Sent {file_path.name} to {recipient}")


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to list")
def chats(max_results: int):
    """List available iMessage chats.

    Shows chat name (or contact name for 1:1 chats) and chat ID.
    Use chat ID to send to groups.

    Example:
        jean-claude imessage chats
    """
    script = f"""use framework "Foundation"

tell application "Messages"
    set chatList to current application's NSMutableArray's new()
    set chatCount to 0

    repeat with c in chats
        if chatCount >= {max_results} then exit repeat

        try
            set chatDict to current application's NSMutableDictionary's new()
            set chatId to id of c as text
            chatDict's setValue:chatId forKey:"chat_id"

            -- Get chat name or participant name for 1:1 chats
            set chatName to name of c
            if chatName is missing value then
                set pList to participants of c
                if (count of pList) = 1 then
                    try
                        set chatName to full name of item 1 of pList
                    end try
                end if
            end if
            if chatName is not missing value then
                chatDict's setValue:chatName forKey:"name"
            end if

            chatList's addObject:chatDict
            set chatCount to chatCount + 1
        end try
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:chatList options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text"""

    output = run_applescript(script)
    if not output:
        logger.info("No chats found")
        click.echo(json.dumps({"chats": []}))
        return

    try:
        chats_list = json.loads(output)
        click.echo(json.dumps({"chats": chats_list}, indent=2))
    except json.JSONDecodeError:
        logger.debug("Failed to parse chats from Messages.app")
        click.echo(json.dumps({"chats": []}))


@cli.command()
@click.argument("chat_id")
def participants(chat_id: str):
    """List participants of a group chat.

    CHAT_ID: The chat ID (e.g., any;+;chat123456789)

    Example:
        jean-claude imessage participants "any;+;chat123456789"
    """
    script = """use framework "Foundation"

on run {chatId}
    set participantList to current application's NSMutableArray's new()

    tell application "Messages"
        set c to chat id chatId
        repeat with p in participants of c
            try
                set pDict to current application's NSMutableDictionary's new()
                set pHandle to handle of p
                pDict's setValue:pHandle forKey:"handle"

                try
                    set pName to full name of p
                    if pName is not missing value then
                        pDict's setValue:pName forKey:"name"
                    end if
                end try

                participantList's addObject:pDict
            end try
        end repeat
    end tell

    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:participantList options:0 |error|:(missing value)
    set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
    return jsonString as text
end run"""

    output = run_applescript(script, chat_id)
    if not output:
        logger.info("No participants found or not a group chat")
        click.echo(json.dumps({"participants": []}))
        return

    try:
        participants_list = json.loads(output)
        click.echo(json.dumps({"participants": participants_list}, indent=2))
    except json.JSONDecodeError:
        logger.debug("Failed to parse participants from Messages.app")
        click.echo(json.dumps({"participants": []}))


@cli.command("open")
@click.argument("chat_id")
def open_chat(chat_id: str):
    """Open a chat in Messages.app (marks messages as read).

    CHAT_ID: The chat ID (e.g., any;-;+12025551234 or any;+;chat123...)

    Example:
        jean-claude imessage open "any;-;+12025551234"
        jean-claude imessage open "any;+;chat123456789"
    """
    script = """on run {chatId}
  tell application "Messages"
    activate
    set targetChat to chat id chatId
  end tell
end run"""

    run_applescript(script, chat_id)
    click.echo(f"Opened chat: {chat_id}")


@cli.command()
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
@click.option("--include-spam", is_flag=True, help="Include messages filtered as spam")
def unread(max_results: int, include_spam: bool):
    """List unread messages (requires Full Disk Access).

    Shows messages that haven't been read yet, excluding messages you sent.
    By default excludes spam-filtered messages (is_filtered=2); use --include-spam
    to include them.

    Example:
        jean-claude imessage unread
        jean-claude imessage unread -n 50
        jean-claude imessage unread --include-spam
    """
    # is_filtered: 0 = contacts, 1 = unknown senders, 2 = spam
    spam_filter = (
        "" if include_spam else "(c.is_filtered IS NULL OR c.is_filtered < 2) AND"
    )
    where_clause = f"{spam_filter} m.is_read = 0 AND m.is_from_me = 0"

    conn = get_db_connection()
    messages = fetch_messages(
        conn,
        MessageQuery(where_clause=where_clause, max_results=max_results),
    )
    conn.close()

    if not messages:
        logger.info("No unread messages")

    click.echo(json.dumps({"messages": messages}, indent=2))


@cli.command()
@click.argument("query", required=False)
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
def search(query: str | None, max_results: int):
    """Search message history (requires Full Disk Access).

    Searches the local Messages database. Your terminal app must have
    Full Disk Access in System Preferences > Privacy & Security.

    QUERY: Search term (searches message text)

    Examples:
        jean-claude imessage search "dinner plans"
        jean-claude imessage search -n 50
    """
    # Search in text column - attributedBody search would require extraction
    where_clause = "m.text LIKE ?" if query else ""
    params = (f"%{query}%",) if query else ()

    conn = get_db_connection()
    messages = fetch_messages(
        conn,
        MessageQuery(where_clause=where_clause, params=params, max_results=max_results),
    )
    conn.close()

    if not messages:
        logger.info("No messages found")

    click.echo(json.dumps({"messages": messages}, indent=2))


@cli.command()
@click.argument("chat_id", required=False)
@click.option("-n", "--max-results", default=20, help="Maximum messages to return")
@click.option("--name", help="Contact name to search for (instead of chat ID)")
def history(chat_id: str | None, max_results: int, name: str | None):
    """Get message history for a specific chat (requires Full Disk Access).

    CHAT_ID: The chat ID or phone number (e.g., any;-;+12025551234 or +12025551234)

    Examples:
        jean-claude imessage history "any;-;+12025551234" -n 10
        jean-claude imessage history --name "Kevin Seals"
        jean-claude imessage history "+12025551234"
    """
    if name:
        # Get raw phone from Contacts, then use Messages.app to get normalized chat ID
        raw_phone = resolve_contact_to_phone(name)
        messages_chat_id = get_chat_id_for_phone(raw_phone)
        if not messages_chat_id:
            raise JeanClaudeError(
                f"No message history found for '{name}' ({raw_phone})"
            )
        # Extract identifier from chat ID (e.g., "any;-;+16467194457" -> "+16467194457")
        chat_identifier = messages_chat_id.split(";")[-1]
    elif chat_id:
        # Use provided chat ID directly
        chat_identifier = chat_id.split(";")[-1] if ";" in chat_id else chat_id
    else:
        raise click.UsageError("Provide either CHAT_ID or --name")

    conn = get_db_connection()
    messages = fetch_messages(
        conn,
        MessageQuery(
            where_clause="(c.chat_identifier = ? OR h.id = ?)",
            params=(chat_identifier, chat_identifier),
            include_is_from_me=True,
            reverse_order=True,
            max_results=max_results,
        ),
    )
    conn.close()

    if not messages:
        logger.info("No messages found for this chat")

    click.echo(json.dumps({"messages": messages}, indent=2))
