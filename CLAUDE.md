# jean-claude Development

This repository contains the jean-claude Claude Code plugin for managing Gmail, Google Calendar, Google Drive, and iMessage.

## Skill Documentation

**The skill is the primary interface for this plugin.** Users interact with
jean-claude through Claude Code, which loads the skill from
`skills/jean-claude/SKILL.md`. Any feature not documented in SKILL.md is
effectively invisible to users.

When modifying CLI commands or adding features:
1. Update the code in `jean_claude/`
2. **Update the skill documentation in `skills/jean-claude/SKILL.md` to match**
3. Ensure examples in SKILL.md accurately reflect command capabilities
4. Regenerate command reference files (see below)

**Maintenance note:** The skill contains both hand-written context (safety rules, workflows, setup guidance) and command reference examples. Command reference is auto-generated:

```bash
# After modifying CLI commands, regenerate reference
uv run python scripts/generate-command-reference.py
```

This creates flat files in `skills/jean-claude/commands/` with hyphenated names:
- `main.txt` - top-level help
- `auth.txt`, `status.txt`, `completions.txt` - simple commands
- `gmail.txt` - gmail command group overview
- `gmail-archive.txt` - gmail archive command
- `gmail-draft.txt` - draft command group with all draft subcommands (create, send, etc.)
- `gcal-create.txt`, `gcal-list.txt`, etc. - individual calendar commands
- Similar pattern for gdrive and imessage

Files mirror command structure with hyphens. Command groups with subcommands (like `gmail-draft.txt`) consolidate all related help into one file.

## Development Workflow

```bash
# Install with dev dependencies
uv sync

# Run tests
uv run pytest

# Run lints
pre-commit run --all-files
```

## Testing Commands

Commands can be tested directly:

```bash
uv run jean-claude --help
uv run jean-claude gmail --help
# etc.
```

For interactive testing with Claude, use the installed plugin version or test locally by temporarily modifying `~/.claude/plugins/marketplaces/jean-claude` to point to your development directory.

## Output & Logging

Two output mechanisms:

- **`click.echo()`** — JSON data to stdout only
  - `click.echo(json.dumps(output))`

- **`logger`** (structlog) — All other output to stderr
  - `logger.info("Archived 5 threads", count=5)` — progress/status (shown by default)
  - `logger.debug("detail", context=data)` — debug info (shown with --verbose)
  - `logger.warning("Rate limited", delay=2)` — warnings
  - `logger.error("Failed to connect")` — errors

Console level is INFO by default, DEBUG with --verbose. All logs also go to
JSON file at `~/Library/Logs/jean-claude/jean-claude.log`.

Import: `from jean_claude.logging import get_logger; logger = get_logger(__name__)`

## Error Handling

Use `JeanClaudeError` for expected errors (invalid input, API failures, etc.):

```python
from jean_claude.logging import JeanClaudeError

raise JeanClaudeError("Event not found: abc123")
```

The CLI entry point catches these, logs them, and shows a clean message:
```
Error: Event not found: abc123
```

Unexpected errors propagate with full traceback for debugging.

## iMessage Safety Principles

When adding or modifying iMessage features:

**Never send to ambiguous recipients.** Any code that resolves names to phone
numbers must fail if there's ambiguity:

- Multiple contacts match a name → fail, list all matches
- One contact has multiple phone numbers → fail, list all numbers
- Never pick "the first one" or guess — require explicit disambiguation

Sending a message to the wrong person is worse than not sending. Fail loudly
and show options rather than silently picking one.
