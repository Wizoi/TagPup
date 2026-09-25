"""Re-read the photos whose index rows no longer describe their files, and record them.

Rows go stale in ways the indexer never notices -- bulk keyword writes that recorded
only some of the fields they wrote, or left mtime and size as they were; ExifTool's
answers decoded as cp1252 -- and this finds them, reads their files the way the indexer
does, and records what the files hold. The files are only read, never written. What it
checks and writes is tagpup.services.refresh_rows; this runs it and prints the report.

Dry run by default: lists what would change and changes nothing.

    .venv/Scripts/python.exe scripts/refresh_rows_from_files.py --db data/photo_index.db
    .venv/Scripts/python.exe scripts/refresh_rows_from_files.py --db data/photo_index.db --apply

--apply backs the database up first and reports rows actually changed. Stop the
servers first: they hold rows in memory.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import maintenance, refresh_rows  # noqa: E402


def progress(stage, counts):
    if stage == "found":
        print("rows that may not describe their file: %d" % counts["stale"])
        for reason, count in counts["reasons"].items():
            print("  %-20s %d" % (reason, count))
        print("rows listing a caption more than once: %d (fixed from the row; no file read)"
              % counts["captions_only"])
        if counts["stale"]:
            print("\nreading %d file(s) (read-only)..." % counts["stale"])
    elif stage == "read":
        print("  read %d / %d" % (counts["done"], counts["total"]), end="\r", flush=True)
        if counts["done"] == counts["total"]:
            print()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--folder", help="only rows under this folder")
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--show", type=int, default=5, help="examples to print")
    parser.add_argument("--exiftool", default=None,
                        help="ExifTool to read with; the one config.ini names by default")
    args = parser.parse_args(argv)

    print("%s\n" % args.db)
    result = refresh_rows.refresh_rows(
        Library(args.db), args.exiftool or tagpup_config.exiftool_path(), apply=args.apply,
        folder=args.folder, examples=args.show, progress=progress)
    counts = result.details["counts"]

    if counts["stale"]:
        print("\nrows whose file says something different: %d" % counts["to_write"])
        for field, count in counts["fields"].items():
            print("  %-20s %d" % (field, count))
    if counts["unreadable"]:
        print("files that could not be read (left alone): %d" % counts["unreadable"])
    for path, before, after in result.details["reveal"]["examples"]:
        print("\n  %s\n    caption was: %s\n    file holds : %s"
              % (os.path.basename(path), before, after))

    if result.details["dry_run"]:
        print("\n%s" % maintenance.rehearsed(result))
        print("Dry run. Nothing was changed. Re-run with --apply to write.")
        return 0
    if not result.attempted:
        print("\nNothing to write.")
        return 0
    if result.refused:
        # A row the app saved while this run read the files: the whole change is refused
        # rather than write the older read over the save.
        print("\n%s" % result.refused)
        print("Run it again to read those files again.")
        return 1
    print("\n%s" % maintenance.recorded(result, args.db))
    changed = result.details["changed"]
    print("rows changed from their files: %d" % changed["from_files"])
    print("rows with repeated captions removed: %d" % changed["captions"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
