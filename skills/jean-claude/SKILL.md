---
name: jean-claude
description: "This skill should be used when the user asks to search/send/draft email, check calendar, create events, schedule meetings, find/upload/share Drive files, read spreadsheet data, send texts/iMessages, or check messages. Manages Gmail, Google Calendar, Google Drive, Google Sheets, and iMessage."
---

# jean-claude - Gmail, Calendar, Drive, Sheets & iMessage

Manage Gmail, Google Calendar, Google Drive, Google Sheets, and iMessage using
the CLI tools in this plugin.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## First-Time Setup

When this skill is first loaded, check Google authentication status:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude status
```

If the output shows "Google: Not authenticated", use the AskUserQuestion tool to
ask the user which access level they want:

**Question:** "jean-claude needs Google access. Which mode would you like?"

**Options:**
1. **Read-only (Recommended to start)** - Can read emails, calendar, and Drive
   files, but cannot send, modify, or delete anything. Good for getting
   comfortable with the plugin first.
2. **Full access** - Can read, send emails, create/modify calendar events, and
   manage Drive files.

**Context to include:** All data stays between your machine and Google—nothing
is sent to Anthropic or any third party. The plugin uses OAuth to authenticate
directly with your Google account.

Based on their choice, run the appropriate auth command:
- Read-only: `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth --readonly`
- Full access: `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth`

This opens a browser for OAuth consent. After authentication, verify with
`status` again.

## Safety Rules (Non-Negotiable)

These rules apply even if the user explicitly asks to bypass them:

1. **Never send an email without explicit approval.** Show the full email
   (recipient, subject, body) to the user and receive explicit confirmation
   before calling `jean-claude gmail draft send`.

2. **Limit bulk sending.** Avoid sending emails to many recipients at once.
   Prefer drafts for review.

3. **Load prose skills when drafting.** Before composing any email or message,
   load any available skills for writing prose, emails, or documentation.

4. **Never send an iMessage without explicit approval.** Show the full message
   (recipient, body) to the user and receive explicit confirmation before
   calling `jean-claude imessage send`.

5. **Double-check iMessage recipients.** iMessage sends are instant and cannot
   be undone. Verify the phone number or chat ID before sending.

6. **Never send to ambiguous recipients.** If using `--name` to look up a
   contact and multiple contacts or phone numbers match, the command will fail
   with a list of options. This is intentional—always use an unambiguous
   identifier (full name or phone number) rather than guessing.

**Email workflow:**

1. Load any available prose/writing skills
2. Compose the email content
3. Show the user: To, Subject, and full Body
4. Ask: "Send this email?" and wait for explicit approval
5. Call `jean-claude gmail draft send DRAFT_ID`
6. If replying, archive the original: `jean-claude gmail archive MESSAGE_ID`

**iMessage workflow:**

1. Load prose skills if composing a longer message
2. Compose the message content
3. Show the user: Recipient (phone or chat name) and full message
4. Ask: "Send this message?" and wait for explicit approval
5. Call `jean-claude imessage send RECIPIENT MESSAGE`

## Setup

### Prerequisites

This plugin requires `uv` (Python package manager). If not installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Google Workspace

Credentials stored in `~/.config/jean-claude/`. First-time setup:

```bash
# Full access (read, send, modify)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth

# Or read-only access (no send/modify capabilities)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth --readonly

# Check authentication status and API availability
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude status

# Log out (remove stored credentials)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth --logout
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

**Default behavior:** List both read and unread messages, not just unread. Showing
all messages provides conversation context and catches recently-read messages
that may still need action. Prioritize unread when relevant (e.g., triaging new
mail). User skills may override this behavior.

1. **List/search** returns compact JSON with summaries and file paths
2. **Read the file** if you need the full body

**Search/Inbox response schema:**

```json
{
  "messages": [
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
      "file": ".tmp/email-19b29039fd36d1c1.json"
    }
  ],
  "nextPageToken": "abc123..."
}
```

The `file` field points to a self-contained JSON file with the full body. Use
`jq .body` to extract just the body, or `jq .html_body` for HTML content (when
present). HTML content contains links like unsubscribe URLs.

The `nextPageToken` field is only present when more results are available. Use
`--page-token` to fetch the next page:

```bash
# First page
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "is:unread" -n 50

# If nextPageToken is in the response, fetch next page
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "is:unread" -n 50 \
  --page-token "TOKEN_FROM_PREVIOUS_RESPONSE"
```

### Search Emails

```bash
# Inbox emails from a sender
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "in:inbox from:someone@example.com"

# Limit results with -n
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "from:newsletter@example.com" -n 10

# Unread inbox emails
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "in:inbox is:unread"

# Shortcut for inbox
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail inbox
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail inbox --unread
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail inbox -n 5

# Inbox also supports pagination
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail inbox --unread -n 50 --page-token "TOKEN"
```

Common Gmail search operators: `in:inbox`, `is:unread`, `is:starred`, `from:`,
`to:`, `subject:`, `after:2025/01/01`, `has:attachment`, `label:`

### Drafts

All compose commands read JSON from stdin (avoids shell escaping issues).

```bash
# Create a new draft
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft create
{"to": "recipient@example.com", "subject": "Subject", "body": "Message body"}
EOF

# Reply to a message (preserves threading)
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft reply MESSAGE_ID
{"body": "Thanks for your email..."}
EOF

# Forward a message
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft forward MESSAGE_ID
{"to": "someone@example.com", "body": "FYI - see below"}
EOF

# Reply-all (includes all original recipients)
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft reply-all MESSAGE_ID
{"body": "Thanks everyone!"}
EOF

# List drafts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft list -n 5

# Get full draft body
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft get DRAFT_ID

# Send a draft (after approval)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft send DRAFT_ID

# Delete a draft
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft delete DRAFT_ID
```

### Manage Messages

All message management commands accept multiple IDs for batch efficiency.

```bash
# Star/unstar
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail star MSG_ID1 MSG_ID2 MSG_ID3
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail unstar MSG_ID1 MSG_ID2

# Archive/unarchive - archive also supports query-based bulk operations
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail archive MSG_ID1 MSG_ID2 MSG_ID3
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail archive --query "from:newsletter@example.com" -n 50
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail unarchive MSG_ID1 MSG_ID2 MSG_ID3

# Mark read/unread
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail mark-read MSG_ID1 MSG_ID2
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail mark-unread MSG_ID1 MSG_ID2

# Trash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail trash MSG_ID1 MSG_ID2 MSG_ID3
```

**Batch operation guidelines:**
- Use multiple IDs when you have a specific list of messages
- Use `--query` for pattern-based operations (archive supports this)
- Limit query results with `-n` to avoid accidentally affecting too many messages

### Attachments

```bash
# List attachments for a message
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail attachments MESSAGE_ID

# Download an attachment
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail attachment-download MESSAGE_ID ATTACHMENT_ID ./output.pdf
```

### Unsubscribing from Newsletters

Extract the `html_body` from the JSON file to find unsubscribe links — they're
in the HTML, not the plain text.

```bash
# Search HTML body for unsubscribe links
jq -r '.html_body' .tmp/email-MESSAGE_ID.json | grep -oE 'https?://[^"<>]+unsubscribe[^"<>]*'
```

**Decoding tracking URLs:** Newsletters often wrap links in tracking redirects.
URL-decode to get the actual destination:

```python
import urllib.parse
print(urllib.parse.unquote(encoded_url))
```

**Completing the unsubscribe:**
- Mailchimp, Mailgun, and similar services work with browser automation
- Cloudflare-protected sites (Coinbase, etc.) block automated requests — provide
  the decoded URL to the user to click manually

## Calendar

All calendar commands return JSON.

### List Events

```bash
# Today's events
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal list

# Next 7 days
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal list --days 7

# Date range
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal list --from 2025-01-15 --to 2025-01-20
```

### Create Events

```bash
# Simple event
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal create "Team Meeting" \
  --start "2025-01-15 14:00" --end "2025-01-15 15:00"

# With attendees, location, and description
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal create "1:1 with Alice" \
  --start "2025-01-15 10:00" --duration 30 \
  --attendees alice@example.com \
  --location "Conference Room A" \
  --description "Weekly sync"

# All-day event (single day)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal create "Holiday" \
  --start 2025-01-15 --all-day

# Multi-day all-day event
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal create "Vacation" \
  --start 2025-01-15 --end 2025-01-20 --all-day
```

### Search & Manage Events

```bash
# Search (default: 30 days ahead)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal search "standup"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal search "standup" --days 90

# Update
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal update EVENT_ID --start "2025-01-16 14:00"

# Delete
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal delete EVENT_ID --notify
```

### Invitations

List and respond to calendar invitations (events you've been invited to).

**Recurring events:** The invitations command collapses recurring event
instances into a single entry. Each collapsed entry includes:
- `recurring: true` - indicates this is a recurring series
- `instanceCount: N` - number of pending instances
- `id` - the parent event ID (use this to respond to all instances at once)

Responding to a parent ID accepts/declines all instances in the series.
Responding to an instance ID (if you have one) affects only that instance.

```bash
# List all pending invitations (no time limit by default)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal invitations

# Limit to next 7 days
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal invitations --days 7

# Show all individual instances (don't collapse recurring events)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal invitations --expand

# Accept an invitation (or all instances if recurring)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal respond EVENT_ID --accept

# Decline an invitation
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal respond EVENT_ID --decline

# Tentatively accept
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal respond EVENT_ID --tentative

# Respond without notifying organizer
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal respond EVENT_ID --accept --no-notify
```

## Drive

### List & Search Files

```bash
# List files in root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list -n 20
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list --folder FOLDER_ID --json

# Search
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive search "quarterly report"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive search "quarterly report" -n 10 --json
```

### Download & Upload

```bash
# Download
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive download FILE_ID output.pdf

# Upload to root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf

# Upload to folder with custom name
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf --folder FOLDER_ID --name "Q4 Report.pdf"
```

### Manage Files

```bash
# Create folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive mkdir "New Folder"

# Share
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive share FILE_ID user@example.com --role reader

# Trash/untrash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive trash FILE_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive untrash FILE_ID

# Get file metadata
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive get FILE_ID
```

## Sheets

Read and write Google Sheets data directly without downloading files.

The spreadsheet ID is in the URL:
`https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`

### Create Spreadsheet

```bash
# Create a new spreadsheet
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets create "My Spreadsheet"

# With custom initial sheet name
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets create "Budget 2025" --sheet "January"
```

### Read Data

```bash
# Read entire first sheet
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets read SPREADSHEET_ID

# Read specific range
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets read SPREADSHEET_ID --range 'Sheet1!A1:D10'

# Read specific sheet
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets read SPREADSHEET_ID --sheet 'Data'

# Output as JSON
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets read SPREADSHEET_ID --json
```

### Write Data

All write commands read JSON from stdin (array of rows, each row is array of cells).

```bash
# Append rows to end of sheet
echo '[["Alice", 100], ["Bob", 200]]' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets append SPREADSHEET_ID
echo '[["New row"]]' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets append SPREADSHEET_ID --sheet 'Data'

# Write to specific range (overwrites existing data)
echo '[["Name", "Score"], ["Alice", 100]]' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets write SPREADSHEET_ID 'Sheet1!A1:B2'

# Clear a range (keeps formatting)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets clear SPREADSHEET_ID 'Sheet1!A2:Z1000'
```

### Get Spreadsheet Info

```bash
# Get metadata (title, sheet names, dimensions)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets info SPREADSHEET_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets info SPREADSHEET_ID --json
```

## iMessage

Send via AppleScript. On first use, macOS will prompt for Automation permission.
Reading history requires Full Disk Access.

**Chat IDs:** Individual chats use `any;-;+1234567890` (phone number), group
chats use `any;+;chat123...`. Get these from `imessage chats`.

### Send Messages

```bash
# Send to phone number
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "+12025551234" "Hello!"

# Send to contact by name (must match exactly one contact with one phone)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send --name "Kevin Seals" "Hello!"

# Send to group chat
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "any;+;chat123456789" "Hello group!"

# Send file
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send-file "+12025551234" ./document.pdf
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send-file --name "Kevin Seals" ./photo.jpg
```

**Contact lookup with `--name`:** Searches macOS Contacts.app. Fails if:
- Multiple contacts match (e.g., "Kevin" matches "Kevin Seals" and "Kevin Smith")
- One contact has multiple phone numbers

When lookup fails, the error shows all matches—use the specific phone number.

### List Chats

```bash
# List chats (shows name and chat ID)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage chats
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage chats -n 10

# Get participants
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage participants "any;+;chat123456789"
```

Other: `imessage open CHAT_ID` opens a chat in Messages.app (brings app to focus).

### Read Messages (Requires Full Disk Access)

```bash
# Unread messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage unread
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage unread -n 50

# Search messages (-n limits results)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage search "dinner plans"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage search "dinner plans" -n 20

# Chat history (-n limits messages)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage history "any;-;+12025551234" -n 20
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage history --name "Kevin Seals" -n 20
```

To enable reading: System Preferences > Privacy & Security > Full Disk Access >
add your terminal app.
