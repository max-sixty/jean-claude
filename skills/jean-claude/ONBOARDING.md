# First-Run Onboarding

Instructions for guiding a new user through jean-claude setup.

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
- WhatsApp (requires phone pairing)
- Signal (requires Signal Desktop)

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
jean-claude auth           # Full access
jean-claude auth --readonly  # Read-only
```

This opens their browser. Tell the user:

> A browser window should open asking you to sign in with Google. After you
> grant permission, you'll see a success message. Let me know when that's done.

### Step 3: Verify authentication

After user confirms, run:

```bash
jean-claude status --json
```

Check `services.google.authenticated`. If true, confirm success and mention
which Google account is connected.

**If auth failed**, troubleshoot:

- Browser didn't open? Ask user to check for a URL in terminal output to open
  manually
- Wrong account? Run `jean-claude auth --logout` and retry
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
jean-claude imessage chats -n 1
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

After user confirms, re-run `jean-claude imessage chats -n 5` to verify.

### Step 4: Confirm success

When chats command succeeds, summarize: "iMessage is set up. I can see X recent
conversations."

## Apple Reminders Setup (macOS Only)

Reminders only needs Automation permission, granted automatically on first use.

Run:

```bash
jean-claude reminders list
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
jean-claude config set enable_whatsapp true
```

### Step 3: Authenticate

Run:

```bash
jean-claude whatsapp auth
```

This displays a QR code in the terminal. Tell the user:

> A QR code should appear in the terminal. On your phone, open WhatsApp →
> Settings → Linked Devices → Link a Device, and scan the code. Let me know
> when it's linked.

### Step 4: Verify

After user confirms, run:

```bash
jean-claude whatsapp chats -n 5
```

If successful, summarize: "WhatsApp is connected. I can see your recent chats."

## Signal Setup (Optional)

Signal links as a secondary device to Signal Desktop.

### Step 1: Check prerequisites

Ask: "Is Signal Desktop installed and signed in on this computer?"

If not, this won't work. Signal CLI links to Signal Desktop, not directly to
phone.

### Step 2: Enable in config

```bash
jean-claude config set enable_signal true
```

### Step 3: Link device

Run:

```bash
jean-claude signal link --device-name "jean-claude"
```

Tell the user:

> A QR code should appear. On your phone, open Signal → Settings → Linked
> Devices → Link New Device, and scan the code.

### Step 4: Verify

After user confirms:

```bash
jean-claude signal chats -n 5
```

## Setup Completion

After all desired services are configured, mark setup complete:

```bash
jean-claude config set setup_completed true
```

Run final status check:

```bash
jean-claude status
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
- "Switch to read-only" → `jean-claude auth --logout` then `jean-claude auth --readonly`
- "What's configured?" → `jean-claude status`

## Troubleshooting Reference

### Google auth issues

- Browser didn't open: Look for URL in terminal output
- Wrong account: `jean-claude auth --logout` and retry
- Scope issues: Re-run auth, pay attention to checkboxes in Google consent

### iMessage permission issues

- Automation denied: System Preferences → Automation → enable terminal
- Full Disk Access: System Preferences → Full Disk Access → add terminal app
- Still failing: Restart terminal completely after granting permissions

### WhatsApp issues

- QR code not appearing: Check `enable_whatsapp` is true in config
- Won't connect: Phone must be online; try `jean-claude whatsapp auth --logout`
  and re-pair
- Connection drops: WhatsApp has a limit on linked devices

### Signal issues

- QR code not working: Signal Desktop must be running and signed in
- Won't link: Try `jean-claude signal unlink` and re-link
