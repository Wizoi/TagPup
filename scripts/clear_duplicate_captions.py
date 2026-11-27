"""Clear the captions that repeat a photo's title: XMP:Description, IPTC:Caption-Abstract and EXIF's ImageDescription, each
where it holds exactly the photo's own title (XMP:Title, else IPTC:ObjectName), trimmed. A caption that differs ("the
title and more") is kept, Windows' Comment (XPComment) is never touched, a video is skipped. A one-off: the title and the caption are
two values (DECISIONS 2026-11-06), and a save no longer makes such a copy; this clears the copies made before
(tagpup.services.tagging.clear_duplicate_captions).

Dry run by default: the counts the index gives, no file read, nothing written. With --apply, the files the index names
are read and written as ONE change of the library's journal (History lists it; `tagpup_cli.py undo <id>` takes it back),
each photo's row following its file. It is long on a share (hours): it prints a line as each chunk of files is done.
Ctrl+C stops it after the file under way and reads back the files written since the last chunk (it says so); what was
written is kept (applied and undoable); run it again to continue, the photos done are not read again. A second Ctrl+C
skips that read back: the files not reached were taken out first (a second press while they are taken out may leave them
in), and the next open of the library settles what is left: the one under way, and any not taken out, written then. A file
changed since it was read is a conflict, left as it is and counted. A photo on a network share that did not answer (looked
at once more after the share's 30 s) is left alone and counted apart: run it again when the share is back. Counts only:
no path, no name, no caption.

Run it while the app is idle (no server writing the library), and back the library up first. A title changed by another
program between the plan and the write is not detected (only the caption fields are compared). Undo is refused once any
swept photo is saved again (a newer change of the same files), and undoing takes hours.

    .venv/Scripts/python.exe scripts/clear_duplicate_captions.py --library photo_index
    .venv/Scripts/python.exe scripts/clear_duplicate_captions.py --library photo_index --apply
"""
import argparse
import contextlib
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402

BEFORE = ("Run this while the app is idle, after a backup of the library (it writes photo files and the library). "
          "A title changed by another program between the plan and the write is not detected. Undo is refused once "
          "any swept photo is saved again (a newer change of the same files), and undoing takes hours.")


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def counted(counts):
    return ", ".join("%s %d" % (what, n) for what, n in counts.items())


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
        self.out("%d of %d files carried out." % (done, of))

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
        if self.presses == 1:
            written = self.written()
            self.out("stopping: reading back the files written (%s recorded so far), press Ctrl+C again only if you must."
                     % ("some" if written is None else written))
        raise KeyboardInterrupt


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs (#2623)."""
    print(line, flush=True)


def run(db_path, apply, out=say):
    """Say what the sweep of `db_path` finds, or does with `apply`. Returns the exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library = Library(db_path)
    out(BEFORE)
    progress = Progress(library, out)
    before = signal.signal(signal.SIGINT, progress.interrupted) if apply else None
    try:
        # A dry run reads the index alone: no Fields, and the library's settings not stamped.
        with runtime.fields(library) if apply else contextlib.nullcontext() as fields:
            result = tagging.clear_duplicate_captions(library, fields, apply=apply, on_planned=progress.planned,
                                                      on_chunk=progress.chunk)
    except KeyboardInterrupt as stopped:
        out(file_changes.what_a_stop_left(library, stopped, progress.change, who=name))
        return 130
    finally:
        if before is not None:
            signal.signal(signal.SIGINT, before)
    counts = result.details["counts"]
    out("%s (from the index): %s." % (name, counted(counts)))
    if result.refused:
        out("Refused: %s" % result.refused)
    shown = counts["newly shown IPTC captions"]
    if not apply:
        out("A dry run: nothing was read or written. --apply reads each of the %d photo(s) and clears the captions "
            "repeating its title, EXIF's ImageDescription too. %d photo(s) will newly show their IPTC caption, because "
            "their XMP Description (equal to the title) is cleared while a different IPTC caption remains."
            % (counts["to read"], shown))
        return 0
    conflicts = len(result.details["conflicts"])
    left = result.details["left alone"]
    out("Cleared: %d photo file(s) written. Left alone: %d with no caption holding the title any more, %d unreadable, "
        "%d away (their network share did not answer). Conflicts: %d (changed since they were read; not written). "
        "Failed: %d." % (result.changed, left["nothing to clear"], left["unreadable"], left["away"], conflicts,
                         len(result.errors) - conflicts))
    if left["away"]:
        out("%d photo(s) were not reached: their network share did not answer. Run this again when it is back to "
            "continue." % left["away"])
    out("%d photo(s) will newly show their IPTC caption (a different one remained when the XMP Description was cleared)."
        % shown)
    stopped = result.details.get("stopped")
    if stopped:
        out("Stopped after %d of %d photos: run this again to continue." % (stopped["after"], stopped["of"]))
    change = result.details.get("change")
    out("Recorded as change %d in History; to undo it: tagpup_cli.py --db \"%s\" undo %d --apply" % (change, db_path, change)
        if change is not None else "Nothing was recorded: no file was written.")
    return 0 if result.ok and not left["away"] else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="read and write the files; without it, count from the index")
    args = parser.parse_args(argv)
    return run(library_at(args.library), args.apply)


if __name__ == "__main__":
    sys.exit(main())
