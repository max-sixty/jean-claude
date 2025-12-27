"""WhatsApp SQLite database builder for testing.

Creates synthetic WhatsApp message databases matching the Go CLI schema
for integration testing without real WhatsApp authentication.

Schema mirrors whatsapp/main.go database structure.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


class DatabaseBuilder:
    """Builder for creating synthetic WhatsApp message databases.

    Creates SQLite databases matching the whatsapp-cli schema for testing
    the Go binary with controlled inputs.
    """

    def __init__(self):
        self._chats: list[dict] = []
        self._contacts: list[dict] = []
        self._messages: list[dict] = []
        self._reactions: list[dict] = []

    def add_chat(
        self,
        jid: str,
        name: str,
        is_group: bool = False,
        last_message_time: int | None = None,
        marked_as_unread: bool = False,
    ) -> str:
        """Add a chat to the database.

        Args:
            jid: WhatsApp JID (e.g., "12025551234@s.whatsapp.net" or "123@g.us")
            name: Display name for the chat
            is_group: True for group chats, False for individual
            last_message_time: Unix timestamp of last message
            marked_as_unread: Whether chat is manually marked unread

        Returns:
            The JID for use in other methods.
        """
        self._chats.append(
            {
                "jid": jid,
                "name": name,
                "is_group": is_group,
                "last_message_time": last_message_time,
                "marked_as_unread": marked_as_unread,
                "updated_at": int(datetime.now().timestamp()),
            }
        )
        return jid

    def add_contact(
        self,
        jid: str,
        name: str | None = None,
        push_name: str | None = None,
    ) -> str:
        """Add a contact to the database.

        Args:
            jid: WhatsApp JID
            name: Contact name from address book
            push_name: Name set by the contact themselves

        Returns:
            The JID for use in other methods.
        """
        self._contacts.append(
            {
                "jid": jid,
                "name": name,
                "push_name": push_name,
                "updated_at": int(datetime.now().timestamp()),
            }
        )
        return jid

    def add_message(
        self,
        message_id: str,
        chat_jid: str,
        sender_jid: str,
        text: str | None = None,
        timestamp: int | datetime | None = None,
        sender_name: str | None = None,
        media_type: str | None = None,
        is_from_me: bool = False,
        is_read: bool = True,
        reply_to_id: str | None = None,
        reply_to_sender: str | None = None,
        reply_to_text: str | None = None,
    ) -> str:
        """Add a message to the database.

        Args:
            message_id: Unique message ID (e.g., "3EB0ABC123")
            chat_jid: JID of the chat this message belongs to
            sender_jid: JID of the sender (or "me" for self)
            text: Message text content
            timestamp: Unix timestamp or datetime (defaults to now)
            sender_name: Display name of sender
            media_type: Type of media ("image", "video", "audio", etc.)
            is_from_me: True if sent by the user
            is_read: True if message has been read
            reply_to_id: ID of message being replied to
            reply_to_sender: Sender name of quoted message
            reply_to_text: Text preview of quoted message

        Returns:
            The message ID for use in add_reaction().
        """
        if timestamp is None:
            timestamp = int(datetime.now().timestamp())
        elif isinstance(timestamp, datetime):
            timestamp = int(timestamp.timestamp())

        self._messages.append(
            {
                "id": message_id,
                "chat_jid": chat_jid,
                "sender_jid": sender_jid,
                "sender_name": sender_name,
                "timestamp": timestamp,
                "text": text,
                "media_type": media_type,
                "is_from_me": is_from_me,
                "is_read": is_read,
                "created_at": int(datetime.now().timestamp()),
                "reply_to_id": reply_to_id,
                "reply_to_sender": reply_to_sender,
                "reply_to_text": reply_to_text,
            }
        )
        return message_id

    def add_reaction(
        self,
        message_id: str,
        chat_jid: str,
        sender_jid: str,
        emoji: str,
        sender_name: str | None = None,
        timestamp: int | datetime | None = None,
    ) -> None:
        """Add a reaction to a message.

        Args:
            message_id: ID of the message being reacted to
            chat_jid: JID of the chat containing the message
            sender_jid: JID of the person reacting
            emoji: The reaction emoji
            sender_name: Display name of the person reacting
            timestamp: Unix timestamp or datetime (defaults to now)
        """
        if timestamp is None:
            timestamp = int(datetime.now().timestamp())
        elif isinstance(timestamp, datetime):
            timestamp = int(timestamp.timestamp())

        self._reactions.append(
            {
                "message_id": message_id,
                "chat_jid": chat_jid,
                "sender_jid": sender_jid,
                "sender_name": sender_name,
                "emoji": emoji,
                "timestamp": timestamp,
            }
        )

    def build(self, db_path: Path | str) -> sqlite3.Connection:
        """Build the database and write to the specified path.

        Args:
            db_path: Path to write the SQLite database file.

        Returns:
            Open connection to the database.
        """
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path))
        self._create_schema(conn)
        self._insert_data(conn)
        conn.commit()
        return conn

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create all tables matching the Go CLI schema."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                chat_jid TEXT NOT NULL,
                sender_jid TEXT NOT NULL,
                sender_name TEXT,
                timestamp INTEGER NOT NULL,
                text TEXT,
                media_type TEXT,
                is_from_me INTEGER NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL,
                mime_type_full TEXT,
                media_key BLOB,
                file_sha256 BLOB,
                file_enc_sha256 BLOB,
                file_length INTEGER,
                direct_path TEXT,
                media_url TEXT,
                media_file_path TEXT,
                reply_to_id TEXT,
                reply_to_sender TEXT,
                reply_to_text TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_jid);
            CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
            CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(is_read, chat_jid);

            CREATE TABLE IF NOT EXISTS contacts (
                jid TEXT PRIMARY KEY,
                name TEXT,
                push_name TEXT,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                jid TEXT PRIMARY KEY,
                name TEXT,
                is_group INTEGER NOT NULL,
                last_message_time INTEGER,
                marked_as_unread INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reactions (
                message_id TEXT NOT NULL,
                chat_jid TEXT NOT NULL,
                sender_jid TEXT NOT NULL,
                sender_name TEXT,
                emoji TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                PRIMARY KEY (message_id, sender_jid)
            );
            CREATE INDEX IF NOT EXISTS idx_reactions_message ON reactions(message_id);
            CREATE INDEX IF NOT EXISTS idx_reactions_chat ON reactions(chat_jid);
        """)

    def _insert_data(self, conn: sqlite3.Connection) -> None:
        """Insert all accumulated data into the database."""
        # Insert chats
        for chat in self._chats:
            conn.execute(
                """
                INSERT INTO chats (jid, name, is_group, last_message_time, marked_as_unread, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat["jid"],
                    chat["name"],
                    1 if chat["is_group"] else 0,
                    chat["last_message_time"],
                    1 if chat["marked_as_unread"] else 0,
                    chat["updated_at"],
                ),
            )

        # Insert contacts
        for contact in self._contacts:
            conn.execute(
                """
                INSERT INTO contacts (jid, name, push_name, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    contact["jid"],
                    contact["name"],
                    contact["push_name"],
                    contact["updated_at"],
                ),
            )

        # Insert messages
        for msg in self._messages:
            conn.execute(
                """
                INSERT INTO messages (
                    id, chat_jid, sender_jid, sender_name, timestamp, text,
                    media_type, is_from_me, is_read, created_at,
                    reply_to_id, reply_to_sender, reply_to_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    msg["id"],
                    msg["chat_jid"],
                    msg["sender_jid"],
                    msg["sender_name"],
                    msg["timestamp"],
                    msg["text"],
                    msg["media_type"],
                    1 if msg["is_from_me"] else 0,
                    1 if msg["is_read"] else 0,
                    msg["created_at"],
                    msg["reply_to_id"],
                    msg["reply_to_sender"],
                    msg["reply_to_text"],
                ),
            )

        # Insert reactions
        for reaction in self._reactions:
            conn.execute(
                """
                INSERT INTO reactions (message_id, chat_jid, sender_jid, sender_name, emoji, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    reaction["message_id"],
                    reaction["chat_jid"],
                    reaction["sender_jid"],
                    reaction["sender_name"],
                    reaction["emoji"],
                    reaction["timestamp"],
                ),
            )


def create_sample_database() -> DatabaseBuilder:
    """Create a sample database with realistic test data.

    Returns a builder populated with sample chats, contacts, messages,
    and reactions for testing.
    """
    builder = DatabaseBuilder()
    base_time = datetime.now() - timedelta(days=1)

    # Add individual chats
    alice_jid = builder.add_chat(
        "12025551234@s.whatsapp.net",
        "Alice Smith",
        is_group=False,
        last_message_time=int((base_time + timedelta(hours=2)).timestamp()),
    )

    bob_jid = builder.add_chat(
        "12025555678@s.whatsapp.net",
        "Bob Johnson",
        is_group=False,
        last_message_time=int((base_time + timedelta(hours=1)).timestamp()),
    )

    # Add group chats
    team_jid = builder.add_chat(
        "120363277025153496@g.us",
        "Project Team",
        is_group=True,
        last_message_time=int((base_time + timedelta(hours=3)).timestamp()),
    )

    builder.add_chat(
        "120363299999999999@g.us",
        "Family Group",
        is_group=True,
        last_message_time=int((base_time - timedelta(hours=5)).timestamp()),
        marked_as_unread=True,
    )

    # Add contacts
    builder.add_contact(alice_jid, name="Alice Smith", push_name="Alice")
    builder.add_contact(bob_jid, name="Bob Johnson", push_name="Bobby")

    # Add messages in Alice chat
    msg1 = builder.add_message(
        "3EB0ABC001",
        alice_jid,
        alice_jid,
        text="Hey, are you free for lunch?",
        timestamp=base_time,
        sender_name="Alice Smith",
        is_read=True,
    )

    builder.add_message(
        "3EB0ABC002",
        alice_jid,
        "me",
        text="Sure! How about noon?",
        timestamp=base_time + timedelta(minutes=5),
        is_from_me=True,
        is_read=True,
    )

    builder.add_message(
        "3EB0ABC003",
        alice_jid,
        alice_jid,
        text="Perfect! See you then",
        timestamp=base_time + timedelta(hours=2),
        sender_name="Alice Smith",
        is_read=False,
    )

    # Add reaction to first message
    builder.add_reaction(
        msg1, alice_jid, "me", "👍", timestamp=base_time + timedelta(minutes=1)
    )

    # Add messages in team chat
    team_msg1 = builder.add_message(
        "3EB0DEF001",
        team_jid,
        bob_jid,
        text="Meeting moved to 3pm",
        timestamp=base_time + timedelta(hours=3),
        sender_name="Bob Johnson",
        is_read=False,
    )

    builder.add_message(
        "3EB0DEF002",
        team_jid,
        alice_jid,
        text="Sounds good!",
        timestamp=base_time + timedelta(hours=3, minutes=5),
        sender_name="Alice Smith",
        is_read=False,
        reply_to_id=team_msg1,
        reply_to_sender="Bob Johnson",
        reply_to_text="Meeting moved to 3pm",
    )

    # Add reactions to team message
    builder.add_reaction(
        team_msg1, team_jid, alice_jid, "👍", sender_name="Alice Smith"
    )
    builder.add_reaction(team_msg1, team_jid, "me", "✅")

    # Add message with media
    builder.add_message(
        "3EB0GHI001",
        bob_jid,
        bob_jid,
        text="",
        timestamp=base_time + timedelta(hours=1),
        sender_name="Bob Johnson",
        media_type="image",
        is_read=True,
    )

    return builder
