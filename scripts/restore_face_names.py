"""Put back face names from a backup written before re-clustering -- only where nothing replaced them.

A backup lists every face with the name it had then, unnamed faces included. Writing
it back wholesale, as this script used to, set every face named since the backup back
to nothing (748 on the main library, one day later) and overwrote names that had been
corrected. So a name is restored only to a face that is unnamed now and that nobody
has decided about since:

* named now, same name          -- nothing to do;
* named now, different name     -- a conflict: listed, left as it is;
* unnamed, name_source 'manual' -- a person said "nobody": left;
* excluded                      -- kept out of identity work: left;
* gone from the database        -- counted, skipped;
* unnamed in the backup too     -- nothing to restore; never un-names anything.

Each photo whose faces gain a name has its people list re-derived (keywords plus
named faces, metadata.photo_people).

Dry run by default:

    .venv/Scripts/python.exe scripts/restore_face_names.py data/face_names_backup_<ts>.json
    .venv/Scripts/python.exe scripts/restore_face_names.py data/face_names_backup_<ts>.json --apply

--apply backs the database up first and reports rows actually changed.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as tagpup_db  # noqa: E402



def plan(conn, backup_faces):
    """(face id -> name to restore, Counter of outcomes, [conflicts])."""
    current = {fid: (name, source, excluded) for fid, name, source, excluded in conn.execute(
        "SELECT id, name, name_source, COALESCE(excluded, 0) FROM faces")}
    restore, outcome, conflicts = {}, Counter(), []
    for face_id, old_name in backup_faces:
        if not old_name:
            outcome["unnamed in the backup"] += 1
            continue
        if face_id not in current:
            outcome["no longer in the database"] += 1
            continue
        name, source, excluded = current[face_id]
        if name == old_name:
            outcome["already carries that name"] += 1
        elif name:
            outcome["named differently since (left)"] += 1
            conflicts.append((face_id, old_name, name))
        elif excluded:
            outcome["excluded since (left)"] += 1
        elif source == "manual":
            outcome["marked 'nobody' by a person (left)"] += 1
        else:
            restore[face_id] = old_name
            outcome["to restore"] += 1
    return restore, outcome, conflicts


def apply(db_path, restore):
    from metadata import photo_people

    def store(conn):
        named = 0
        photos = set()
        for face_id, name in restore.items():
            cur = conn.execute(
                "UPDATE faces SET name = ? WHERE id = ? AND name IS NULL"
                " AND COALESCE(excluded, 0) = 0 AND name_source IS NOT 'manual'",
                (name, face_id))
            if cur.rowcount:
                named += cur.rowcount
                photos.add(conn.execute("SELECT photo_path FROM faces WHERE id = ?",
                                        (face_id,)).fetchone()[0])
        people_rows = 0
        for photo in photos:
            row = conn.execute("SELECT tags, raw_metadata FROM photos WHERE path = ?",
                               (photo,)).fetchone()
            if not row:
                continue   # faces saved for a photo that was never indexed
            people = photo_people(json.loads(row[1] or "{}"), json.loads(row[0] or "[]"),
                                  photo, db_path=db_path, conn=conn)
            people_rows += conn.execute("UPDATE photos SET people = ? WHERE path = ?",
                                        (json.dumps(people), photo)).rowcount
        return named, people_rows
    return tagpup_db.write_with_connection(db_path, store, label="restore face names")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("backup_file")
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--show", type=int, default=10, help="conflicts to list")
    args = parser.parse_args(argv)

    with open(args.backup_file, encoding="utf-8") as fh:
        payload = json.load(fh)
    db_path = payload["db"]
    print("%s (backup taken %s)\n" % (db_path, payload.get("taken", "?")))

    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    try:
        restore, outcome, conflicts = plan(conn, payload["faces"])
    finally:
        conn.close()
    for label, count in outcome.most_common():
        print("  %-40s %d" % (label, count))
    if conflicts:
        print("\nnamed differently since the backup (left as they are now):")
        by_pair = defaultdict(int)
        for _, old, new in conflicts:
            by_pair[(old, new)] += 1
        for (old, new), count in sorted(by_pair.items(), key=lambda kv: -kv[1])[:args.show]:
            print("  %3d face(s): backup %r, now %r" % (count, old, new))

    if not args.apply:
        print("\nDry run. Nothing was changed. Re-run with --apply to write.")
        return 0
    if not restore:
        print("\nNothing to restore.")
        return 0
    print("\nbacked up to %s" % tagpup_db.backup(db_path, "restore-names"))
    named, people_rows = apply(db_path, restore)
    print("faces given back their name: %d" % named)
    print("photos whose people list was re-derived: %d" % people_rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
