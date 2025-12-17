# jean-claude

A Claude Code plugin for managing Gmail, Google Calendar, Google Drive, and
iMessage.

## Installation

### As a Claude Code Plugin

```bash
# Install from the plugin marketplace (when published)
claude plugins install jean-claude

# Or install from local directory
claude plugins install /path/to/jean-claude
```

### Manual Installation

Clone this repository and add it to your Claude Code plugins directory.

## Setup

### Google Workspace (Gmail, Calendar, Drive)

1. Create a Google Cloud project at https://console.cloud.google.com
2. Enable the Gmail, Calendar, and Drive APIs
3. Create OAuth credentials:
   - Go to "Credentials" > "Create Credentials" > "OAuth client ID"
   - Select "Desktop app" as application type
   - Download the JSON file
4. Save the file as `~/.config/jean-claude/client_secret.json`
5. Run the auth script to complete OAuth flow:

```bash
uv run /path/to/jean-claude/skills/jean-claude/scripts/auth.py
```

This opens a browser for OAuth consent. Credentials are saved to
`~/.config/jean-claude/token.json` and persist until revoked.

### iMessage

- **Sending messages**: Works immediately via AppleScript
- **Reading messages**: Requires Full Disk Access for your terminal app
  - System Preferences > Privacy & Security > Full Disk Access
  - Add and enable your terminal (Terminal, iTerm2, Ghostty, etc.)

## Usage

Once installed, the skill activates automatically when you ask Claude to:

- Search, send, or draft emails
- Check calendar or create events
- Find, upload, or share Drive files
- Send texts or check iMessages

### Example Prompts

```
"Check my inbox for unread emails"
"What's on my calendar today?"
"Send an email to alice@example.com about the meeting"
"Search Drive for quarterly reports"
"Text +12025551234 that I'm running late"
```

## Features

### Gmail

- Search and list emails
- Create, send, and manage drafts
- Reply and forward with threading preserved
- Star, archive, mark read/unread, trash messages

### Google Calendar

- List events (today, date range, or N days)
- Create events with attendees, location, description
- Search, update, and delete events
- Timezone auto-detection

### Google Drive

- List and search files
- Upload and download files
- Create folders and share files
- Trash and restore files

### iMessage

- Send messages to individuals or groups
- Send file attachments
- List chats and participants
- Search message history
- View unread messages

## Security

- OAuth tokens are stored with 0600 permissions (owner read/write only)
- No credentials are stored in the plugin itself
- Each user must provide their own Google Cloud OAuth credentials
- All email/message sends require explicit user approval

## License

MIT
