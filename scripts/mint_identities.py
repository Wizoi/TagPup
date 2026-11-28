"""Give an identity (XMP-xmpMM:DocumentID) to each photo whose file holds none: the one-off B1 of
docs/plans/own-metadata-library.md (DECISIONS 2026-11-26, decision 1). Live, 2026-11-27: 56,272 photos of photo_index
whose row records none, in 2,521 folders (at most 392 a folder); none in the other three libraries. About a quarter of
them (13 of a random 60, 2026-11-27) hold one in their file already, written by another program: their row never
recorded it, and is given it.

Steady state: a photo added or changed afterwards is given its identity by the index's bake (stage M), no command run,
and a file read by the index has the identity it holds recorded in its row. This calls that bake
(tagpup.services.bake_after_index.bake_folders with `mint_current`), never a copy: a folder at a time, its write the
index's internal change (in no History, never undone), each photo decoded in full and its tail checked. A photo holding
its bake is written its identity alone, its bake never rewritten; one owed its bake is baked with it, as the next index
would. A photo holding its bake and an identity its row does not record has its row given it (store.photos.follow_fields,
as a read records it), its file written nothing. A photo that does not decode, may be an incomplete copy, or that the
library cannot plan (no vector, never examined) is counted and left as it is. A PNG or a TIFF is written by ExifTool's
rewrite, as the bake's mint of one is. A clip is never baked, and is not minted here.

Dry run by default: each folder holding a photo whose row records no identity is planned, its photos decoded (hours on
photo_index; --limit N plans the first N folders), and counted, every photo of the folder in one of the line's parts;
nothing is written, a file or a row. With --apply: run with TagPup closed and no `TagPup CLI` index running meanwhile
(a running app is refused; a CLI index is not seen, and shares no lock with this run), refused while another run holds
the library; a snapshot of the library first (snapshots.before_a_long_write), then each folder's write, its line counted
from the write's report and the rows changed. Ctrl+C stops after the folder under way; a second press stops at once (the
next open of the library settles the file under way). A share that stops answering ends the run at that folder. Run it
again to go on: a photo given an identity is never given a second, and its row records it. Counts only, unless
--show-folders.

Deleted when the header count ("N photo(s) ... whose row records no identity") is 0 after an --apply; a photo it
refuses (one that does not decode, an incomplete copy, one the library cannot plan) keeps it above 0, and is the owner's
to look at first. Deleted with it: this script; in tagpup/services/bake_after_index.py folders_owed_an_identity,
bake_folders' `mint_current` and `write=False` and their branches (in _planned and _minted too), Baked.to_mint,
to_mint_alone, to_record and recorded, and _record_held; tests/test_photos_with_no_identity_are_given_one.py and its
rows in tests/owners.py and tests/tiers.py.

    .venv/Scripts/python.exe scripts/mint_identities.py --library photo_index
    .venv/Scripts/python.exe scripts/mint_identities.py --library photo_index --apply
"""
import argparse
import collections
import dataclasses
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher, runtime  # noqa: E402
from tagpup.core import byte_lock  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import bake_after_index, snapshots  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402

APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Run this with TagPup closed and no `TagPup CLI` "
               "index running meanwhile: their index writes the same folders' bakes, and no lock is shared between it and "
               "this run.")
TWO_AT_ONCE = "Not run: another run of this script is writing this library."
#: The lock a run with --apply holds, beside the library's file: the system lets go of it however the run ends.
LOCK = ".mint-identities.lock"


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs."""
    print(line, flush=True)


def added(total, one):
    """Add the counts of `one` (a folder's Baked) into `total`."""
    for field in dataclasses.fields(one):
        value = getattr(one, field.name)
        if isinstance(value, bool):
            setattr(total, field.name, getattr(total, field.name) or value)
        elif isinstance(value, collections.Counter):
            getattr(total, field.name).update(value)
        else:
            setattr(total, field.name, getattr(total, field.name) + value)


def told(baked, apply):
    """A run's or a folder's counts in one sentence: with `apply`, what the writes reported; else what was planned."""
    if apply:
        return "wrote %d identit(ies), recorded %d from their files. %s" % (baked.minted, baked.recorded, baked.said())
    left = [("%d possibly incomplete copies" % baked.incomplete) if baked.incomplete else "",
            ("%d not answered (a share away)" % baked.not_answered) if baked.not_answered else "",
            ", ".join("%d %s" % (n, kind) for kind, n in sorted(baked.cannot.items()))]
    left = ", ".join(each for each in left if each)
    return "%d to mint (%d alone, %d with their bake), %d owed their bake alone, %d already hold one in the file, their " \
        "row to be recorded, %d current%s." % (
            baked.to_mint, baked.to_mint_alone, baked.to_mint - baked.to_mint_alone, baked.planned - baked.to_mint,
            baked.to_record, baked.current, ("; left as they are: " + left) if left else "")


class Stop:
    """Ctrl+C during --apply: the first press stops the run after the folder under way, the second at once."""

    def __init__(self):
        self.presses = 0

    def __call__(self, _signum=None, _frame=None):
        self.presses += 1
        if self.presses > 1:
            raise KeyboardInterrupt
        say("Stopping after the folder under way (Ctrl+C again stops at once).")


def run(db_path, apply, limit=None, show_folders=False, out=say):
    """Plan, or with `apply` write, the identities of `db_path`'s photos that hold none. Returns the exit code."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    library, lock, stop = Library(db_path), None, Stop()
    if apply:
        servers = launcher.running()
        if servers:
            out(APP_RUNNING % len(servers))
            return 1
        lock = byte_lock.Lock(db_path + LOCK)
        if not lock.acquire():
            out(TWO_AT_ONCE)
            return 1
    before = signal.signal(signal.SIGINT, stop) if apply else None
    total = bake_after_index.Baked()
    try:
        # A dry run writes nothing: the settings peeked, a half-done change left for the next open to settle.
        settings = runtime.library_settings(library) if apply else runtime.peek_settings(library)
        folders = bake_after_index.folders_owed_an_identity(library)
        out("%s: %d photo(s) in %d folder(s) whose row records no identity." % (
            name, sum(no_id for _folder, _photos, no_id in folders), len(folders)))
        folders = folders[:limit] if limit else folders
        if apply and folders:
            taken = snapshots.before_a_long_write(library)
            out("Snapshot first: %s." % (", ".join(taken.details["taken"]) or "today's stands, " + taken.details["today"]))
        detector = face_service.detector_named(settings.faces)
        with runtime.fields(library, settings) as fields:
            for number, (folder, photos, no_id) in enumerate(folders, 1):
                if stop.presses:
                    out("Stopped before folder %d of %d: run it again to go on." % (number, len(folders)))
                    break
                baked = bake_after_index.bake_folders(library, fields, detector, [folder], {}, mint_current=True,
                                                      write=apply)
                added(total, baked)
                out("Folder %d of %d (%d photo(s), %d with no identity recorded): %s%s" % (
                    number, len(folders), photos, no_id, told(baked, apply), (" " + folder) if show_folders else ""))
                if baked.stalled or baked.roots_changed:
                    out("Ended at that folder: run it again when its share answers.")
                    break
    except KeyboardInterrupt:
        if not apply:
            out("Stopped: a dry run, nothing was written.")
            return 130
        out("Stopped at once: the file under way is settled at the next open of the library. Before the folder under "
            "way: %s Run it again to go on." % told(total, apply))
        return 130
    finally:
        if before is not None:
            signal.signal(signal.SIGINT, before)
        if lock is not None:
            lock.release()
            try:
                os.remove(db_path + LOCK)
            except OSError:   # another run holds it meanwhile, or it is gone
                pass
    out("%s, all: %s" % (name, told(total, apply)))
    if not apply:
        out("A dry run: nothing was written. --apply writes them, with TagPup closed and no `TagPup CLI` index "
            "running meanwhile, after a snapshot.")
    return 0 if not (total.stalled or total.roots_changed or total.failed or total.conflicts) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="write; without it, plan and count, writing nothing")
    parser.add_argument("--limit", type=int, default=None, help="plan (or write) only the first N folders")
    parser.add_argument("--show-folders", action="store_true", help="name each folder on its line (names people)")
    args = parser.parse_args(argv)
    named = args.library
    db_path = named if os.path.isabs(named) else tagpup_config.library_path(
        named if named.endswith(".db") else named + ".db")
    return run(db_path, args.apply, args.limit, args.show_folders)


if __name__ == "__main__":
    sys.exit(main())
