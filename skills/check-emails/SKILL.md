---
name: check-emails
description: "Check inbox and present emails as a fresh grouped list. Use when the user says /check-emails or asks to check their email starting from a clean list."
---

Load the jean-claude skill, then fetch the inbox and present results as a fresh
grouped list starting from A1.

This resets any prior numbering from the current session. After this command,
email identifiers start fresh — "archive A1" refers to the first item in the
first group of this new list, not a previous one.

Run:

```bash
jean-claude gmail inbox --since yesterday
```

Present the results following the "Presenting Messages" format from the
jean-claude skill: group-letter numbering (A1, A2, B1, ...), compact lines,
conversational dates, cross-referenced with calendar.

If the user provided arguments (e.g., `--unread`, `--since "3 days ago"`,
`-n 20`), pass them to the inbox command instead of the defaults above.
