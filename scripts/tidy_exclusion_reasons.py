"""Fold one-off exclusion reasons into the four the app now offers.

The reason was a free-text prompt, so it collected synonyms: "fuzzy" beside "bad crop",
"wrong person" beside "stranger". Three ways of recording two things, and the Excluded
bucket shows the reason on every face now, so the inconsistency is visible.

    fuzzy         -> bad crop      a blurry crop is a bad crop
    wrong person  -> stranger      excluded because it is not who it was taken for

"ignored cluster" is left alone deliberately. It is not a synonym of anything: it is
set by the app when a whole cluster is excluded in one action, and it says so. Folding
it into "stranger" would discard the one thing it records that the others do not --
that the decision was made about a group rather than a face -- across 829 rows here.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from . import db as tagpup_db
except ImportError:  # imported as a top-level module
    import db as tagpup_db


#: Only synonyms. A reason that records something the four do not is not in here.
SYNONYMS = {
    "fuzzy": "bad crop",
    "blurry": "bad crop",
    "not a clean crop": "bad crop",
    "not a clear crop": "bad crop",
    "wrong person": "stranger",
    "not a face": "not a person",
}

#: Set by the app rather than chosen, and meaning something none of the four do.
SYSTEM_REASONS = {"ignored cluster"}

CANONICAL = {"not a person", "stranger", "bad crop", "duplicate"}


def survey(db_path):
    """Every reason recorded, with how many faces carry it."""
    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    try:
        rows = conn.execute(
            "SELECT excluded_reason, COUNT(*) FROM faces WHERE excluded = 1 "
            "GROUP BY excluded_reason ORDER BY 2 DESC").fetchall()
    finally:
        conn.close()
    return [(r[0], r[1]) for r in rows]


def plan_for(db_path):
    """Which reasons will be folded, and which are left as they are."""
    folding, keeping, unknown = [], [], []
    for reason, count in survey(db_path):
        key = (reason or "").strip().lower()
        if key in SYNONYMS:
            folding.append((reason, SYNONYMS[key], count))
        elif key in CANONICAL or key in SYSTEM_REASONS:
            keeping.append((reason, count))
        else:
            # Something nobody anticipated. Reported, never guessed at.
            unknown.append((reason, count))
    return folding, keeping, unknown


def apply_plan(db_path, folding):
    def store(conn):
        cursor = conn.cursor()
        changed = 0
        for old, new, _count in folding:
            cursor.execute(
                "UPDATE faces SET excluded_reason = ? WHERE excluded = 1 "
                "AND excluded_reason = ?", (new, old))
            changed += cursor.rowcount
        return changed

    return tagpup_db.write_with_connection(
        db_path, store, label="tidy %d exclusion reason(s)" % len(folding))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/kr-track.db")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    folding, keeping, unknown = plan_for(args.db)

    print("%s\n" % args.db)
    print("to fold:")
    for old, new, count in folding:
        print("   %-18s -> %-14s %d face(s)" % (repr(old), new, count))
    if not folding:
        print("   nothing")

    print("\nleft as they are:")
    for reason, count in keeping:
        note = " (set by the app, not chosen)" if (reason or "").strip().lower() in SYSTEM_REASONS else ""
        print("   %-18s %d face(s)%s" % (repr(reason), count, note))

    if unknown:
        print("\nnot recognised, left alone for a person to look at:")
        for reason, count in unknown:
            print("   %-18s %d face(s)" % (repr(reason), count))

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return
    if not folding:
        print("\nNothing to fold.")
        return

    print("\nbacked up to %s" % tagpup_db.backup(args.db, "tidy-exclusions"))

    changed = apply_plan(args.db, folding)
    print("\nrewrote %d face(s)." % changed)
    print("\nafterwards:")
    for reason, count in survey(args.db):
        print("   %-18s %d" % (repr(reason), count))


if __name__ == "__main__":
    main()
