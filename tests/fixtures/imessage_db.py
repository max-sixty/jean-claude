"""iMessage database fixture generator.

Creates a synthetic chat.db with realistic structure for testing iMessage queries.
All data is synthetic - no personal information.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# Apple's epoch offset (Jan 1, 2001 vs Jan 1, 1970)
APPLE_EPOCH_OFFSET = 978307200

# Chat styles from Messages.app
IMESSAGE_INDIVIDUAL_CHAT_STYLE = 45
IMESSAGE_GROUP_CHAT_STYLE = 43


@dataclass
class SyntheticHandle:
    """A synthetic contact/handle."""

    rowid: int
    identifier: str  # Phone number or email
    service: str = "iMessage"
    country: str = "us"


@dataclass
class SyntheticChat:
    """A synthetic chat."""

    rowid: int
    guid: str
    style: int  # 43=group, 45=individual
    chat_identifier: str
    display_name: str | None = None
    handles: list[int] = field(default_factory=list)  # Handle ROWIDs


@dataclass
class SyntheticMessage:
    """A synthetic message."""

    rowid: int
    guid: str
    text: str | None
    handle_id: int  # 0 for messages from self
    chat_id: int
    date: datetime
    is_from_me: bool = False
    is_read: bool = True
    attributed_body: bytes | None = None
    cache_has_attachments: bool = False


@dataclass
class SyntheticAttachment:
    """A synthetic attachment."""

    rowid: int
    guid: str
    filename: str
    mime_type: str
    total_bytes: int
    transfer_state: int = 5  # 5 = complete


def create_schema(conn: sqlite3.Connection) -> None:
    """Create the iMessage database schema (minimal version for testing)."""
    conn.executescript("""
        -- Handle table (contacts)
        CREATE TABLE handle (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT UNIQUE,
            id TEXT NOT NULL,
            country TEXT,
            service TEXT NOT NULL,
            uncanonicalized_id TEXT,
            person_centric_id TEXT,
            UNIQUE (id, service)
        );

        -- Chat table
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE NOT NULL,
            style INTEGER,
            state INTEGER,
            account_id TEXT,
            properties BLOB,
            chat_identifier TEXT,
            service_name TEXT,
            room_name TEXT,
            account_login TEXT,
            is_archived INTEGER DEFAULT 0,
            last_addressed_handle TEXT,
            display_name TEXT,
            group_id TEXT,
            is_filtered INTEGER DEFAULT 0,
            successful_query INTEGER,
            engram_id TEXT,
            server_change_token TEXT,
            ck_sync_state INTEGER DEFAULT 0,
            original_group_id TEXT,
            last_read_message_timestamp INTEGER DEFAULT 0,
            cloudkit_record_id TEXT,
            last_addressed_sim_id TEXT,
            is_blackholed INTEGER DEFAULT 0,
            syndication_date INTEGER DEFAULT 0,
            syndication_type INTEGER DEFAULT 0,
            is_recovered INTEGER DEFAULT 0,
            is_deleting_incoming_messages INTEGER DEFAULT 0,
            is_pending_review INTEGER DEFAULT 0
        );

        -- Message table
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE NOT NULL,
            text TEXT,
            replace INTEGER DEFAULT 0,
            service_center TEXT,
            handle_id INTEGER DEFAULT 0,
            subject TEXT,
            country TEXT,
            attributedBody BLOB,
            version INTEGER DEFAULT 0,
            type INTEGER DEFAULT 0,
            service TEXT,
            account TEXT,
            account_guid TEXT,
            error INTEGER DEFAULT 0,
            date INTEGER,
            date_read INTEGER,
            date_delivered INTEGER,
            is_delivered INTEGER DEFAULT 0,
            is_finished INTEGER DEFAULT 0,
            is_emote INTEGER DEFAULT 0,
            is_from_me INTEGER DEFAULT 0,
            is_empty INTEGER DEFAULT 0,
            is_delayed INTEGER DEFAULT 0,
            is_auto_reply INTEGER DEFAULT 0,
            is_prepared INTEGER DEFAULT 0,
            is_read INTEGER DEFAULT 0,
            is_system_message INTEGER DEFAULT 0,
            is_sent INTEGER DEFAULT 0,
            has_dd_results INTEGER DEFAULT 0,
            is_service_message INTEGER DEFAULT 0,
            is_forward INTEGER DEFAULT 0,
            was_downgraded INTEGER DEFAULT 0,
            is_archive INTEGER DEFAULT 0,
            cache_has_attachments INTEGER DEFAULT 0,
            cache_roomnames TEXT,
            was_data_detected INTEGER DEFAULT 0,
            was_deduplicated INTEGER DEFAULT 0,
            is_audio_message INTEGER DEFAULT 0,
            is_played INTEGER DEFAULT 0,
            date_played INTEGER,
            item_type INTEGER DEFAULT 0,
            other_handle INTEGER DEFAULT 0,
            group_title TEXT,
            group_action_type INTEGER DEFAULT 0,
            share_status INTEGER DEFAULT 0,
            share_direction INTEGER DEFAULT 0,
            is_expirable INTEGER DEFAULT 0,
            expire_state INTEGER DEFAULT 0,
            message_action_type INTEGER DEFAULT 0,
            message_source INTEGER DEFAULT 0,
            associated_message_guid TEXT,
            associated_message_type INTEGER DEFAULT 0,
            balloon_bundle_id TEXT,
            payload_data BLOB,
            expressive_send_style_id TEXT,
            associated_message_range_location INTEGER DEFAULT 0,
            associated_message_range_length INTEGER DEFAULT 0,
            time_expressive_send_played INTEGER,
            message_summary_info BLOB,
            ck_sync_state INTEGER DEFAULT 0,
            ck_record_id TEXT,
            ck_record_change_tag TEXT,
            destination_caller_id TEXT,
            is_corrupt INTEGER DEFAULT 0,
            reply_to_guid TEXT,
            sort_id INTEGER,
            is_spam INTEGER DEFAULT 0,
            has_unseen_mention INTEGER DEFAULT 0,
            thread_originator_guid TEXT,
            thread_originator_part TEXT,
            syndication_ranges TEXT,
            synced_syndication_ranges TEXT,
            was_delivered_quietly INTEGER DEFAULT 0,
            did_notify_recipient INTEGER DEFAULT 0,
            date_retracted INTEGER,
            date_edited INTEGER,
            was_detonated INTEGER DEFAULT 0,
            part_count INTEGER,
            is_stewie INTEGER DEFAULT 0,
            is_sos INTEGER DEFAULT 0,
            is_critical INTEGER DEFAULT 0,
            bia_reference_id TEXT,
            is_kt_verified INTEGER DEFAULT 0,
            fallback_hash TEXT,
            associated_message_emoji TEXT DEFAULT NULL,
            is_pending_satellite_send INTEGER DEFAULT 0,
            needs_relay INTEGER DEFAULT 0,
            schedule_type INTEGER DEFAULT 0,
            schedule_state INTEGER DEFAULT 0,
            sent_or_received_off_grid INTEGER DEFAULT 0,
            date_recovered INTEGER DEFAULT 0,
            is_time_sensitive INTEGER DEFAULT 0,
            ck_chat_id TEXT
        );

        -- Attachment table
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY AUTOINCREMENT,
            guid TEXT UNIQUE NOT NULL,
            created_date INTEGER DEFAULT 0,
            start_date INTEGER DEFAULT 0,
            filename TEXT,
            uti TEXT,
            mime_type TEXT,
            transfer_state INTEGER DEFAULT 0,
            is_outgoing INTEGER DEFAULT 0,
            user_info BLOB,
            transfer_name TEXT,
            total_bytes INTEGER DEFAULT 0,
            is_sticker INTEGER DEFAULT 0,
            sticker_user_info BLOB,
            attribution_info BLOB,
            hide_attachment INTEGER DEFAULT 0,
            ck_sync_state INTEGER DEFAULT 0,
            ck_server_change_token_blob BLOB,
            ck_record_id TEXT,
            original_guid TEXT UNIQUE NOT NULL,
            is_commsafety_sensitive INTEGER DEFAULT 0,
            emoji_image_content_identifier TEXT DEFAULT NULL,
            emoji_image_short_description TEXT DEFAULT NULL,
            preview_generation_state INTEGER DEFAULT 0
        );

        -- Join tables
        CREATE TABLE chat_handle_join (
            chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE,
            handle_id INTEGER REFERENCES handle (ROWID) ON DELETE CASCADE,
            UNIQUE(chat_id, handle_id)
        );

        CREATE TABLE chat_message_join (
            chat_id INTEGER REFERENCES chat (ROWID) ON DELETE CASCADE,
            message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE,
            message_date INTEGER DEFAULT 0,
            PRIMARY KEY (chat_id, message_id)
        );

        CREATE TABLE message_attachment_join (
            message_id INTEGER REFERENCES message (ROWID) ON DELETE CASCADE,
            attachment_id INTEGER REFERENCES attachment (ROWID) ON DELETE CASCADE,
            UNIQUE(message_id, attachment_id)
        );

        -- Indexes used by our queries
        CREATE INDEX message_idx_handle ON message(handle_id, date);
        CREATE INDEX chat_message_join_idx_chat_id ON chat_message_join(chat_id);
        CREATE INDEX chat_handle_join_idx_handle_id ON chat_handle_join(handle_id);
    """)


def datetime_to_apple(dt: datetime) -> int:
    """Convert datetime to Apple's nanosecond timestamp format."""
    unix_ts = dt.timestamp()
    apple_ts = unix_ts - APPLE_EPOCH_OFFSET
    return int(apple_ts * 1_000_000_000)


def insert_handle(conn: sqlite3.Connection, handle: SyntheticHandle) -> None:
    """Insert a handle into the database."""
    conn.execute(
        """
        INSERT INTO handle (ROWID, id, country, service)
        VALUES (?, ?, ?, ?)
        """,
        (handle.rowid, handle.identifier, handle.country, handle.service),
    )


def insert_chat(conn: sqlite3.Connection, chat: SyntheticChat) -> None:
    """Insert a chat and its handle associations."""
    conn.execute(
        """
        INSERT INTO chat (ROWID, guid, style, chat_identifier, display_name, service_name)
        VALUES (?, ?, ?, ?, ?, 'iMessage')
        """,
        (chat.rowid, chat.guid, chat.style, chat.chat_identifier, chat.display_name),
    )
    for handle_id in chat.handles:
        conn.execute(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
            (chat.rowid, handle_id),
        )


def insert_message(conn: sqlite3.Connection, msg: SyntheticMessage) -> None:
    """Insert a message and its chat association."""
    apple_date = datetime_to_apple(msg.date)
    conn.execute(
        """
        INSERT INTO message (
            ROWID, guid, text, handle_id, date, is_from_me, is_read,
            attributedBody, cache_has_attachments, is_finished, service
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'iMessage')
        """,
        (
            msg.rowid,
            msg.guid,
            msg.text,
            msg.handle_id,
            apple_date,
            1 if msg.is_from_me else 0,
            1 if msg.is_read else 0,
            msg.attributed_body,
            1 if msg.cache_has_attachments else 0,
        ),
    )
    conn.execute(
        """
        INSERT INTO chat_message_join (chat_id, message_id, message_date)
        VALUES (?, ?, ?)
        """,
        (msg.chat_id, msg.rowid, apple_date),
    )


def insert_attachment(
    conn: sqlite3.Connection, attachment: SyntheticAttachment, message_id: int
) -> None:
    """Insert an attachment and link it to a message."""
    conn.execute(
        """
        INSERT INTO attachment (
            ROWID, guid, filename, mime_type, total_bytes, transfer_state, original_guid
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            attachment.rowid,
            attachment.guid,
            attachment.filename,
            attachment.mime_type,
            attachment.total_bytes,
            attachment.transfer_state,
            attachment.guid,
        ),
    )
    conn.execute(
        "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
        (message_id, attachment.rowid),
    )


class DatabaseBuilder:
    """Builder for creating test iMessage databases with synthetic data."""

    def __init__(self):
        self.handles: list[SyntheticHandle] = []
        self.chats: list[SyntheticChat] = []
        self.messages: list[SyntheticMessage] = []
        self.attachments: list[
            tuple[SyntheticAttachment, int]
        ] = []  # (attachment, msg_id)
        self._next_handle_id = 1
        self._next_chat_id = 1
        self._next_message_id = 1
        self._next_attachment_id = 1

    def add_handle(self, identifier: str, service: str = "iMessage") -> SyntheticHandle:
        """Add a contact/handle."""
        handle = SyntheticHandle(
            rowid=self._next_handle_id,
            identifier=identifier,
            service=service,
        )
        self._next_handle_id += 1
        self.handles.append(handle)
        return handle

    def add_individual_chat(
        self, handle: SyntheticHandle, display_name: str | None = None
    ) -> SyntheticChat:
        """Add an individual (1:1) chat."""
        chat = SyntheticChat(
            rowid=self._next_chat_id,
            guid=f"iMessage;-;{handle.identifier}",
            style=IMESSAGE_INDIVIDUAL_CHAT_STYLE,
            chat_identifier=handle.identifier,
            display_name=display_name,
            handles=[handle.rowid],
        )
        self._next_chat_id += 1
        self.chats.append(chat)
        return chat

    def add_group_chat(
        self,
        handles: list[SyntheticHandle],
        display_name: str | None = None,
        chat_identifier: str | None = None,
    ) -> SyntheticChat:
        """Add a group chat."""
        if chat_identifier is None:
            chat_identifier = f"chat{self._next_chat_id}@group.imessage"
        chat = SyntheticChat(
            rowid=self._next_chat_id,
            guid=f"iMessage;+;{chat_identifier}",
            style=IMESSAGE_GROUP_CHAT_STYLE,
            chat_identifier=chat_identifier,
            display_name=display_name,
            handles=[h.rowid for h in handles],
        )
        self._next_chat_id += 1
        self.chats.append(chat)
        return chat

    def add_message(
        self,
        chat: SyntheticChat,
        text: str | None,
        sender: SyntheticHandle | None = None,
        date: datetime | None = None,
        is_read: bool = True,
        attributed_body: bytes | None = None,
    ) -> SyntheticMessage:
        """Add a message to a chat.

        If sender is None, message is from self (is_from_me=True).
        """
        if date is None:
            date = datetime.now()
        msg = SyntheticMessage(
            rowid=self._next_message_id,
            guid=f"msg-{self._next_message_id}",
            text=text,
            handle_id=sender.rowid if sender else 0,
            chat_id=chat.rowid,
            date=date,
            is_from_me=sender is None,
            is_read=is_read,
            attributed_body=attributed_body,
        )
        self._next_message_id += 1
        self.messages.append(msg)
        return msg

    def add_attachment(
        self,
        message: SyntheticMessage,
        filename: str,
        mime_type: str,
        size: int = 1024,
    ) -> SyntheticAttachment:
        """Add an attachment to a message."""
        attachment = SyntheticAttachment(
            rowid=self._next_attachment_id,
            guid=f"att-{self._next_attachment_id}",
            filename=filename,
            mime_type=mime_type,
            total_bytes=size,
        )
        self._next_attachment_id += 1
        self.attachments.append((attachment, message.rowid))
        # Mark message as having attachments
        message.cache_has_attachments = True
        return attachment

    def build(self, path: Path | str | None = None) -> sqlite3.Connection:
        """Build the database and return a connection.

        If path is None, creates an in-memory database.
        """
        if path is None:
            conn = sqlite3.connect(":memory:")
        else:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path.unlink()
            conn = sqlite3.connect(str(path))

        create_schema(conn)

        for handle in self.handles:
            insert_handle(conn, handle)

        for chat in self.chats:
            insert_chat(conn, chat)

        for msg in self.messages:
            insert_message(conn, msg)

        for attachment, msg_id in self.attachments:
            insert_attachment(conn, attachment, msg_id)

        conn.commit()
        return conn


def create_sample_database() -> DatabaseBuilder:
    """Create a sample database with realistic synthetic data.

    Returns a builder that can be customized or built directly.
    """
    builder = DatabaseBuilder()

    # Create synthetic contacts (no real data)
    alice = builder.add_handle("+15551234567")
    bob = builder.add_handle("+15559876543")
    charlie = builder.add_handle("+15555551234")
    diana = builder.add_handle("diana.test@example.com")

    # Create individual chats
    chat_alice = builder.add_individual_chat(alice)
    chat_bob = builder.add_individual_chat(bob)
    chat_diana = builder.add_individual_chat(diana)

    # Create a group chat (unnamed - will show participants)
    group_unnamed = builder.add_group_chat([alice, bob, charlie])

    # Create a named group chat
    group_named = builder.add_group_chat([alice, diana], display_name="Project Team")

    # Add messages with realistic timing
    base_time = datetime.now() - timedelta(days=7)

    # Individual chat with Alice - conversation
    builder.add_message(
        chat_alice,
        "Hey, are you free for lunch tomorrow?",
        sender=alice,
        date=base_time,
    )
    builder.add_message(
        chat_alice,
        "Sure! How about noon at the usual place?",
        sender=None,  # from self
        date=base_time + timedelta(minutes=5),
    )
    builder.add_message(
        chat_alice,
        "Perfect, see you then!",
        sender=alice,
        date=base_time + timedelta(minutes=7),
    )

    # Bob chat with an unread message
    builder.add_message(
        chat_bob,
        "Did you see the game last night?",
        sender=bob,
        date=base_time + timedelta(days=1),
        is_read=False,
    )

    # Diana chat with email handle
    builder.add_message(
        chat_diana,
        "Thanks for sending over the documents",
        sender=diana,
        date=base_time + timedelta(days=2),
    )

    # Group chat messages
    builder.add_message(
        group_unnamed,
        "Who's bringing snacks to the party?",
        sender=alice,
        date=base_time + timedelta(days=3),
    )
    builder.add_message(
        group_unnamed,
        "I can bring chips",
        sender=bob,
        date=base_time + timedelta(days=3, minutes=10),
    )
    builder.add_message(
        group_unnamed,
        "I'll get drinks",
        sender=None,  # from self
        date=base_time + timedelta(days=3, minutes=15),
    )
    builder.add_message(
        group_unnamed,
        "Great, I'll handle dessert",
        sender=charlie,
        date=base_time + timedelta(days=3, minutes=20),
    )

    # Named group chat
    builder.add_message(
        group_named,
        "Meeting moved to 3pm",
        sender=alice,
        date=base_time + timedelta(days=4),
    )
    builder.add_message(
        group_named,
        "Got it, thanks for the heads up",
        sender=diana,
        date=base_time + timedelta(days=4, minutes=5),
    )

    # Message with attachment
    msg_with_photo = builder.add_message(
        chat_alice,
        "Check out this photo from lunch!",
        sender=alice,
        date=base_time + timedelta(days=5),
    )
    builder.add_attachment(
        msg_with_photo,
        "~/Library/Messages/Attachments/ab/cd/IMG_1234.jpeg",
        "image/jpeg",
        size=2048576,  # 2MB
    )

    # Recent message (will be first in results)
    builder.add_message(
        chat_alice,
        "Just checking in - how are things?",
        sender=alice,
        date=datetime.now() - timedelta(hours=1),
    )

    return builder
