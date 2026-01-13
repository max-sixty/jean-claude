# Google Drive

**Command prefix:** `jean-claude `

## List & Search Files

```bash
# List files in root
jean-claude gdrive list
jean-claude gdrive list -n 20
jean-claude gdrive list --folder FOLDER_ID --json

# Search
jean-claude gdrive search "quarterly report"
jean-claude gdrive search "quarterly report" -n 10 --json
```

## Download & Upload

```bash
# Download (saved to ~/.cache/jean-claude/drive/)
jean-claude gdrive download FILE_ID

# Download to specific directory
jean-claude gdrive download FILE_ID --output ./

# Upload to root
jean-claude gdrive upload document.pdf

# Upload to folder with custom name
jean-claude gdrive upload document.pdf --folder FOLDER_ID --name "Q4 Report.pdf"
```

## Manage Files

```bash
# Create folder
jean-claude gdrive mkdir "New Folder"

# Move file to different folder
jean-claude gdrive move FILE_ID FOLDER_ID

# Share
jean-claude gdrive share FILE_ID user@example.com --role reader

# Trash/untrash
jean-claude gdrive trash FILE_ID
jean-claude gdrive untrash FILE_ID

# Get file metadata
jean-claude gdrive get FILE_ID
```
