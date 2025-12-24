# jean-claude Development

A Claude Code plugin for Gmail, Google Calendar, Google Drive, and iMessage.

## Architecture

This repo serves two audiences with a clear boundary between them:

**Agents** read `skills/jean-claude/SKILL.md`, call CLI commands, never touch
Python code. SKILL.md is the complete interface — if a feature isn't documented
there, it doesn't exist for agents.

**Developers** read this file, work on the CLI code in `jean_claude/`. The CLI
encapsulates all Google API and iMessage complexity. Agents should never need to
understand OAuth flows, API pagination, or AppleScript internals.

The CLI is the abstraction layer. SKILL.md documents what the CLI does.
CLAUDE.md documents how it works.

## Updating the Skill

When modifying CLI commands or adding features:

1. Update code in `jean_claude/`
2. Update `skills/jean-claude/SKILL.md` to match
3. Regenerate command reference: `uv run python scripts/generate-command-reference.py`

The skill contains hand-written context (safety rules, workflows, setup) plus
auto-generated command reference. The script creates files in
`skills/jean-claude/commands/` with hyphenated names mirroring the command
structure (`gmail-draft.txt` for `gmail draft`, etc.).

## Development Workflow

```bash
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
