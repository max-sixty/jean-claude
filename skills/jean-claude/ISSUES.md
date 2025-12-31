# Creating GitHub Issues

Detailed guide for filing bug reports when jean-claude encounters library problems.

## Gather Information

Collect before drafting the issue:

- Command that failed
- Error message or unexpected behavior
- Expected behavior
- Jean-claude version: `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude --version`
- Python version: `python3 --version`
- OS: `uname -s` (or check platform from status)

## Scrub Personal Information

Remove before including in issue:

- Email addresses → replace with `user@example.com`
- Phone numbers → replace with `+1-555-XXX-XXXX`
- Names → replace with `[Name]` or `[Contact]`
- OAuth tokens or credentials → remove entirely
- File paths containing usernames → use `~` or `$HOME`
- Message content → summarize as "message body" or "[content]"
- Calendar event details → use generic descriptions

Tell the user what you've scrubbed:

> I've removed your email address, two phone numbers, and the message content
> from the report.

## Issue Format

```markdown
## Description

[One-sentence summary of the problem]

## Steps to Reproduce

1. Run `jean-claude [command]`
2. [What happens]

## Expected Behavior

[What should happen]

## Actual Behavior

[What actually happened, including error message]

## Environment

- jean-claude version: X.Y.Z
- Python: 3.X.Y
- OS: macOS/Linux

## Additional Context

[Any other relevant details]
```

## Submit with Approval

**Always show the user the full issue before submitting. Wait for explicit
approval.**

Check if `gh` CLI is available:

```bash
which gh
```

### If `gh` is available

```bash
gh issue create --repo anthropics/jean-claude \
  --title "Brief description of bug" \
  --body "$(cat <<'EOF'
[Issue body here]
EOF
)"
```

After submission, share the issue URL with the user so they can track it.

### If `gh` is not available

Generate a pre-filled GitHub URL. URL-encode the title and body:

```
https://github.com/anthropics/jean-claude/issues/new?title=URL_ENCODED_TITLE&body=URL_ENCODED_BODY
```

Provide the link to the user:

> Here's a link that will open GitHub with the issue pre-filled. Click it to
> review and submit:
>
> [Create Issue](https://github.com/anthropics/jean-claude/issues/new?title=...)
