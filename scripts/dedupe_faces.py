"""Remove face rows that describe a face already recorded for the same photo.

Two paths can record the same face. TagPup detects faces when it shows a photo's
Detected Faces strip and saves any it does not already have; the indexer detects them
during a pass. Normally the second one finds the first and skips, because both look up
by photo path.

They stop agreeing when a photo's path changes. A folder renamed outside TagPup was
browsed under its new names -- so faces were saved against those -- and the old index
rows were later re-pointed at the same files, bringing their own faces with them.
Both sets are now filed under one path, and every face in that folder shows twice.

The copies are not equal. The older row carries the curation: names assigned by hand,
faces deliberately excluded. The newer one is a bare re-detection. So the rule is keep
whichever row knows something, and a group whose copies disagree about a name is left
alone for a person to look at rather than resolved by guesswork.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _root  # noqa: E402,F401
import db as tagpup_db  # noqa: E402
import paths  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402


def knows_something(row):
    """How much curation a face row carries. Higher wins.

    A decision a person made outranks everything: it is the one thing clustering
    cannot make again. A name used to count for most, so a name clustering gave beat
    a face somebody had marked nobody, and the decision was the row thrown away.
    """
    _id, _path, _box, name, name_source, excluded = row
    score = 0
    if name_source == "manual":
        score += 4
    if excluded:
        score += 2
    if name:
        score += 1
    return score


def plan_for(db_path):
    """Which face rows duplicate another, and which copy to keep."""
    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    try:
        rows = store_faces.decisions(conn)
    finally:
        conn.close()

    # By paths.key: the same box on the same file is one face however each copy
    # spelled the photo's path.
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(paths.key(row[1]), str(row[2]))].append(row)

    redundant, disputed = [], []
    for copies in groups.values():
        if len(copies) < 2:
            continue
        photo_path = copies[0][1]

        names = {r[3] for r in copies if r[3]}
        if len(names) > 1:
            # Two names for one face is a disagreement, not a duplicate.
            disputed.append((photo_path, sorted(names)))
            continue
        if names and any(r[5] for r in copies):
            # So is a name beside "not a person". Keeping either throws the other
            # away, and the exclusion used to be the one that went.
            disputed.append((photo_path, sorted(names) + ["(excluded)"]))
            continue

        # Best-curated first; oldest id breaks a tie, since it is the one every other
        # table and every bookmark already refers to.
        copies.sort(key=lambda r: (-knows_something(r), r[0]))
        for row in copies[1:]:
            redundant.append(row)

    return redundant, disputed


def apply_plan(db_path, redundant):
    ids = [row[0] for row in redundant]

    def remove(conn):
        return store_faces.delete(conn, ids)

    return tagpup_db.write_with_connection(
        db_path, remove, label="remove %d duplicate face row(s)" % len(ids))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/kr-track.db")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    redundant, disputed = plan_for(args.db)

    print("%s\n" % args.db)
    print("duplicate face rows to remove: %d" % len(redundant))
    named = sum(1 for r in redundant if r[3])
    excluded = sum(1 for r in redundant if r[5])
    print("   of those, carrying a name  : %d" % named)
    print("   of those, marked excluded  : %d" % excluded)
    if named or excluded:
        print("   (kept copies carry at least as much, or this would not be a duplicate)")

    for row in redundant[:4]:
        print("   id=%-7d %s" % (row[0], os.path.basename(str(row[1]))[:60]))

    print("\nfaces two rows disagree about: %d (left alone)" % len(disputed))
    for photo_path, names in disputed[:5]:
        print("   %-50s %s" % (os.path.basename(str(photo_path))[:50], " vs ".join(names)))

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return
    if not redundant:
        print("\nNothing to remove.")
        return

    print("\nbacked up to %s" % tagpup_db.backup(args.db, "dedupe-faces"))

    removed = apply_plan(args.db, redundant)
    print("\nremoved %d row(s)." % removed)

    left, still_disputed = plan_for(args.db)
    print("duplicates remaining: %d; disagreements remaining: %d"
          % (len(left), len(still_disputed)))


if __name__ == "__main__":
    main()
