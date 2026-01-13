# Google Sheets

Read and write Google Sheets data directly without downloading files.

**Command prefix:** `jean-claude `

The spreadsheet ID is in the URL:
`https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`

## Create Spreadsheet

```bash
# Create a new spreadsheet
jean-claude gsheets create "My Spreadsheet"

# With custom initial sheet name
jean-claude gsheets create "Budget 2025" --sheet "January"
```

## Read Data

```bash
# Read entire first sheet
jean-claude gsheets read SPREADSHEET_ID

# Read specific range
jean-claude gsheets read SPREADSHEET_ID --range 'Sheet1!A1:D10'

# Read specific sheet
jean-claude gsheets read SPREADSHEET_ID --sheet 'Data'

# Output as JSON
jean-claude gsheets read SPREADSHEET_ID --json
```

## Write Data

All write commands read JSON from stdin (array of rows, each row is array of cells).

```bash
# Append rows to end of sheet
echo '[["Alice", 100], ["Bob", 200]]' | jean-claude gsheets append SPREADSHEET_ID
echo '[["New row"]]' | jean-claude gsheets append SPREADSHEET_ID --sheet 'Data'

# Write to specific range (overwrites existing data)
echo '[["Name", "Score"], ["Alice", 100]]' | jean-claude gsheets write SPREADSHEET_ID 'Sheet1!A1:B2'

# Clear a range (keeps formatting)
jean-claude gsheets clear SPREADSHEET_ID 'Sheet1!A2:Z1000'
```

## Get Spreadsheet Info

```bash
# Get metadata (title, sheet names, dimensions)
jean-claude gsheets info SPREADSHEET_ID
jean-claude gsheets info SPREADSHEET_ID --json
```

## Manage Sheets

```bash
# Add a new sheet to a spreadsheet
jean-claude gsheets add-sheet SPREADSHEET_ID "February"

# Add at specific position (0 = first)
jean-claude gsheets add-sheet SPREADSHEET_ID "Summary" --index 0

# Delete a sheet
jean-claude gsheets delete-sheet SPREADSHEET_ID "Old Data"
```

## Sort Data

```bash
# Sort by column A (ascending)
jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A

# Sort by multiple columns
jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by B --by 'C desc'

# Sort with header row (exclude first row from sorting)
jean-claude gsheets sort SPREADSHEET_ID 'Sheet1!A1:D100' --by A --header
```
