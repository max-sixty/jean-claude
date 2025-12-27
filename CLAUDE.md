# jean-claude Development

A Claude Code plugin for Gmail, Google Calendar, Google Drive, and iMessage.

Two parts: a **skill** that agents read, and a **library** that implements it.
The skill (`skills/jean-claude/SKILL.md`) documents commands and outputs. The
library (`jean_claude/`) handles the complexity — OAuth, APIs, AppleScript.

## Design Philosophy

The library exists to make the skill simple. Every design decision follows from
this: complexity belongs in the Python code, not in agent workflows.

When an agent reads email, it runs `jean-claude gmail list` and gets JSON. The
agent doesn't know about OAuth tokens, API pagination, rate limits, or retry
logic. The library handles all of that.

**When adding features, ask:** "Can the agent's job be simpler?" Push complexity
into the library. Handle edge cases in Python. Parse and validate in code.

## Two Audiences

- **Agents** read SKILL.md, call CLI commands, never touch Python code. If a
  feature isn't documented there, it doesn't exist for agents.
- **Developers** read this file, work on code in `jean_claude/`.

SKILL.md documents what the CLI does. CLAUDE.md documents how it works.

The skill is the only API. Breaking changes to the library are fine — just keep
the skill in sync.

## Testing Is Real

**Every command you run sends real messages to real people.**

When developing messaging features (iMessage, WhatsApp, Gmail), "testing" still
means sending actual messages. There's no sandbox. Getting into a "just testing
the code" mindset is dangerous — test messages go to real recipients.

**Before running any send command, ask for permission:**

> **Test message I'd like to send:**
> - To: [recipient]
> - Body: "[message content]"
>
> Can I send this?

This applies even when:
- You're "just checking if the code works"
- The message seems trivial ("test", "hello")
- You're iterating on a bug fix
- The recipient is a group chat

Read-only commands (`list`, `search`, `messages`, `chats`) are safe to run
freely. Anything that sends, creates, or modifies requires explicit approval.

## Updating the Skill

When modifying CLI commands or adding features:

1. Update code in `jean_claude/`
2. Update `skills/jean-claude/SKILL.md` to match
3. Regenerate command reference: `uv run python scripts/generate-command-reference.py`

The skill contains hand-written context (safety rules, workflows, setup) plus
auto-generated command reference. The script creates files in
`skills/jean-claude/commands/` with hyphenated names mirroring the command
structure (`gmail-draft.txt` for `gmail draft`, etc.).

**Plugin cache vs repo:** For quick testing, you can edit files directly in
`~/.claude/plugins/cache/jean-claude/...` — changes take effect immediately.
However, only changes committed to this repo are retained. The plugin cache is
overwritten when the plugin updates. Always copy tested changes back to the repo.

## Development Workflow

```bash
uv run pytest                # Run tests (integration tests excluded by default)
pre-commit run --all-files   # Run lints
```

### WhatsApp (Go)

The WhatsApp CLI is written in Go and lives in `whatsapp/` with its own
`go.mod`. Build and test separately:

```bash
cd whatsapp && go build -o /dev/null .   # Verify it compiles
cd whatsapp && go build -o whatsapp-cli . # Build the binary
```

The Python wrapper (`jean_claude/whatsapp.py`) auto-compiles the Go binary on
first use if Go is installed, or downloads a pre-built binary from PyPI.

## Testing Commands

Commands can be tested directly:

```bash
uv run jean-claude --help
uv run jean-claude gmail --help
# etc.
```

## CLI API Conventions

Consistent flags across all commands:

- **`-n` / `--max-results`** - Limit number of results (never `--limit`)
- **`--page-token`** - Pagination token for large result sets

### Input Conventions

- **Stdin** — Content that needs to avoid shell escaping
  - **JSON** for structured data with multiple fields (e.g., `gmail draft create`)
  - **Plain text** for message bodies (e.g., `imessage send`, `gmail draft reply`)
- **Positional args** — IDs, recipients, file paths
- **Flags** — Options and modifiers (`-n`, `--unread`, `--cc`)

One canonical input method per command. No auto-detection between formats.

**Message commands** use plain text stdin to avoid shell escaping issues (Claude
Code's Bash tool can mangle `!` when combined with apostrophes):

```bash
echo "Hello!" | jean-claude imessage send "+1234567890"
echo "Thanks!" | jean-claude gmail draft reply MESSAGE_ID
echo "FYI" | jean-claude gmail draft forward MESSAGE_ID recipient@example.com
```

### Output: File Indirection for Full Content

Commands return compact JSON to stdout. Full content (email bodies, draft text)
is written to XDG cache with the path included in the JSON response.

```json
{"id": "abc123", "snippet": "First 200 chars...", "file": "~/.cache/jean-claude/emails/email-abc123.json"}
```

**Why files instead of inline content?**

- **Composability** — Agents can use `cat`, `grep`, `jq` on the files
- **Context efficiency** — Summaries fit in output; full bodies read on demand
- **Iteration** — Edit files with standard tools, pipe back to update commands
- **No project pollution** — Cache files don't clutter working directories

### Input/Output Format Consistency

**Copy-paste principle:** Any ID in command output should work as input to other
commands. If `chats` outputs `{"id": "abc123", "name": "Team"}`, then
`send "abc123"` and `send "Team"` should both work.

**Fail on ambiguity:** When resolving names to IDs, never guess. If multiple
items match a name, fail with a list of options rather than picking one. Sending
to the wrong recipient is worse than not sending.

**Consistent schemas:** Similar commands across services should have matching
output structures. iMessage and WhatsApp `chats` commands use the same fields:

```json
{"id": "...", "name": "...", "is_group": true, "last_message_time": 1234567890, "unread_count": 5}
```

**Field naming:**
- `id` — primary identifier (not `chat_id`, `jid`, `message_id` in list output)
- `name` — display name
- `is_group` — boolean for group vs individual
- `last_message_time` — Unix timestamp
- `unread_count` — integer

When adding new list commands, follow this schema. When modifying existing
commands, ensure input accepts both `id` and `name` from output.

## Storage Layout

XDG Base Directory compliant:

```
~/.config/jean-claude/           # Config and credentials
├── token.json                   # Google OAuth token
├── client_secret.json           # Custom OAuth credentials (optional)
└── whatsapp/
    └── session.db               # WhatsApp device/session auth

~/.local/share/jean-claude/      # Persistent user data
└── whatsapp/
    ├── messages.db              # Message history
    └── media/                   # Downloaded media files

~/.cache/jean-claude/            # Re-fetchable content (clearable)
├── emails/                      # Fetched email bodies
├── drafts/                      # Draft files for editing
├── attachments/                 # Downloaded email attachments
└── drive/                       # Downloaded Drive files
```

Cache can be cleared without data loss (content is re-fetchable from APIs).

## Output Policy

**All data is JSON.** No `--json` flags, no formatted display modes.

- **Queries** (list, search, get, read) → JSON to stdout
- **Operations returning data** (create, update) → JSON to stdout (agent needs the ID/result)
- **Operations with no result** (delete, star) → `logger.info()` only (exit code indicates success)
- **File content** → Write to file (e.g., `gdrive download`)

Two output mechanisms:

- **`click.echo()`** — JSON data to stdout only
  - `click.echo(json.dumps(output, indent=2))`

- **`logger`** (structlog) — Status/progress to stderr
  - `logger.info("Archived 5 threads", count=5)` — progress (shown by default)
  - `logger.debug("detail", context=data)` — debug info (shown with --verbose)

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

**Never silently swallow exceptions.** If you catch a broad exception, log it:

```python
except Exception as e:
    logger.warning("Failed to fetch counts", error=str(e))
```

### API Error Handling

All Google API errors (`HttpError`) are handled at the top-level CLI entry point
in `cli.py`. Commands don't need try/except blocks — errors bubble up and get
converted to user-friendly messages automatically.

```python
# In cli.py - handles all HttpError across all commands
class ErrorHandlingGroup(click.Group):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except HttpError as e:
            # Convert to JeanClaudeError with user-friendly message
            ...
        except JeanClaudeError as e:
            # Log and display clean error
            ...

# In command files - no try/except needed
@cli.command()
def read(spreadsheet_id: str):
    result = service.spreadsheets().values().get(...).execute()
    click.echo(json.dumps(result["values"]))
```

## Messaging Safety Principles

When adding or modifying messaging features (iMessage, WhatsApp, Gmail):

**Never send to ambiguous recipients.** Any code that resolves names to IDs
must fail if there's ambiguity:

- Multiple chats match a name → fail, list all matches with IDs
- Multiple contacts match a name → fail, list all matches
- One contact has multiple phone numbers → fail, list all numbers
- Never pick "the first one" or guess — require explicit disambiguation

Sending a message to the wrong person is worse than not sending. Fail loudly
and show options rather than silently picking one.

## Integration Tests

Integration tests in `tests/integration/` make real Gmail API calls — **they
send actual emails** to yourself. Run as a final check before major changes,
not on every edit.

```bash
uv run pytest -m integration
```

**Prerequisites:** Valid OAuth credentials (`jean-claude auth` + `jean-claude status`)

**Cleanup:** Test messages are trashed (auto-deleted by Gmail after 30 days).
Drafts created during tests are permanently deleted.

**Gotchas:**

- **Use `result.stdout`** not `result.output` — the latter mixes stdout/stderr
- **Find drafts by snippet** — Gmail API header casing bug causes empty subjects
- **Thread vs message IDs** — `archive`, `mark-read` use threads; `star`, `get` use messages
- **Nested fixtures** — `sent_message` → `test_message` ensures cleanup even if polling fails
