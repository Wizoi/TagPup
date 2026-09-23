"""Rewrite every stored photo path in one spelling: paths.stored().

Until paths.py existed, several components each spelled paths their own way. The
indexer stored whatever folder string it was handed joined with "\\", so a folder
typed with forward slashes produced rows like `D:/Pictures/Meets\\01.jpg`; the
servers' save-with-rename inserted a fresh `D:/Pictures/...` row beside the real one
instead of moving it. The result, per library:

* rows whose only problem is spelling -- rewritten in place, the photo row and its
  faces together;
* twins: one file under two spellings. The real row holds the embedding and the
  faces; the stub beside it holds whatever a later save wrote. They are merged into
  one row at the stored spelling: embedding, document_id and faces from whichever
  has them, and tags/people/captions/metadata from the row that matches the file on
  disk now (size and mtime), since that is the one that was written last. If both
  twins carry faces the merge is not guessed at -- the pair is reported and left.
* embedding_cache entries the same way: one per file, the one matching the file.

Dry run by default: prints what it would change and changes nothing.

    .venv/Scripts/python.exe scripts/canonicalize_paths.py --db data/kr-track.db
    .venv/Scripts/python.exe scripts/canonicalize_paths.py --db data/kr-track.db --apply

--apply backs the database up first (SQLite backup API, into backups/) and reports
rows actually changed, not rows it meant to change. Stop the servers first: they
hold these rows in memory.
"""
import argparse
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db as tagpup_db  # noqa: E402
import paths  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def file_matches(path, mtime, size):
    """Does this row describe the file as it is on disk now?"""
    try:
        stat = os.stat(path)
    except OSError:
        return False
    return size == stat.st_size and mtime is not None and abs(mtime - stat.st_mtime) < 0.01


def plan_photos(conn):
    """Decide what happens to every photos row whose path is not in stored form."""
    rows = conn.execute(
        "SELECT path, mtime, size, embedding IS NOT NULL, document_id FROM photos").fetchall()
    face_counts = dict(conn.execute(
        "SELECT photo_path, COUNT(*) FROM faces GROUP BY photo_path").fetchall())

    groups = defaultdict(list)
    for path, mtime, size, has_emb, doc_id in rows:
        groups[paths.key(path)].append({
            "path": path, "mtime": mtime, "size": size, "has_emb": bool(has_emb),
            "doc_id": doc_id, "faces": face_counts.get(path, 0),
        })

    renames, merges, conflicts = [], [], []
    for members in groups.values():
        target = paths.stored(members[0]["path"])
        if len(members) == 1:
            if members[0]["path"] != target:
                renames.append((members[0]["path"], target))
            continue
        with_faces = [m for m in members if m["faces"]]
        duplicate_faces = []
        if len(with_faces) > 1:
            # Both spellings were indexed, so the photo's faces were detected twice.
            # Where every extra face is the same detection as one on the keeper --
            # same box, and no conflicting name -- the extra is a duplicate and goes.
            # Anything else needs a person to decide.
            keeper, duplicate_faces = same_detections(conn, with_faces)
            if keeper is None:
                conflicts.append(members)
                continue
        else:
            # The row that keeps its identity: the one with faces, else an embedding.
            keeper = (with_faces or [m for m in members if m["has_emb"]] or members)[0]
        current = [m for m in members if file_matches(target, m["mtime"], m["size"])]
        # Metadata from the row matching the file; failing that, the newest.
        source = (current or sorted(members, key=lambda m: m["mtime"] or 0))[-1]
        merges.append({"target": target, "keeper": keeper, "metadata_from": source,
                       "members": members, "duplicate_faces": duplicate_faces})
    return renames, merges, conflicts


def same_detections(conn, with_faces):
    """(keeper, [(duplicate face id, keeper face id)]) or (None, []) if not safe.

    The keeper is the spelling whose faces carry the most decisions (names, manual
    choices, exclusions), then the one detected first.
    """
    faces = {}
    for member in with_faces:
        faces[member["path"]] = conn.execute(
            "SELECT id, box, name, name_source, excluded FROM faces WHERE photo_path = ?"
            " ORDER BY id", (member["path"],)).fetchall()

    def decisions(member):
        rows = faces[member["path"]]
        return (-sum(1 for r in rows if r[2] or r[3] or r[4]), min(r[0] for r in rows))

    keeper = sorted(with_faces, key=decisions)[0]
    by_box = {}
    for row in faces[keeper["path"]]:
        by_box.setdefault(row[1], row)
    pairs = []
    for member in with_faces:
        if member is keeper:
            continue
        for face_id, box, name, source, excluded in faces[member["path"]]:
            twin = by_box.get(box)
            if twin is None:
                return None, []
            if name and twin[2] and name != twin[2]:
                return None, []
            if (name or source or excluded) and not (twin[2] or twin[3] or twin[4]):
                # The duplicate holds the decision and the keeper's copy does not:
                # carrying it over is more than dropping a duplicate. Leave it.
                return None, []
            pairs.append((face_id, twin[0]))
    return keeper, pairs


def plan_cache(conn):
    rows = conn.execute("SELECT path, mtime, size FROM embedding_cache").fetchall()
    groups = defaultdict(list)
    for path, mtime, size in rows:
        groups[paths.key(path)].append((path, mtime, size))
    renames, merges = [], []
    for members in groups.values():
        target = paths.stored(members[0][0])
        if len(members) == 1:
            if members[0][0] != target:
                renames.append((members[0][0], target))
            continue
        current = [m for m in members if file_matches(target, m[1], m[2])]
        keeper = (current or sorted(members, key=lambda m: m[1] or 0))[-1]
        merges.append((target, keeper[0], [m[0] for m in members if m[0] != keeper[0]]))
    return renames, merges


def plan_orphan_faces(conn):
    """faces.photo_path spellings with no photos row (e.g. faces the suggester saved
    for photos that were never indexed). Rewritten to stored form like the rest."""
    return [(p, paths.stored(p)) for (p,) in conn.execute(
        "SELECT DISTINCT photo_path FROM faces WHERE photo_path NOT IN (SELECT path FROM photos)")
        if p != paths.stored(p)]


def apply(conn, photo_renames, merges, cache_renames, cache_merges, orphan_faces):
    changed = defaultdict(int)
    # Parent and child keys change together; the constraint is checked at the end.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        for old, new in photo_renames:
            changed["photos renamed"] += conn.execute(
                "UPDATE photos SET path = ? WHERE path = ?", (new, old)).rowcount
            changed["faces re-pointed"] += conn.execute(
                "UPDATE faces SET photo_path = ? WHERE photo_path = ?", (new, old)).rowcount
        for old, new in orphan_faces:
            changed["unindexed faces re-spelled"] += conn.execute(
                "UPDATE faces SET photo_path = ? WHERE photo_path = ?", (new, old)).rowcount

        for merge in merges:
            keeper, source, target = merge["keeper"], merge["metadata_from"], merge["target"]
            if source["path"] != keeper["path"]:
                changed["twins: newer metadata carried over"] += conn.execute(
                    "UPDATE photos SET (mtime, size, tags, people, captions, raw_metadata) ="
                    " (SELECT mtime, size, tags, people, captions, raw_metadata"
                    "  FROM photos WHERE path = ?) WHERE path = ?",
                    (source["path"], keeper["path"])).rowcount
            for duplicate_id, _keeper_face_id in merge["duplicate_faces"]:
                changed["duplicate faces removed"] += conn.execute(
                    "DELETE FROM faces WHERE id = ?", (duplicate_id,)).rowcount
            for member in merge["members"]:
                if member["path"] == keeper["path"]:
                    continue
                if not keeper["has_emb"] and member["has_emb"]:
                    conn.execute(
                        "UPDATE photos SET embedding = (SELECT embedding FROM photos WHERE path = ?)"
                        " WHERE path = ?", (member["path"], keeper["path"]))
                if not keeper["doc_id"] and member["doc_id"]:
                    conn.execute("UPDATE photos SET document_id = ? WHERE path = ?",
                                 (member["doc_id"], keeper["path"]))
                changed["faces re-pointed"] += conn.execute(
                    "UPDATE faces SET photo_path = ? WHERE photo_path = ?",
                    (keeper["path"], member["path"])).rowcount
                changed["twins: stub rows removed"] += conn.execute(
                    "DELETE FROM photos WHERE path = ?", (member["path"],)).rowcount
            if keeper["path"] != target:
                changed["photos renamed"] += conn.execute(
                    "UPDATE photos SET path = ? WHERE path = ?", (target, keeper["path"])).rowcount
                changed["faces re-pointed"] += conn.execute(
                    "UPDATE faces SET photo_path = ? WHERE photo_path = ?",
                    (target, keeper["path"])).rowcount

        for target, keep, drop in cache_merges:
            for path in drop:
                changed["embedding_cache duplicates removed"] += conn.execute(
                    "DELETE FROM embedding_cache WHERE path = ?", (path,)).rowcount
            if keep != target:
                cache_renames.append((keep, target))
        for old, new in cache_renames:
            changed["embedding_cache renamed"] += conn.execute(
                "UPDATE embedding_cache SET path = ? WHERE path = ?", (new, old)).rowcount

        violations = conn.execute("PRAGMA foreign_key_check(faces)").fetchall()
        # Faces for photos never indexed have no parent row by design; only a face whose
        # parent this run renamed away would be new, and those were re-pointed above.
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return changed, violations


def backup(db_path):
    os.makedirs(os.path.join(REPO_ROOT, "backups"), exist_ok=True)
    target = os.path.join(REPO_ROOT, "backups", "%s.before-canonicalize-%s.db" % (
        os.path.splitext(os.path.basename(db_path))[0], time.strftime("%Y%m%d_%H%M%S")))
    source = tagpup_db.connect(db_path)
    destination = tagpup_db.connect(target)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return target


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True)
    parser.add_argument("--apply", action="store_true", help="write; the default is a dry run")
    parser.add_argument("--show", type=int, default=5, help="examples to print per category")
    args = parser.parse_args(argv)

    conn = tagpup_db.connect(args.db, isolation_level=None)
    try:
        photo_renames, merges, conflicts = plan_photos(conn)
        cache_renames, cache_merges = plan_cache(conn)
        orphan_faces = plan_orphan_faces(conn)

        print("%s\n" % args.db)
        print("photos re-spelled in place : %d" % len(photo_renames))
        for old, new in photo_renames[:args.show]:
            print("    %s\n -> %s" % (old, new))
        print("twins to merge             : %d  (duplicate faces dropped: %d)"
              % (len(merges), sum(len(m["duplicate_faces"]) for m in merges)))
        for merge in merges[:args.show]:
            print("    %s" % merge["target"])
            for m in merge["members"]:
                role = []
                if m is merge["keeper"]:
                    role.append("kept (identity)")
                if m is merge["metadata_from"]:
                    role.append("metadata")
                print("      %-26s faces=%-3d embedding=%-5s %s"
                      % (", ".join(role) or "removed", m["faces"], m["has_emb"], m["path"]))
        print("twins left alone (both have faces): %d" % len(conflicts))
        for members in conflicts[:args.show]:
            for m in members:
                print("    faces=%-3d %s" % (m["faces"], m["path"]))
        print("unindexed faces re-spelled : %d path(s)" % len(orphan_faces))
        print("embedding_cache re-spelled : %d, duplicates merged: %d"
              % (len(cache_renames), len(cache_merges)))

        if not args.apply:
            print("\nDry run. Nothing was changed. Re-run with --apply to write.")
            return 0

        print("\nbacked up to %s" % backup(args.db))
        changed, violations = apply(conn, photo_renames, merges, cache_renames,
                                    cache_merges, orphan_faces)
        print("\nrows changed:")
        for label, count in sorted(changed.items()):
            print("  %-40s %d" % (label, count))
        if violations:
            print("\nfaces with no photos row after the run: %d (unindexed photos' faces;"
                  " expected)" % len(violations))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
