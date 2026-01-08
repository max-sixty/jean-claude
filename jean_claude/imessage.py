"""iMessage CLI - send messages and list chats via AppleScript."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import click

from .applescript import run_applescript
from .input import read_body_stdin
from .logging import JeanClaudeError, get_logger
from .messaging import (
    disambiguate_chat_matches,
    resolve_recipient as _resolve_recipient,
)
from .phone import normalize_phone

logger = get_logger(__name__)

DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
# Apple's Cocoa epoch (2001-01-01) offset from Unix epoch (1970-01-01)
APPLE_EPOCH_OFFSET = 978307200


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


def find_group_chat_with_participants(participants: list[str]) -> str | None:
    """Find an existing group chat containing exactly the given participants.

    Args:
        participants: List of phone numbers/handles to find

    Returns:
        Chat ID (e.g., "any;+;chat123456") if found, None otherwise.
    """
    if len(participants) < 2:
        return None

    # Normalize the requested participants for comparison
    requested = {normalize_phone(p) for p in participants}

    # Query the database for group chats (style = 43) and their participants
    conn = get_db_connection()
    cursor = conn.cursor()

    # Get all group chats with their participants
    cursor.execute("""
        SELECT
            c.chat_identifier,
            c.guid,
            GROUP_CONCAT(h.id, '|') as participants
        FROM chat c
        JOIN chat_handle_join chj ON c.ROWID = chj.chat_id
        JOIN handle h ON chj.handle_id = h.ROWID
        WHERE c.style = 43
        GROUP BY c.ROWID
    """)

    for row in cursor.fetchall():
        chat_identifier, guid, participants_str = row
        if not participants_str:
            continue

        # Normalize the chat's participants
        chat_participants = {normalize_phone(p) for p in participants_str.split("|")}

        # Check if the participants match exactly
        if chat_participants == requested:
            conn.close()
            # Build the chat ID in Messages.app format
            return f"iMessage;+;{chat_identifier}"

    conn.close()
    return None


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


def find_chats_by_name(name: str) -> list[tuple[str, str]]:
    """Find all chats matching a display name.

    Returns list of (chat_id, display_name) tuples.
    """
    script = """use framework "Foundation"

on run {chatName}
    set matchList to current application's NSMutableArray's new()

    tell application "Messages"
        repeat with c in chats
            try
                if name of c is chatName then
                    set matchDict to current application's NSMutableDictionary's new()
                    matchDict's setValue:(id of c) forKey:"id"
                    matchDict's setValue:(name of c) forKey:"name"
                    matchList's addObject:matchDict
                end if
            end try
        end repeat
    end tell

    set jsonData to current application's NSJSONSerialization's dataWithJSONObject:matchList options:0 |error|:(missing value)
    set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
    return jsonString as text
end run"""
    output = run_applescript(script, name)
    if not output:
        return []
    try:
        matches = json.loads(output)
        return [(m["id"], m["name"]) for m in matches]
    except (json.JSONDecodeError, KeyError):
        return []


def find_chat_by_name(name: str) -> str | None:
    """Find a chat (group or individual) by its display name.

    Returns the chat ID if exactly one match, None if no matches.
    Raises JeanClaudeError if multiple chats have the same name.
    """
    matches = find_chats_by_name(name)
    return disambiguate_chat_matches(matches, name, id_type="chat ID")


def _is_imessage_native_id(value: str) -> bool:
    """Check if value is a native iMessage ID (chat ID or email/Apple ID)."""
    return value.startswith(("any;", "iMessage;")) or "@" in value


def resolve_recipient(value: str) -> str:
    """Resolve a 'to' value to a phone/chat ID or iMessage handle.

    Auto-detects whether the value is:
    - A chat ID (starts with "any;" or "iMessage;")
    - An email/Apple ID (contains "@" - passed directly as iMessage handle)
    - A phone number (starts with "+" followed by digits, or digit-only string)
    - A chat name (looked up in Messages.app)
    - A contact name (looked up in Contacts.app)
    """
    return _resolve_recipient(
        value,
        is_native_id=_is_imessage_native_id,
        find_chat_by_name=find_chat_by_name,
        resolve_contact=resolve_contact_to_phone,
        service_name="iMessage",
    )


def find_contacts_by_name(name: str) -> list[tuple[str, list[str]]]:
    """Find all contacts matching a name with their phone numbers.

    Returns list of (contact_name, [phone_numbers]) tuples.
    Only returns contacts with at least one valid phone number.
    """
    return [
        (contact_name, valid_phones)
        for contact_name, phones in search_contacts_by_name(name)
        if (valid_phones := [p for p in phones if any(c.isdigit() for c in p)])
    ]


def resolve_contact_to_phone(name: str) -> str:
    """Resolve a contact name to a phone number (raw format from Contacts.app).

    Raises JeanClaudeError if no match found or ambiguous.
    Use find_contacts_by_name() for read operations where multiple matches are OK.
    """
    contacts_with_phones = find_contacts_by_name(name)

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
    """Query parameters for fetching messages from the database.

    Filtering:
        chat_identifiers: Filter to specific chats (phones/emails/chat IDs)
        search_text: Search in message text (LIKE match)
        unread_only: Only fetch unread messages from others
        include_spam: Include filtered/spam messages (excluded by default)

    Output:
        show_both_directions: Include is_from_me field in output (for chat history)
        chronological: Return oldest-first instead of newest-first
        max_results: Maximum messages to return
    """

    # Filtering
    chat_identifiers: list[str] | None = None
    search_text: str | None = None
    unread_only: bool = False
    include_spam: bool = False

    # Output
    show_both_directions: bool = False
    chronological: bool = False
    max_results: int = 20


def fetch_messages(conn: sqlite3.Connection, query: MessageQuery) -> list[dict]:
    """Fetch messages with full enrichment (names, group participants).

    This is the central function for querying messages. It handles:
    - Building WHERE clause from query filters
    - Executing the query with common SELECT columns
    - Identifying unnamed group chats and fetching their participants
    - Resolving phone numbers to contact names
    - Building consistent message dictionaries

    Args:
        conn: Database connection
        query: MessageQuery specifying filters and options

    Returns:
        List of message dictionaries ready for JSON output.
    """
    # Build WHERE clause from query filters
    where_clauses: list[str] = []
    params: list[str] = []

    if not query.include_spam:
        where_clauses.append("(c.is_filtered IS NULL OR c.is_filtered < 2)")

    if query.chat_identifiers:
        # Build OR clause for multiple identifiers
        id_conditions = " OR ".join(
            "(c.chat_identifier = ? OR h.id = ?)" for _ in query.chat_identifiers
        )
        where_clauses.append(f"({id_conditions})")
        for identifier in query.chat_identifiers:
            params.extend([identifier, identifier])

    if query.unread_only:
        # Match Apple's unread criteria from their database index:
        # is_read=0, is_from_me=0, item_type=0, is_finished=1, is_system_message=0
        where_clauses.append(
            "m.is_read = 0 AND m.is_from_me = 0 "
            "AND m.is_finished = 1 AND m.is_system_message = 0 AND m.item_type = 0"
        )

    if query.search_text:
        where_clauses.append("m.text LIKE ?")
        params.append(f"%{query.search_text}%")

    where_clause = " AND ".join(where_clauses) if where_clauses else ""
    cursor = conn.cursor()

    # Always fetch is_from_me - simpler than conditional column building
    # Use subqueries with GROUP_CONCAT/json_group_array for participants and attachments
    # to avoid N+1 queries
    sql = f"""
        SELECT
            datetime(m.date/1000000000 + {APPLE_EPOCH_OFFSET}, 'unixepoch', 'localtime') as date,
            CASE WHEN m.is_from_me = 1 THEN 'me' ELSE COALESCE(h.id, c.chat_identifier, 'unknown') END as sender,
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
            ) as attachments_json,
            m.is_from_me
        FROM message m
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        LEFT JOIN chat c ON cmj.chat_id = c.ROWID
        WHERE (m.text IS NOT NULL OR m.attributedBody IS NOT NULL OR m.cache_has_attachments = 1)
        {("AND " + where_clause) if where_clause else ""}
        ORDER BY m.date DESC
        LIMIT ?
    """

    cursor.execute(sql, (*params, query.max_results))
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
            is_from_me_int,
        ) = row

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

        # Only include is_from_me when requested (e.g., for chat history views)
        is_from_me = bool(is_from_me_int) if query.show_both_directions else False

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

    if query.chronological:
        messages.reverse()

    return messages


@click.group()
def cli():
    """iMessage CLI - send messages and list chats.

    Send via AppleScript (always works). Reading message history requires
    Full Disk Access for the terminal app to query ~/Library/Messages/chat.db.
    """


@cli.command()
@click.argument("recipients", nargs=-1, required=True)
def send(recipients: tuple[str, ...]):
    """Send an iMessage to one or more recipients.

    RECIPIENTS: Phone numbers, chat IDs, group names, or contact names.
    Multiple recipients sends to an existing group chat with those participants.

    Message body is read from stdin.

    \b
    Examples:
        echo "Hello!" | jean-claude imessage send "+12025551234"
        echo "Hello team!" | jean-claude imessage send "Team OA"
        echo "Hello!" | jean-claude imessage send "+12025551234" "+16467194457"
        cat << 'EOF' | jean-claude imessage send "+12025551234"
        It's great to hear from you!
        EOF
    """
    message = read_body_stdin()

    # Handle single recipient (existing behavior)
    if len(recipients) == 1:
        recipient = resolve_recipient(recipients[0])

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
        return

    # Multiple recipients - find existing group chat or error
    resolved = [resolve_recipient(r) for r in recipients]

    # Can't use chat IDs with multiple recipients
    if any(r.startswith("any;") for r in resolved):
        raise JeanClaudeError(
            "Cannot use chat IDs with multiple recipients. "
            "Use a single chat ID to send to an existing group."
        )

    # Find an existing group chat with these participants
    chat_id = find_group_chat_with_participants(resolved)
    if not chat_id:
        raise JeanClaudeError(
            f"No existing group chat found with: {', '.join(resolved)}\n"
            "macOS doesn't allow creating group chats programmatically.\n"
            "Send a message to these recipients manually in Messages.app first,\n"
            "then this command will find and use that group chat."
        )

    # Send to the existing group chat
    script = """on run {chatId, msg}
  tell application "Messages"
    set targetChat to chat id chatId
    send msg to targetChat
  end tell
end run"""

    run_applescript(script, chat_id, message)
    click.echo(f"Sent to group ({chat_id}): {', '.join(resolved)}")


@cli.command()
@click.argument("recipient")
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
def send_file(recipient: str, file_path: Path):
    """Send a file attachment via iMessage.

    RECIPIENT: Phone number, chat ID, email/Apple ID, or contact name.
    FILE_PATH: Path to file to send

    \b
    Examples:
        jean-claude imessage send-file "+12025551234" ./document.pdf
        jean-claude imessage send-file "any;+;chat123456789" ./photo.jpg
        jean-claude imessage send-file "Kevin Seals" ./photo.jpg
    """
    recipient = resolve_recipient(recipient)
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


def get_chat_names_from_applescript(max_results: int) -> dict[str, str]:
    """Get chat ID -> display name mapping from Messages.app.

    Messages.app has access to contact names that aren't in the database.
    Returns dict mapping chat_id to display name.
    """
    script = f"""use framework "Foundation"

tell application "Messages"
    set chatDict to current application's NSMutableDictionary's new()
    set chatCount to 0

    repeat with c in chats
        if chatCount >= {max_results} then exit repeat

        try
            set chatId to id of c as text

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
                chatDict's setValue:chatName forKey:chatId
            end if

            set chatCount to chatCount + 1
        end try
    end repeat
end tell

set jsonData to current application's NSJSONSerialization's dataWithJSONObject:chatDict options:0 |error|:(missing value)
set jsonString to current application's NSString's alloc()'s initWithData:jsonData encoding:(current application's NSUTF8StringEncoding)
return jsonString as text"""

    try:
        output = run_applescript(script)
        if output:
            return json.loads(output)
    except (JeanClaudeError, json.JSONDecodeError):
        pass
    return {}


@cli.command()
@click.option("-n", "--max-results", default=50, help="Maximum chats to list")
@click.option("--unread", is_flag=True, help="Show only chats with unread messages")
def chats(max_results: int, unread: bool):
    """List available iMessage chats.

    Shows chat name, ID, group status, and message timestamps.
    Use chat ID or name to send to groups. Use --unread to show only
    chats with unread messages.

    Examples:
        jean-claude imessage chats
        jean-claude imessage chats --unread
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    # Query chats with stats from database
    # style = 43 is group chat, 45 is individual
    cursor.execute(
        """
        SELECT
            c.guid,
            c.chat_identifier,
            c.display_name,
            c.style,
            (SELECT MAX(m.date) FROM message m
             JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
             WHERE cmj.chat_id = c.ROWID) as last_message_date,
            (SELECT COUNT(*) FROM message m
             JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
             WHERE cmj.chat_id = c.ROWID
               AND m.is_read = 0
               AND m.is_from_me = 0
               AND m.is_finished = 1
               AND m.is_system_message = 0
               AND m.item_type = 0) as unread_count
        FROM chat c
        ORDER BY last_message_date DESC
        LIMIT ?
        """,
        (max_results,),
    )

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        logger.info("No chats found")
        click.echo(json.dumps([]))
        return

    # Get display names from Messages.app (has contact name access)
    name_map = get_chat_names_from_applescript(max_results)

    chats_list = []
    for (
        guid,
        chat_identifier,
        display_name,
        style,
        last_message_date,
        unread_count,
    ) in rows:
        # Build Messages.app-style chat ID
        # guid format: "iMessage;-;+12345" or "iMessage;+;chat12345"
        # We convert to "any;" prefix for compatibility
        if guid.startswith("iMessage;"):
            chat_id = "any" + guid[8:]  # Replace "iMessage" with "any"
        else:
            chat_id = guid

        # Prefer AppleScript name (has contact names), fall back to database
        name = name_map.get(chat_id) or display_name or chat_identifier

        # Convert Apple epoch to Unix timestamp
        last_message_time = None
        if last_message_date:
            last_message_time = int(
                last_message_date / 1_000_000_000 + APPLE_EPOCH_OFFSET
            )

        chats_list.append(
            {
                "id": chat_id,
                "name": name,
                "is_group": style == IMESSAGE_GROUP_CHAT_STYLE,
                "last_message_time": last_message_time,
                "unread_count": unread_count or 0,
            }
        )

    # Filter to unread only if requested
    if unread:
        chats_list = [c for c in chats_list if c["unread_count"] > 0]

    click.echo(json.dumps(chats_list, indent=2))


@cli.command()
@click.argument("chat_id")
def participants(chat_id: str):
    """List participants of a group chat.

    CHAT_ID: The chat ID (e.g., any;+;chat123456789)

    \b
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


_OPEN_CHAT_SCRIPT = """on run {chatId}
  tell application "Messages"
    activate
    set targetChat to chat id chatId
  end tell
end run"""


@cli.command("open")
@click.argument("chat_id")
def open_chat(chat_id: str):
    """Open a chat in Messages.app (marks messages as read).

    CHAT_ID: The chat ID (e.g., any;-;+12025551234 or any;+;chat123...)

    \b
    Example:
        jean-claude imessage open "any;-;+12025551234"
        jean-claude imessage open "any;+;chat123456789"
    """
    run_applescript(_OPEN_CHAT_SCRIPT, chat_id)
    click.echo(f"Opened chat: {chat_id}")


@cli.command("mark-read")
@click.argument("chat_ids", nargs=-1, required=True)
def mark_read(chat_ids: tuple[str, ...]):
    """Mark messages in chats as read by opening them in Messages.app.

    CHAT_IDS: One or more chat IDs (e.g., any;-;+12025551234 or any;+;chat123...)

    Note: This opens each chat in Messages.app briefly to mark messages as read.
    Messages.app must be running for this to work.

    \b
    Examples:
        jean-claude imessage mark-read "any;-;+12025551234"
        jean-claude imessage mark-read "chat1" "chat2" "chat3"
    """
    chats_marked = 0
    for chat_id in chat_ids:
        try:
            run_applescript(_OPEN_CHAT_SCRIPT, chat_id)
            chats_marked += 1
        except Exception as e:
            logger.warning("Failed to mark chat as read", chat_id=chat_id, error=str(e))

    output = {
        "success": True,
        "chats_marked": chats_marked,
    }
    click.echo(json.dumps(output, indent=2))


@cli.command()
@click.option("--chat", "chat_id", help="Filter to specific chat ID or phone number")
@click.option("--name", help="Filter to specific contact by name")
@click.option("-n", "--max-results", default=50, help="Maximum messages to return")
@click.option("--unread", is_flag=True, help="Show only unread messages")
@click.option("--include-spam", is_flag=True, help="Include spam-filtered messages")
def messages(
    chat_id: str | None,
    name: str | None,
    max_results: int,
    unread: bool,
    include_spam: bool,
):
    """List messages from local database (requires Full Disk Access).

    Shows messages with sender, timestamp, and text content.
    Use --chat or --name to filter to a specific conversation.
    Use --unread to show only unread messages.

    \b
    Examples:
        jean-claude imessage messages -n 20
        jean-claude imessage messages --chat "any;-;+12025551234"
        jean-claude imessage messages --name "Kevin Seals"
        jean-claude imessage messages --unread
        jean-claude imessage messages --unread --include-spam
    """
    # Resolve chat identifiers from name or chat_id
    chat_identifiers: list[str] | None = None
    if name:
        contacts = find_contacts_by_name(name)
        if not contacts:
            raise JeanClaudeError(
                f"No contact found matching '{name}' with a phone number"
            )
        # Collect all phone numbers from all matching contacts
        all_phones = [phone for _, phones in contacts for phone in phones]
        # Find chat IDs for these phones
        chat_identifiers = []
        for phone in all_phones:
            chat_id_for_phone = get_chat_id_for_phone(phone)
            if chat_id_for_phone:
                chat_identifiers.append(chat_id_for_phone.split(";")[-1])
        if not chat_identifiers:
            contact_names = [c[0] for c in contacts]
            raise JeanClaudeError(
                f"No message history found for contacts matching '{name}' ({', '.join(contact_names)})"
            )
        # Log which contacts we're showing messages from
        if len(contacts) > 1:
            contact_names = [c[0] for c in contacts]
            logger.info(
                "Showing messages from multiple contacts", contacts=contact_names
            )
    elif chat_id:
        chat_identifiers = [chat_id.split(";")[-1] if ";" in chat_id else chat_id]

    # When viewing specific chat(s), show both directions in chronological order
    viewing_chat = chat_identifiers is not None

    conn = get_db_connection()
    result = fetch_messages(
        conn,
        MessageQuery(
            chat_identifiers=chat_identifiers,
            unread_only=unread,
            include_spam=include_spam,
            show_both_directions=viewing_chat,
            chronological=viewing_chat,
            max_results=max_results,
        ),
    )
    conn.close()

    if not result:
        logger.info("No messages found")

    click.echo(json.dumps(result, indent=2))


@cli.command()
@click.argument("query", required=False)
@click.option("-n", "--max-results", default=50, help="Maximum messages to return")
def search(query: str | None, max_results: int):
    """Search message history (requires Full Disk Access).

    Searches the local Messages database. Your terminal app must have
    Full Disk Access in System Preferences > Privacy & Security.

    QUERY: Search term (searches message text)

    \b
    Examples:
        jean-claude imessage search "dinner plans"
        jean-claude imessage search -n 50
    """
    conn = get_db_connection()
    messages = fetch_messages(
        conn,
        MessageQuery(search_text=query, max_results=max_results),
    )
    conn.close()

    if not messages:
        logger.info("No messages found")

    click.echo(json.dumps(messages, indent=2))
