# Signal

Send and receive Signal messages via end-to-end encrypted protocol. Requires
Rust binary to be built and QR code linking (see Setup section in SKILL.md).

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## List Chats

```bash
# List contacts and groups
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal chats
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal chats -n 20
```

**Output schema:**

```json
[
  {
    "id": "abc123-def456-...",
    "name": "Alice Smith",
    "is_group": false,
    "phone": "+12025551234"
  },
  {
    "id": "fedcba987654...",
    "name": "Team Chat",
    "is_group": true
  }
]
```

## Send Messages

Message body is read from stdin. **Always use heredocs** (Claude Code's Bash
tool has a bug that escapes '!' to '\!' when using echo). Recipient can be a
UUID or contact name.

```bash
# Send by contact name
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal send "Alice Smith"
Hello!
EOF

# Send by UUID (from chats command)
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal send "abc123-def456-..."
Hello!
EOF

# Message with multiple lines
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal send "Alice"
Great to hear from you!
Let me know when you're free.
EOF
```

**Recipient resolution:** Accepts UUID directly or contact name (case-insensitive
substring match). If multiple contacts match, the command fails with a list of
options—use a more specific name or the UUID.

## Receive Messages

```bash
# Receive pending messages from Signal
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal receive
```

This fetches any pending messages and stores them locally. Messages are returned
as JSON.

## Read Stored Messages

```bash
# Read messages from a specific chat (use ID from chats command)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal messages "abc123-def456-..."

# Limit results
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal messages "abc123-def456-..." -n 20
```

Messages are stored locally after `receive`. Use the chat ID (UUID for contacts,
hex for groups) from the `chats` command.

**Output schema:**

```json
[
  {
    "id": "1234567890123",
    "chat_id": "abc123-def456-...",
    "sender": "abc123-def456-...",
    "timestamp": 1735000000,
    "text": "Hello!",
    "is_outgoing": false,
    "is_read": true
  }
]
```

## Other Commands

```bash
# Show account information
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal whoami

# Check connection status
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude signal status
```
