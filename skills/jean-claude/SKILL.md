---
name: jean-claude
description: "This skill should be used when the user asks to search/send/draft email, check calendar, create events, schedule meetings, find/upload/share Drive files, read/edit Google Docs, read spreadsheet data, send texts/iMessages, send WhatsApp messages, check messages, or create reminders. Manages Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets, iMessage, WhatsApp, and Apple Reminders."
---

# jean-claude - Gmail, Calendar, Drive, Docs, Sheets, iMessage, WhatsApp & Reminders

Manage Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets,
iMessage, WhatsApp, and Apple Reminders using the CLI tools in this plugin.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## Before You Start (Required)

**STOP. Load user personalization skills before doing anything else.**

When this skill loads for inbox/email/message tasks:

1. Check if a user skill like `managing-messages` exists (look at available
   skills list for anything mentioning inbox, email, message, or communication)
2. If found, invoke `Skill` tool to load it BEFORE running any jean-claude
   commands
3. User preferences in those skills override the defaults below

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

6. **Never send to ambiguous recipients.** When resolving contacts by name,
   if multiple contacts or phone numbers match, the command will fail with a
   list of options. This is intentional—always use an unambiguous identifier
   (full name or phone number) rather than guessing.

7. **Never send a WhatsApp message without explicit approval.** Show the full
   message (recipient, body) to the user and receive explicit confirmation
   before calling `jean-claude whatsapp send`.

8. **Verify WhatsApp recipients carefully.** WhatsApp sends are instant and
   cannot be undone. Always confirm the phone number before sending.

**Email workflow:**

1. Load any available prose/writing skills
2. Compose the email content
3. Show the user: To, Subject, and full Body
4. Ask: "Send this email?" and wait for explicit approval
5. Call `jean-claude gmail draft send DRAFT_ID`
6. If replying, archive the original: `jean-claude gmail archive THREAD_ID`

**iMessage workflow:**

1. Load prose skills if composing a longer message
2. Compose the message content
3. Show the user: Recipient (phone or chat name) and full message
4. Ask: "Send this message?" and wait for explicit approval
5. Pipe message body to `jean-claude imessage send RECIPIENT`

**WhatsApp workflow:**

1. Load prose skills if composing a longer message
2. Compose the message content
3. Show the user: Recipient (phone number with country code) and full message
4. Ask: "Send this WhatsApp message?" and wait for explicit approval
5. Pipe message body to `jean-claude whatsapp send RECIPIENT`

## Personalization

**REQUIRED: Search for and load user skills before any messaging action.**

Before reviewing inbox, drafting emails, or managing messages:

1. **List available skills** — check descriptions for skills mentioning:
   inbox, email, message, communication, contacts, or similar
2. **Load matching user skills** using the Skill tool BEFORE proceeding
3. **Only then** fetch messages or compose drafts

Skip this step only if you already loaded a relevant user skill in this session.

User skills override any defaults below. They may define:
- Priority contacts and relationships
- Triage rules (what to archive, what needs attention)
- Response tone and style
- Default message counts

Use these defaults in lieu of any user preferences:

### Email Defaults
- Fetch both read and unread messages (context helps)
- 20 messages per request
- Present messages neutrally — don't assume priority
- No automatic archiving without user guidance

### iMessage Defaults
- Prioritize known contacts over unknown senders
- 20 messages per request

### Response Drafting Defaults
- Load prose/writing skills before composing
- No assumed tone or style — ask if unclear
- Show full message for approval before sending

### Presenting Messages

When showing messages (inbox, unread, search results), use a numbered list so
the user can reference items by number: "archive 1, reply to 2", "star 3 and 5".

```
1. **From:** alice@example.com — Meeting tomorrow
   Quick sync about the project timeline...

2. **From:** bob@example.com — Invoice #1234
   Please find attached...

3. **From:** newsletter@company.com — Weekly digest
   This week's highlights...
```

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

### WhatsApp

WhatsApp requires a Go binary and QR code authentication. First-time setup:

```bash
# Build the Go CLI (requires Go installed)
cd ${CLAUDE_PLUGIN_ROOT}/whatsapp && go build -o whatsapp-cli .

# Authenticate with WhatsApp (scan QR code with your phone)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp auth

# Check authentication status
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude status
```

The QR code will be displayed in the terminal and saved as a PNG file. Scan it
with WhatsApp: Settings > Linked Devices > Link a Device.

Credentials are stored in `~/.config/jean-claude/whatsapp/`. To log out:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp logout
```

## Gmail

### Reading Emails

See "Personalization" section for default behaviors and user skill overrides.

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
      "file": "~/.cache/jean-claude/emails/email-19b29039fd36d1c1.json"
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

### Get a Single Message

```bash
# Get message by ID (writes full body to ~/.cache/jean-claude/emails/)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail get MESSAGE_ID
```

Use this when you have a specific message ID and want to read its full content.

### Drafts

All compose commands read JSON from stdin (avoids shell escaping issues).

```bash
# Create a new draft
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft create
{"to": "recipient@example.com", "subject": "Subject", "body": "Message body"}
EOF

# Reply to a message (body from stdin, preserves threading, includes quoted original)
echo "Thanks for your email..." | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft reply MESSAGE_ID

# Reply with custom CC
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft reply MESSAGE_ID --cc "manager@example.com"
Thanks for the update!
EOF

# Forward a message (TO as argument, optional note from stdin)
echo "FYI - see below" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft forward MESSAGE_ID someone@example.com

# Forward without a note
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft forward MESSAGE_ID someone@example.com < /dev/null

# Reply-all (includes all original recipients)
echo "Thanks everyone!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft reply-all MESSAGE_ID

# List drafts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft list -n 5

# Get draft as JSON (writes to ~/.cache/jean-claude/drafts/)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft get DRAFT_ID

# Update a draft (preserves threading, only updates fields provided)
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft update DRAFT_ID
{"cc": "added@example.com", "subject": "Updated subject"}
EOF

# Send a draft (after approval)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft send DRAFT_ID

# Delete a draft
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail draft delete DRAFT_ID
```

**Iterating on long emails:** For complex emails, use file editing to iterate
with the user without rewriting the full email each time:

1. Create initial draft: `jean-claude gmail draft create`
2. Get draft as JSON: `jean-claude gmail draft get DRAFT_ID` (writes to cache)
3. Use Edit tool to modify the `body` field in `~/.cache/jean-claude/drafts/draft-DRAFT_ID.json`
4. Update draft: `cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.json | jean-claude gmail draft update DRAFT_ID`
5. Show user, get feedback, repeat steps 3-4 until approved

### Manage Threads and Messages

Most commands operate on threads (matching Gmail UI behavior). Use `threadId` from
inbox/search output. Star/unstar operate on individual messages (use `latestMessageId`).

```bash
# Star/unstar (message-level - use latestMessageId)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail star MSG_ID1 MSG_ID2 MSG_ID3
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail unstar MSG_ID1 MSG_ID2

# Archive/unarchive (thread-level - use threadId)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail archive THREAD_ID1 THREAD_ID2 THREAD_ID3
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail archive --query "from:newsletter@example.com" -n 50
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail unarchive THREAD_ID1 THREAD_ID2 THREAD_ID3

# Mark read/unread (thread-level - use threadId)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail mark-read THREAD_ID1 THREAD_ID2
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail mark-unread THREAD_ID1 THREAD_ID2

# Trash (thread-level - use threadId)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail trash THREAD_ID1 THREAD_ID2 THREAD_ID3
```

**Which ID to use:**
- Thread operations (archive, mark-read, trash): use `threadId`
- Message operations (star): use `latestMessageId`
- Use `--query` for pattern-based operations (archive supports this)

### Attachments

```bash
# List attachments for a message
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail attachments MESSAGE_ID

# Download an attachment (saved to ~/.cache/jean-claude/attachments/)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail attachment-download MESSAGE_ID ATTACHMENT_ID filename.pdf

# Download to specific directory
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail attachment-download MESSAGE_ID ATTACHMENT_ID filename.pdf --output ./
```

### Unsubscribing from Newsletters

Extract the `html_body` from the JSON file to find unsubscribe links — they're
in the HTML, not the plain text.

```bash
# Search HTML body for unsubscribe links
jq -r '.html_body' ~/.cache/jean-claude/emails/email-MESSAGE_ID.json | grep -oE 'https?://[^"<>]+unsubscribe[^"<>]*'
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
# Download (saved to ~/.cache/jean-claude/drive/)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive download FILE_ID

# Download to specific directory
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive download FILE_ID --output ./

# Upload to root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf

# Upload to folder with custom name
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf --folder FOLDER_ID --name "Q4 Report.pdf"
```

### Manage Files

```bash
# Create folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive mkdir "New Folder"

# Move file to different folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive move FILE_ID FOLDER_ID

# Share
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive share FILE_ID user@example.com --role reader

# Trash/untrash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive trash FILE_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive untrash FILE_ID

# Get file metadata
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive get FILE_ID
```

## Docs

Read and write Google Docs documents.

The document ID is in the URL:
`https://docs.google.com/document/d/DOCUMENT_ID/edit`

### Create Document

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs create "My Document"
```

### Read Content

```bash
# Read as plain text
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs read DOCUMENT_ID

# Read full JSON structure (includes indices for advanced editing)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs read DOCUMENT_ID --json
```

### Write Content

```bash
# Append text to end of document (JSON stdin)
echo '{"text": "New paragraph to add"}' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs append DOCUMENT_ID

# Find and replace text
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs replace DOCUMENT_ID --find "old text" --replace-with "new text"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs replace DOCUMENT_ID --find "TODO" --replace-with "DONE" --match-case
```

### Get Document Info

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs info DOCUMENT_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs info DOCUMENT_ID --json
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

### Manage Sheets

```bash
# Add a new sheet to a spreadsheet
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets add-sheet SPREADSHEET_ID "February"

# Add at specific position (0 = first)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets add-sheet SPREADSHEET_ID "Summary" --index 0

# Delete a sheet
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets delete-sheet SPREADSHEET_ID "Old Data"
```

### Sort Data

```bash
# Sort by column A (ascending)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A

# Sort by multiple columns
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by B --by 'C desc'

# Sort with header row (exclude first row from sorting)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A --header
```

## iMessage

Send via AppleScript. On first use, macOS will prompt for Automation permission.
Reading history requires Full Disk Access. See "Personalization" section for
default behaviors.

**Chat IDs:** Individual chats use `any;-;+1234567890` (phone number), group
chats use `any;+;chat123...`. Get these from `imessage chats`.

### Send Messages

Message body is read from stdin (avoids shell escaping issues with apostrophes
and special characters). Supports one or more recipients.

```bash
# Send to phone number
echo "Hello!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "+12025551234"

# Send to contact by name (must match exactly one contact with one phone)
echo "Hello!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "Kevin Seals"

# Send to group chat by name
echo "Hello team!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "Team OA"

# Multiline message with heredoc
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "+12025551234"
It's great to hear from you!
Let me know when you're free.
EOF

# Send to group chat by ID
echo "Hello group!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "any;+;chat123456789"

# Send to multiple recipients (uses existing group with those participants)
echo "Hello!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send "+12025551234" "+16467194457"

# Send file (recipient auto-detects phone, contact name, or group name)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send-file "+12025551234" ./document.pdf
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage send-file "Kevin Seals" ./photo.jpg
```

**Recipient resolution:** Auto-detects the recipient type:
1. Chat IDs (e.g., `any;+;chat123...`) - used directly
2. Phone numbers (e.g., `+12025551234`) - sent to that number
3. Group/chat names (e.g., `Team OA`) - looked up in Messages.app
4. Contact names (e.g., `Kevin Seals`) - looked up in Contacts.app

**Multiple recipients:** When you specify multiple recipients, the command finds
an existing group chat with those exact participants and sends to it. If no
group exists, you'll be prompted to create one manually in Messages.app first
(macOS doesn't allow creating group chats programmatically).

**Contact lookup fails if:**
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
# Recent messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages -n 20

# Unread messages only (excludes spam-filtered by default)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages --unread

# Include spam-filtered messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages --unread --include-spam

# Messages from specific chat
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages --chat "any;-;+12025551234"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages --name "Kevin Seals"

# Search messages
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage search "dinner plans"
```

To enable reading: System Preferences > Privacy & Security > Full Disk Access >
add your terminal app.

### Image Attachments

Messages include an `attachments` field with file paths to images. Use Claude's
Read tool to view and describe the images.

**Attachment schema:**

```json
{
  "attachments": [
    {
      "type": "image",
      "filename": "IMG_1234.heic",
      "mimeType": "image/heic",
      "size": 456789,
      "file": "/Users/you/Library/Messages/Attachments/.../IMG_1234.heic"
    }
  ]
}
```

Only image attachments are included (HEIC, JPEG, PNG, GIF, WebP). Other media
types (video, audio, documents) are not exposed.

## WhatsApp

Send and receive WhatsApp messages. Requires Go binary to be built and QR code
authentication (see Setup section above).

### Sync Messages

WhatsApp messages are stored locally for fast access. The `messages --unread`
command auto-syncs, so explicit sync is only needed for other queries:

```bash
# Sync messages (also fetches chat names)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp sync
```

The sync command downloads new messages and automatically fetches names for
chats that don't have them.

### Send Messages

Message body is read from stdin (avoids shell escaping issues). Recipient is a
positional argument.

```bash
# Send to phone number (with country code)
echo "Hello!" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp send "+12025551234"

# Multiline message with heredoc
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp send "+12025551234"
It's great to hear from you!
Let me know when you're free.
EOF

# Reply to a specific message
echo "Reply text" | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp send "+12025551234" --reply-to MSG_ID
```

### List Chats

```bash
# List recent chats
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp chats

# Limit results
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp chats -n 10
```

### Read Messages

```bash
# Recent messages (from local database)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp messages -n 20

# Unread messages (auto-syncs and downloads all media)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp messages --unread

# Messages from specific chat (use JID from chats command)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp messages --chat "120363277025153496@g.us"

# Explicitly download media for non-unread queries
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp messages --chat "..." --with-media
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

### Media Downloads

Use `download` to fetch media from specific messages:

```bash
# Download media from a specific message
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp download MESSAGE_ID

# Download to custom path
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp download MESSAGE_ID --output ./photo.jpg
```

Files are stored with content-hash filenames for deduplication (same image sent
twice → downloaded once).

### Other Commands

```bash
# List contacts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp contacts

# Check status
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp status
```

## Reminders

Create and manage Apple Reminders via AppleScript. Reminders sync across all
Apple devices via iCloud. On first use, macOS will prompt for Automation
permission.

### Create Reminders

```bash
# Simple reminder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Buy groceries"

# With due date and time
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Call doctor" --due "2025-12-27 14:00"

# Date only (defaults to 9:00 AM)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Submit report" --due 2025-12-30

# With list, priority, and notes
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Important task" \
  --list Work --priority high --notes "Don't forget!"
```

### List Reminders

```bash
# List incomplete reminders (all lists)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list

# From specific list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list --list Work

# Show completed reminders
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list --completed

# Limit results
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list -n 10
```

### Manage Reminder Lists

```bash
# Show all reminder lists with counts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders lists
```

### Complete & Delete

```bash
# Mark as completed
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders complete "x-apple-reminder://..."

# Delete a reminder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders delete "x-apple-reminder://..."
```

### Search

```bash
# Search reminders by title
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders search "groceries"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders search "groceries" -n 10
```
