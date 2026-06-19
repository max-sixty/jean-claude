---
name: check-emails
description: "Check the whole inbox and present it as a fresh grouped list, with lingering mail surfaced as a separate stale group. Use when the user says /check-emails or asks to check their email starting from a clean list."
---

Load the jean-claude skill, then survey the entire inbox and present it as a
fresh grouped list starting from A1.

This resets any prior numbering from the current session. After this command,
email identifiers start fresh: "archive A1" refers to the first item in the
first group of this new list, not a previous one.

## Fetch the whole inbox

Cover every thread, not just recent arrivals. Mail that has sat unhandled for
weeks is exactly what this command exists to surface.

```bash
jean-claude gmail inbox -n 200
```

`total_threads` and `total_unread` report the true inbox size, whatever `-n`
fetched. A `nextPageToken` in the response means the inbox is larger than the
fetch: page through with `--page-token` until it's gone, since the oldest mail
sorts last and a short fetch hides the stale threads below. Stop short of paging
through thousands: for an inbox that large, present the recent threads and pull
the old tail with `jean-claude gmail search "in:inbox older_than:14d"`.

## Present

Group the threads with the "Presenting Messages" format from the jean-claude
skill: group-letter numbering (A1, A2, B1, ...), compact lines, conversational
dates, cross-referenced with calendar.

A few hundred threads won't fit on one screen. Lead with what needs attention.
Collapse bulk groups (newsletters, receipts, promotions) to a count and a few
examples, like "Newsletters (18): The Economist, Stratechery, +16", then offer
to expand or bulk-archive them.

### Stale

End with a **Stale** group: threads whose latest message is older than about two
weeks and still in the inbox. List them oldest first with how long each has
lingered ("Jun 2, 17 days"), and offer to clear them in one pass: archive
together, or group by sender to unsubscribe. Tighten or loosen the two-week line
to fit the inbox, and skip the group when nothing has lingered.

## User arguments

If the user passed arguments (`--unread`, `--since "3 days ago"`, `-n 20`),
forward them to the inbox command instead of the whole-inbox fetch. A `--since`
filter already narrows to recent mail, so the stale group doesn't apply.
