---
name: check-emails
description: "Fetch inbox and present emails as a fresh numbered list, resetting any prior numbering in the session. Use when the user says /check-emails, 'check my email', 'what's in my inbox', or wants to start a clean email triage. Shows subject, sender, and conversational date; cross-references with calendar for meeting invites."
---

Load the jean-claude skill, then fetch the inbox:

```bash
jean-claude gmail inbox --since yesterday
```

If the user provided arguments (e.g., `--unread`, `--since "3 days ago"`, `-n 20`), pass them instead of the defaults above.

Present results as a **fresh numbered list starting from 1** — this resets any prior numbering. After this command, "archive 1" or "reply to 2" refers to items in this new list only.

**Format each email as:**
```
1: Alice Johnson — Q3 budget review (2h ago)
2: GitHub — [jean-claude] PR #42 merged (yesterday)
3: Bob Smith — Lunch Thursday? (2 days ago)
```

For meeting invites or time-sensitive items, cross-reference with calendar:

```bash
jean-claude gcal events --from today --to "in 3 days"
```

Note conflicts or free slots inline, e.g.: `3: Bob Smith — Lunch Thursday? → you're free 12–2pm Thu`.
