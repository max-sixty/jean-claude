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
