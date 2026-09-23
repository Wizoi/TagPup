"""Point index rows at photos that were renamed under them.

Renaming a photo outside TagPup leaves its index row behind: the row still names the
old file, which no longer exists, so the photo looks unindexed while its row looks
dead. Both halves are wrong, and the row is the valuable half -- it carries the
photo's embedding and its faces, including the names somebody assigned by hand.

Deleting those rows is the obvious move and the expensive one. In this library 78
dead rows in kr-track hold 234 faces, 88 of them named: an afternoon of identifying
people, thrown away to tidy up a path.

They can be re-pointed instead, because TagPup's renamer records where a file came
from in `XMP-xmpMM:PreservedFileName`. Matching that against the stem of each dead
row's filename reconnects the row to the file:

    row:  .../2Z6A5820.jpg          (no such file)
    file: .../Meet - 01.jpg         PreservedFileName = 2Z6A5820.CR3
    ->    the row now names Meet - 01.jpg, with its faces and embedding intact

Extensions are ignored on both sides: the preserved name is usually the RAW original
(.CR3) while the indexed file was the JPEG derived from it.

Run with --apply to write. Without it, nothing is changed and the plan is printed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from . import db as tagpup_db
    from . import paths as photo_paths
except ImportError:  # imported as a top-level module
    import db as tagpup_db
    import paths as photo_paths   # not `paths`: the walks below use that name


IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".heic", ".webp")


def stem_of(path):
    return os.path.splitext(os.path.basename(str(path)))[0].strip().lower()


def dead_rows(conn):
    """Index rows whose file is not on disk, keyed by the folder they claim."""
    rows = []
    for (path,) in conn.execute("SELECT path FROM photos"):
        if path and not os.path.exists(path):
            rows.append(path)
    return rows


def identities(folder, exiftool_path=None):
    """Every photo in a folder, keyed by its DocumentID.

    The better of the two signals, and the one that survives what the other does not:
    a move between folders, a rename by a tool that knows nothing about TagPup, a
    filename that collides with another photo's original. PreservedFileName only ever
    worked for renames TagPup itself performed.
    """
    import exiftool

    paths = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith(IMAGE_SUFFIXES):
                paths.append(os.path.join(root, name))

    by_id = {}
    if not paths:
        return by_id

    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
        for i in range(0, len(paths), 100):
            batch = paths[i:i + 100]
            try:
                results = et.get_tags(batch, tags=["XMP-xmpMM:DocumentID"])
            except Exception:
                results = []
                for one in batch:
                    try:
                        results.extend(et.get_tags([one], tags=["XMP-xmpMM:DocumentID"]))
                    except Exception:
                        continue
            for row in results:
                doc_id = row.get("XMP:DocumentID") or row.get("XMP-xmpMM:DocumentID")
                source = row.get("SourceFile")
                if not doc_id or not source:
                    continue
                key = str(doc_id).strip()
                # Two files claiming one identity is a copy, not a rename; neither can
                # be matched to a row without guessing which.
                # Stored form: this is what a row is re-pointed at, and ExifTool
                # answers with forward slashes.
                by_id[key] = None if key in by_id else photo_paths.stored(source)

    return {k: v for k, v in by_id.items() if v}


def preserved_names(folder, exiftool_path=None):
    """Every photo under a folder, keyed by (its folder's key, the stem it was renamed from).

    Keyed by folder as well as stem: a rename never moves a file, and camera names
    repeat -- a library holds many IMG_0421s -- so a dead row may only be matched to
    a renamed file beside it. Keyed by stem alone, the last folder searched won, and
    a row's named faces could be re-pointed at a stranger's photo in another folder.
    """
    import exiftool

    paths = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if name.lower().endswith(IMAGE_SUFFIXES):
                paths.append(os.path.join(root, name))

    by_original = {}
    if not paths:
        return by_original

    with exiftool.ExifToolHelper(executable=exiftool_path) as et:
        for i in range(0, len(paths), 100):
            batch = paths[i:i + 100]
            try:
                results = et.get_tags(batch, tags=["XMP-xmpMM:PreservedFileName"])
            except Exception:
                results = []
                for one in batch:
                    try:
                        results.extend(
                            et.get_tags([one], tags=["XMP-xmpMM:PreservedFileName"]))
                    except Exception:
                        continue
            for row in results:
                original = row.get("XMP:PreservedFileName")
                source = row.get("SourceFile")
                if not original or not source:
                    continue
                key = (photo_paths.key(os.path.dirname(photo_paths.stored(source))),
                       stem_of(original))
                # Two files claiming one original cannot be told apart; leave both.
                by_original[key] = None if key in by_original else photo_paths.stored(source)

    return {k: v for k, v in by_original.items() if v}


def merge_unambiguous(into, found):
    """Add `found` to `into`, dropping any key two different files claim.

    Folders are walked recursively, so one file can turn up from two walks -- that
    is the same claim twice. Two different files claiming one key is a copy, not a
    rename, and neither can be matched without guessing.
    """
    for key, path in found.items():
        if key in into and (into[key] is None or not photo_paths.same(into[key], path)):
            into[key] = None
        else:
            into[key] = path


def plan_for(db_path, exiftool_path=None):
    """Which dead rows can be re-pointed, and to what."""
    conn = tagpup_db.connect(tagpup_db.readonly_uri(db_path), uri=True)
    dead = dead_rows(conn)

    folders = sorted({os.path.dirname(p) for p in dead if os.path.isdir(os.path.dirname(p))})
    lookup = {}
    by_identity = {}
    for folder in folders:
        merge_unambiguous(lookup, preserved_names(folder, exiftool_path))
        merge_unambiguous(by_identity, identities(folder, exiftool_path))

    # A dead row's own identity, where indexing recorded one.
    row_identity = {}
    try:
        for path, doc_id in conn.execute(
                "SELECT path, document_id FROM photos WHERE document_id IS NOT NULL"):
            if path and doc_id:
                row_identity[path] = str(doc_id).strip()
    except Exception:
        pass   # a database indexed before identities existed

    live = set()
    for (path,) in conn.execute("SELECT path FROM photos"):
        if path and os.path.exists(path):
            live.add(photo_paths.key(path))

    moves, unmatched = [], []
    claimed = set()
    for old in dead:
        # Identity first: it survives a move and a rename by any tool. The preserved
        # filename is the fallback, and only ever worked for TagPup's own renames.
        new = (by_identity.get(row_identity.get(old, ""))
               or lookup.get((photo_paths.key(os.path.dirname(old)), stem_of(old))))
        if not new:
            unmatched.append(old)
            continue
        key = photo_paths.key(new)
        # Never point two rows at one file, and never collide with a row already there.
        if key in live or key in claimed:
            unmatched.append(old)
            continue
        claimed.add(key)
        faces = conn.execute(
            "SELECT COUNT(*) FROM faces WHERE photo_path = ?", (old,)).fetchone()[0]
        named = conn.execute(
            "SELECT COUNT(*) FROM faces WHERE photo_path = ? AND name IS NOT NULL",
            (old,)).fetchone()[0]
        moves.append({"from": old, "to": new, "faces": faces, "named": named})

    conn.close()
    return moves, unmatched


def apply_moves(db_path, moves):
    def store(conn):
        # Rows changed, not rows planned: a plan that matches nothing must say so.
        cursor = conn.cursor()
        photos = faces = 0
        for move in moves:
            photos += cursor.execute("UPDATE photos SET path = ? WHERE path = ?",
                                     (move["to"], move["from"])).rowcount
            faces += cursor.execute("UPDATE faces SET photo_path = ? WHERE photo_path = ?",
                                    (move["to"], move["from"])).rowcount
        return photos, faces

    return tagpup_db.write_with_connection(db_path, store, label="relink renamed photos")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/kr-track.db")
    parser.add_argument("--exiftool", default=None)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes; without this nothing is modified")
    args = parser.parse_args()

    moves, unmatched = plan_for(args.db, args.exiftool)

    print("%s\n" % args.db)
    print("rows that can be re-pointed: %d" % len(moves))
    print("   faces they carry: %d (%d named)"
          % (sum(m["faces"] for m in moves), sum(m["named"] for m in moves)))
    for move in moves[:4]:
        print("   %-28s -> %-46s (%d faces, %d named)"
              % (os.path.basename(move["from"]), os.path.basename(move["to"])[:46],
                 move["faces"], move["named"]))
    if len(moves) > 4:
        print("   ... and %d more" % (len(moves) - 4))

    print("\ndead rows with no renamed file to match: %d" % len(unmatched))
    for path in unmatched[:5]:
        print("   %s" % os.path.basename(path))

    if not args.apply:
        print("\nNothing was changed. Re-run with --apply to write it.")
        return
    if not moves:
        print("\nNothing to re-point.")
        return

    photos, faces = apply_moves(args.db, moves)
    print("\nre-pointed %d of %d planned row(s), and %d face(s)." % (photos, len(moves), faces))

    remaining, still_unmatched = plan_for(args.db, args.exiftool)
    print("rows still re-pointable: %d; dead rows remaining: %d"
          % (len(remaining), len(still_unmatched)))


if __name__ == "__main__":
    main()
