# Apple Reminders

Create and manage Apple Reminders via AppleScript. Reminders sync across all
Apple devices via iCloud. On first use, macOS will prompt for Automation
permission.

**Command prefix:** `jean-claude `

## Create Reminders

```bash
# Simple reminder
jean-claude reminders create "Buy groceries"

# With due date and time
jean-claude reminders create "Call doctor" --due "2025-12-27 14:00"

# Date only (defaults to 9:00 AM)
jean-claude reminders create "Submit report" --due 2025-12-30

# With list, priority, and notes
jean-claude reminders create "Important task" \
  --list Work --priority high --notes "Don't forget!"
```

## List Reminders

```bash
# List incomplete reminders (all lists)
jean-claude reminders list

# From specific list
jean-claude reminders list --list Work

# Show completed reminders
jean-claude reminders list --completed

# Limit results
jean-claude reminders list -n 10
```

## Manage Reminder Lists

```bash
# Show all reminder lists with counts
jean-claude reminders lists
```

## Complete & Delete

```bash
# Mark as completed
jean-claude reminders complete "x-apple-reminder://..."

# Delete a reminder
jean-claude reminders delete "x-apple-reminder://..."
```

## Search

```bash
# Search reminders by title
jean-claude reminders search "groceries"
jean-claude reminders search "groceries" -n 10
```
