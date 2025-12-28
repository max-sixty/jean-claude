# First-Run Onboarding

Instructions for guiding a new user through jean-claude setup.

**Command prefix:** All commands in this guide use the full prefix:
```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude ...
```

## When to Load This Guide

Load this guide when:

- `jean-claude status --json` shows `setup_completed: false`
- Google services not authenticated AND user asks about email/calendar/drive
- User explicitly requests setup ("set up jean-claude", "configure email", etc.)

## Orientation

Introduce jean-claude briefly. Keep it conversational:

> I can connect to your Gmail, Calendar, Drive, iMessage, and other services to
> help manage email, schedule meetings, find files, and send messages. Let's set
> up the services you want to use—this takes 2-5 minutes.

## Service Selection

Ask which services they want. Present options conversationally, not as a menu:

**Most users want:** Gmail + Calendar (one OAuth flow covers both)

**Power users might also want:**

- Google Drive, Docs, Sheets (same OAuth, just more scopes)
- iMessage (macOS only, requires granting permissions)
- Apple Reminders (macOS only)
- WhatsApp (requires phone pairing via QR code)
- Signal (requires phone pairing via QR code)

Ask: "What would you like to set up? Most people start with email and calendar."

Based on response, proceed to relevant setup sections.

## Google Services Setup

All Google services (Gmail, Calendar, Drive, Docs, Sheets) share one OAuth flow.

### Step 1: Explain access levels

Two options to present to the user:

**Read-only access** - Can read email/calendar/files but not send or modify.
Good for trying it out. Can upgrade later.

**Full access** - Can send email, create events, upload files. Required for
actually getting things done.

Ask which they prefer. Most users who know what they want should choose full
access.

### Step 2: Initiate OAuth

Run the appropriate command:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth           # Full access
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude auth --readonly  # Read-only
```

This opens their browser. Tell the user:

> A browser window should open asking you to sign in with Google. After you
> grant permission, you'll see a success message. Let me know when that's done.

**If the user sees "This app isn't verified":** Explain that this warning
appears because jean-claude uses a personal OAuth app that hasn't gone through
Google's verification process (which costs money and requires a company). It's
safe to proceed—they should click "Advanced" → "Go to jean-claude (unsafe)" →
then grant the permissions. The "unsafe" label just means unverified.

### Step 3: Verify authentication

After user confirms, run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude status --json
```

Check `services.google.authenticated`. If true, confirm success and mention
which Google account is connected.

**If auth failed**, troubleshoot:

- Browser didn't open? Ask user to check for a URL in terminal output to open
  manually
- Wrong account? Run `jean-claude auth --logout` (with prefix) and retry
- Permission denied? Work/school accounts may have admin restrictions

### Step 4: Confirm scope

Check `services.google.scopes` matches what they chose. If they picked full but
got readonly, they may have clicked wrong options in Google's consent screen—
re-run auth.

## iMessage Setup (macOS Only)

Skip this section if not on macOS (`platform` in status output is not `darwin`).

iMessage requires two macOS permissions that the user must grant manually.

### Step 1: Explain what's needed

Tell the user:

> iMessage needs two permissions: one for sending messages, one for reading your
> message history. macOS will prompt for the first one automatically. The second
> requires a trip to System Preferences.

### Step 2: Test send permission

Run a read-only command to trigger the Automation permission prompt:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage chats -n 1
```

If this returns a permission error, guide the user:

> macOS should have shown a permission prompt. If you denied it or it didn't
> appear, go to **System Preferences → Security & Privacy → Privacy →
> Automation** and enable the checkbox for your terminal app under "System
> Events".

### Step 3: Test read permission

The `chats` command also requires Full Disk Access. If it failed with a database
error, guide the user:

> To read your message history, you need to grant Full Disk Access:
>
> 1. Open **System Preferences → Security & Privacy → Privacy → Full Disk Access**
> 2. Click the lock icon to make changes
> 3. Click the + button and add your terminal app (Terminal, iTerm2, VS Code, Cursor, etc.)
> 4. Restart your terminal completely (quit and reopen)
>
> Let me know when you've done this and restarted your terminal.

After user confirms, re-run `jean-claude imessage chats -n 5` (with prefix) to verify.

### Step 4: Confirm success

When chats command succeeds, summarize: "iMessage is set up. I can see X recent
conversations."

## Apple Reminders Setup (macOS Only)

Reminders only needs Automation permission, granted automatically on first use.

Run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list
```

If permission prompt appears, tell user to allow it. If denied, same Automation
fix as iMessage.

## WhatsApp Setup (Optional)

WhatsApp is disabled by default because it requires phone pairing and maintains
a persistent connection.

### Step 1: Confirm user wants this

Only proceed if user explicitly wants WhatsApp. It's not part of default setup.

### Step 2: Enable in config

Run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude config set enable_whatsapp true
```

### Step 3: Authenticate

Run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp auth
```

This displays a QR code in the terminal. Tell the user:

> A QR code should appear in the terminal. On your phone, open WhatsApp →
> Settings → Linked Devices → Link a Device, and scan the code. Let me know
> when it's linked.

### Step 4: Verify

After user confirms, run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp chats -n 5
```

If successful, summarize: "WhatsApp is connected. I can see your recent chats."

## Signal Setup (Optional)

Signal links jean-claude as a secondary device to the user's Signal account.
The user scans a QR code with their phone to authorize the connection.

**Prerequisite:** Confirm the user has Signal installed on their phone—they'll
need it to scan the QR code.

### Step 1: Enable in config

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude config set enable_signal true
```

### Step 2: Link device

Run:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal link --device-name "jean-claude"
```

Tell the user:

> A QR code should appear in the terminal. On your phone, open Signal →
> Settings → Linked Devices → Link New Device, and scan the code.

### Step 3: Verify

After user confirms:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal chats -n 5
```

## Setup Completion

After all desired services are configured, mark setup complete:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude config set setup_completed true
```

Run final status check:

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude status
```

Summarize what's configured in natural language:

> "Setup complete! Here's what I can access:
>
> - Gmail and Calendar (full access) - connected as user@gmail.com
> - iMessage - can send and read messages
> - Reminders - working
>
> WhatsApp and Signal are available to add later if you want."

## Post-Setup: First Actions

Once setup is complete, offer to help with something based on what's enabled.
Don't list commands—ask what they'd like to do:

- "Want me to check your inbox for anything urgent?"
- "Should I look at your calendar for today?"
- "Any messages you'd like me to help with?"

The agent runs the appropriate commands and presents results conversationally.

## Re-Running Setup

If user wants to add services later or change access level:

- "Add WhatsApp" → WhatsApp Setup section
- "Switch to read-only" → Run `auth --logout` then `auth --readonly` (with prefix)
- "What's configured?" → Run `status` (with prefix)

## Troubleshooting Reference

### Google auth issues

- Browser didn't open: Look for URL in terminal output
- Wrong account: Run `auth --logout` (with prefix) and retry
- Scope issues: Re-run auth, pay attention to checkboxes in Google consent

### iMessage permission issues

- Automation denied: System Preferences → Automation → enable terminal app
- Full Disk Access: System Preferences → Full Disk Access → add terminal app
- Still failing: Quit and reopen the terminal app after granting permissions
- **Which terminal app?** Use the app where Claude Code is running (Terminal,
  iTerm2, VS Code, Cursor, etc.)

### WhatsApp issues

- QR code not appearing: Check `enable_whatsapp` is true in config
- Won't connect: Phone must be online; run `whatsapp logout` and re-pair
- Connection drops: WhatsApp limits linked devices to 4

### Signal issues

- QR code not working: Ensure phone has Signal installed and is online
- Won't link: Run `signal unlink` and re-link
