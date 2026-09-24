"""Whether a library keeps its own rules. Reads only; changes nothing.

    .venv/Scripts/python.exe tools/doctor.py --db data/photo_index.db
    .venv/Scripts/python.exe tools/doctor.py --db data/photo_index.db --show 5

Prints what the library holds, then each rule (tagpup.store.checks) with how many rows
break it, then the rows whose file is not on disk, by folder. Counts only, unless
--show asks for examples: they are paths and tags, and paths name people.

Exits 1 when a rule is broken, else 0. Missing files do not count against it: a folder
on an unplugged drive looks the same as a deleted one, and removing either is the
owner's choice.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tagpup.store import checks, db  # noqa: E402


def report(db_path, show=0, out=print):
    """Report on the library at `db_path`. Returns the number of rules broken."""
    if not os.path.exists(db_path):
        raise SystemExit("There is no library at %s." % db_path)
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        held = checks.summary(conn)
        results = checks.run(conn)
        missing = checks.missing_files(conn)
    finally:
        conn.close()

    out("library: %s" % db_path)
    out("  " + "  ".join("%s %d" % (name, count) for name, count in held.items()))
    out("")
    broken = 0
    for check in results:
        out("%-48s %s" % (check.name, "ok" if not check.count else check.count))
        if check.count:
            broken += 1
            for example in check.examples[:show]:
                out("    %s" % example)
    out("")
    rows = sum(count for _folder, count, _there in missing)
    gone = [(folder, count) for folder, count, there in missing if not there]
    out("rows whose file is not on disk: %d, in %d folder(s); %d folder(s) are gone entirely"
        % (rows, len(missing), len(gone)))
    for folder, count, there in missing[:show]:
        out("    %6d  %s%s" % (count, folder, "" if there else "  (folder gone)"))
    return broken


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="the library's database file")
    parser.add_argument("--show", type=int, default=0, help="examples to list per rule (paths; default none)")
    args = parser.parse_args(argv)
    return 1 if report(args.db, args.show) else 0


if __name__ == "__main__":
    sys.exit(main())
