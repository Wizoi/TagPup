"""Derive each photo's stored captions (and tags) again from the metadata its row holds, by the rule the index reads by
now: XMP's copy when the file holds one, IPTC's only when it holds none (DECISIONS 2026-11-03, XMP IS THE COPY THE INDEX
READS). A one-off: the rows stored before that rule put IPTC's caption first (photo_index 383 rows, 104 of them showing
another caption; kr-track and renton_parkrun none; counted 2026-11-03), and a save from such a photo is refused as
changed on disk. A photo indexed or saved afterwards is derived by the rule already (the indexer, follow_fields).

No file is read or written. Dry run by default: the counts and a rehearsal. With --apply, one change of the library's
journal (History lists it; `tagpup_cli.py undo <id>` takes it back), the search words with it; each row written only
while it is what the plan read. Counts only: no path, no name, no caption. Each --db in turn.

    .venv/Scripts/python.exe scripts/rederive_captions.py --db data/photo_index.db --db data/kr-track.db
    .venv/Scripts/python.exe scripts/rederive_captions.py --db data/photo_index.db --apply
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
    result = refresh_rows.rederive(library, apply=apply)
    said = ["%s: %s." % (name, counted(result.details["counts"]))]
    said += maintenance.failed(result) + maintenance.skipped(result)
    if result.refused:
        said.append("Refused: %s" % result.refused)
    if not apply:
        return said + [maintenance.rehearsed(result)], result.ok
    left, _rows = refresh_rows.apart(library)
    said += ["Written: %d row(s). %s" % (result.changed, maintenance.recorded(result, db_path)),
             "Read again: %d row(s) still apart." % left["apart"]]
    return said, result.ok and left["apart"] == len(result.skipped)


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
