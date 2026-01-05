# Google Drive

**Command prefix:** `uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude `

## List & Search Files

```bash
# List files in root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list -n 20
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive list --folder FOLDER_ID --json

# Search
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive search "quarterly report"
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive search "quarterly report" -n 10 --json
```

## Download & Upload

```bash
# Download (saved to ~/.cache/jean-claude/drive/)
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive download FILE_ID

# Download to specific directory
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive download FILE_ID --output ./

# Upload to root
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf

# Upload to folder with custom name
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive upload document.pdf --folder FOLDER_ID --name "Q4 Report.pdf"
```

## Manage Files

```bash
# Create folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive mkdir "New Folder"

# Move file to different folder
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive move FILE_ID FOLDER_ID

# Share
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive share FILE_ID user@example.com --role reader

# Trash/untrash
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive trash FILE_ID
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive untrash FILE_ID

# Get file metadata
uv run --project ${CLAUDE_PLUGIN_ROOT} jean-claude gdrive get FILE_ID
```
