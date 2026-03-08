# import_journey_to_daylio

A script that imports [Journey](https://journey.cloud/) journal export folders directly into a [Daylio](https://daylio.net/) `.daylio` backup file — including entries, photos, tags, and moods.

Point it at your Journey folders and your existing Daylio backup, and it produces a new `.daylio` file ready to restore.

Handles duplicate entries and moving photos over automatically.

Warning: Vibe-coded with Claude.

---

## Requirements

- Python 3.9+
- No third-party packages (uses only the standard library)

---

## How to export from Journey

1. Open the Journey app on your phone or the Journey web app.
2. Go to **Settings → Export**.
3. Choose **JSON** format and export by year (or all at once).
4. You'll get one or more folders, each containing `.json` files and any attached photos.

## How to export your Daylio backup

1. Open Daylio → **More → Backup & Restore → Create Backup**.
2. Save the `.daylio` file somewhere accessible.

---

## Usage

```bash
python import_journey_to_daylio.py <journey_folder> [<journey_folder> ...] --input <backup.daylio> [options]
```

### Examples

```bash
# Import three years of Journey entries into an existing Daylio backup
python import_journey_to_daylio.py 2017/ 2018/ 2019/ --input backup.daylio

# Write to a specific output file (the input file is never modified)
python import_journey_to_daylio.py 2017/ --input old.daylio --output updated.daylio

# Preview what would be imported without writing anything
python import_journey_to_daylio.py 2017/ 2018/ --input backup.daylio --dry-run

# Use Pacific Daylight Time (-7) and print every entry as it's processed
python import_journey_to_daylio.py 2017/ --input backup.daylio --timezone -7 --verbose
```

### Options

| Option | Default | Description |
|---|---|---|
| `journey_folders` | *(required)* | One or more paths to Journey export folders |
| `--input PATH` | *(required)* | Path to the input `.daylio` backup file |
| `--output PATH` | `backup_imported_<YYYY_MM_DD>.daylio` next to `--input` | Path to write the updated `.daylio` file |
| `--timezone OFFSET` | `-8` (US/Pacific Standard) | Your timezone as a UTC offset in hours (e.g. `-5` for EST, `1` for CET) |
| `--tag-group NAME` | `Journey` | Tag group name to put newly-created Journey tags under |
| `--dry-run` | off | Print a summary of changes without writing any files |
| `-v` / `--verbose` | off | Print each entry as it is processed |

---

## How to restore to Daylio

1. Copy the output `.daylio` file to your phone.
2. Open Daylio → **More → Backup & Restore → Restore Backup**.
3. Select the file. Done.

---

## What gets imported

| Journey field | Daylio field | Notes |
|---|---|---|
| `text` | `note` | Newlines converted to `<br>` tags |
| `mood` | `mood` | See mood mapping below |
| `tags` | `tags` | New tags are created in a `Journey` tag group |
| `photos` | `assets` | Copied to the assets folder; duplicates skipped by MD5 checksum |
| `favourite` | `isFavorite` | Carried over as-is |
| `address` | `address` | Carried over when present |
| `date_journal` | `datetime` | Millisecond timestamp; used to detect duplicates |

### Mood mapping

| Journey | Daylio |
|---|---|
| 1 — Amazing | 1 — Rad |
| 2 — Good | 2 — Good |
| 3 — Fine | 3 — Meh |
| 4 — Bad | 4 — Bad |
| 5 — Terrible | 5 — Awful |
| 0 — Unset | 3 — Meh |

---

## Duplicate handling

- **Entries:** any Journey entry whose millisecond timestamp already exists in the Daylio backup is skipped.
- **Media files:** files are deduplicated by MD5 checksum, so the same photo attached to multiple entries is only stored once.
- Running the script multiple times on the same input is safe — no duplicates will be created.

---

## Notes

- The input `.daylio` file is **never modified**. A new output file is always written.
- Multiple Journey folders are processed in the order you provide them. Tags created while processing the first folder are reused by subsequent folders.
- Folders with no `.json` files are skipped with a warning.
- Media files referenced in a Journey entry but not present on disk are skipped with a warning.
