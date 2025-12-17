---
name: jean-claude
description: "This skill should be used when the user asks to search/send/draft email, check calendar, create events, schedule meetings, find/upload/share Drive files, send texts/iMessages, or check messages. Manages Gmail, Google Calendar, Google Drive, and iMessage."
---

# jean-claude - Gmail, Calendar, Drive & iMessage

Manage Gmail, Google Calendar, Google Drive, and iMessage using the scripts in
this skill.

**Scripts location:** `${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/`

## Safety Rules (Non-Negotiable)

These rules apply even if the user explicitly asks to bypass them:

1. **Never send an email without explicit approval.** Show the full email
   (recipient, subject, body) to the user and receive explicit confirmation
   before calling `draft send`.

2. **Limit bulk sending.** Avoid sending emails to many recipients at once.
   Prefer drafts for review.

3. **Load prose skills when drafting.** Before composing any email or message,
   load relevant skills for writing prose (e.g., `documentation`).

4. **Never send an iMessage without explicit approval.** Show the full message
   (recipient, body) to the user and receive explicit confirmation before
   calling `imessage.py send`.

5. **Double-check iMessage recipients.** iMessage sends are instant and cannot
   be undone. Verify the phone number or chat ID before sending.

**Email workflow:**

1. Load prose skills (e.g., `documentation`)
2. Compose the email content
3. Show the user: To, Subject, and full Body
4. Ask: "Send this email?" and wait for explicit approval
5. Call `gmail.py draft send DRAFT_ID`
6. If replying, archive the original: `gmail.py archive MESSAGE_ID`

**iMessage workflow:**

1. Load prose skills if composing a longer message
2. Compose the message content
3. Show the user: Recipient (phone or chat name) and full message
4. Ask: "Send this message?" and wait for explicit approval
5. Call `imessage.py send RECIPIENT MESSAGE`

## Setup

Credentials stored in `~/.config/jean-claude/`. First-time setup:

1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable Gmail, Calendar, and Drive APIs
3. Create OAuth credentials (Application type: Desktop)
4. Download `client_secret.json` to `~/.config/jean-claude/`
5. Run:

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/auth.py
```

This opens a browser for OAuth consent. Credentials persist until revoked.

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
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py search "in:inbox from:someone@example.com"

# Unread inbox emails
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py search "in:inbox is:unread"

# Shortcut for inbox
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py inbox
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py inbox --unread
```

Common Gmail search operators: `in:inbox`, `is:unread`, `is:starred`, `from:`,
`to:`, `subject:`, `after:2024/01/01`, `has:attachment`, `label:`

### Drafts

All compose commands read JSON from stdin (avoids shell escaping issues).

```bash
# Create a new draft
cat << 'EOF' | uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft create
{"to": "recipient@example.com", "subject": "Subject", "body": "Message body"}
EOF

# Reply to a message (preserves threading)
cat << 'EOF' | uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft reply MESSAGE_ID
{"body": "Thanks for your email..."}
EOF

# Forward a message
cat << 'EOF' | uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft forward MESSAGE_ID
{"to": "someone@example.com", "body": "FYI - see below"}
EOF

# List drafts
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft list

# Get full draft body
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft get DRAFT_ID

# Send a draft (after approval)
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft send DRAFT_ID

# Delete a draft
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py draft delete DRAFT_ID
```

### Manage Messages

```bash
# Star/unstar
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py star MESSAGE_ID
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py unstar MESSAGE_ID

# Archive
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py archive MESSAGE_ID
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py archive --query "from:newsletter@example.com"

# Mark read/unread
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py mark-read MESSAGE_ID

# Trash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gmail.py trash MESSAGE_ID
```

## Calendar

### List Events

```bash
# Today's events
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py list

# Next 7 days
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py list --days 7

# Date range
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py list --from 2024-01-15 --to 2024-01-20
```

### Create Events

```bash
# Simple event
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py create "Team Meeting" \
  --start "2024-01-15 14:00" --end "2024-01-15 15:00"

# With attendees
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py create "1:1 with Alice" \
  --start "2024-01-15 10:00" --duration 30 \
  --attendees alice@example.com
```

### Search & Manage Events

```bash
# Search
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py search "standup"

# Update
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py update EVENT_ID --start "2024-01-16 14:00"

# Delete
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gcal.py delete EVENT_ID --notify
```

## Drive

### List & Search Files

```bash
# List files in root
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py list

# Search
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py search "quarterly report"
```

### Download & Upload

```bash
# Download
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py download FILE_ID output.pdf

# Upload
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py upload document.pdf
```

### Manage Files

```bash
# Create folder
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py mkdir "New Folder"

# Share
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py share FILE_ID user@example.com --role reader

# Trash
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/gdrive.py trash FILE_ID
```

## iMessage

Send via AppleScript (always works). Reading history requires Full Disk Access.

### Send Messages

```bash
# Send to phone number
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py send "+12025551234" "Hello!"

# Send to group chat
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py send "any;+;chat123456789" "Hello group!"

# Send file
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py send-file "+12025551234" ./document.pdf
```

### List Chats

```bash
# List chats (shows name and chat ID)
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py chats

# Get participants
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py participants "any;+;chat123456789"
```

### Read Messages (Requires Full Disk Access)

```bash
# Unread messages
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py unread

# Search messages
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py search "dinner plans"

# Chat history
uv run ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/scripts/imessage.py history "any;-;+12025551234" -n 20
```

To enable reading: System Preferences > Privacy & Security > Full Disk Access >
add your terminal app.
