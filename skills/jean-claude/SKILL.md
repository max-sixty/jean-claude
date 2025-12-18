---
name: jean-claude
description: "This skill should be used when the user asks to search/send/draft email, check calendar, create events, schedule meetings, find/upload/share Drive files, send texts/iMessages, or check messages. Manages Gmail, Google Calendar, Google Drive, and iMessage."
---

# jean-claude - Gmail, Calendar, Drive & iMessage

Manage Gmail, Google Calendar, Google Drive, and iMessage using the CLI tools
in this plugin.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean`

## Safety Rules (Non-Negotiable)

These rules apply even if the user explicitly asks to bypass them:

1. **Never send an email without explicit approval.** Show the full email
   (recipient, subject, body) to the user and receive explicit confirmation
   before calling `jean gmail draft send`.

2. **Limit bulk sending.** Avoid sending emails to many recipients at once.
   Prefer drafts for review.

3. **Load prose skills when drafting.** Before composing any email or message,
   load relevant skills for writing prose (e.g., `documentation`).

4. **Never send an iMessage without explicit approval.** Show the full message
   (recipient, body) to the user and receive explicit confirmation before
   calling `jean imessage send`.

5. **Double-check iMessage recipients.** iMessage sends are instant and cannot
   be undone. Verify the phone number or chat ID before sending.

**Email workflow:**

1. Load prose skills (e.g., `documentation`)
2. Compose the email content
3. Show the user: To, Subject, and full Body
4. Ask: "Send this email?" and wait for explicit approval
5. Call `jean gmail draft send DRAFT_ID`
6. If replying, archive the original: `jean gmail archive MESSAGE_ID`

**iMessage workflow:**

1. Load prose skills if composing a longer message
2. Compose the message content
3. Show the user: Recipient (phone or chat name) and full message
4. Ask: "Send this message?" and wait for explicit approval
5. Call `jean imessage send RECIPIENT MESSAGE`

## Setup

Credentials stored in `~/.config/jean-claude/`. First-time setup:

```bash
# Full access (read, send, modify)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean auth

# Or read-only access (no send/modify capabilities)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean auth --readonly
```

This opens a browser for OAuth consent. Click "Advanced" → "Go to jean-claude
(unsafe)" if you see an unverified app warning. Credentials persist until
revoked.

To use your own Google Cloud credentials instead (if default ones hit the 100
user limit), download your OAuth JSON from Google Cloud Console and save it as
`~/.config/jean-claude/client_secret.json` before running the auth script. See
README for detailed setup steps.

## Gmail

### Reading Emails

1. **List/search** returns compact JSON with summaries and file paths
2. **Read the file** if you need the full body

**Message JSON schema:**

```json
{
  "id": "19b29039fd36d1c1",
  "threadId": "19b29039fd36d1c1",
  "from": "Name <email@example.com>",
  "to": "recipient@example.com",
  "cc": "other@example.com",
  "subject": "Subject line",
  "date": "Tue, 16 Dec 2025 21:12:21 +0000",
  "snippet": "First ~200 chars of body...",
  "labels": ["INBOX", "UNREAD"],
  "file": ".tmp/email-19b29039fd36d1c1.txt"
}
```

### Search Emails

```bash
# Inbox emails from a sender
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail search "in:inbox from:someone@example.com"

# Unread inbox emails
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail search "in:inbox is:unread"

# Shortcut for inbox
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail inbox
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail inbox --unread
```

Common Gmail search operators: `in:inbox`, `is:unread`, `is:starred`, `from:`,
`to:`, `subject:`, `after:2024/01/01`, `has:attachment`, `label:`

### Drafts

All compose commands read JSON from stdin (avoids shell escaping issues).

```bash
# Create a new draft
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft create
{"to": "recipient@example.com", "subject": "Subject", "body": "Message body"}
EOF

# Reply to a message (preserves threading)
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft reply MESSAGE_ID
{"body": "Thanks for your email..."}
EOF

# Forward a message
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft forward MESSAGE_ID
{"to": "someone@example.com", "body": "FYI - see below"}
EOF

# List drafts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft list

# Get full draft body
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft get DRAFT_ID

# Send a draft (after approval)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft send DRAFT_ID

# Delete a draft
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail draft delete DRAFT_ID
```

### Manage Messages

```bash
# Star/unstar
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail star MESSAGE_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail unstar MESSAGE_ID

# Archive
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail archive MESSAGE_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail archive --query "from:newsletter@example.com"

# Mark read/unread
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail mark-read MESSAGE_ID

# Trash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gmail trash MESSAGE_ID
```

## Calendar

### List Events

```bash
# Today's events
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal list

# Next 7 days
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal list --days 7

# Date range
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal list --from 2024-01-15 --to 2024-01-20
```

### Create Events

```bash
# Simple event
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal create "Team Meeting" \
  --start "2024-01-15 14:00" --end "2024-01-15 15:00"

# With attendees
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal create "1:1 with Alice" \
  --start "2024-01-15 10:00" --duration 30 \
  --attendees alice@example.com
```

### Search & Manage Events

```bash
# Search
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal search "standup"

# Update
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal update EVENT_ID --start "2024-01-16 14:00"

# Delete
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gcal delete EVENT_ID --notify
```

## Drive

### List & Search Files

```bash
# List files in root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive list

# Search
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive search "quarterly report"
```

### Download & Upload

```bash
# Download
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive download FILE_ID output.pdf

# Upload
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive upload document.pdf
```

### Manage Files

```bash
# Create folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive mkdir "New Folder"

# Share
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive share FILE_ID user@example.com --role reader

# Trash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean gdrive trash FILE_ID
```

## iMessage

Send via AppleScript. On first use, macOS will prompt for Automation permission.
Reading history requires Full Disk Access.

### Send Messages

```bash
# Send to phone number
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage send "+12025551234" "Hello!"

# Send to group chat
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage send "any;+;chat123456789" "Hello group!"

# Send file
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage send-file "+12025551234" ./document.pdf
```

### List Chats

```bash
# List chats (shows name and chat ID)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage chats

# Get participants
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage participants "any;+;chat123456789"
```

### Read Messages (Requires Full Disk Access)

```bash
# Unread messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage unread

# Search messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage search "dinner plans"

# Chat history
uv run --project ${CLAUDE_PLUGIN_ROOT} jean imessage history "any;-;+12025551234" -n 20
```

To enable reading: System Preferences > Privacy & Security > Full Disk Access >
add your terminal app.
