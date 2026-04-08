#!/usr/bin/env python3
"""
import_journey_to_daylio.py
Import Journey app export folder(s) directly into a Daylio .daylio backup file.

A .daylio file is a ZIP archive containing:
  - backup.daylio  : minified JSON, base64-encoded
  - /assets/...    : media files

Usage:
    python import_journey_to_daylio.py <journey_folder> [<journey_folder> ...] --input backup.daylio [options]

Examples:
    # Import entries from three year folders into an existing backup
    python import_journey_to_daylio.py 2017/ 2018/ 2019/ --input backup.daylio

    # Write to a specific output file (input is never overwritten)
    python import_journey_to_daylio.py 2017/ --input old.daylio --output updated.daylio

    # Dry-run: preview what would change without writing any files
    python import_journey_to_daylio.py 2017/ 2018/ --input backup.daylio --dry-run

    # Use Pacific Daylight Time and verbose entry logging
    python import_journey_to_daylio.py 2017/ --input backup.daylio --timezone -7 -v

Arguments:
    journey_folders     One or more paths to Journey export folders.

Options:
    --input PATH        Path to the input .daylio backup file (required).
    --output PATH       Path to write the updated .daylio backup file.
                        Defaults to backup_imported_<YYYY_MM_DD>.daylio in the
                        same directory as --input.
    --timezone OFFSET   Timezone offset in hours (e.g. -8 for PST, -7 for PDT).
                        Defaults to -8 (US/Pacific Standard Time).
    --tag-group NAME    Name of the tag group to create for new Journey tags.
                        Defaults to 'Journey'.
    --dry-run           Preview changes without writing any files.
    -v, --verbose       Print individual entries as they are processed.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Journey → Daylio mood mapping
#   Journey: 0 = unset, 1 = amazing, 2 = good, 3 = fine, 4 = bad, 5 = terrible
#   Daylio:  1 = rad,   2 = good,    3 = meh,  4 = bad,  5 = awful
#   Journey 0 (unset) maps to Daylio 3 (meh/neutral).
# ---------------------------------------------------------------------------
JOURNEY_TO_DAYLIO_MOOD = {0: 3, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5}

# File extensions treated as photos (type 1). Everything else is audio (type 2).
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".sticker"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def md5_of_file(path: Path) -> str:
    """Return the MD5 hex-digest of a file's contents."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def journey_text_to_daylio_note(text: str) -> str:
    """
    Convert Journey plain-text / markdown to the HTML-ish format Daylio uses.
    Daylio stores notes as plain text with <br> tags for line breaks.
    """
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{2,}", "<br><br>", text)
    text = text.replace("\n", "<br>")
    return text.strip()


def parse_journey_file(path: Path) -> Optional[dict]:
    """Load and return a Journey JSON entry, or None if the file is invalid."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if "date_journal" not in data:
            return None
        return data
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [WARN] Could not read {path.name}: {exc}", file=sys.stderr)
        return None


def timestamp_to_date_parts(ts_ms: int, tz_offset_hours: float) -> Tuple[int, int, int, int, int]:
    """
    Convert a millisecond timestamp to (minute, hour, day, month_0idx, year).
    Month is 0-indexed as Daylio expects (January = 0).
    """
    tz = timezone(timedelta(hours=tz_offset_hours))
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=tz)
    return dt.minute, dt.hour, dt.day, dt.month - 1, dt.year


# ---------------------------------------------------------------------------
# .daylio file I/O
# ---------------------------------------------------------------------------

def read_daylio(daylio_file: Path, assets_dir: Path) -> dict:
    """
    Read a .daylio ZIP backup file.

    Decodes backup.daylio (base64 → JSON) and extracts all /assets/... entries
    to assets_dir so media files are available for checksum deduplication.

    Returns the parsed Daylio data dict.
    """
    with zipfile.ZipFile(daylio_file, "r") as zf:
        encoded = zf.read("backup.daylio").decode("ascii")
        daylio = json.loads(base64.b64decode(encoded))

        assets_dir.mkdir(parents=True, exist_ok=True)
        for name in zf.namelist():
            if name.startswith("/assets/"):
                rel = name[len("/assets/"):]
            elif name.startswith("assets/"):
                rel = name[len("assets/"):]
            else:
                continue
            if not rel:
                continue
            dest = assets_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(name) as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)

    return daylio


def write_daylio(daylio: dict, assets_dir: Path, output_path: Path) -> None:
    """
    Write a .daylio ZIP backup file.

    Stores minified, base64-encoded JSON as backup.daylio and every file under
    assets_dir as /assets/<relative path>.
    """
    minified = json.dumps(daylio, ensure_ascii=False, separators=(",", ":"))
    encoded = base64.b64encode(minified.encode("utf-8")).decode("ascii")

    asset_files = sorted(assets_dir.rglob("*")) if assets_dir.exists() else []
    asset_files = [f for f in asset_files if f.is_file()]

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("backup.daylio", encoded)
        for asset_file in asset_files:
            rel = asset_file.relative_to(assets_dir)
            zf.write(asset_file, "assets/" + rel.as_posix())

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"\nWrote {output_path.name}  ({size_mb:.2f} MB)")


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------

def convert_folder(
    journey_folder: Path,
    daylio: dict,
    assets_path: Path,
    tz_offset_hours: float = -8.0,
    new_tag_group_name: str = "Journey",
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """
    Import all Journey JSON entries from journey_folder into the daylio dict
    (modified in-place) and copy media files to assets_path.

    Returns the number of new entries that were (or would be) added.
    """
    existing_entries: list = daylio.setdefault("dayEntries", [])
    existing_assets: list = daylio.setdefault("assets", [])
    existing_tags: list = daylio.setdefault("tags", [])
    existing_tag_groups: list = daylio.setdefault("tag_groups", [])

    existing_datetimes: Set[int] = {e["datetime"] for e in existing_entries}
    tag_name_to_id: Dict[str, int] = {t["name"].lower(): t["id"] for t in existing_tags}
    existing_checksums: Set[str] = {a["checksum"] for a in existing_assets}

    next_entry_id = max((e["id"] for e in existing_entries), default=0) + 1
    next_asset_id = max((a["id"] for a in existing_assets), default=0) + 1
    next_tag_id = max((t["id"] for t in existing_tags), default=0) + 1
    next_group_id = max((g["id"] for g in existing_tag_groups), default=0) + 1
    next_tag_order = max((t["order"] for t in existing_tags), default=0) + 1

    journey_group_id: Optional[int] = None
    new_tags_count = 0

    def get_or_create_journey_group() -> int:
        nonlocal journey_group_id, next_group_id
        if journey_group_id is not None:
            return journey_group_id
        for g in existing_tag_groups:
            if g["name"].lower() == new_tag_group_name.lower():
                journey_group_id = g["id"]
                return journey_group_id
        journey_group_id = next_group_id
        next_group_id += 1
        new_group = {
            "id": journey_group_id,
            "name": new_tag_group_name,
            "is_expanded": True,
            "order": max((g["order"] for g in existing_tag_groups), default=0) + 1,
            "id_predefined": -1,
        }
        if not dry_run:
            existing_tag_groups.append(new_group)
        print(f"  [NEW TAG GROUP] '{new_tag_group_name}' (id={journey_group_id})")
        return journey_group_id

    def get_or_create_tag(name: str) -> int:
        nonlocal next_tag_id, next_tag_order, new_tags_count
        key = name.lower()
        if key in tag_name_to_id:
            return tag_name_to_id[key]
        gid = get_or_create_journey_group()
        tag_id = next_tag_id
        next_tag_id += 1
        new_tag = {
            "id": tag_id,
            "name": name,
            "createdAt": 0,
            "icon": 0,
            "order": next_tag_order,
            "state": 0,
            "id_tag_group": gid,
        }
        next_tag_order += 1
        tag_name_to_id[key] = tag_id
        new_tags_count += 1
        if not dry_run:
            existing_tags.append(new_tag)
        if verbose:
            print(f"    [NEW TAG] '{name}' (id={tag_id})")
        return tag_id

    journey_files = sorted(journey_folder.glob("*.json"), key=lambda p: p.name)
    if not journey_files:
        print(f"  No .json files found in '{journey_folder}'", file=sys.stderr)
        return 0

    print(f"  Found {len(journey_files)} Journey JSON file(s).")

    new_entries: List[dict] = []
    new_assets: List[dict] = []
    skipped_duplicate = 0
    skipped_invalid = 0
    photo_count = 0
    photo_skipped = 0

    for jpath in journey_files:
        jdata = parse_journey_file(jpath)
        if jdata is None:
            skipped_invalid += 1
            continue

        ts_ms = jdata["date_journal"]

        if ts_ms in existing_datetimes:
            skipped_duplicate += 1
            if verbose:
                print(f"  [SKIP] Duplicate: {jpath.name}")
            continue

        minute, hour, day, month_0, year = timestamp_to_date_parts(ts_ms, tz_offset_hours)
        tz_offset_ms = int(tz_offset_hours * 3600 * 1000)

        journey_mood = int(jdata.get("mood", 0))
        daylio_mood = JOURNEY_TO_DAYLIO_MOOD.get(journey_mood, 3)

        journey_tags: list = jdata.get("tags", [])
        daylio_tag_ids: list = [get_or_create_tag(t) for t in journey_tags if t]

        asset_ids_for_entry: list = []
        for photo_ref in jdata.get("photos", []):
            photo_path = journey_folder / photo_ref
            if not photo_path.exists():
                print(f"  [WARN] Media file not found: {photo_path.name}", file=sys.stderr)
                continue

            ext = photo_path.suffix.lower()
            asset_type = 1 if ext in PHOTO_EXTENSIONS else 2
            subfolder = "photos" if asset_type == 1 else "audio"

            checksum = md5_of_file(photo_path)

            if checksum in existing_checksums:
                existing_id = next(
                    (a["id"] for a in existing_assets if a["checksum"] == checksum), None
                )
                if existing_id is not None:
                    asset_ids_for_entry.append(existing_id)
                photo_skipped += 1
                if verbose:
                    print(f"    [SKIP MEDIA] Already exists: {photo_path.name}")
                continue

            month_1 = month_0 + 1
            dest_dir = assets_path / subfolder / str(year) / str(month_1)
            dest_file = dest_dir / checksum

            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(photo_path, dest_file)

            asset_id = next_asset_id
            next_asset_id += 1

            original_name = photo_path.name
            if asset_type == 1:
                metadata = json.dumps(
                    {"Name": original_name, "LastModified": ts_ms, "Orientation": 0},
                    separators=(",", ":")
                )
            else:
                metadata = json.dumps(
                    {"Name": original_name, "LastModified": ts_ms},
                    separators=(",", ":")
                )

            asset_record = {
                "id": asset_id,
                "checksum": checksum,
                "type": asset_type,
                "createdAt": ts_ms,
                "createdAtOffset": tz_offset_ms,
                "android_metadata": metadata,
            }
            new_assets.append(asset_record)
            existing_checksums.add(checksum)
            asset_ids_for_entry.append(asset_id)
            photo_count += 1

        note_text = journey_text_to_daylio_note(jdata.get("text", ""))

        entry: dict = {
            "id": next_entry_id,
            "minute": minute,
            "hour": hour,
            "day": day,
            "month": month_0,
            "year": year,
            "datetime": ts_ms,
            "timeZoneOffset": tz_offset_ms,
            "mood": daylio_mood,
            "note": note_text,
            "tags": daylio_tag_ids,
            "assets": asset_ids_for_entry,
            "isFavorite": bool(jdata.get("favourite", False)),
            "scaleValues": [],
        }

        address = jdata.get("address", "")
        if address:
            entry["address"] = address

        next_entry_id += 1
        existing_datetimes.add(ts_ms)
        new_entries.append(entry)

        if verbose:
            dt_str = datetime(year, month_0 + 1, day, hour, minute).strftime("%Y-%m-%d %H:%M")
            print(
                f"  [ENTRY] {dt_str}  mood={daylio_mood}  tags={len(daylio_tag_ids)}"
                f"  photos={len(asset_ids_for_entry)}  note={note_text[:60]!r}"
            )

    if not dry_run:
        existing_entries.extend(new_entries)
        existing_assets.extend(new_assets)

        if "metadata" in daylio:
            total_photos = sum(1 for a in existing_assets if a.get("type") == 1)
            photos_size = sum(
                os.path.getsize(
                    assets_path / "photos"
                    / str(datetime.fromtimestamp(
                        a["createdAt"] / 1000,
                        tz=timezone(timedelta(hours=tz_offset_hours)),
                    ).year)
                    / str(datetime.fromtimestamp(
                        a["createdAt"] / 1000,
                        tz=timezone(timedelta(hours=tz_offset_hours)),
                    ).month)
                    / a["checksum"]
                )
                for a in existing_assets
                if a.get("type") == 1
                and (
                    assets_path / "photos"
                    / str(datetime.fromtimestamp(
                        a["createdAt"] / 1000,
                        tz=timezone(timedelta(hours=tz_offset_hours)),
                    ).year)
                    / str(datetime.fromtimestamp(
                        a["createdAt"] / 1000,
                        tz=timezone(timedelta(hours=tz_offset_hours)),
                    ).month)
                    / a["checksum"]
                ).exists()
            )
            daylio["metadata"]["number_of_entries"] = len(existing_entries)
            daylio["metadata"]["number_of_photos"] = total_photos
            daylio["metadata"]["photos_size"] = photos_size

    print(f"  Entries imported        : {len(new_entries)}")
    print(f"  Entries skipped (dupes) : {skipped_duplicate}")
    print(f"  Entries skipped (bad)   : {skipped_invalid}")
    print(f"  Media files imported    : {photo_count}")
    print(f"  Media files skipped     : {photo_skipped}")
    print(f"  New tags created        : {new_tags_count}")

    return len(new_entries)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Import Journey export folder(s) directly into a Daylio .daylio backup file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "journey_folders",
        nargs="+",
        help="One or more paths to Journey export folders.",
    )
    parser.add_argument(
        "--input",
        required=True,
        metavar="PATH",
        help="Path to the input .daylio backup file.",
    )
    parser.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help=(
            "Path for the output .daylio file "
            "(default: backup_imported_<YYYY_MM_DD>.daylio next to --input)."
        ),
    )
    parser.add_argument(
        "--timezone",
        type=float,
        default=-8.0,
        metavar="OFFSET",
        help="Timezone offset in hours, e.g. -8 for PST (default: -8).",
    )
    parser.add_argument(
        "--tag-group",
        default="Journey",
        metavar="NAME",
        help="Tag group name for new Journey-imported tags (default: 'Journey').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing any files.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print each entry as it is processed.",
    )

    args = parser.parse_args()

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        stamp = date.today().strftime("%Y_%m_%d")
        output_path = input_path.parent / f"backup_imported_{stamp}.daylio"

    journey_folders: List[Path] = []
    for folder_arg in args.journey_folders:
        p = Path(folder_arg).resolve()
        if not p.is_dir():
            print(f"Warning: not a directory, skipping: {p}", file=sys.stderr)
        else:
            journey_folders.append(p)

    if not journey_folders:
        print("Error: no valid Journey folders provided.", file=sys.stderr)
        sys.exit(1)

    print(f"Input .daylio  : {input_path}")
    print(f"Output .daylio : {output_path}")
    print(f"Timezone offset: {args.timezone:+.1f} hours")
    print(f"Folders        : {len(journey_folders)}")
    if args.dry_run:
        print("Mode           : DRY RUN (no files will be written)")

    # Use a temporary directory to stage the extracted assets.
    # On dry-run we still extract so checksums can be compared, but skip all writes.
    with tempfile.TemporaryDirectory() as tmp_dir:
        assets_dir = Path(tmp_dir) / "assets"

        # 1. Extract the input .daylio
        print(f"\nReading {input_path.name}...")
        daylio = read_daylio(input_path, assets_dir)
        existing_count = len(daylio.get("dayEntries", []))
        print(f"  Existing entries: {existing_count}")

        # 2. Import each Journey folder in order
        total_new = 0
        for journey_folder in journey_folders:
            print(f"\nProcessing folder: {journey_folder.name}")
            total_new += convert_folder(
                journey_folder=journey_folder,
                daylio=daylio,
                assets_path=assets_dir,
                tz_offset_hours=args.timezone,
                new_tag_group_name=args.tag_group,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )

        # 3. Pack the output .daylio
        print("\n" + "=" * 60)
        print("OVERALL SUMMARY")
        print("=" * 60)
        print(f"  Entries before import   : {existing_count}")
        print(f"  Entries after import    : {existing_count + total_new}")
        print(f"  Total new entries added : {total_new}")
        if args.dry_run:
            print("\n  ** DRY RUN — no files were written **")
        else:
            print(f"\nPacking output file...")
            write_daylio(daylio, assets_dir, output_path)
            print(f"  Output written to       : {output_path}")
        print("=" * 60)


if __name__ == "__main__":
    main()
