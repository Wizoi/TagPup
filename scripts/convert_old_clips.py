"""Convert a library's clips in an older format (.wmv .mpg .mpeg .avi .mod) to H.264 and AAC in an .mp4 of the same
name, once (DECISIONS 2026-11-12; tagpup.services.old_clip_convert, which says how each clip is converted, verified,
and its original sent to the Recycle Bin -- from a network share, copied through this PC -- before the .mp4 is renamed
into place). ONE-OFF: deleted after the owner's run; the journaled conversion behind the page's buttons is another's.

Dry run by default: the library opened read-only, every clip looked at, nothing written; it says how many by format,
their size, how many have their .mp4's name taken, a temp or a verified temp left by an earlier run, and the room the
run needs. With --apply, refused while TagPup runs; a snapshot of the library first (one a day); then a line for each
clip as it is done (its id, its format, the time so far, its size before and after). Ctrl+C stops the conversion under
way (its temp removed; the original as it was) and starts nothing new; run it again to continue. A delete the Recycle
Bin refuses stops the run, the original and its verified copy kept: empty the Recycle Bin, then run it again.

The library is not written: the next sync of the whole library (TagPup's catch-up as it starts, the daily sync, or
`tagpup_cli.py --db <library> sync --apply`) removes each old row (undoable from History) and indexes the .mp4.

--also-mpeg4-part-2 adds the .mp4/.mov/.m4v clips whose picture no browser plays (ffprobe reads every one: about 2,200
on the share, minutes); one of them is replaced by an .mp4 of its own name.

The first live run (the owner's, with TagPup stopped): --limit 1, look at that clip; then --limit 5; then all.

    .venv/Scripts/python.exe scripts/convert_old_clips.py --library photo_index
    .venv/Scripts/python.exe scripts/convert_old_clips.py --library photo_index --apply --limit 1
"""
import argparse
import collections
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
from tagpup.services import old_clip_convert as old, snapshots  # noqa: E402

APP_RUNNING = ("Not run: TagPup is running (%d app server(s)). Close it first (from its tray or its window), then run "
               "this again.")
STOPPED_BY_DELETE = ("Stopped: %s The clip's original and its verified copy are both kept. Empty the Recycle Bin, then "
                     "run this again.")
NEXT_SYNC = ("The library is not changed by this: the next sync of the whole library (TagPup's as it starts, or "
             "tagpup_cli.py sync --apply) removes the old rows and indexes the .mp4s.")
WROTE = "Wrote %d clip(s): the original gone and the .mp4 in place; %d not converted. %s"
MB = 1024 * 1024


def library_at(named):
    """The library's file: a name in the data folder (photo_index), or a path to a .db."""
    if os.path.isabs(named) or os.sep in named or "/" in named:
        return named
    return tagpup_config.library_path(named if named.endswith(".db") else named + ".db")


def say(line):
    print(line, flush=True)


def _mb(count):
    return "{:,} MB".format(-(-count // MB))


def run(db_path, apply, out, limit=None, ids=None, also_part_2=False, stop=None):
    """Say what the conversion finds in `db_path`, or do it with `apply` (the first `limit` clips to act on, by id;
    of `ids` alone). `stop`, an Event, is Ctrl+C's. Returns the exit code: 0 done, 1 something left, 130 stopped."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        out("%s: there is no library at the path given." % name)
        return 1
    if apply:
        servers = launcher.running()
        if servers:
            out(APP_RUNNING % len(servers))
            return 1
    tools = tagpup_config.locate_ffmpeg()
    if tools.problem:
        out("Not run: %s" % tools.problem)
        return 1
    clips, rows_taken = old.survey(db_path, ids=ids, tools=tools, also_part_2=also_part_2,
                                   on_probe=lambda done, of: out("%d of %d .mp4/.mov/.m4v clips probed." % (done, of)))
    found = [(clip,) + old.state_of(clip, rows_taken) for clip in clips]
    states = collections.Counter(state for _clip, state, _size in found)
    formats = collections.Counter(os.path.splitext(clip.path)[1].lower() for clip, _s, _z in found)
    acted = [(clip, state, size) for clip, state, size in found if state in old.ACTED_ON]
    sizes = [size for _clip, _state, size in acted]
    out("%s: %d clip(s) to look at (%s); %s." % (name, len(found), ", ".join("%s %d" % e for e in sorted(formats.items())),
                                                 ", ".join("%s %d" % e for e in sorted(states.items())) or "none"))
    out("Originals to convert: %s in all, the largest %s. Each needs about 1.5 times its size free beside it, plus 1 GB "
        "(about %s for the largest), and is copied through this PC's Recycle Bin as it is deleted from a share."
        % (_mb(sum(sizes)), _mb(max(sizes or [0])), _mb(int(max(sizes or [0]) * 1.5) + 1024 * MB)))
    if not apply:
        out("A dry run: nothing was written. --apply converts them; the first run: --apply --limit 1, then --limit 5, "
            "then all.")
        return 0
    taken = snapshots.before_a_long_write(Library(db_path))
    out("Snapshot of the library: %s." % (", ".join(taken.details["taken"]) or "today's stands (%s)"
                                          % taken.details.get("today")))
    stop = stop or threading.Event()
    began, wrote, left = time.monotonic(), 0, 0
    todo = acted[:limit] if limit else acted
    for number, (clip, state, size) in enumerate(todo, 1):
        if stop.is_set():
            break
        outcome = old.convert_one(tools, clip, state, size, stop=stop)
        said = "%d of %d, clip %d (%s), %d s:" % (number, len(todo), clip.photo_id,
                                                  os.path.splitext(clip.path)[1].lower(), time.monotonic() - began)
        if outcome.stop:
            out("%s %s" % (said, STOPPED_BY_DELETE % outcome.stop))
            out(WROTE % (wrote, 0, NEXT_SYNC))
            return 1
        if outcome.wrote:
            wrote += 1
            out("%s converted, %s to %s." % (said, _mb(outcome.before) if outcome.before else "(deleted earlier)",
                                             _mb(outcome.after)))
        else:
            left += 1
            out("%s not converted: %s" % (said, outcome.problem))
    out(WROTE % (wrote, left, NEXT_SYNC))
    if stop.is_set():
        out("Stopped by Ctrl+C: run this again to continue.")
        return 130
    return 1 if left else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--library", required=True, help="the library: its name in the data folder, or its .db file")
    parser.add_argument("--apply", action="store_true", help="convert the clips; without it, look and count")
    parser.add_argument("--limit", type=int, help="with --apply, the first N clips to convert (by id)")
    parser.add_argument("--ids", help="only these clips: ids, comma-separated")
    parser.add_argument("--also-mpeg4-part-2", action="store_true",
                        help="also the .mp4/.mov/.m4v clips whose picture no browser plays (probes every one)")
    args = parser.parse_args(argv)
    ids = {int(each) for each in args.ids.split(",") if each.strip()} if args.ids else None
    stop = threading.Event()

    def interrupted(_signum, _frame):
        if stop.is_set():
            raise KeyboardInterrupt
        say("stopping: the conversion under way is stopped and nothing new is started; Ctrl+C again only if you must.")
        stop.set()

    before = signal.signal(signal.SIGINT, interrupted)
    try:
        return run(library_at(args.library), args.apply, say, limit=args.limit, ids=ids,
                   also_part_2=args.also_mpeg4_part_2, stop=stop)
    except KeyboardInterrupt:
        say("Stopped again: what was under way may be left as a temp beside its original; run this again to settle it.")
        return 130
    finally:
        signal.signal(signal.SIGINT, before)


if __name__ == "__main__":
    sys.exit(main())
