# Google Docs

Read and write Google Docs documents.

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

The document ID is in the URL:
`https://docs.google.com/document/d/DOCUMENT_ID/edit`

## Create Document

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs create "My Document"
```

## Read Content

```bash
# Read as plain text
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs read DOCUMENT_ID

# Read full JSON structure (includes indices for advanced editing)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs read DOCUMENT_ID --json
```

## Write Content

**Use heredocs** for text content (Claude Code's Bash tool escapes '!' to '\!'
when using echo).

```bash
# Append text to end of document
cat << 'EOF' | uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs append DOCUMENT_ID
New paragraph to add!
EOF

# Find and replace text
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs replace DOCUMENT_ID --find "old text" --replace-with "new text"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs replace DOCUMENT_ID --find "TODO" --replace-with "DONE" --match-case
```

## Get Document Info

```bash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs info DOCUMENT_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdocs info DOCUMENT_ID --json
```
