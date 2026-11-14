"""Write each clip's Date Taken into its QuickTime headers, as a save of its date from the panel now does: a clip whose
XMP holds a Date Taken and whose movie header is zero or absent (group A), and each clip of the owner's reviewed list
whose header holds another time (group B, `--csv`), are saved with the date the library shows, through the same
journaled clip save (tagpup.services.clip_headers.date_clip_headers; DECISIONS 2026-11-13, A CLIP'S DATE TAKEN IS WRITTEN INTO
ITS QUICKTIME HEADERS; docs/plans/clip-date-in-quicktime-headers.md). A one-off, deleted once its dry run says 0 left:
a clip dated afterwards gets its headers from the save that dates it.

The list (`--csv`, UTF-8): its file_name and folder (relative to the root: the clip under the root's place here),
and difference_hours; a clip 8 to 10 hours from its XMP date keeps its header (the owner reads it as true UTC). Names no
clip of the library is at are counted.

Dry run by default: the clips listed from the index, by group and by folder, nothing read or written. With --apply
(TagPup closed: it refuses while an app runs), a snapshot of the library first, then ONE change of the library's
journal (History lists it; `tagpup_cli.py undo <id>` takes it back, reading every clip once before it writes). A clip
whose date changed since the listing, or whose file changed between its read and its write, is left alone and said.
--limit N writes the first N. Ctrl+C stops it after the clip under way, what was written kept; run it again to
continue: a clip written is listed no more. Counts and ids only, the folders as the library spells them.

    .venv/Scripts/python.exe scripts/date_clip_headers.py --library photo_index --csv reports/clip_date_disagreements.csv
    .venv/Scripts/python.exe scripts/date_clip_headers.py --library photo_index --csv ... --apply --limit 1
"""
import argparse
import collections
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher, runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import clip_headers, file_changes, snapshots  # noqa: E402

#: Why --apply is refused while an app runs: a running app writes the same clips and library (a save, an index).
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Close it first (from its tray or its window), then run "
               "this again.")
#: The hours from its XMP date a listed clip's header keeps (the owner's 11 of 2026-11-13: a camera's true UTC).
KEPT_HOURS = (8, 10)


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def named_in(csv_path):
    """([(folder, file_name)] of the list's clips whose header takes the XMP date, how many keep theirs: KEPT_HOURS)."""
    with open(csv_path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    kept = [KEPT_HOURS[0] <= abs(float(row["difference_hours"])) <= KEPT_HOURS[1] for row in rows]
    return [(row["folder"].strip("/\\"), row["file_name"]) for row, keep in zip(rows, kept) if not keep], sum(kept)


def say(line):
    print(line, flush=True)


def run(db_path, csv_path, apply, limit, out=say):
    """Say what writing the clips' dates into their headers finds, or do it with `apply`. Returns the exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library = Library(db_path)
    named, kept = named_in(csv_path) if csv_path else ([], 0)
    if apply:
        servers = launcher.running()
        if servers:
            out(APP_RUNNING % len(servers))
            return 1
    planned = []
    try:
        exiftool = None
        if apply:
            taken = snapshots.before_a_long_write(library)
            out("Snapshot of the library: %s." % (", ".join(taken.details["taken"]) or
                                                  "today's stands (%s)" % taken.details.get("today")))
            exiftool = runtime.exiftool(library)
        result = clip_headers.date_clip_headers(library, exiftool, named, apply=apply, limit=limit, on_planned=planned.append,
                                           on_chunk=lambda done, of: out("%d of %d clips carried out." % (done, of)))
    except KeyboardInterrupt as stopped:
        # A press while the clips are listed or read, the snapshot taken, or the clips left counted after the write is
        # no stop of the write's own: what it left is the journal's (#3020).
        out(file_changes.what_a_stop_left(library, stopped, planned[-1] if planned else None, "clip", who=name))
        return 130
    listed, counts = result.details["listed"], result.details["counts"]
    groups = collections.Counter(group for group, *_ in listed)
    out("%s (from the index): %d clip(s) whose headers do not hold their XMP Date Taken: %d zero or absent (A), %d named "
        "in the list (B)." % (name, len(listed), groups["A"], groups["B"]))
    for folder, n in sorted(collections.Counter(folder for _g, _i, _p, folder, *_ in listed).items()):
        out("  %s: %d" % (folder, n))
    if csv_path:
        out("The list names %d clip(s) to date and %d that keep their header (%d to %d hours: true UTC); %d of its names "
            "are no clip of this library." % (len(named), kept, KEPT_HOURS[0], KEPT_HOURS[1], len(result.details["unmatched"])))
    for problem, n in collections.Counter(one[5] for one in listed if one[5]).items():
        out("Refused, never written: %d clip(s): %s" % (n, problem))
    if not apply:
        out("A dry run: nothing was read or written. --apply (TagPup closed) saves each clip's Date Taken into its XMP and "
            "its headers.")
        return 0
    out("Dated: wrote %d clip file(s). Held it already: %d. Left alone: %d %s, %d unreadable or gone, %d away (their "
        "network share did not answer). Conflicts: %d (changed since they were read; not written). Failed: %d. Left to "
        "date: %d." % (counts["wrote"], counts["held"], counts[clip_headers.REDATED_SINCE], clip_headers.REDATED_SINCE,
                       counts["unreadable"], counts["away"], counts["conflicts"], counts["failed"], counts["left"]))
    for photo_id, why in result.details["failures"]:
        out("Clip %s: %s" % (photo_id, why))
    if result.refused:
        out("Stopped: %s" % result.refused)
    change = result.details.get("change")
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing was recorded: no file was written.")
    return 0 if result.ok and not counts["away"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--csv", help="the owner's reviewed list of clips whose header differs from their XMP date")
    parser.add_argument("--apply", action="store_true", help="write the clips (TagPup closed); without it, count from the index")
    parser.add_argument("--limit", type=int, help="write the first N clips listed")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.csv, args.apply, args.limit)


if __name__ == "__main__":
    sys.exit(main())
