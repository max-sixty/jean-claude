---
name: check-emails
description: "Check the whole inbox and present it as a fresh grouped list, surfacing mail that has gone stale as its own group. Use when the user says /check-emails or asks to check their email starting from a clean list."
---

Load the jean-claude skill, then fetch the whole inbox and present it as a fresh
grouped list starting from A1.

This resets any prior numbering from the current session. After this command,
email identifiers start fresh: "archive A1" refers to the first item in the
first group of this new list, not a previous one.

```bash
jean-claude gmail inbox
```

Cover the whole inbox, not just recent mail. `-n` caps how many threads are
fetched, not how many exist, so when `total_threads` is larger, raise `-n` past
it (or page through `nextPageToken`) until you have them all. The oldest threads
sort last, and reaching them is the point of checking everything.

Present with the "Presenting Messages" format from the jean-claude skill:
group-letter numbering (A1, A2, B1, ...), compact lines, conversational dates,
calendar cross-references. A full inbox is large, so lead with what needs
attention and collapse bulk groups (newsletters, receipts) to a count plus a few
examples rather than listing every line.

End with a **Stale** group: threads older than about two weeks still sitting in
the inbox, listed oldest first with how long each has lingered ("Jun 2, 17
days"). Offer to clear them in one pass. Skip the group when nothing is that old.

If the user provided arguments (e.g., `--unread`, `--since "3 days ago"`,
`-n 20`), pass them to the inbox command instead.
