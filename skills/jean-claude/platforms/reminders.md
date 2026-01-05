# Apple Reminders

Create and manage Apple Reminders via AppleScript. Reminders sync across all
Apple devices via iCloud. On first use, macOS will prompt for Automation
permission.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## Create Reminders

```bash
# Simple reminder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Buy groceries"

# With due date and time
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Call doctor" --due "2025-12-27 14:00"

# Date only (defaults to 9:00 AM)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Submit report" --due 2025-12-30

# With list, priority, and notes
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders create "Important task" \
  --list Work --priority high --notes "Don't forget!"
```

## List Reminders

```bash
# List incomplete reminders (all lists)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list

# From specific list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list --list Work

# Show completed reminders
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list --completed

# Limit results
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders list -n 10
```

## Manage Reminder Lists

```bash
# Show all reminder lists with counts
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders lists
```

## Complete & Delete

```bash
# Mark as completed
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders complete "x-apple-reminder://..."

# Delete a reminder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders delete "x-apple-reminder://..."
```

## Search

```bash
# Search reminders by title
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders search "groceries"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude reminders search "groceries" -n 10
```
