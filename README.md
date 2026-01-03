# jean-claude

A Claude Code plugin for managing Gmail, Google Calendar, Google Drive, and
iMessage (macOS only).

<img width="1536" height="1024" alt="image" src="https://github.com/user-attachments/assets/6481069a-a730-434a-b3f1-6fd88a78da60" />

## Prerequisites

### Claude Code

This is a plugin for [Claude Code](https://code.claude.com/docs/en/setup),
Anthropic's agentic coding tool. Install it first:

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

### uv (Python package manager)

This plugin requires [uv](https://docs.astral.sh/uv/) to manage Python
dependencies (requires Python 3.11+, which uv will install automatically if
needed):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Installation

From your terminal, run:

```bash
claude plugin marketplace add max-sixty/jean-claude
claude plugin install jean-claude@jean-claude
```

## Setup

### Google Workspace (Gmail, Calendar, Drive)

After installing the plugin, ask Claude to authenticate:

```
"Set up Google authentication for jean-claude"
```

Claude will run the auth command for you, which opens a browser for OAuth
consent.

<details>
<summary><strong>Manual setup</strong> (if you prefer running commands yourself)</summary>

```bash
# The plugin is installed at:
cd ~/.claude/plugins/marketplaces/jean-claude

# Full access (read, send, modify)
uv run jean-claude auth

# Or read-only access (no send/modify capabilities)
uv run jean-claude auth --readonly

# Verify it worked
uv run jean-claude status
```

Credentials are saved to `~/.config/jean-claude/token.json` and persist until
revoked.

</details>

> [!NOTE]
> You may see an "unverified app" warning during OAuth—this is normal for apps
> pending Google verification. Click "Advanced" → "Go to jean-claude (unsafe)"
> to proceed. The app only requests the permissions you approve and stores
> credentials locally on your machine.

<details>
<summary><strong>Using your own Google Cloud credentials</strong> (optional)</summary>

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
5. Move the downloaded file:
   ```bash
   mkdir -p ~/.config/jean-claude
   mv ~/Downloads/client_secret_*.json ~/.config/jean-claude/client_secret.json
   ```
6. Run the auth command—it will automatically use your credentials

</details>

### iMessage (macOS only)

iMessage integration requires macOS—it uses AppleScript and the local Messages
database.

- **Sending messages**: Works via AppleScript. On first use, macOS prompts to
  allow your terminal to control Messages.app (Automation permission). Grant
  this to whichever app runs Claude Code (Terminal, iTerm2, VS Code, etc.).

- **Reading messages**: Requires Full Disk Access to read the Messages database.
  1. Open **System Settings** → **Privacy & Security** → **Full Disk Access**
  2. Click **+** and add the app that runs Claude Code:
     - If using Terminal: add `/Applications/Utilities/Terminal.app`
     - If using iTerm2: add `/Applications/iTerm.app`
     - If using VS Code: add `/Applications/Visual Studio Code.app`
     - If using another terminal: find it in `/Applications` or via `which`
  3. Toggle the app **on** and restart it

## Usage

Once installed, Claude automatically uses this plugin when you ask it to:

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

<details>
<summary><strong>CLI Commands</strong></summary>

The plugin provides a unified CLI with subcommands. Claude invokes these
automatically, but you can also run them manually:

```bash
cd ~/.claude/plugins/marketplaces/jean-claude
uv run jean-claude --help
uv run jean-claude gmail --help
uv run jean-claude gcal --help
uv run jean-claude gdrive --help
uv run jean-claude imessage --help  # macOS only
```

</details>

### Shell Completions

Enable tab completion for the CLI:

```bash
cd ~/.claude/plugins/marketplaces/jean-claude

# Bash (~/.bashrc)
eval "$(uv run jean-claude completions bash)"

# Zsh (~/.zshrc)
eval "$(uv run jean-claude completions zsh)"

# Fish (~/.config/fish/config.fish)
uv run jean-claude completions fish | source
```

## Features

### Gmail

- Search and list emails
- Create, send, and manage drafts
- Reply and forward with threading preserved
- Star, archive/unarchive, mark read/unread, trash messages

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

### iMessage (macOS only)

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

<details>
<summary><strong>Development</strong></summary>

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Run lints
pre-commit run --all-files
```

</details>

## License

MIT
