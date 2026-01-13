# WhatsApp

Send and receive WhatsApp messages. Requires Go binary to be built and QR code
authentication (see Setup section in SKILL.md).

**Command prefix:** `jean-claude `

## Sync Messages

WhatsApp messages are stored locally for fast access. The `messages --unread`
command auto-syncs, so explicit sync is only needed for other queries:

```bash
# Sync messages (also fetches chat names)
jean-claude whatsapp sync
```

The sync command downloads new messages and automatically fetches names for
chats that don't have them.

## Send Messages

Message body is read from stdin. **Always use heredocs** (Claude Code's Bash
tool has a bug that escapes '!' to '\!' when using echo). Recipient is a
positional argument.

```bash
# Send to phone number (with country code)
cat << 'EOF' | jean-claude whatsapp send "+12025551234"
Hello!
EOF

# Message with apostrophe and multiple lines
cat << 'EOF' | jean-claude whatsapp send "+12025551234"
It's great to hear from you!
Let me know when you're free.
EOF

# Reply to a specific message
cat << 'EOF' | jean-claude whatsapp send "+12025551234" --reply-to MSG_ID
Reply text!
EOF
```

## List Chats

```bash
# List recent chats
jean-claude whatsapp chats

# Limit results
jean-claude whatsapp chats -n 10
```

## Read Messages

```bash
# Recent messages (from local database)
jean-claude whatsapp messages -n 20

# Unread messages (auto-syncs and downloads all media)
jean-claude whatsapp messages --unread

# Messages from specific chat (use ID from chats command)
jean-claude whatsapp messages --chat "120363277025153496@g.us"

# Explicitly download media for non-unread queries
jean-claude whatsapp messages --chat "..." --with-media
```

**Output includes:**
- `reply_to`: When a message is a reply, shows the original message context (id, sender, text preview)
- `reactions`: List of emoji reactions with sender info
- `file`: Path to downloaded media (with `--with-media`)

**Example output with new fields:**
```json
{
  "id": "ABC123",
  "text": "That's great!",
  "sender_name": "Alice",
  "reply_to": {
    "id": "XYZ789",
    "sender": "12025551234@s.whatsapp.net",
    "text": "Check out this article: https://..."
  },
  "reactions": [
    {"emoji": "👍", "sender_name": "Bob"},
    {"emoji": "❤️", "sender_name": "Carol"}
  ]
}
```

**Note:** The `--unread` flag automatically syncs with WhatsApp and downloads
all media (images, videos, audio, documents, stickers). Other queries read from
the local database only—run `whatsapp sync` first if you need the latest
messages, and use `--with-media` to download media.

## Media Downloads

Use `download` to fetch media from specific messages:

```bash
# Download media from a specific message
jean-claude whatsapp download MESSAGE_ID

# Download to custom path
jean-claude whatsapp download MESSAGE_ID --output ./photo.jpg
```

Files are stored with content-hash filenames for deduplication (same image sent
twice → downloaded once).

## Other Commands

```bash
# List contacts
jean-claude whatsapp contacts

# Check status
jean-claude whatsapp status
```
