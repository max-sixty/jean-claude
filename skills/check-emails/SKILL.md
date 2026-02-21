---
name: check-emails
description: "Check inbox and present emails as a fresh numbered list. Use when the user says /check-emails or asks to check their email starting from a clean list."
---

Load the jean-claude skill, then fetch the inbox and present results as a new
numbered list starting from 1.

This resets any prior numbering from the current session. After this command,
email numbers start fresh — "archive 1" refers to the first item in this new
list, not a previous one.

Run:

```bash
jean-claude gmail inbox --since yesterday
```

Present the results following the "Presenting Messages" format from the
jean-claude skill: manual `N:` numbering, compact lines, conversational dates,
cross-referenced with calendar.

If the user provided arguments (e.g., `--unread`, `--since "3 days ago"`,
`-n 20`), pass them to the inbox command instead of the defaults above.
