"""Test fixtures for WhatsApp CLI wrapper.

Provides sample data mimicking the Go CLI output for testing
the Python wrapper functions without calling the actual binary.
"""

from __future__ import annotations

# Sample chats mimicking Go CLI "chats" output
SAMPLE_CHATS = [
    {
        "jid": "12025551234@s.whatsapp.net",
        "name": "Alice Smith",
        "is_group": False,
        "last_message_time": 1703894400,
        "unread_count": 2,
    },
    {
        "jid": "12025555678@s.whatsapp.net",
        "name": "Bob Johnson",
        "is_group": False,
        "last_message_time": 1703808000,
        "unread_count": 0,
    },
    {
        "jid": "120363277025153496@g.us",
        "name": "Project Team",
        "is_group": True,
        "last_message_time": 1703980800,
        "unread_count": 5,
    },
    {
        "jid": "120363299999999999@g.us",
        "name": "Family Group",
        "is_group": True,
        "last_message_time": 1703721600,
        "unread_count": 0,
    },
    # Chat with duplicate name (for testing ambiguous lookups)
    {
        "jid": "12025559999@s.whatsapp.net",
        "name": "Alice Smith",
        "is_group": False,
        "last_message_time": 1703635200,
        "unread_count": 0,
    },
]

