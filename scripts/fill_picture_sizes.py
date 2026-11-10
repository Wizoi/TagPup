"""Fill the picture size (photos.width, height) of the photos a library indexed before migration 39, from each file's
header (tagpup.services.picture_sizes). A ONE-OFF (owner, 2026-11-09): run once on each of the three libraries, then
DELETE this script and the service with it. Nothing runs after it: the index records the size of every photo it reads.

Dry run by default: the rows' counts (to fill, already filled, clips), and a sample of the photos to fill read, written
nowhere, timed, to say what --apply finds (sized, unreadable, away, gone, stale) and how long it takes. Counts only: no
path, no name.

With --apply, each photo with no size is read and its size recorded, a chunk at a time; a header that gives no size, a file
that changed since its row was read (stale: the index reads it again) and a photo on a network share that did not answer
(away: looked at once more after the share's wait) keep no size, and are counted. Refused while TagPup runs (stop the app
first: it would read the share beside this run). A snapshot of the library is taken first unless one was taken today (the
write only adds two columns' values to rows that had none). Ctrl+C stops it after the photo under way, writing the chunk
read so far; a second Ctrl+C stops at once, and the chunks written stand. Run it again to go on: the photos filled are not
read again. --limit N looks at N photos at most (the first live run: --limit 500). It exits 1 when a network share did
not answer (its photos keep no size: run it again once the share is back), 130 when stopped, 0 otherwise.

    .venv/Scripts/python.exe scripts/fill_picture_sizes.py --library photo_index
    .venv/Scripts/python.exe scripts/fill_picture_sizes.py --library photo_index --apply --limit 500
"""
import argparse
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import config as tagpup_config  # noqa: E402
from tagpup import launcher  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import picture_sizes, snapshots  # noqa: E402

#: Why --apply is refused while an app runs.
APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Stop the app first (close it from its tray or its window), "
               "then run this again.")
#: How often a running --apply says how far it got, in seconds.
EVERY = 10.0


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def say(line):
    """A line of the run's output, flushed: a log the output is redirected to is current while it runs."""
    print(line, flush=True)


def counted(counts):
    return ", ".join("%s %d" % (what, n) for what, n in counts.items())


def took(seconds):
    return "%d h %02d min" % (seconds // 3600, seconds % 3600 // 60) if seconds >= 3600 else "%d min" % (seconds // 60 + 1)


class Stop:
    """Ctrl+C (and Ctrl+Break): the first asks the run to stop after the photo under way, the second stops it at once."""

    def __init__(self):
        self.asked = threading.Event()

    def __enter__(self):
        self.kinds = [kind for kind in (signal.SIGINT, getattr(signal, "SIGBREAK", None)) if kind is not None]
        self.before = {kind: signal.signal(kind, self.pressed) for kind in self.kinds}
        return self

    def __exit__(self, *_exc):
        for kind, handler in self.before.items():
            signal.signal(kind, handler)
        return False

    def pressed(self, _signum=None, _frame=None):
        if self.asked.is_set():
            raise KeyboardInterrupt
        self.asked.set()
        say("stopping after the photo under way (press Ctrl+C again to stop at once).")


def dry_run(db_path, sample, limit, out):
    found = picture_sizes.dry_run(db_path, sample, limit)
    out("From the index: %s." % counted(found["counts"]))
    out("Read (nothing written) a sample of %d photo(s) to fill: %s." % (sum(found["sample"].values()),
                                                                         counted(found["sample"])))
    if found["away"]:
        out("%d network share(s) did not answer: their photos are not timed." % len(found["away"]))
    ms = found["ms"]
    if ms["timed"]:
        out("A photo's look took %.1f ms (median), %.1f ms (p90), %.1f ms (mean) over %d photo(s): --apply on %d photo(s) "
            "takes about %s." % (ms["median"], ms["p90"], ms["mean"], ms["timed"],
                                 found["counts"]["to fill"] if limit is None else min(limit, found["counts"]["to fill"]),
                                 took(found["estimate_s"])))
    out("A dry run: nothing was written. --apply reads each photo to fill and records its size.")
    return 0


def apply(db_path, limit, out):
    servers = launcher.running()
    if servers:
        out(APP_RUNNING % len(servers))
        return 1
    try:
        taken = snapshots.before_a_long_write(Library(db_path))
    except Exception as problem:
        out("Nothing was written: the snapshot of the library could not be taken (%s: %s)." % (type(problem).__name__,
                                                                                                problem))
        return 1
    out("Snapshot: %s." % ("taken (%s)" % ", ".join(taken.details["taken"]) if taken.details["taken"]
                           else "today's stands (%s)" % taken.details["today"]))
    last = [time.monotonic()]

    def heard(filled):
        if time.monotonic() - last[0] >= EVERY:
            last[0] = time.monotonic()
            out("%d photo(s) read, %d size(s) recorded." % (filled.read, filled.wrote))

    with Stop() as stop:
        try:
            filled = picture_sizes.fill(db_path, limit, stop=stop.asked.is_set, heard=heard)
        except KeyboardInterrupt:
            out("Stopped at once: the chunks written before stand (the photos read since the last are not recorded). "
                "Run this again to go on.")
            return 130
    out("Wrote %d row(s): their picture size recorded. Read %d photo(s): %s." % (filled.wrote, filled.read,
                                                                                counted(filled.counts)))
    if filled.wrote != filled.counts[picture_sizes.SIZED]:
        out("%d photo(s) sized were not written: their row was read again or changed meanwhile."
            % (filled.counts[picture_sizes.SIZED] - filled.wrote))
    if filled.away:
        out("%d network share(s) did not answer: run this again when they are back." % len(filled.away))
    if filled.stopped:
        out("Stopped after %d photo(s): run this again to go on." % filled.read)
    return 130 if filled.stopped else 1 if filled.away else 0


def run(db_path, apply_, limit=None, sample=picture_sizes.SAMPLE, out=say):
    """Say what the fill of `db_path` finds, or does with `apply_`. Returns the exit code."""
    if not os.path.isfile(db_path):
        out("There is no library at the path given.")
        return 1
    return apply(db_path, limit, out) if apply_ else dry_run(db_path, sample, limit, out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="read the photos and record their size; without it, count")
    parser.add_argument("--limit", type=int, help="look at this many photos at most")
    parser.add_argument("--sample", type=int, default=picture_sizes.SAMPLE, help="photos a dry run reads, timed")
    args = parser.parse_args(argv)
    if (args.limit is not None and args.limit < 1) or args.sample < 1:
        parser.error("--limit and --sample are 1 or more")
    return run(library_at(args.library), args.apply, args.limit, args.sample)


if __name__ == "__main__":
    sys.exit(main())
