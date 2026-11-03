"""Count the shapes of the metadata segments of a library's JPEGs, before TagPup writes them itself
(docs/PLAN_OWN_METADATA.md, M1; tagpup.services.segment_census says what is counted and how).

Read-only: the library is opened read-only, and of a seeded random sample of its JPEGs (3,000 by default) only the bytes
before the first scan and the last 64 are read, each read bounded on a share; nothing is written, to the library, a photo
or a file. The process runs at background priority (its disk reads too) and loads no model, so it may run beside the
owner's apps. Counts only: no path, no name, no tag, no value -- the library is photographs of real people, many of them
minors. Each --db is censused in turn, its counts dated.

    .venv/Scripts/python.exe scripts/census_segments.py --db data/photo_index.db --db data/kr-track.db
    .venv/Scripts/python.exe scripts/census_segments.py --db data/renton_parkrun.db --sample 500 --json
"""
import argparse
import ctypes
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import segment_census  # noqa: E402

#: SetPriorityClass's background mode: low CPU and low disk priority for this process.
BACKGROUND = 0x00100000


def background():
    """This process at background priority, CPU and disk, on Windows; elsewhere nothing."""
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32")
        kernel.SetPriorityClass(kernel.GetCurrentProcess(), BACKGROUND)


def _counted(found):
    return ", ".join("%s %d" % (name, n) for name, n in sorted(found.items())) or "none"


def lines(name, census):
    """The census of the library called `name` as the lines printed: counts only."""
    if "roots_problem" in census:
        return ["%s: %s" % (name, census["roots_problem"]), "No file was read."]
    said = ["%s, counted %s: %d photos, %d JPEGs, %d sampled (seed %d)." % (
                name, census["counted"], census["photos"], census["jpegs"], census["sampled"], census["seed"]),
            "  by extension and kind: " + _counted(census["by_type"]),
            "  looked: " + _counted(census["looked"]),
            "  journal files by state (files, with a patch record): " + ", ".join(
                "%s %d (%d)" % tuple(row) for row in census["journal"]),
            "  header read, ms (the first %d reads): %s" % (census["read_ms"]["timed"], _counted(
                {k: v for k, v in census["read_ms"].items() if k != "timed"}))]
    for title, key in (("segments and fields", "counts"), ("XMP toolkits", "toolkits"), ("segment orders", "orders")):
        said.append("  %s:" % title)
        said.extend("    %s: %d" % (label, n) for label, n in sorted(census[key].items()))
    said.append("  MakerNotes by maker (offsets):")
    said.extend("    %s: %s" % (vendor, _counted(found)) for vendor, found in sorted(census["makers"].items()))
    return said


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, action="append", help="a library's database file (more than once: each)")
    parser.add_argument("--sample", type=int, default=segment_census.SAMPLE,
                        help="JPEGs to read, at most (default %(default)s)")
    parser.add_argument("--seed", type=int, default=segment_census.SEED, help="the draw's seed (default %(default)s)")
    parser.add_argument("--json", action="store_true", help="print the counts as JSON")
    args = parser.parse_args(argv)
    background()
    answers, failed = {}, False
    for db_path in args.db:
        name = os.path.splitext(os.path.basename(db_path))[0]
        if not os.path.isfile(db_path):
            print("%s: there is no library at the path given." % name)
            failed = True
            continue
        try:
            answers[name] = segment_census.census(Library(db_path), args.sample, args.seed)
        except Refused as why:
            print("%s: %s" % (name, why))
            failed = True
            continue
        failed = failed or "roots_problem" in answers[name]
        if not args.json:
            print("\n".join(lines(name, answers[name])))
    if args.json:
        print(json.dumps(answers, indent=2, sort_keys=True))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
