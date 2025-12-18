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

Run the auth command to authenticate:

```bash
# Full access (read, send, modify)
jean-claude auth

# Or read-only access (no send/modify capabilities)
jean-claude auth --readonly
```

This opens a browser for OAuth consent. Credentials are saved to
`~/.config/jean-claude/token.json` and persist until revoked.

**Note**: You may see an "unverified app" warning during OAuth. Click
"Advanced" → "Go to jean-claude (unsafe)" to proceed. This is normal for
apps pending Google verification.

#### Using Your Own Google Cloud Credentials (Optional)

Use your own credentials if the default ones stop working (Google limits
unverified apps to 100 users) or if you want your own quota.

1. Create a Google Cloud project at https://console.cloud.google.com
2. Go to "APIs & Services" → "Enabled APIs" and enable:
   - Gmail API
   - Google Calendar API
   - Google Drive API
3. Go to "APIs & Services" → "OAuth consent screen":
   - Choose "External" user type
   - Fill in app name and your email
   - Add scopes: `gmail.modify`, `calendar`, `drive`
   - Add yourself as a test user
4. Go to "APIs & Services" → "Credentials":
   - Click "Create Credentials" → "OAuth client ID"
   - Choose "Desktop app" as application type
   - Download the JSON file
5. Rename and move the downloaded file:
   ```bash
   mv ~/Downloads/client_secret_*.json ~/.config/jean-claude/client_secret.json
   ```
6. Run the auth command—it will automatically use your credentials

### iMessage

- **Sending messages**: Works via AppleScript. On first use, macOS will prompt
  to allow your terminal to control Messages.app (Automation permission).
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

### CLI Commands

The plugin provides a unified CLI with subcommands:

```bash
jean-claude --help
jean-claude gmail --help
jean-claude gcal --help
jean-claude gdrive --help
jean-claude imessage --help
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
- Default OAuth client credentials are embedded for convenience (standard
  practice for desktop/CLI apps per Google's guidelines)
- Users can provide their own Google Cloud credentials if preferred
- All email/message sends require explicit user approval

## License

MIT
