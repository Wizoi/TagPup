"""Date each photo and clip of a library that holds no Date Taken and sits in a folder whose name starts with a full date
(`2018-06-23 Picnic`): that day at midnight (tagpup.core.dates.folder_date) written into its file as the photo panel's
save of a Date Taken writes it (fields.date_taken_fields: a photo's EXIF and XMP dates, a clip's XMP dates and QuickTime
headers), its row following its file (DECISIONS 2026-11-15; docs/plans/date-undated-by-folder.md). A year-month or
year-only folder dates nothing; a photo whose file name holds another date is left for the owner, named by id. A one-off:
a photo added undated afterwards is not dated by its folder. Deleted, with dates.folder_date and photos.undated, once its
dry run says 0 left.

Dry run by default: counts and ids from the index, no file read, nothing written. With --apply (TagPup closed: it refuses
while an app runs), a snapshot of the library first, then the photos listed are read and written as one change of the
library's journal and the clips as another (a clip's write reads only what it may patch), each listed in History and
taken back by `tagpup_cli.py undo <id> --apply`. A file holding a date of its own when read is left alone (its row
follows it); a file its writer refuses (open in another program, a clip it cannot patch) and a file on a share that did
not answer are counted and skipped. Ctrl+C stops it after the file under way, what was written kept; run it again to
continue: what was dated is no longer listed. Counts and ids only: no path, no name.

    .venv/Scripts/python.exe scripts/date_undated_by_folder.py --library photo_index
    .venv/Scripts/python.exe scripts/date_undated_by_folder.py --library photo_index --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher, runtime  # noqa: E402
from tagpup.core import dates, fields, paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes, snapshots  # noqa: E402
from tagpup.store import db, photos  # noqa: E402

#: The change's kind, as History names it.
OPERATION = "date undated photos by their folders"
#: Why a file listed is left alone: it holds a date of its own when read (saved since the index read it).
DATED_SINCE = "it holds a date of its own when read"
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Close it first (from its tray or its window), then run "
               "this again.")
#: What is read of each file (clip or not): the fields its Date Taken save writes, which the library dates it by.
READ = {False: dates.SHIFTED_FIELDS, True: dates.CLIP_XMP_DATE_FIELDS + fields.CLIP_HEADER_DATES}


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs."""
    print(line, flush=True)


def listed(library):
    """([(id, path, is a clip, date)] of the undated rows its folder dates, [id] of those whose file name holds another
    date, left for the owner (4 in photo_index, counted 2026-11-15)), by id, from the index alone."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        rows = photos.undated(conn)
    finally:
        conn.close()
    to_date, named_otherwise = [], []
    for photo_id, path, kind in rows:
        date = dates.folder_date(path)
        if date and dates.name_date(path) not in (None, date):
            named_otherwise.append(photo_id)
        elif date:
            to_date.append((photo_id, path, kind == dates.VIDEO, date))
    return to_date, named_otherwise


def write(library, exiftool, rows, clip, planned, out):
    """One change dating `rows` (all clips, or none) through the panel's save's write; its Result."""
    dated = {paths.key(path): date for _id, path, _clip, date in rows}

    def plan_one(path, held):
        if dates.date_taken(held, None, dates.VIDEO if clip else "photo"):
            return file_changes.skip(DATED_SINCE)
        return file_changes.Plan(after=fields.held_dates(fields.date_taken_fields(dated[paths.key(path)], clip=clip), held))

    return file_changes.write_fields(
        library, OPERATION, exiftool, [path for _id, path, _clip, _date in rows], list(READ[clip]), plan_one,
        summary={"photos": len(rows)}, unreadable="skip", uncapped=True, patient=True, stop_at_first_error=True,
        on_planned=planned.append, on_chunk=lambda done, of: out("%d of %d files carried out." % (done, of)))


def said(library, result, rows, noun, db_path, out):
    """What one change did, from the journal: `wrote N` its files done, never what it tried."""
    change = result.details.get("change")
    away = {paths.key(path) for path in result.details.get("not_answered", [])}
    left = {DATED_SINCE: 0, "unreadable": 0, "away": 0}
    for path, why in result.skipped:
        left["away" if paths.key(path) in away else DATED_SINCE if why == DATED_SINCE else "unreadable"] += 1
    refused = len(result.details.get(file_changes.WRITER_REFUSED, []))
    conflicts = len(result.details.get("conflicts", []))
    out("%ss: wrote %d. Left alone: %d holding a date of its own when read, %d unreadable, %d away (their network share "
        "did not answer). Refused by the writer: %d. Conflicts: %d. Failed: %d."
        % (noun, file_changes.files_done(library, change) if change is not None else 0, left[DATED_SINCE],
           left["unreadable"], left["away"], refused, conflicts, len(result.errors) - refused - conflicts))
    if result.refused:
        out("Refused: %s" % result.refused)
    id_of = {paths.key(path): photo_id for photo_id, path, _clip, _date in rows}
    for path, why in result.errors:
        out("%s %s: %s" % (noun, id_of.get(paths.key(path)), why))
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing recorded for the %ss: no file was written." % noun)


def run(db_path, apply, out=say):
    """Say what dating the undated photos of `db_path` by their folders finds, or do it with `apply`. The exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library = Library(db_path)
    servers = launcher.running() if apply else []
    if servers:
        out(APP_RUNNING % len(servers))
        return 1
    to_date, named_otherwise = listed(library)
    groups = [("photo", [row for row in to_date if not row[2]]), ("clip", [row for row in to_date if row[2]])]
    out("%s (from the index): %s; left for you to date, their file name holding another date: %d%s." % (
        name, "; ".join("%d %s(s) to date by their folder%s" % (len(rows), noun, (": ids " + ", ".join(
            str(row[0]) for row in rows)) if rows else "") for noun, rows in groups),
        len(named_otherwise), (": ids " + ", ".join(map(str, named_otherwise))) if named_otherwise else ""))
    if not apply:
        out("A dry run: nothing was read or written. --apply (TagPup closed) writes each one's date into its file.")
        return 0
    taken = snapshots.before_a_long_write(library)
    out("Snapshot of the library: %s." % (", ".join(taken.details["taken"]) or "today's stands (%s)" % taken.details.get("today")))
    exiftool, planned, ok = runtime.exiftool(library), [], True
    try:
        for noun, rows in groups:
            if rows:
                result = write(library, exiftool, rows, noun == "clip", planned, out)
                said(library, result, rows, noun, db_path, out)
                ok = ok and result.ok
    except KeyboardInterrupt as stopped:
        out(file_changes.what_a_stop_left(library, stopped, planned[-1] if planned else None, who=name))
        return 130
    out("Left to date by their folder: %d." % len(listed(library)[0]))
    return 0 if ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="write the files (TagPup closed); without it, count from the index")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.apply)


if __name__ == "__main__":
    sys.exit(main())
