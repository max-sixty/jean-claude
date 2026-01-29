# gogcli Integration Plan

Features from [gogcli](https://github.com/steipete/gogcli) worth integrating into
jean-claude's Gmail and Calendar implementations.

## Summary

gogcli is a Go CLI for Google services, designed for AI agents. Potentially
useful features:

- **Relative dates** — "today", "tomorrow", "monday", "3 days"
- **FreeBusy/Conflicts** — Availability checking

jean-claude's advantages to preserve:

- **Batch HTTP API** — Already uses `new_batch_http_request` for efficient fetching
- **File caching** — Bodies as separate files for `cat`, `grep`, `jq`
- **Auto-quoting** — Reply/forward with proper quoting
- **Inline image preservation** — Forwards keep embedded images
- **Unified messaging** — iMessage, WhatsApp, Signal in same tool

### Fetching: Already Optimized

jean-claude uses Google's batch HTTP API (`_batch_fetch` in gmail.py), which
bundles 15 requests into a single HTTP call. This is different from gogcli's
concurrent approach (10 parallel separate HTTP calls).

| Approach | HTTP calls | Parallelism | Quota impact |
|----------|------------|-------------|--------------|
| Batch HTTP (jean-claude) | 1 per 15 items | Server-side | Lower |
| Concurrent (gogcli) | 1 per item | Client-side | Higher |

Batch HTTP is already efficient — no change needed.

---

## Priority 1: Relative Date Parsing (Calendar)

**Value:** Medium — More natural agent commands
**Effort:** Low — `dateparser` library already handles this

### Current behavior

```bash
jean-claude gcal list --from "2025-01-29" --to "2025-01-31"
jean-claude gcal create "Meeting" --start "2025-01-30 14:00"
```

### gogcli approach

```bash
gog gcal events --from today --to "3 days"
gog gcal create "Meeting" --start tomorrow --duration 1h
```

Supported values: `today`, `tomorrow`, `monday`..`sunday`, `N days`, `N weeks`

### Implementation

```python
# Proposed addition to gcal.py
import dateparser
from datetime import datetime, timedelta

def parse_relative_date(value: str) -> datetime:
    """Parse relative date like 'today', 'tomorrow', '3 days'."""
    value = value.lower().strip()
    now = datetime.now()

    if value == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if value == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    # Try dateparser for "monday", "next week", "3 days", etc.
    parsed = dateparser.parse(value, settings={"PREFER_DATES_FROM": "future"})
    if parsed:
        return parsed

    raise ValueError(f"Cannot parse date: {value}")
```

### Files to modify

- `jean_claude/gcal.py` — Update `--from`, `--to`, `--start`, `--end` parsing

---

## Priority 2: FreeBusy API

**Value:** Medium — Essential for scheduling assistance
**Effort:** Low — Single API endpoint

### Use case

"When is everyone free for a meeting?" — check availability across multiple
calendars before suggesting times.

### gogcli command

```bash
gog gcal freebusy user1@example.com user2@example.com --from today --to "1 week"
```

### Implementation

```python
@gcal_cli.command("freebusy")
@click.argument("calendars", nargs=-1, required=True)
@click.option("--from", "from_date", help="Start date")
@click.option("--to", "to_date", help="End date")
def freebusy(calendars: tuple[str, ...], from_date: str, to_date: str):
    """Check availability across calendars."""
    service = get_calendar_service()

    body = {
        "timeMin": parse_datetime(from_date).isoformat(),
        "timeMax": parse_datetime(to_date).isoformat(),
        "items": [{"id": cal} for cal in calendars],
    }

    result = service.freebusy().query(body=body).execute()
    click.echo(json.dumps(result["calendars"], indent=2))
```

### Output format

```json
{
  "user1@example.com": {
    "busy": [
      {"start": "2025-01-30T10:00:00Z", "end": "2025-01-30T11:00:00Z"},
      {"start": "2025-01-30T14:00:00Z", "end": "2025-01-30T15:30:00Z"}
    ]
  },
  "user2@example.com": {
    "busy": [...]
  }
}
```

### Files to modify

- `jean_claude/gcal.py` — Add `freebusy` command

---

## Priority 3: Conflicts Detection

**Value:** Medium — Useful for calendar review
**Effort:** Low — Post-processing of event list

### Use case

"Do I have any scheduling conflicts this week?"

### Implementation

```python
@gcal_cli.command("conflicts")
@click.option("--from", "from_date", default="today")
@click.option("--to", "to_date", default="1 week")
@click.option("--calendar", multiple=True)
def conflicts(from_date: str, to_date: str, calendar: tuple[str, ...]):
    """Find overlapping events."""
    events = fetch_events(from_date, to_date, calendar)

    # Sort by start time
    events.sort(key=lambda e: e["start"].get("dateTime", e["start"].get("date")))

    conflicts = []
    for i, event in enumerate(events):
        for other in events[i+1:]:
            if events_overlap(event, other):
                conflicts.append({"event1": event, "event2": other})

    click.echo(json.dumps(conflicts, indent=2))
```

### Files to modify

- `jean_claude/gcal.py` — Add `conflicts` command

---

## Not Recommended

### Gmail History API

Incremental sync — only fetch messages since a checkpoint. Useful for background
daemons, not interactive agent use. Current `inbox --since` covers the use case.

### Email Tracking

gogcli has a full Cloudflare Worker pipeline for tracking email opens. This
requires:

- Cloudflare account and Workers setup
- D1 database for tracking data
- Pixel injection into HTML emails

**Skip** — Complex infrastructure, limited agent use case.

### Watch/Push Notifications

Real-time Gmail notifications via Pub/Sub. Requires:

- Google Cloud Pub/Sub topic
- Webhook endpoint
- Background process to handle notifications

**Skip** — Overkill for interactive agent use.

### Gmail Settings Commands

Vacation responder, delegates, auto-forward, send-as aliases.

**Skip** — Rarely needed, easy to add later if requested.

---

## Implementation Order

1. **Relative dates** — Quick win, improves agent UX
2. **FreeBusy** — Single command, high value for scheduling
3. **Conflicts** — Quick add-on after FreeBusy

---

## Testing Strategy

Each feature should have:

1. **Unit tests** — Mock API responses
2. **Integration test** (where safe) — Real API calls to personal account
3. **Skill update** — Document new commands/options in SKILL.md

---

## Migration Notes

- No breaking changes to existing commands
- New features are additive
- Preserve jean-claude's file caching approach (don't adopt gogcli's inline-only output)
- Keep auto-quoting for replies/forwards (gogcli doesn't have this)
