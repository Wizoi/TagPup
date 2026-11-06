"""Clear the captions that repeat a photo's title: XMP:Description, IPTC:Caption-Abstract and EXIF:ImageDescription, each
where it holds exactly the photo's own title (XMP:Title, else IPTC:ObjectName), trimmed. A caption that differs ("the
title and more") is kept, EXIF:XPComment is never touched, a video is skipped. A one-off: the title and the caption are
two values (DECISIONS 2026-11-06), and a save no longer makes such a copy; this clears the copies made before
(tagpup.services.tagging.clear_duplicate_captions).

Dry run by default: the counts the index gives, no file read, nothing written. With --apply, the files the index names
are read and written as ONE change of the library's journal (History lists it; `tagpup_cli.py undo <id>` takes it back),
each photo's row following its file. It is long on a share (hours): Ctrl+C stops it after the file under way, keeping
what was written (applied and undoable); run it again to continue, the photos done are not read again. A file changed
since it was read is a conflict, left as it is and counted. Counts only: no path, no name, no caption.

Run it while the app is idle (no server writing the library), and back the library up first.

    .venv/Scripts/python.exe scripts/clear_duplicate_captions.py --library photo_index
    .venv/Scripts/python.exe scripts/clear_duplicate_captions.py --library photo_index --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import tagging  # noqa: E402

BEFORE = "Run this while the app is idle, after a backup of the library (it writes photo files and the library)."


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def counted(counts):
    return ", ".join("%s %d" % (what, n) for what, n in counts.items())


def run(db_path, apply, out=print):
    """Say what the sweep of `db_path` finds, or does with `apply`. Returns the exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library = Library(db_path)
    out(BEFORE)
    exiftool = runtime.exiftool(library) if apply else None
    try:
        result = tagging.clear_duplicate_captions(library, exiftool, apply=apply)
    except KeyboardInterrupt as stopped:
        written = getattr(stopped, "written", None)
        change = getattr(stopped, "change", None)
        if change is None:
            out("%s: stopped before anything was written." % name)
        else:
            out("%s: stopped: %s file(s) written, recorded as change %d in History, which can be undone; "
                "run this again to continue." % (name, "some" if written is None else written, change))
            if getattr(stopped, "left", 0):
                out("%d file could not be read as it stopped: the next start of TagPup settles it." % stopped.left)
        return 130
    counts = result.details["counts"]
    out("%s (from the index): %s." % (name, counted(counts)))
    if result.refused:
        out("Refused: %s" % result.refused)
    if not apply:
        out("A dry run: nothing was read or written. --apply reads each of the %d photo(s) and clears the captions "
            "repeating its title, EXIF's ImageDescription too." % counts["to read"])
        return 0
    conflicts = len(result.details["conflicts"])
    out("Cleared: %d photo file(s) written. Left alone: %d (no caption holds the title any more, or unreadable). "
        "Conflicts: %d (changed since they were read; not written). Failed: %d."
        % (result.changed, len(result.skipped), conflicts, len(result.errors) - conflicts))
    stopped = result.details.get("stopped")
    if stopped:
        out("Stopped after %d of %d photos: run this again to continue." % (stopped["after"], stopped["of"]))
    change = result.details.get("change")
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing was recorded: no file was written.")
    return 0 if result.ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="read and write the files; without it, count from the index")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.apply)


if __name__ == "__main__":
    sys.exit(main())
