"""Move each clip's Windows Tags into its XMP, once (DECISIONS 2026-11-09; tagpup.services.windows_tags). A clip tagged in
Windows Photo Gallery holds its Tags in a Windows block of its file (the WM/Category of its Xtra), which TagPup does not
read as tags. This writes them into the clip's XMP tags, beside those it holds (nothing it holds is lost), and frees the
Windows block in the same patch: Explorer's Tags column goes blank for the clips moved, and WM/EncodingTime (the date it
was tagged, not taken) is not copied anywhere. One time only: nothing reads a clip's Windows Tags after it.

A clip holding a Windows tag the tag tree does not hold is left as it is and counted (--show names it and its texts);
add the tag to the tree, or leave it. So is a clip holding a Windows tag that is, under a people root, no person. A
branch of the tree (Trips, when the tree has Trips/Coast) is a tag like another and moves (owner, 2026-11-12). A clip is
moved whole or left whole: one holding a person beside such a tag keeps all its Windows Tags. A clip holding no Windows Tags is left alone.

Dry run by default: every clip is read (ExifTool, read-only, the library opened read-only, nothing written, no change
an earlier run left half done settled) and the counts said. With --apply, refused while TagPup runs; a snapshot of the
library first (one a day: a daily taken today stands); then ONE change of the library's journal (History lists it as a
bulk Tags edit; `tagpup_cli.py undo <id>` gives every clip its Windows block back and takes the tags off), each clip's
row following its file. It prints a line as each chunk is read and written. Ctrl+C stops it after the file under way
and reads back the files written since the last chunk (it says so); what was written is kept (applied and undoable);
run it again to continue: a clip moved holds no Windows Tags and is not written again. A second Ctrl+C skips that read
back; the next open of the library settles what is left. A clip open in another program (a player) is refused by its
writer and counted; the others are written. A clip on a network share that did not answer (looked at once more after
the share's 30 s) is left alone and counted as away: run it again when the share is back. Counts only, unless --show.

The first live run (the owner's, 2026-11-09): --limit 5, look at those five clips; then --limit 50; then all.

    .venv/Scripts/python.exe scripts/move_windows_tags.py --library photo_index
    .venv/Scripts/python.exe scripts/move_windows_tags.py --library photo_index --apply --limit 5
"""
import argparse
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher, runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes, snapshots, windows_tags  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402

#: Why --apply is refused while an app runs: a running app writes the same clips and library (a save, an index).
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Close it first (from its tray or its window), then run "
               "this again.")


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def counted(counts):
    return ", ".join("%s %d" % (what, n) for what, n in counts.items())


class Progress:
    """Where an --apply has got: the change it planned, and the Ctrl+C presses so far. Counts only. (The caption sweep's,
    scripts/clear_duplicate_captions.py: each is a one-off, deleted after its run.)"""

    def __init__(self, library, out):
        self.library = library
        self.out = out
        self.change = None
        self.presses = 0

    def planned(self, change_id):
        self.change = change_id

    def read(self, done, of):
        self.out("%d of %d clips read." % (done, of))

    def chunk(self, done, of):
        self.out("%d of %d clips written." % (done, of))

    def written(self):
        """How many files the journal holds done for the change, or None when it cannot be read now."""
        if self.change is None:
            return 0
        try:
            return file_changes.files_done(self.library, self.change)
        except Exception:
            return None

    def interrupted(self, _signum=None, _frame=None):
        """Ctrl+C: say so before the read back of the files written since the last chunk; the second press is the
        read back's end (it is the person's, and it is taken)."""
        self.presses += 1
        if self.presses == 1 and self.change is not None:
            written = self.written()
            self.out("stopping: reading back the files written (%s recorded so far), press Ctrl+C again only if you must."
                     % ("some" if written is None else written))
        raise KeyboardInterrupt


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs (#2623)."""
    print(line, flush=True)


def shown(surveyed, out):
    """--show: the ids of each count, and the texts the tree does not hold or the move may not write (they name people:
    asked for)."""
    for photo_id, texts in surveyed.not_in_tree:
        out("not in the tree: clip %d: %s" % (photo_id, "; ".join(texts)))
    for photo_id, texts in surveyed.not_a_person:
        out("a non-person tag under People: clip %d: %s" % (photo_id, "; ".join(texts)))
    for what, ids in (("unreadable", surveyed.unreadable), ("away", surveyed.away)):
        if ids:
            out("%s: clips %s" % (what, ", ".join(map(str, ids))))


def stopped_said(name, stopped, progress, out):
    """What a Ctrl+C left, said as the caption sweep says it."""
    change = getattr(stopped, "change", None)
    if change is None and progress.change is None:
        # --apply's write first finishes a change an earlier run left half done (file_changes.settle), which writes.
        out("%s: stopped before this run planned or wrote anything. Its --apply first finishes a change an earlier run "
            "left half done, if there is one: one it was finishing as it stopped is finished by the next run." % name)
    elif change is None:
        # A second Ctrl+C, in the read back: how far it had been written is the journal's, not claimed away.
        written = progress.written()
        out("%s: stopped again: at least %s file(s) written (the one under way may be too), recorded as change %d in "
            "History; the next open of the library finishes settling them. Run this again to continue."
            % (name, "some" if written is None else written, progress.change))
    else:
        written = getattr(stopped, "written", None)
        out("%s: stopped: %s file(s) written, recorded as change %d in History, which can be undone; run this again to "
            "continue." % (name, "some" if written is None else written, change))
        if getattr(stopped, "left", 0):
            out("%d file could not be read as it stopped: the next start of TagPup settles it." % stopped.left)


def run(db_path, apply, out, show=False, limit=None, ids=None):
    """Say what the move finds in `db_path`, or do it with `apply` (the first `limit` clips to move; of `ids` alone).
    Returns the exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library = Library(db_path)
    if apply:
        servers = launcher.running()
        if servers:
            out(APP_RUNNING % len(servers))
            return 1
    progress = Progress(library, out)
    before = signal.signal(signal.SIGINT, progress.interrupted) if apply else None
    try:
        if apply:
            taken = snapshots.before_a_long_write(library)
            out("Snapshot of the library: %s." % (", ".join(taken.details["taken"]) or
                                                  "today's stands (%s)" % taken.details.get("today")))
            # Opened as the apps open it: a change an earlier run left half done (killed) is finished first
            # (runtime.library_settings), so the clips it wrote are read as they now are.
            exiftool = runtime.exiftool(library)
        else:
            # A dry run reads the settings without stamping them, and settles nothing (a settle writes files).
            exiftool = runtime.exiftool(library, library_settings.read(library))
        surveyed = windows_tags.survey(library, exiftool, ids=ids, on_chunk=progress.read)
        out("%s: %s." % (name, counted(surveyed.counts())))
        if show:
            shown(surveyed, out)
        if not apply:
            out("A dry run: nothing was written. --apply moves the Windows Tags of %d clip(s) into their XMP (%d tag(s) "
                "added) and frees their Windows block; a clip holding a tag not in the tree, or a non-person tag "
                "under People, is left as it is. The first "
                "run: --apply --limit 5, then --limit 50, then all." % (len(surveyed.to_move), surveyed.tags))
            return 0
        result = windows_tags.move(library, exiftool, surveyed, limit=limit, on_planned=progress.planned,
                                   on_chunk=progress.chunk)
    except KeyboardInterrupt as stopped:
        stopped_said(name, stopped, progress, out)
        return 130
    finally:
        if before is not None:
            signal.signal(signal.SIGINT, before)
    counts = result.details["counts"]
    out("Moved: wrote %d clip file(s), %d tag(s) added. Left alone: %d with no Windows Tags any more, %d with a tag not "
        "in the tree, %d with a non-person tag under People, %d unreadable, %d away (their network share did not answer). "
        "Refused by the writer (open in another program): %d. Conflicts: %d (changed since they were read; not "
        "written). Failed: %d."
        % (counts["wrote"], counts["tags added"], counts["no Windows Tags"], counts["a tag not in the tree"],
           counts["a non-person tag under People"], counts["unreadable"], counts["away"], counts["refused"],
           counts["conflicts"], counts["failed"]))
    if result.refused:
        out("Refused: %s" % result.refused)
    away = len(surveyed.away) + counts["away"]
    if away or counts["refused"]:
        out("%d clip(s) were not reached: run this again (with the share back, the player closed) to continue."
            % (away + counts["refused"]))
    stopped = result.details.get("stopped")
    if stopped:
        out("Stopped after %d of %d clips: run this again to continue." % (stopped["after"], stopped["of"]))
    change = result.details.get("change")
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing was recorded: no file was written.")
    return 0 if result.ok and not away and not stopped else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="write the clips; without it, read them and count")
    parser.add_argument("--show", action="store_true",
                        help="name the clips by id, and the tags not in the tree or no person under People")
    parser.add_argument("--limit", type=int, help="with --apply, move the first N clips to move (by id)")
    parser.add_argument("--ids", help="only these clips: ids, comma-separated")
    args = parser.parse_args(argv)
    ids = [int(each) for each in args.ids.split(",") if each.strip()] if args.ids else None
    return run(library_at(args.library), args.apply, say, show=args.show, limit=args.limit, ids=ids)


if __name__ == "__main__":
    sys.exit(main())
