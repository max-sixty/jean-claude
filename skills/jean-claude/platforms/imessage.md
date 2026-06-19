# iMessage

Send via AppleScript. On first use, macOS will prompt for Automation permission.
Reading history requires Full Disk Access. See "Personalization" section in
SKILL.md for default behaviors.

**Command prefix:** `jean-claude `

**Chat IDs:** Individual chats use `any;-;+1234567890` (phone number), group
chats use `any;+;chat123...`. Get these from `imessage chats`.

## Send Messages

Message body is read from stdin. **Always use heredocs** (Claude Code's Bash
tool has a bug that escapes '!' to '\!' when using echo). Supports one or more
recipients.

```bash
# Send to phone number
cat << 'EOF' | jean-claude imessage send "+12025551234"
Hello!
EOF

# Send to contact by name (must match exactly one contact with one phone)
cat << 'EOF' | jean-claude imessage send "John Smith"
Hello!
EOF

# Send to group chat by name
cat << 'EOF' | jean-claude imessage send "Team OA"
Hello team!
EOF

# Message with apostrophe and multiple lines
cat << 'EOF' | jean-claude imessage send "+12025551234"
It's great to hear from you!
Let me know when you're free.
EOF

# Send to group chat by ID
cat << 'EOF' | jean-claude imessage send "any;+;chat123456789"
Hello group!
EOF

# Send to multiple recipients (uses existing group with those participants)
cat << 'EOF' | jean-claude imessage send "+12025551234" "+16467194457"
Hello!
EOF

# Send file (recipient auto-detects phone, contact name, or group name)
jean-claude imessage send-file "+12025551234" ./document.pdf
jean-claude imessage send-file "John Smith" ./photo.jpg
```

**Recipient resolution:** Auto-detects the recipient type:
1. Chat IDs (e.g., `any;+;chat123...`) - used directly
2. Phone numbers (e.g., `+12025551234`) - sent to that number
3. Group/chat names (e.g., `Team OA`) - looked up in Messages.app
4. Contact names (e.g., `John Smith`) - looked up in Contacts.app

**Multiple recipients:** When you specify multiple recipients, the command finds
an existing group chat with those exact participants and sends to it. If no
group exists, you'll be prompted to create one manually in Messages.app first
(macOS doesn't allow creating group chats programmatically).

**Contact lookup fails if:**
- Multiple contacts match (e.g., "John" matches "John Smith" and "John Davis")
- One contact has multiple phone numbers

When lookup fails, the error shows all matches—use the specific phone number.

## List Chats

```bash
# List chats (shows name, chat ID, unread count)
jean-claude imessage chats
jean-claude imessage chats -n 10

# Show only chats with unread messages
jean-claude imessage chats --unread

# Get participants
jean-claude imessage participants "any;+;chat123456789"
```

Other: `imessage open CHAT_ID` opens a chat in Messages.app (brings app to focus).

## Read Messages (Requires Full Disk Access)

```bash
# Recent messages
jean-claude imessage messages -n 20

# Unread messages only (excludes spam-filtered by default)
jean-claude imessage messages --unread

# Include spam-filtered messages
jean-claude imessage messages --unread --include-spam

# Messages from specific chat
jean-claude imessage messages --chat "any;-;+12025551234"
jean-claude imessage messages --name "John Smith"

# Search messages
jean-claude imessage search "dinner plans"
```

To enable reading: System Settings > Privacy & Security > Full Disk Access >
add your terminal app.

## Image Attachments

Messages carry an `attachments` field listing image attachments. Each has a
`downloaded` flag:

- `downloaded: true`: the bytes are on local disk. The `file` path points to
  them; read it with Claude's Read tool to view and describe the image.
- `downloaded: false`: the bytes aren't on local disk. Usually the original is
  in iCloud (Messages "Optimize Mac Storage" offloads attachment bytes), so the
  photo exists but isn't readable locally. The entry carries `transfer_state`
  and no `file`. To pull the original down, run `imessage open CHAT_ID` with the
  message's `chat_id` to open the chat in Messages.app, then re-fetch the
  messages. (If `transfer_state` is 5, the bytes were downloaded once and later
  deleted; opening the chat re-fetches from iCloud only while iCloud still holds
  the original.)

```json
{
  "chat_id": "any;+;chat123456789",
  "attachments": [
    {
      "type": "image",
      "filename": "IMG_1234.heic",
      "mimeType": "image/heic",
      "size": 456789,
      "downloaded": true,
      "file": "/Users/you/Library/Messages/Attachments/.../IMG_1234.heic"
    },
    {
      "type": "image",
      "filename": "IMG_5678.heic",
      "mimeType": "image/heic",
      "size": 987654,
      "downloaded": false,
      "transfer_state": 0
    }
  ]
}
```

Only image attachments are included (HEIC, JPEG, PNG, GIF, WebP). Other media
types (video, audio, documents) are not exposed.

A photo the user remembers sending that doesn't appear at all (no entry, even
with `downloaded: false`) hasn't synced to this Mac: its row isn't in the local
database. Opening the thread in Messages.app on this Mac, or on the device that
has it, lets iCloud sync it down.
