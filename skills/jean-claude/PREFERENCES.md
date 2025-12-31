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

Instead of asking questions, analyze the user's existing messages to infer
preferences. This is faster and captures their actual patterns.

### What to Analyze

**Sent emails** (most valuable):

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gmail search "in:sent" -n 20
```

Read the full bodies of several emails to look for:

- **Sign-off patterns** — How do they end emails? Just name? "Best,"? "Cheers,"?
  Do they sign differently for work vs personal?
- **Tone** — Casual ("hey!", contractions, exclamation marks) vs formal
  ("Dear", full sentences, no contractions)?
- **Greeting style** — "Hi [Name]", just the name, or no greeting?
- **Common phrases** — Do they have verbal tics or favorite expressions?
- **Length** — Terse one-liners or detailed paragraphs?

**Sent iMessages/WhatsApp** (if enabled):

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude imessage messages -n 50
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude whatsapp messages -n 50
```

Look for:
- **Frequent contacts** — Who do they message most? Note names and relationships
  if apparent from context.
- **Message style** — Emoji usage, punctuation habits, formality level

**Calendar patterns** (optional):

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gcal list --days 30
```

Look for:
- Recurring meeting patterns
- Preferred meeting times
- Common attendees

### Inferring Preferences

After reading messages, synthesize what you learned. Be specific:

**Good inferences:**
- "Signs emails 'Cheers, Max' for personal, 'Best, Max' for work"
- "Uses exclamation marks freely, casual tone"
- "Messages wife Sarah frequently at +1-555-123-4567"
- "Prefers short, direct emails—rarely more than 3 sentences"

**Bad inferences** (too vague):
- "Casual tone" (how casual? what markers?)
- "Has contacts" (which ones? how referenced?)

### Show for Review

After analyzing, generate the skill file and **show it to the user before
saving**. They should review and approve:

> Based on your sent messages, here's what I learned about your style:
>
> ```markdown
> [Generated skill content]
> ```
>
> Does this look right? I can adjust anything before saving.

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
