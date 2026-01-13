# Creating User Preferences

Guide for creating a personalization skill from user messages and corrections.

## When to Offer

After setup completes, check if the user has a personalization skill:

1. Check `~/.claude/skills/` for existing user skills
2. Look for any skill mentioning: inbox, email, message, communication,
   preferences, or contacts in its description
3. If none found, offer to learn from their messages

**How to offer** (conversational, not pushy):

> I can learn your communication style from your sent messages—things like how
> you sign off emails, your tone, people you message often. Want me to take a
> look and create a preferences file? You'll review it before I save anything.

If user declines, respect that. Don't ask again in the same session.

## Learning from Messages

Analyze the user's existing messages to infer preferences. This is faster than
asking questions and captures their actual patterns.

**Goal:** Build a comprehensive picture without reading thousands of messages.
Use strategic sampling and targeted searches to extract patterns efficiently.

**Time:** This process takes 5-10 minutes. The user can request it anytime,
including after completing their current task—no need to do it right now.

**Scope:** Focus on communication preferences that affect how to draft messages:
- Sign-offs, greetings, tone
- Key contacts (name + phone/email for disambiguation)
- Writing patterns and tics

Do NOT extract: full biographical details, work history, address, topics of
interest. This is a messaging preferences file, not a dossier.

### Efficiency Notes

- **Run searches in parallel** — Phases 1-5 are presented sequentially but many
  searches are independent. Run them concurrently when possible.
- **Distinguish user text from quotes** — Email bodies include quoted replies.
  Look for text BEFORE the "On [date], [name] wrote:" line. For emails with
  long quoted chains, only analyze the first 20-50 lines.
- **Deduplication** — The same email may appear in multiple search results.
  Focus on unique messages, not thread count.
- **Sample size** — Read 15-20+ unique email bodies across contexts. For
  iMessage/WhatsApp, read 30-50 messages from top 3-5 contacts.
- **Empty search results** — If a search (e.g., "Regards,") returns nothing,
  that's useful data. Move on and focus on patterns that do appear.
- **Email body files** — The gmail output includes a `file` path to JSON
  metadata. Read that JSON to get the `body_file` path for the actual text.
- **"No sign-off" is a preference** — If the user just ends with their name
  and no preceding word ("Best,", "Thanks,"), that's itself a pattern.
- **User can always edit** — Mention that they can edit the file directly
  anytime, and that preferences will also update over time as they correct
  drafts or provide feedback.

### Phase 1: Survey the Landscape

Get a high-level view of message volume and categories before diving deep.

**Email overview** (100+ snippets to understand patterns):

```bash
# Recent emails - current style
jean-claude gmail search "in:sent" -n 100

# Older emails - check for consistency
jean-claude gmail search "in:sent older_than:3m" -n 50
jean-claude gmail search "in:sent older_than:6m" -n 50
```

From snippets alone, note:
- **Snippet length** — proxy for email length (short snippets = terse writer)
- **Recipient patterns** — domains suggest professional vs personal
- **Subject line style** — formal vs casual

**iMessage/WhatsApp chat list** (identify key relationships):

```bash
# Get all chats sorted by activity
jean-claude imessage chats -n 50
jean-claude whatsapp chats -n 50
```

Note the top 5-10 most active chats — these are the key relationships.

**Group chat names reveal context** — Names like "Team OA Lesly" suggest
household staff; "Smith Family" indicates family group. Use these to understand
relationships without reading every message.

### Phase 2: Targeted Sign-Off Analysis

Sign-offs are the most actionable preference. Search for common patterns:

```bash
# Search for emails with common sign-offs
jean-claude gmail search "in:sent Thanks," -n 30
jean-claude gmail search "in:sent Best," -n 30
jean-claude gmail search "in:sent Cheers," -n 30
jean-claude gmail search "in:sent Regards," -n 30
```

**Important:** Gmail search matches anywhere in the message, including quoted
replies. You must read full email bodies to verify the sign-off is actually
the user's text, not quoted from someone else.

For each search that returns results, read a few full bodies to see:
- What comes after (just name? full name? nothing?)
- What context triggers this sign-off (professional? casual?)
- Does the user use a dash before their name? (`-Max` vs `Max`)

**Joint sign-offs** (for couples/families):

```bash
# Find emails CC'd to spouse - often use joint sign-off
jean-claude gmail search "in:sent cc:spouse@email.com" -n 20
```

Look for patterns like "Max & Sarah", "Best, The Smiths", etc. in family/social
correspondence.

If no common sign-offs found, read 10-15 random email bodies and look at endings.

### Phase 3: Context-Based Sampling

Different contexts reveal different styles. Sample strategically:

**Professional/vendor emails:**

```bash
# Support and vendor communications
jean-claude gmail search "in:sent to:support@" -n 20
jean-claude gmail search "in:sent to:help@" -n 20
jean-claude gmail search "in:sent to:info@" -n 20
```

Read 5-10 of these to understand professional tone.

**Personal emails** (if user mixes personal and work in one account):

```bash
# Personal domains often indicate informal correspondence
jean-claude gmail search "in:sent to:gmail.com" -n 20
```

Compare tone and formality to professional emails.

**Emails to spouse** (forwarding patterns):

```bash
# Forwards and FYIs to spouse reveal communication patterns
jean-claude gmail search "in:sent to:spouse@email.com" -n 30
```

Look for how they introduce forwarded content ("FYI", "thoughts?", "reminder!").

**Replies vs new threads:**

```bash
# Replies (may be more casual, shorter)
jean-claude gmail search "in:sent subject:Re:" -n 30

# New threads (may have fuller greetings)
jean-claude gmail search "in:sent -subject:Re:" -n 30
```

**Repeated recipients** (reveals relationship patterns):

From the Phase 1 results, identify email addresses that appear multiple times.
Read a few emails to each to see how the user's style varies by recipient.

### Phase 4: Message Style Deep Dive

For iMessage/WhatsApp, sample from key relationships identified in Phase 1.

**Prefer 1:1 chats over group chats** for style analysis — group chats mix
multiple people's messages. Use groups to identify relationships, but read
1:1 conversations to understand the user's style.

```bash
# Messages from specific chats (use chat ID from chats command)
jean-claude imessage messages --chat "CHAT_ID" -n 50
jean-claude whatsapp messages --chat "JID" -n 50

# Or filter by contact name (iMessage only)
jean-claude imessage messages --name "Contact Name" -n 50
```

**Focus on outgoing messages** — filter to `is_from_me: true` when analyzing
the user's style. Incoming messages show how others write, not the user. Look for:

- **Case usage** — Always proper case? Often lowercase?
- **Punctuation** — Heavy exclamation marks? Minimal punctuation?
- **Emoji usage** — Frequent? Rare? Which ones?
- **Message length** — One-liners? Paragraphs?
- **Parenthetical asides** — "(just kidding)", "(ironic that...)"

### Phase 5: Extract Key Contacts

From all the data gathered:

1. **Most frequent email recipients** — Note name, email, apparent relationship
2. **Most active iMessage/WhatsApp chats** — Note name, phone, relationship
3. **Family members** — Often identifiable from context (spouse, parents, kids)

For contacts that will be referenced by name, capture both name and phone/email
so the user can say "text Sarah" without ambiguity.

**Finding family relationships:**

- Spouse often CC'd on emails or mentioned ("my wife", "Ursula and I")
- Parents identifiable from surnames, context ("my father", formal address)
- Children mentioned in scheduling, school emails, family updates
- Siblings from family group chats, shared parents

**Finding household staff/assistants:**

- Look for group chats with family + another person (nanny, housekeeper)
- Calendar events with recurring names
- Emails about scheduling, logistics with non-family

### What to Look For (Checklist)

**Email patterns:**
- [ ] Default sign-off (and variations by context)
- [ ] Default greeting (and variations)
- [ ] Typical email length
- [ ] Formality level (contractions? exclamation marks?)
- [ ] Writing tics (em-dashes? specific phrases?)
- [ ] Professional vs personal style differences
- [ ] Apology phrases ("Very sorry for the slow reply" vs "Apologies for...")
- [ ] Forwarding style (brief note before forward? "FYI", "thoughts?")
- [ ] Thread position style (first message vs quick reply — often different)

**Messaging patterns:**
- [ ] Case usage (proper vs lowercase)
- [ ] Emoji frequency and types
- [ ] Punctuation habits
- [ ] Message length
- [ ] Reaction style (tapbacks like "Loved [quote]" vs emoji reactions)
- [ ] Cross-platform consistency (same style on iMessage and WhatsApp?)

**Key contacts** (use format: +1-555-123-4567 for phones):
- [ ] Spouse/partner (name + phone + email)
- [ ] Family members
- [ ] Frequent correspondents (top 5-10)
- [ ] Assistants or staff (if apparent)

**Contextual preferences:**
- [ ] Different styles for different audiences?
- [ ] Seasonal greetings ("HNY" in Dec/Jan)?
- [ ] Emoji usage by channel (often none in email, more in messages?)
- [ ] Link sharing style (with context? just the URL?)
- [ ] Scheduling phrases ("How about Tuesday?", "any day works")

### Inferring Preferences

Synthesize what you learned. Be specific and actionable:

**Good inferences:**
- "Signs emails 'Max' for friends, 'Thanks, Max' for vendors/support"
- "Uses exclamation marks freely with friends, sparingly in professional emails"
- "Keeps emails to 2-3 sentences; uses numbered lists for technical issues"
- "Opens casual emails with just the name + '!', professional with 'Hi [Name],'"
- "Messages wife Ursula frequently at +1-555-123-4567"
- "Often uses parenthetical asides: '(ironic that X!)'"

**Bad inferences** (too vague to act on):
- "Casual tone" — *how* casual? what markers?
- "Has contacts" — *which* ones? how should they be referenced?
- "Friendly" — what does that mean for drafting?

**Subtle patterns emerge from volume:**

Some patterns only become clear after reading 10+ examples:
- Punctuation quirks (em-dashes, ellipses, exclamation frequency)
- Phrase patterns ("Random question —", "gentle ping on this")
- Apology styles ("Very sorry for the slow reply" vs "Apologies for the delay")

If you notice something once, search for more examples to confirm it's a pattern.

### Show for Review

After analyzing, generate the skill file and **show it to the user before
saving**. They should review and approve:

> Based on reviewing ~200 email snippets and reading samples from key threads,
> plus your recent iMessages/WhatsApp, here's what I learned:
>
> ```markdown
> [Generated skill content]
> ```
>
> Does this look right? I can adjust anything before saving.

Tell them how many messages you analyzed so they understand the basis.

Only save after they approve. If they want changes, edit and show again.

## Generating the Skill File

### Check for Existing Skills

Before creating a new skill, check if the user already has one:

```bash
ls ~/.claude/skills/
```

Look for:
- `managing-messages`
- Any skill mentioning email/inbox/messages in its description

If found, confirm: "You already have a preferences skill. Want me to update
that one, or create a new one?"

### Skill Location

User skills go in `~/.claude/skills/`. Create a directory for their preferences:

```bash
mkdir -p ~/.claude/skills/managing-messages
```

### Skill Structure

Generate a SKILL.md based on what you learned. Keep it minimal—only include
patterns you actually observed.

**Minimal example:**

```markdown
---
name: managing-messages
description: "Personal messaging preferences. Load before jean-claude for inbox review, email drafting, or sending messages. Use when user asks about email, messages, or communication."
---

# Managing Messages

Sign off emails with "Cheers, Max"

Tone: casual with friends, professional but warm with work contacts.
```

**Fuller example** (when more patterns observed):

```markdown
---
name: managing-messages
description: "Personal messaging preferences. Load before jean-claude for inbox review, email drafting, or sending messages. Use when user asks about email, messages, or communication."
---

# Managing Messages

Load `jean-claude` for Gmail, Calendar, iMessage, and WhatsApp commands.

## Email Style

Sign off emails with "Cheers, Max" (just first name).

**Tone by context:**
- Work emails: professional but warm, not stiff
- Friends and family: casual, uses exclamation marks freely

**Patterns observed:**
- Keeps emails short—usually 2-3 sentences
- Opens with just the person's name, no "Hi" or "Dear"
- Uses em-dashes frequently

## Key Contacts

- **Sarah** (wife): +1-555-123-4567
- **Mom**: +1-555-987-6543

## Inbox Approach

[Only include if you can infer from their behavior or they mention it]
```

### Writing Guidelines

- **Be specific** — "Signs 'Cheers, Max'" not "casual sign-off"
- **Only include observed patterns** — Don't invent preferences
- **Make it actionable** — Each line should change how you draft messages

### After Creating

1. Show the generated content to the user for review
2. Make any requested changes
3. Write to `~/.claude/skills/managing-messages/SKILL.md`
4. Confirm: "Saved your preferences. I'll use these when drafting messages."

## Updating Preferences

When adding or changing preferences (from initial setup or learned corrections):

1. Read the existing skill file
2. Find the right section (or create one)
3. Add/update the preference
4. Write the updated file

Keep the file organized. Don't just append—integrate new preferences into
existing sections.

For guidance on learning preferences from user corrections (confirmation mode
vs. auto-learn mode), see the "Learning from Corrections" section in SKILL.md.
