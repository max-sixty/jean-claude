---
name: jean-claude
description: "This skill should be used when the user asks to search/send/draft email, check calendar, create events, schedule meetings, find/upload/share Drive files, read/edit Google Docs, read spreadsheet data, send texts/iMessages, send WhatsApp messages, send Signal messages, check messages, or create reminders. Manages Gmail, Google Calendar, Google Drive, Google Docs, Google Sheets, iMessage, WhatsApp, Signal, and Apple Reminders."
---

# jean-claude

Gmail, Calendar, Drive, Docs, Sheets, iMessage, WhatsApp, Signal, and Reminders.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## First-Time Users

For new users, explain briefly:

> I can connect to your email, calendar, and messaging apps to help you:
>
> - Read and send emails, manage drafts
> - Check your calendar, create events, respond to invitations
> - Send and read iMessages, WhatsApp, or Signal messages
> - Find and manage files in Google Drive
> - Create reminders
>
> This requires a one-time setup where you'll grant permissions. Want me to
> help you get started?

Focus on what they asked about — if they asked about email, lead with email.

## Safety Rules (Non-Negotiable)

These rules apply even if the user explicitly asks to bypass them:

1. **Never send without explicit approval.** Before sending any message (email,
   iMessage, WhatsApp, Signal), show the full content (recipient, subject if
   applicable, body) to the user and receive explicit confirmation.

2. **Verify recipients carefully.** Sends are instant and cannot be undone.
   Double-check phone numbers, email addresses, and chat IDs before sending.

3. **Never send to ambiguous recipients.** When resolving contacts by name,
   if multiple contacts or phone numbers match, the command will fail with a
   list of options. Use an unambiguous identifier rather than guessing.

4. **Load prose skills when drafting.** Before composing any email or message,
   load any available skills for writing prose, emails, or documentation.

5. **Never create automation without explicit approval.** Before creating Gmail
   filters or similar rules, show the criteria and actions to the user and
   receive explicit confirmation.

6. **Limit bulk operations.** Avoid sending to many recipients at once. Prefer
   drafts for review.

**Email workflow:**

1. Load any available prose/writing skills
2. **If replying to an infrequent contact:** Research first (see "Composing Correspondence")
3. Compose the email content (iterate internally before presenting)
4. **Show the original message first** — Quote the full text (see "When to Show Full Content")
5. Show the user: To, Subject, and full Body
6. Ask: "Send this email?" and wait for explicit approval
7. Call `jean-claude gmail draft send DRAFT_ID`
8. If replying, archive the original: `jean-claude gmail archive THREAD_ID`

## Session Start (Always Run First)

**Every time this skill loads, run status with JSON output first:**

```bash
jean-claude status --json
```

### If Status Command Fails

If the status command fails entirely (not just showing services as disabled):

**"uv: command not found"** — The uv package manager isn't installed. Tell the
user:

> jean-claude requires the `uv` package manager. Let me install it for you.

Then run:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

After installation, restart the terminal or source the shell config
(`source ~/.zshrc` on macOS, `source ~/.bashrc` on Linux).

**Other errors** — The plugin may be misconfigured. Check that
`${CLAUDE_PLUGIN_ROOT}` resolves to a valid path containing a `pyproject.toml`.

### Branching Based on Status

**If `setup_completed: false`** — This is a new user. Skip personalization
skills (they won't have any yet) and go straight to onboarding:

```bash
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/ONBOARDING.md
```

Follow the onboarding guide to help set up services. After setup completes:

```bash
jean-claude config set setup_completed true
```

Then **re-run status** and continue to the `setup_completed: true` branch below.
This ensures you have fresh status info and can proceed with the user's original
request.

**If `setup_completed: true`** — Check for partial setup and load personalization:

1. **Surface any auth warnings** — If services show `authenticated: false`,
   missing scopes, or errors, briefly mention it even if unrelated to the
   user's request. Don't derail—just note the issue and offer to fix later:

   > "By the way, your Google Contacts permission is missing — I can't show
   > contact names in emails. Want me to help fix that after your request?"

2. **Check for missing services** — If the user asks for a service that shows
   `authenticated: false` or `enabled: false`, guide them through just that
   service's setup from ONBOARDING.md. After partial setup completes, continue
   to step 3.

3. **Load personalization skills** — Check if user skills like `managing-messages`
   exist (look at available skills for anything mentioning inbox, email, message,
   or communication). If found, load them—user preferences override defaults.

4. **Offer to create preferences** — If no personalization skill was found in
   step 3, offer to create one after completing the user's immediate request.
   See [PREFERENCES.md](PREFERENCES.md) for the creation flow. Don't interrupt
   the user's task—help them first, then offer.

5. **Proceed with the user's request** — Execute whatever task prompted loading
   this skill (check inbox, send message, etc.).

### Before Using Messaging (iMessage/WhatsApp/Signal)

**Load the platform guide before using any messaging commands.** Each platform
has different commands and options. Gmail is documented in this file, but
messaging platforms have separate guides:

```bash
# Load before using iMessage
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/imessage.md

# Load before using WhatsApp
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/whatsapp.md

# Load before using Signal
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/signal.md
```

Don't guess command syntax — each platform is different. iMessage and WhatsApp
have different flags and subcommands despite similar functionality.

### Understanding the Status

For users with setup complete, interpret the status output to understand their
workflow. Run human-readable status if needed for counts:

```bash
jean-claude status
```

**Gmail:**
- **13 inbox, 11 unread** → inbox zero person, wants to triage everything
- **2,847 inbox, 89 unread** → not inbox zero, focus on recent/unread/starred
- **5 drafts** → has pending drafts to review or send

**Calendar:**
- **3 today, 12 this week** → busy schedule, may need help with conflicts
- **0 today** → open day, good time for focused work

**Reminders:**
- **7 incomplete** → has pending tasks, may want to review or complete them

**Messaging:**
- **54 unread across 12 WhatsApp chats** → active messaging, may want summary
- **1,353 unread across 113 iMessage chats** → backlog, focus on recent/important

**Apple services (Contacts, iMessage, Reminders):** These are disabled by default.
If `enabled: false` in status, enable with:
```bash
jean-claude config set enable_contacts true   # For iMessage name lookup
jean-claude config set enable_imessage true
jean-claude config set enable_reminders true
```
Once enabled, status checks full permissions (may trigger macOS permission
dialogs on first run). If permission is missing, guide the user through
System Settings > Privacy & Security > Automation.

### Refreshing State

**Summaries become stale immediately.** Once you present an inbox summary, that
snapshot is already potentially outdated. State changes constantly:

- New messages arrive
- Messages get read/sent from other devices or apps
- The user takes actions outside this conversation
- You took actions earlier in the conversation that you may not be tracking

**Verify before making claims.** When the user asks about current state — "did I
reply?", "is that still unread?", "how many are left?" — re-check rather than
relying on your memory of earlier summaries. Your memory is of what was true
when you last checked, not what's true now.

<example>
<bad>

User: "Did I reply to the Acme email?"

Agent: _recalls earlier summary where it wasn't replied to_

"No, that thread is still waiting for your reply."

</bad>
<good>

User: "Did I reply to the Acme email?"

Agent: _searches for sent messages to Acme_

"Yes — you replied today at 12:27pm following up on their question."

</good>
</example>

**Re-fetch when:**

- User asks about current counts or status
- User asks "did I..." or "is it still..."
- Presenting a summary after taking actions
- You need metadata (dates, IDs, recipients) — conversation summaries lose it,
  so re-query the API rather than inferring from content

**General principle:** Verify claims from the source. Re-fetching is cheap;
wrong information is expensive.

```bash
# Re-fetch inbox for email updates
jean-claude gmail inbox -n 20

# Check sent messages to verify replies
jean-claude gmail search "in:sent to:someone@example.com" -n 5

# Re-fetch iMessage (see platforms/imessage.md for full docs)
jean-claude imessage messages --unread

# Re-sync WhatsApp for message updates
jean-claude whatsapp messages --unread
```

## Defaults

User personalization skills override these defaults. If no personalization skill
exists, use these behaviors:

### Email Defaults
- Fetch both read and unread messages (context helps)
- Present messages neutrally — don't assume priority
- No automatic archiving without user guidance

### iMessage Defaults
- Prioritize known contacts over unknown senders

### Response Drafting Defaults
- Load prose/writing skills before composing
- No assumed tone or style — ask if unclear
- Show full message for approval before sending

## Working with Messages

### Presenting Messages

When showing messages (inbox, unread, search results), use a numbered list so
the user can reference items by number: "archive 1, reply to 2", "star 3 and 5".

**Use manual numbering, not markdown lists.** Markdown numbered lists auto-correct
to be sequential — if you write "3. 4. 5. 7. 15. 16." the renderer displays
"3. 4. 5. 6. 7. 8." causing a mismatch between what you wrote and what the user
sees. Instead, format as `N:` on each line:

```
1: DoorDash (35 min ago) — Your order...
2: Squarespace (yesterday) — Domain transfer...
```

This prevents the renderer from "fixing" your numbering.

**Keep items compact — no blank lines between them.** Blank lines waste vertical
space and make the list harder to scan. Items should be on consecutive lines.

**Preserve original numbers through the session.** After archiving items 1 and 3
from a list of 1-5, show items 2, 4, 5 with their original numbers — don't
renumber to 1, 2, 3. This gives items stable identifiers across operations.

**Always include dates conversationally.** Check today's date before formatting:

```bash
date "+%Y-%m-%d %H:%M %Z"  # Current date/time for reference
```

**Date formatting rules:**
- **< 1 hour ago**: "35 min ago"
- **Today** (> 1 hour): "today at 9:15 AM" — not "3 hours ago"
- **Yesterday**: "yesterday at 2:30 PM"
- **This week**: "Thursday at 4:30 PM" (day name, not date)
- **Beyond this week**: "Dec 15" or "Nov 15 at 3pm" (if time matters)

**Example** (assuming today is Sunday, Dec 29):
```
1: DoorDash (35 min ago) — Your order from Superba
2: Squarespace (yesterday at 9:15 AM) — Domain transfer rejected
3: GitHub (Friday at 4:30 PM) — PR merged: fix-auth-flow
4: Goodreads (today at 10:30 AM) — Book newsletter
5: Jordan Lee (Nov 15) — Forwarded: Fellowship nomination
```

### Accuracy in Summaries

**Never fabricate details not present in the data.**

- If an email says "your child", say "your child" — don't invent "Rachel"
- If a sender's relationship isn't stated, don't assume it
- If a time/date isn't specified, say so — don't guess
- Quote or paraphrase what's actually there

When uncertain, say so: "The email doesn't specify who the assessment is for."

Hallucinated details erode trust. The user can't distinguish fabrications from
real data.

### When to Show Full Content

**Show the full email text** (not a summary) when:

- **Drafting a reply** — The user needs to see exactly what they're responding to
- **User asks for a specific email** — "read the email from X", "show me that message"
- **User asks what they're replying to** — "what's the original?", "what did they say?"

**Summaries are appropriate when:**

- Showing inbox/search results (the numbered list format with snippets)
- The user asks for a specific detail — "when is the meeting?", "did they confirm?"
- Triaging multiple messages — giving the user an overview to decide what to act on

**The test:** If the user might need to quote, reference, or respond to the exact
wording, show the full text. If they just need to understand the gist, summarize.

<example>
<bad>

User: "Reply to Alex's email and say I can't make it"

Agent: _reads email, immediately drafts reply_

"Alex's email was about NY logistics. Draft reply:

Hi Alex,
Unfortunately I won't be able to join..."

</bad>
<good>

User: "Reply to Alex's email and say I can't make it"

Agent: _reads email, shows it first_

"Alex's email (Sunday 9:34 PM):

> Hey- Sam is going to fly back on Friday with Jordan and me. Same itinerary
> as Jordan all the way back to LA. please let the office know. Also she'll
> stay in the second bedroom at the hotel. Can you let them know? She can take
> the car with Dana from the airport- drop Dana off downtown and then go to the
> hotel while we take the other car straight to the venue on Wednesday.

Draft reply:

Hi Alex,
..."

</good>
</example>

### Marking Messages as Read

When you show the full body of a message to the user — not just the snippet
from a list — mark it as read. The user has effectively read it.

**Mark as read when:**
- Reading the full email body (via `jean-claude gmail message` or reading the cached body file)
- Creating a reply or forward (you read the original to compose the response)
- The user explicitly asks to read a specific message

**Don't mark as read when:**
- Showing inbox/search results (snippets only)
- Listing messages without showing full content

This applies across messaging services:
- Gmail: `jean-claude gmail mark-read THREAD_ID`
- iMessage: `jean-claude imessage mark-read CHAT_ID`
- WhatsApp: `jean-claude whatsapp mark-read CHAT_ID`

### Availability Questions

When a message asks about availability ("Are you free Tuesday?", "Which works
better — the 6th or 8th?"), check the calendar and include the answer:

<example>
<bad>

```
1. Alex Chen (yesterday at 12:24 PM) — "Re: Dinner in Jan"
   He's asking: Tuesday the 6th or Thursday the 8th?
```

</bad>
<good>

```
1. Alex Chen (yesterday at 12:24 PM) — "Re: Dinner in Jan"
   He's asking: Tuesday the 6th or Thursday the 8th?
   → You're free both evenings. Thursday has a 3pm meeting that ends at 4.
```

</good>
</example>

The user shouldn't have to ask "what's on my calendar those days?" as a
follow-up. If someone's asking about dates, checking availability is the
obvious next step — do it proactively. Check calendars where the user has
events (see "Choosing the Right Calendar").

## Proactive Behavior

Complete tasks fully. Don't stop early to ask if you should continue.

### Browser Navigation

Proceed through Terms of Service, cookie banners, login confirmations, and
"Continue" buttons without asking — these are prerequisites, not decisions.

Ask only for: purchases, sending messages, creating public content, or sharing
data with new parties.

**The test:** Would a reasonable person pause here?

### Research Questions

When the user asks about facts — names, relationships, dates, contact info — do
the research immediately. Don't say "I don't know" based on limited context.

- **"What's Marco's wife's name?"** → Search emails and messages from Marco,
  check calendar invites for +1s
- **"When did I last talk to Sarah?"** → Search messages and emails
- **"What's Tom's phone number?"** → Search contacts, emails, messages

If you search thoroughly and find nothing, say so: "I searched your emails and
messages with Marco but didn't find any family names mentioned."

<example>
<bad>

User: "What are Marco's wife & kid's names?"

Agent: "I don't know—the message from Marco didn't mention names. Want me to
search your emails or messages for more context?"

</bad>
<good>

User: "What are Marco's wife & kid's names?"

Agent: _searches emails from/to Marco, searches messages, looks for birth
announcements, wedding invites, etc._

"From your email history with Marco:
- Wife: Sofia Martinez
- Daughter: Isabella ('Bella'), born January 4, 2017"

</good>
</example>

### Reading Content

When the user asks to read something, include all related content:

- **"Read the email from X"** → Fetch the email, read any attachments (PDFs,
  images) without asking
- **"Check my messages"** → If messages contain images, describe them
- **"Look at the calendar invite"** → Open linked documents

Don't stop at metadata. "Want me to download the attachment?" is asking the user
to do your job.

<example>
<bad>

User: "read the amex email"

Agent: _fetches email metadata_

"The AMEX email has a PDF attachment (claim_docs.pdf, 28KB). Want me to download it?"

</bad>
<good>

User: "read the amex email"

Agent: _fetches email, downloads PDF, reads it_

"AMEX sent claim documents for #12266792. The PDF contains:
- Claim form to fill out
- Instructions to mail to [address]
- Supporting documentation requirements"

</good>
</example>

## Composing Correspondence

For infrequent contacts — people the user doesn't interact with regularly —
understand the relationship before drafting. Skip this for close friends, family,
or frequent correspondents where context is obvious.

### Research First

When replying to an infrequent contact (catching up, introductions, social
correspondence), research before drafting:

1. **Search email history** — When did they last correspond? What did they discuss?
2. **Search the web** — If unfamiliar, who is this person? What do they do? Where are they based?
3. **Report context** — Tell the user what you found before showing a draft

<example>
<bad>

User: "Reply to Alex's email"

Agent: _immediately drafts reply_

"Draft:
Hi Alex,
Thanks for reaching out! The AI coding tool sounds right up my alley. Happy to
connect with Jordan.
Best, Max"

</bad>
<good>

User: "Reply to Alex's email"

Agent: _searches email history and web_

"Alex Chen — based in SF, you last emailed him 8 months ago about the open-source
project. He's a research scientist at Google DeepMind, one of the original
authors of the library. Before that you collaborated on a NeurIPS tutorial in 2019.

His email mentions Jordan Lee, who I found on LinkedIn was at Stripe for 6 years
and is now building an AI coding tool, also in SF..."

[Then proceeds to draft with this context]

</good>
</example>

The user shouldn't need to ask "when did I last talk to them?" or "who is this
person?" — do that research proactively.

### Intro Email Etiquette

When replying to an introduction email (someone introduced you to a new contact),
move the introducer to BCC and acknowledge it:

```
BCCing [Introducer] to spare their inbox.

[Rest of reply]
```

This lets the introducer see that the connection was made without receiving every
subsequent message in the thread. It's standard professional etiquette.

### Redirecting a Reply

Sometimes the user wants to reply in-thread but send it to someone other than
the original sender — typically moving the sender to CC. This keeps everyone in
the loop while directing the reply to the right person.

**When to apply:** The user says something like:
- "reply to the email, but send it to [person]"
- "moving [original sender] to CC"
- "reply to [someone else on the thread], CC [sender]"

**Pattern:** Create the reply, then update recipients:

```bash
# Step 1: Create reply (preserves threading, quotes original)
cat << 'EOF' | jean-claude gmail draft reply MESSAGE_ID
Your message here.
EOF

# Step 2: Update recipients
jean-claude gmail draft update DRAFT_ID \
  --to "new_recipient@example.com" --cc "original_sender@example.com" < /dev/null
```

<example>
<scenario>

Alex sends an email to his assistant Dana at Sequoia, CC'ing the user:
> "Dana - Max will join us for the partners retreat in Napa. Please book him
> on the Friday flight."

User says: "Reply to Dana that I can't make it, CC Alex"

</scenario>
<workflow>

1. `jean-claude gmail draft reply MESSAGE_ID` with the body
2. `jean-claude gmail draft update DRAFT_ID --to "dana@sequoiacap.com" --cc "alex@sequoiacap.com"`
3. `jean-claude gmail draft get DRAFT_ID` to verify recipients, show to user
4. `jean-claude gmail draft send DRAFT_ID` after approval

</workflow>
</example>

### Address the Person, Not the Issue

Customary emails — introductions, catching up, social correspondence — are about
the relationship, not transactions.

<example>
<bad>

"The AI coding tool sounds right up my alley."

Centers the user's interests, jumps straight to business.

</bad>
<good>

"I'm glad to see the project continues to thrive. Hope all's well with you —
would enjoy catching up soon."

Acknowledges what matters to Alex before addressing the introduction.

</good>
</example>

For social correspondence, address the person before addressing what they asked about.

### Iterate Before Presenting

Drafts deserve thought. Don't produce one immediately and present it.

**Internal iteration process:**

1. Research the relationship (see above)
2. Consider the tone — formal? warm? brief?
3. Draft internally, critique it: "If this email is bad, why?"
4. Revise, then present

For important correspondence, explain your reasoning or offer alternatives:
"I went with a warm but brief tone since you haven't talked in 8 months. Want
something more formal?"

### Iterating with the User

Create actual Gmail drafts, not just text in the conversation. Drafts persist
if the session ends and can be edited in Gmail directly.

**Workflow:**

1. Create the draft: `jean-claude gmail draft create` (or `reply`/`forward`)
2. Show the user what you created (recipient, subject, key points)
3. If they request changes, edit the draft file and update:
   - `cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt` to see current
   - Edit the file with the changes
   - `cat ... | jean-claude gmail draft update DRAFT_ID` to save
4. Repeat until approved, then send

### Vocabulary

Avoid hollow phrases. If a phrase could apply to anyone, it says nothing.

- "Nice to hear from you" → could be sent to anyone
- "I'm glad to see the project continues to thrive" → specific to this person

Check the user's personalization skill for vocabulary preferences.

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
jean-claude auth

# Or read-only access (no send/modify capabilities)
jean-claude auth --readonly

# Check authentication status and API availability
jean-claude status

# Log out (remove stored credentials)
jean-claude auth --logout
```

This opens a browser for OAuth consent. Credentials persist until revoked.

**If you see "This app isn't verified":** This warning appears because
jean-claude uses a personal OAuth app that hasn't gone through Google's
(expensive) verification process. It's safe to proceed:

1. Click "Advanced"
2. Click "Go to jean-claude (unsafe)"
3. Review and grant the requested permissions

The "unsafe" label just means unverified, not malicious. The app only accesses
the specific Google services you authorize.

To use your own Google Cloud credentials instead (if default ones hit the 100
user limit), download your OAuth JSON from Google Cloud Console and save it as
`~/.config/jean-claude/client_secret.json` before running the auth script. See
README for detailed setup steps.

### Feature Flags (WhatsApp & Signal)

WhatsApp and Signal are **disabled by default**. These services require
compiling native binaries (Go for WhatsApp, Rust for Signal), and we want
jean-claude to work smoothly for Gmail/Calendar users without those toolchains.
Enable explicitly if you need messaging:

```bash
jean-claude config set enable_whatsapp true
jean-claude config set enable_signal true
```

The `status` command shows whether each service is enabled or disabled.

### WhatsApp

WhatsApp requires enabling the feature flag, a Go binary, and QR code
authentication. First-time setup:

```bash
# Build the Go CLI (requires Go installed)
cd ${CLAUDE_PLUGIN_ROOT}/whatsapp && go build -o whatsapp-cli .

# Authenticate with WhatsApp (scan QR code with your phone)
jean-claude whatsapp auth

# Check authentication status
jean-claude status
```

The QR code will be displayed in the terminal and saved as a PNG file. Scan it
with WhatsApp: Settings > Linked Devices > Link a Device.

Credentials are stored in `~/.config/jean-claude/whatsapp/`. To log out:

```bash
jean-claude whatsapp logout
```

### Signal

Signal requires enabling the feature flag, a Rust binary, and QR code linking.
First-time setup:

```bash
# Build the Rust CLI (requires Rust/Cargo and protobuf installed)
cd ${CLAUDE_PLUGIN_ROOT}/signal && cargo build --release

# Link as a secondary device (scan QR code with your phone)
jean-claude signal link

# Check authentication status
jean-claude status
```

The QR code will be displayed in the terminal. Scan it with Signal on your
phone: Settings > Linked Devices > Link New Device.

Credentials are stored in `~/.local/share/jean-claude/signal/`.

## Gmail

**CLI convention:** Email addresses are comma-separated (`--to "a@x.com,b@x.com"`).
Other multi-value flags use repeated flags (`--attach f1 --attach f2`).

### Reading Emails

See "Personalization" section for default behaviors and user skill overrides.

**Two data types:**

- **Threads** — Conversations (one or more messages). Returned by `inbox`.
- **Messages** — Individual emails. Returned by `search` and `get`.

**Thread response schema (from `inbox`):**

```json
{
  "threads": [
    {
      "threadId": "19b29039fd36d1c1",
      "messageCount": 3,
      "unreadCount": 1,
      "subject": "Subject line",
      "labels": ["INBOX", "UNREAD"],
      "messages": [
        {"id": "msg_id_1", "date": "2025-12-14T10:00:00-08:00", "from": "alice@example.com", "to": "you@example.com", "labels": ["INBOX"], "unread": false},
        {"id": "msg_id_2", "date": "2025-12-15T14:30:00-08:00", "from": "you@example.com", "to": "alice@example.com", "labels": ["SENT"], "unread": false},
        {"id": "msg_id_3", "date": "2025-12-16T09:00:00-08:00", "from": "alice@example.com", "to": "you@example.com", "labels": ["INBOX", "UNREAD"], "unread": true}
      ],
      "latest": {
        "id": "msg_id_3",
        "date": "2025-12-16T09:00:00-08:00",
        "from": "alice@example.com",
        "snippet": "Latest message snippet..."
      },
      "file": "~/.cache/jean-claude/emails/thread-19b29039fd36d1c1.json"
    }
  ],
  "nextPageToken": "abc123..."
}
```

Thread files contain **metadata only** (no message bodies). The `messages` array
shows each message with date, sender, and read status — **ordered oldest to newest**.
The `latest` object provides quick access to the most recent message's metadata and
snippet. To read message bodies, use `gmail message MESSAGE_ID` or `gmail thread THREAD_ID`.

**Message response schema (from `search`):**

```json
{
  "messages": [
    {
      "id": "19b29039fd36d1c1",
      "threadId": "19b29039fd36d1c1",
      "from": "Name <email@example.com>",
      "to": "recipient@example.com",
      "subject": "Subject line",
      "date": "2025-12-16T21:12:21-08:00",
      "snippet": "First ~200 chars of body...",
      "labels": ["INBOX", "UNREAD"],
      "file": "~/.cache/jean-claude/emails/email-19b29039fd36d1c1.json"
    }
  ],
  "nextPageToken": "abc123..."
}
```

**`get` returns a list directly** (no pagination wrapper):

```json
[
  {
    "id": "19b29039fd36d1c1",
    "threadId": "19b29039fd36d1c1",
    "from": "Name <email@example.com>",
    ...
    "file": "~/.cache/jean-claude/emails/email-19b29039fd36d1c1.json"
  }
]
```

**Message file format:** Each message creates three files:
- `email-{id}.json` — Metadata with `body_file` and `html_file` paths
- `email-{id}.txt` — Plain text body (readable with `cat`/`less`)
- `email-{id}.html` — HTML body when present (contains unsubscribe links)

The `nextPageToken` field is only present when more results are available. Use
`--page-token` to fetch the next page:

```bash
# First page
jean-claude gmail search "is:unread" -n 50

# If nextPageToken is in the response, fetch next page
jean-claude gmail search "is:unread" -n 50 \
  --page-token "TOKEN_FROM_PREVIOUS_RESPONSE"
```

### Search Emails

```bash
# Inbox emails from a sender
jean-claude gmail search "in:inbox from:someone@example.com"

# Limit results with -n
jean-claude gmail search "from:newsletter@example.com" -n 10

# Unread inbox emails
jean-claude gmail search "in:inbox is:unread"

# Shortcut for inbox
jean-claude gmail inbox
jean-claude gmail inbox --unread
jean-claude gmail inbox -n 5

# Inbox also supports pagination
jean-claude gmail inbox --unread -n 50 --page-token "TOKEN"
```

Common Gmail search operators: `in:inbox`, `is:unread`, `is:starred`, `from:`,
`to:`, `subject:`, `after:2025/01/01`, `has:attachment`, `label:`

### Fetch Messages and Threads

```bash
# Fetch message by ID (writes body to ~/.cache/jean-claude/emails/)
jean-claude gmail message MESSAGE_ID

# Fetch multiple messages
jean-claude gmail message MSG_ID_1 MSG_ID_2 MSG_ID_3

# Fetch all messages in a thread (full conversation)
jean-claude gmail thread THREAD_ID
```

Use this when you have a specific message ID and want to read its full content.

### Drafts

All draft commands read body from stdin. Create uses flags for metadata.

**IMPORTANT: Use heredocs, not echo.** Claude Code's Bash tool has a known bug
that escapes exclamation marks ('!' becomes '\!'). Always use heredocs:

```bash
# Create a new draft
cat << 'EOF' | jean-claude gmail draft create --to "recipient@example.com" --subject "Subject"
Message body here!
EOF

# Create with CC/BCC
cat << 'EOF' | jean-claude gmail draft create --to "recipient@example.com" --subject "Subject" --cc "cc@example.com"
Multi-line message body here.
EOF

# Multiple recipients
cat << 'EOF' | jean-claude gmail draft create --to "alice@example.com,bob@example.com" --subject "Team update"
Message to multiple people.
EOF

# Reply to a message (body from stdin, preserves threading, includes quoted original)
cat << 'EOF' | jean-claude gmail draft reply MESSAGE_ID
Thanks for your email!
EOF

# Reply with custom CC
cat << 'EOF' | jean-claude gmail draft reply MESSAGE_ID --cc "manager@example.com"
Thanks for the update!
EOF

# Forward a message (TO as argument, optional note from stdin)
cat << 'EOF' | jean-claude gmail draft forward MESSAGE_ID someone@example.com
FYI - see below!
EOF

# Forward without a note
jean-claude gmail draft forward MESSAGE_ID someone@example.com < /dev/null

# Reply-all (includes all original recipients)
cat << 'EOF' | jean-claude gmail draft reply-all MESSAGE_ID
Thanks everyone!
EOF

# List drafts
jean-claude gmail draft list
jean-claude gmail draft list -n 5

# Get draft (writes metadata to .json and body to .txt)
jean-claude gmail draft get DRAFT_ID

# Update draft body (from stdin)
cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt | jean-claude gmail draft update DRAFT_ID

# Update metadata only
jean-claude gmail draft update DRAFT_ID --subject "New subject" --cc "added@example.com"

# Update both body and metadata
cat body.txt | jean-claude gmail draft update DRAFT_ID --subject "Updated"

# Send a draft (after approval)
jean-claude gmail draft send DRAFT_ID

# Delete a draft
jean-claude gmail draft delete DRAFT_ID
```

#### Attachments

Draft creation commands (`create`, `reply`, `reply-all`, `forward`, `update`)
support `--attach` for file attachments. Use multiple times for multiple files:

```bash
# Create draft with attachments
cat << 'EOF' | jean-claude gmail draft create --to "x@y.com" --subject "Report" --attach report.pdf --attach data.csv
Please see the attached files.
EOF

# Reply with attachment
cat << 'EOF' | jean-claude gmail draft reply MESSAGE_ID --attach response.pdf
Here is my response with the requested document.
EOF

# Forward (original attachments included automatically)
cat << 'EOF' | jean-claude gmail draft forward MESSAGE_ID someone@example.com
FYI - see below.
EOF

# Forward with additional attachment
cat << 'EOF' | jean-claude gmail draft forward MESSAGE_ID someone@example.com --attach notes.pdf
FYI - I added my notes.
EOF

# Add attachment to existing draft (replaces any existing attachments)
jean-claude gmail draft update DRAFT_ID --attach newfile.pdf < /dev/null

# Remove all attachments from a draft
jean-claude gmail draft update DRAFT_ID --clear-attachments < /dev/null
```

**Viewing attachments:** Use `draft get` to see what files are attached:

```bash
jean-claude gmail draft get DRAFT_ID
# Output includes: {"attachments": [{"filename": "report.pdf", "mimeType": "application/pdf"}]}
```

**Iterating on long emails:** For complex emails, use file editing to iterate
with the user without rewriting the full email each time:

1. Create initial draft using heredoc (see examples above)
2. Get draft files: `jean-claude gmail draft get DRAFT_ID` (writes `.json` and `.txt`)
3. Use Edit tool to modify `~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt`
4. Update draft: `cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt | jean-claude gmail draft update DRAFT_ID`
5. Show user, get feedback, repeat steps 3-4 until approved

**Verifying important drafts:** For important emails, read the draft back after
creating it to confirm formatting is correct:

```bash
# Create draft
cat << 'EOF' | jean-claude gmail draft create --to "..." --subject "..."
Email body here!
EOF

# Verify the draft content (check for escaping issues like \!)
jean-claude gmail draft get DRAFT_ID
cat ~/.cache/jean-claude/drafts/draft-DRAFT_ID.txt
```

This catches any escaping bugs before sending. Also verify drafts with
attachments — confirm the right files are attached before sending.

### Manage Threads and Messages

Most commands operate on threads (matching Gmail UI behavior). Use `threadId` from
inbox output. Star/unstar operate on individual messages (use IDs from `messages` array).

```bash
# Star/unstar (message-level - use message IDs from `messages` array)
jean-claude gmail star MSG_ID1 MSG_ID2 MSG_ID3
jean-claude gmail unstar MSG_ID1 MSG_ID2

# Archive/unarchive (thread-level - use threadId)
jean-claude gmail archive THREAD_ID1 THREAD_ID2 THREAD_ID3
jean-claude gmail archive --query "from:newsletter@example.com" -n 50
jean-claude gmail unarchive THREAD_ID1 THREAD_ID2 THREAD_ID3

# Mark read/unread (thread-level - use threadId)
jean-claude gmail mark-read THREAD_ID1 THREAD_ID2
jean-claude gmail mark-unread THREAD_ID1 THREAD_ID2

# Trash (thread-level - use threadId)
jean-claude gmail trash THREAD_ID1 THREAD_ID2 THREAD_ID3
```

**Which ID to use:**
- Thread operations (archive, mark-read, trash, `gmail thread`): use `threadId`
- Message operations (star, `gmail message`, reply): use `id` from `messages` array
- Use `--query` for pattern-based operations (archive supports this)

### Attachments

```bash
# List attachments for a message
jean-claude gmail attachments MESSAGE_ID

# Download an attachment (saved to ~/.cache/jean-claude/attachments/)
jean-claude gmail attachment-download MESSAGE_ID ATTACHMENT_ID filename.pdf

# Download to specific directory
jean-claude gmail attachment-download MESSAGE_ID ATTACHMENT_ID filename.pdf --output ./
```

### Unsubscribing from Newsletters

Unsubscribe links are in the HTML file, not the plain text. Note: HTML files are
only created when the email has HTML content (most newsletters do).

```bash
# Search HTML body for unsubscribe links (if HTML file exists)
grep -oE 'https?://[^"<>]+unsubscribe[^"<>]*' ~/.cache/jean-claude/emails/email-MESSAGE_ID.html
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

### Labels

List all Gmail labels to get label IDs for filtering:

```bash
jean-claude gmail labels
```

Returns system labels (INBOX, SENT, TRASH, etc.) and custom labels (Label_123...).

### Filters

Filters automatically process incoming mail based on criteria.

```bash
# List all filters
jean-claude gmail filter list

# Get a specific filter
jean-claude gmail filter get FILTER_ID

# Delete a filter
jean-claude gmail filter delete FILTER_ID
```

**Creating filters** — query + label operations:

```bash
# Archive (remove INBOX label)
jean-claude gmail filter create \
    "to:reports@company.com" -r INBOX

# Star and mark important
jean-claude gmail filter create \
    "from:boss@company.com" -a STARRED -a IMPORTANT

# Mark as read (remove UNREAD label)
jean-claude gmail filter create \
    "from:alerts@service.com" -r UNREAD

# Forward
jean-claude gmail filter create \
    "from:vip@example.com" -f backup@example.com
```

**Common labels:** `INBOX`, `UNREAD`, `STARRED`, `IMPORTANT`, `TRASH`, `SPAM`,
`CATEGORY_PROMOTIONS`, `CATEGORY_SOCIAL`, `CATEGORY_UPDATES`

**Custom labels:** Use `jean-claude gmail labels` to get IDs like `Label_123456`.

## Calendar

All calendar commands return JSON.

### Multiple Calendars

By default, commands operate on the primary calendar. Use `--calendar` to work
with other calendars.

```bash
# List available calendars
jean-claude gcal calendars
```

Returns:
```json
[
  {"id": "user@gmail.com", "name": "Personal", "primary": true, "accessRole": "owner"},
  {"id": "work@company.com", "name": "Work Calendar", "primary": false, "accessRole": "writer"},
  {"id": "family@group.calendar.google.com", "name": "Family", "primary": false, "accessRole": "owner"}
]
```

Use `--calendar` with any command to specify a different calendar:

```bash
# List events from a specific calendar
jean-claude gcal list --calendar work@company.com

# Create event on another calendar
jean-claude gcal create "Team Sync" \
  --start "2025-01-15 14:00" --calendar work@company.com

# Search by calendar name (case-insensitive substring match)
jean-claude gcal list --calendar "Family"
```

The `--calendar` flag accepts:
- Calendar ID (email or group calendar ID)
- Calendar name (case-insensitive substring match)
- `primary` (default)

If a name matches multiple calendars, the command fails with a list of options.

**Multiple calendars in one query:** The `list`, `search`, and `invitations`
commands accept multiple `--calendar` flags to query across calendars:

```bash
jean-claude gcal list --from 2025-01-15 --to 2025-01-15 \
  --calendar "Personal" --calendar "Work"
```

Each event includes `calendar_id` and `calendar_name` in the output.

### Choosing the Right Calendar

The status command shows participation metrics:

```
Max @ Personal (primary) (24 upcoming: 10 yours, 14 invited) [owner]
Roos family (81 upcoming: 3 invited) [owner]
Max @ TGS (44 upcoming, 0 yours) [freeBusyReader]
```

- **X yours** — Events where user is the organizer
- **X invited** — Events where user is an attendee
- **0 yours** — Likely a "block" calendar (someone else's schedule)

Check user preferences for which calendars represent their availability vs
calendars they just view. When in doubt, ask.

**Checking availability:** Query multiple calendars at once:

```bash
jean-claude gcal list --from 2025-01-07 --to 2025-01-07 \
  --calendar "Max @ Personal" --calendar "Roos family"
```

**Creating events:** Use the primary calendar unless the user names a different
one or context suggests otherwise (work event → work calendar).

### Proactive Calendar Management

When creating events, add useful information proactively:

- **Attendees** — If the user mentions meeting someone, look up their email and
  add them
- **Locations** — If the user mentions a place, search for the full address
- **Video links** — For remote meetings, include the call link if known

A calendar invite should have everything attendees need to show up prepared.

**Description is visible to all attendees.** Never put internal notes or context
about the person/company in the `--description` field.

### Check for Conflicts

Always check the calendar before creating events.

```bash
jean-claude gcal list --from 2025-01-21 --to 2025-01-21
```

If there's a conflict the user may not be aware of, confirm before creating:

- "You have a dentist appointment at 11am. Want me to schedule this for a
  different time?"
- "You already have 'NYU Meeting' at 11am. Want me to update it instead?"

### Calendar Safety (Non-Negotiable)

1. **Never guess dates.** When the user says "Sunday" or "next week", calculate:
   ```bash
   date -v+0d "+%A %Y-%m-%d"
   ```
   Then verify: "Sunday is 2025-12-28 — creating the event for that date."

2. **Never hallucinate emails.** If the user says "add Ursula", look up her
   email or ask. Never invent addresses.

3. **Verify after creating.** Run `jean-claude gcal list` for that date to confirm. If
   wrong, delete and recreate before confirming to the user.

4. **Show invite and get confirmation.** Before `jean-claude gcal create`, show
   the full invite and ask for approval — just like messages:

   > **Calendar invite to create:**
   > - **Title:** 1:1 with Alice
   > - **When:** Tuesday 2025-12-30, 2:00–2:30pm PT
   > - **Attendees:** alice@example.com
   > - **Location:** Zoom (link in description)
   > - **Description:** Weekly sync
   >
   > Create this invite?

   Include location and description if set. Wait for "yes" before creating.

5. **Confirm ambiguous locations.** "SVB" could mean West Hollywood or Santa
   Monica — ask which one.

**Example workflow:**
```
User: "Add a meeting with Alice for next Tuesday at 2pm"

1. Check: date -v+0d "+%A %Y-%m-%d" → "Friday 2025-12-26"
2. Calculate: next Tuesday = 2025-12-30
3. Look up Alice's email
4. Show the full invite preview (title, date/time, attendees, location, description)
5. Wait for user confirmation
6. Create, then verify with `jean-claude gcal list`
```

### List Events

```bash
# Today's events
jean-claude gcal list

# Next 7 days
jean-claude gcal list --days 7

# Date range
jean-claude gcal list --from 2025-01-15 --to 2025-01-20
```

### Create Events

```bash
# Simple event
jean-claude gcal create "Team Meeting" \
  --start "2025-01-15 14:00" --end "2025-01-15 15:00"

# With attendee, location, and description
jean-claude gcal create "1:1 with Alice" \
  --start "2025-01-15 10:00" --duration 30 \
  --attendees alice@example.com \
  --location "Conference Room A" \
  --description "Weekly sync"

# Multiple attendees
jean-claude gcal create "Team Sync" \
  --start "2025-01-15 14:00" --duration 25 \
  --attendees "alice@example.com,bob@example.com"

# All-day event (single day)
jean-claude gcal create "Holiday" \
  --start 2025-01-15 --all-day

# Multi-day all-day event
jean-claude gcal create "Vacation" \
  --start 2025-01-15 --end 2025-01-20 --all-day
```

### Search & Manage Events

```bash
# Search (default: 30 days ahead)
jean-claude gcal search "standup"
jean-claude gcal search "standup" --days 90

# Update
jean-claude gcal update EVENT_ID --start "2025-01-16 14:00"

# Delete
jean-claude gcal delete EVENT_ID --notify
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
jean-claude gcal invitations

# Limit to next 7 days
jean-claude gcal invitations --days 7

# Show all individual instances (don't collapse recurring events)
jean-claude gcal invitations --expand

# Accept an invitation (or all instances if recurring)
jean-claude gcal respond EVENT_ID --accept

# Decline an invitation
jean-claude gcal respond EVENT_ID --decline

# Tentatively accept
jean-claude gcal respond EVENT_ID --tentative

# Respond without notifying organizer
jean-claude gcal respond EVENT_ID --accept --no-notify
```

## Other Platforms

Load these platform-specific guides as needed:

```bash
# Messaging
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/imessage.md
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/whatsapp.md
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/signal.md

# Google Workspace (non-Gmail)
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/drive.md
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/docs.md
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/sheets.md

# Apple
cat ${CLAUDE_PLUGIN_ROOT}/skills/jean-claude/platforms/reminders.md
```

**When to load:** Load the relevant platform file when the user asks about that
service. For example, if they ask "send a WhatsApp message", load whatsapp.md
first.

**Safety rules still apply:** The messaging safety rules in this file (never
send without approval, verify recipients) apply to all messaging platforms.

## Appendix

### Location Context

For "near me" queries, use these sources in order:

1. **User preferences** — Home/work location in personalization skills
2. **Calendar events** — Frequent venues reveal neighborhood
3. **System timezone** — Narrows to region
4. **IP geolocation** (last resort, often inaccurate):
   ```bash
   curl -s ipinfo.io/json | jq '{city, region, country, loc}'
   ```

State your assumption: "I see you're in the LA area based on your calendar."

### Learning from Corrections

When users correct drafts or behavior, offer to save learnable preferences:

- "Actually, sign it 'Best' not 'Cheers'" → sign-off preference
- "Always CC my assistant on work emails" → email rule
- "Don't use exclamation marks" → tone preference

One-off corrections ("make this shorter") are situational, not preferences.

**Default behavior:** Make the correction, then ask "Want me to remember this?"
If yes, update their personalization skill file (e.g., `~/.claude/skills/managing-messages/SKILL.md`).

**Auto-learn mode:** After a few saved corrections, offer to enable auto-learning.
When enabled, save preferences automatically and confirm briefly: "Noted — I'll
sign emails 'Best' from now on."

Never auto-learn anything affecting recipients or major workflow changes.

### Reporting Issues

When you hit unexpected exceptions, schema mismatches, or behavior that
contradicts documentation, offer to file a GitHub issue.

Don't offer for: authentication failures, permission errors, network issues, or
user configuration problems.

> I ran into what looks like a bug in jean-claude. Want me to create a GitHub
> issue? I'll show you the report first and remove any personal information.

See [ISSUES.md](ISSUES.md) for formatting and submission.
