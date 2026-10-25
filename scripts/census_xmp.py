"""Count what a library's photo files hold of XMP, before anything is baked into them (docs/PHASE6_DESIGN.md, 3.5).

Read-only: one ExifTool reads a sample of the photos spread over the library's folders (3,000 by default, so the two small libraries are read whole; every photo
of a smaller library) and this prints, by extension, how many hold MWG face regions, Microsoft Photo regions, Extended
XMP, two XMP blocks, how big their XMP block is and the Orientation they say (tagpup.services.inspect.xmp_census).
Counts only: no path, no name, no value of a file is printed -- the library is photographs of real people, many of
them minors. Nothing is written, to the library or to a file.

    .venv/Scripts/python.exe scripts/census_xmp.py --db data/photo_index.db
    .venv/Scripts/python.exe scripts/census_xmp.py --db data/kr-track.db --sample 5000 --json

A share that stops answering ends the read: what was read is printed, and the files not read are said, not counted.
"""
import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup import runtime  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import NotFound, Refused  # noqa: E402
from tagpup.services import inspect  # noqa: E402

FLAGS = (("mwg_regions", "MWG regions"), ("mp_regions", "MP regions"), ("extended_xmp", "Extended XMP"),
         ("duplicate_xmp", "two XMP blocks"))


def lines(census):
    """The census as the lines printed: counts only."""
    if "roots_problem" in census:
        return [census["roots_problem"], "No file was read."]
    said = ["Photos in the library: %d; read of them: %d (spread over its folders by path)."
            % (census["photos"], census["sampled"])]
    if not census["sampled"]:
        return said + ["The library holds no photos: there is nothing to count."]
    said.append("Read: %d; unreadable: %d; not answered: %d."
                % (census["read"], census["unreadable"], census["not_answered"]))
    if census["stopped"]:
        said.append("ExifTool stopped answering (a share asleep?): the counts below are of the %d files read; the %d "
                    "not answered are not counted as holding anything or nothing." % (census["read"], census["not_answered"]))
    for extension, tally in sorted(census["by_extension"].items()):
        said.append("")
        said.append("%s: %d (read %d, unreadable %d, not answered %d)" % (
            extension or "(no extension)", tally["n"], tally["read"], tally["unreadable"], tally["not_answered"]))
        said.append("  with " + ", ".join("%s %d" % (label, tally[name]) for name, label in FLAGS))
        said.append("  XMP block: " + ", ".join("%s %d" % (name, n) for name, n in tally["xmp_size"].items()))
        said.append("  Orientation: " + ", ".join("%s: %d" % (name, n) for name, n in sorted(tally["orientation"].items())))
    return said


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="the library's database file")
    parser.add_argument("--sample", type=int, default=inspect.CENSUS_SAMPLE,
                        help="photos to read, spread over the folders (default %(default)s)")
    parser.add_argument("--json", action="store_true", help="print the counts as JSON")
    args = parser.parse_args(argv)
    if not os.path.exists(args.db):
        print("There is no library at the path given.")
        return 2
    library = Library(args.db)
    # The ExifTool session logs a file it cannot read by its path: nothing but the counts reaches the console.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        census = inspect.xmp_census(library, args.sample, runtime.exiftool(library, runtime.peek_settings(library)))
    except (NotFound, Refused) as why:
        print(why)
        return 2
    finally:
        logging.disable(previous)
    print(json.dumps(census, indent=2) if args.json else "\n".join(lines(census)))
    return 2 if "roots_problem" in census else 0


if __name__ == "__main__":
    sys.exit(main())
