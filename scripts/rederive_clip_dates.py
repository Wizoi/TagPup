"""Date each clip's row again from the metadata it holds, by the rule the index reads by now: a clip's XMP date first,
then its QuickTime ones (DECISIONS 2026-11-08, A5 (D) REVISED). A one-off: the clips indexed before that rule hold an
XMP date they were not dated by (photo_index 80 clip rows, Adobe's wrapping; the other two libraries none; counted
2026-11-08). A clip indexed or saved afterwards is dated by the rule already (the indexer, follow_fields), with no
command.

No file is read or written. Dry run by default: the counts and a rehearsal. With --apply, one change of the library's
journal (History lists it; `tagpup_cli.py undo <id>` takes it back, the dates as they were); each row written only while
it is what the plan read. Counts only: no path, no name, no date. Each --db in turn.

    .venv/Scripts/python.exe scripts/rederive_clip_dates.py --db data/photo_index.db --db data/kr-track.db
    .venv/Scripts/python.exe scripts/rederive_clip_dates.py --db data/photo_index.db --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import maintenance, refresh_rows  # noqa: E402


def counted(counts):
    return ", ".join("%s %d" % (what, n) for what, n in counts.items())


def run(db_path, apply):
    """The lines said of one library, and whether it went as asked."""
    name = os.path.splitext(os.path.basename(db_path))[0]
    if not os.path.isfile(db_path):
        return ["%s: there is no library at the path given." % name], False
    library = Library(db_path)
    result = refresh_rows.redate_clips(library, apply=apply)
    said = ["%s: %s." % (name, counted(result.details["counts"]))]
    said += maintenance.failed(result) + maintenance.skipped(result)
    if result.refused:
        said.append("Refused: %s" % result.refused)
    if not apply:
        return said + [maintenance.rehearsed(result)], result.ok
    left, _rows = refresh_rows.clips_apart(library)
    said += ["Written: %d row(s). %s" % (result.changed, maintenance.recorded(result, db_path)),
             "Read again: %d clip(s) still apart." % left["apart"]]
    # A skipped row was saved meanwhile and is dated by the rule already, so it is not counted apart.
    return said, result.ok and left["apart"] == 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, action="append", help="a library's database file (more than once: each)")
    parser.add_argument("--apply", action="store_true", help="write the change; without it, count and rehearse")
    args = parser.parse_args(argv)
    every = True
    for db_path in args.db:
        said, ok = run(db_path, args.apply)
        print("\n".join(said))
        every = every and ok
    return 0 if every else 1


if __name__ == "__main__":
    sys.exit(main())
