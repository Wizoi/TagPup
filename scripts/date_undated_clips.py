"""Write into each clip's XMP the Date Taken the library reads from its file name: a clip whose file holds no date (its
QuickTime dates zeros, no XMP date) and whose name holds one (`2019-07-04 jetty.mp4`) is dated by it at midnight in the
library already (tagpup.core.dates.name_date); this makes the file say so too, as the photo panel's save of a clip's
date does: XMP:DateTimeOriginal patched in place, its QuickTime dates never written (owner, 2026-11-13;
docs/plans/clip-date-from-name.md; tagpup.services.tagging.date_clips_by_name). A one-off, deleted once its dry run says
0 left on the three libraries: a clip added afterwards is dated by the index from its name, its file not touched.

Dry run by default: the counts the index gives, no file read, nothing written; the clips whose name holds no date either
are named by id, left undated for the owner to date by hand. With --apply (TagPup closed: it refuses while an app runs),
a snapshot of the library first, then the clips listed are read and written as ONE change of the library's journal
(History lists it; `tagpup_cli.py undo <id>` takes it back), each clip's row following its file. A clip whose XMP holds a
date when it is read (saved by hand since the dry run) is left alone and said. It stops at the first write that fails;
a clip open in a player is refused by its writer, said, and does not stop it. Ctrl+C stops it after the clip under way;
what was written is kept (applied and undoable). Run it again to continue: the clips dated are no longer listed. A clip
on a network share that did not answer is left alone and counted apart. Counts and ids only: no path, no name.

    .venv/Scripts/python.exe scripts/date_undated_clips.py --library photo_index
    .venv/Scripts/python.exe scripts/date_undated_clips.py --library photo_index --apply
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
from tagpup.services import file_changes, snapshots, tagging  # noqa: E402

#: Why --apply is refused while an app runs: a running app writes the same clips and library (a save, an index).
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Close it first (from its tray or its window), then run "
               "this again.")


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


class Progress:
    """Where an --apply has got: the change it planned, and the Ctrl+C presses so far. Counts only."""

    def __init__(self, library, out):
        self.library = library
        self.out = out
        self.change = None
        self.presses = 0

    def planned(self, change_id):
        self.change = change_id

    def chunk(self, done, of):
        self.out("%d of %d clips carried out." % (done, of))

    def written(self):
        """How many files the journal holds done for the change, or None when it cannot be read now."""
        if self.change is None:
            return 0
        try:
            return file_changes.files_done(self.library, self.change)
        except Exception:
            return None

    def interrupted(self, _signum=None, _frame=None):
        """Ctrl+C: say so before the read back of the clips written since the last chunk; the second press is the
        read back's end."""
        self.presses += 1
        if self.presses == 1:
            written = self.written()
            self.out("stopping: reading back the clips written (%s recorded so far), press Ctrl+C again only if you must."
                     % ("some" if written is None else written))
        raise KeyboardInterrupt


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs."""
    print(line, flush=True)


def stopped_said(name, stopped, progress, out):
    """What a Ctrl+C left, said as the caption sweep says it."""
    change = getattr(stopped, "change", None)
    if change is None and progress.change is None:
        out("%s: stopped before this run planned or wrote anything. Its --apply first finishes a change an earlier run "
            "left half done, if there is one: one it was finishing as it stopped is finished by the next run." % name)
    elif change is None:
        written = progress.written()
        out("%s: stopped again: at least %s clip(s) written (the one under way may be too), recorded as change %d in "
            "History; the next open of the library finishes settling them. Run this again to continue."
            % (name, "some" if written is None else written, progress.change))
    else:
        written = getattr(stopped, "written", None)
        out("%s: stopped: %s clip(s) written, recorded as change %d in History, which can be undone; run this again to "
            "continue." % (name, "some" if written is None else written, change))
        if getattr(stopped, "left", 0):
            out("%d file could not be read as it stopped: the next start of TagPup settles it." % stopped.left)


def run(db_path, apply, out=say):
    """Say what dating the clips of `db_path` by their names finds, or do it with `apply`. Returns the exit code."""
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
        exiftool = None
        if apply:
            taken = snapshots.before_a_long_write(library)
            out("Snapshot of the library: %s." % (", ".join(taken.details["taken"]) or
                                                  "today's stands (%s)" % taken.details.get("today")))
            # Opened as the apps open it: a change an earlier run left half done is finished first.
            exiftool = runtime.exiftool(library)
        result = tagging.date_clips_by_name(library, exiftool, apply=apply, on_planned=progress.planned,
                                            on_chunk=progress.chunk)
    except KeyboardInterrupt as stopped:
        stopped_said(name, stopped, progress, out)
        return 130
    finally:
        if before is not None:
            signal.signal(signal.SIGINT, before)
    counts, undated = result.details["counts"], result.details["undated"]
    out("%s (from the index): %d clip(s) with no date of their own dated by their names; %d left undated: no date in "
        "its name%s." % (name, counts["listed"], len(undated), (": clips " + ", ".join(map(str, undated))) if undated else ""))
    if result.refused:
        out("Refused: %s" % result.refused)
    if not apply:
        out("A dry run: nothing was read or written. --apply (TagPup closed) writes the date of each of the %d clip(s) "
            "into its XMP." % counts["listed"])
        return 0
    out("Dated: wrote %d clip file(s). Left alone: %d %s, %d unreadable, %d away (their network share did not answer). "
        "Refused by the writer (open in another program): %d. Conflicts: %d (changed since they were read; not "
        "written). Failed: %d. Left to date: %d."
        % (counts["wrote"], counts[tagging.DATED_SINCE], tagging.DATED_SINCE, counts["unreadable"], counts["away"],
           counts["refused"], counts["conflicts"], counts["failed"], counts["left"]))
    stopped = result.details.get("stopped")
    if stopped:
        out("Stopped after %d of %d clips: run this again to continue." % (stopped["after"], stopped["of"]))
    change = result.details.get("change")
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing was recorded: no file was written.")
    return 0 if result.ok and not counts["left"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="write the clips (TagPup closed); without it, count from the index")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.apply)


if __name__ == "__main__":
    sys.exit(main())
