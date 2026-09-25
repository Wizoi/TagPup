"""Point index rows at photos that were renamed under them.

Renaming a photo outside TagPup leaves its index row behind: the row still names the
old file, which no longer exists, so the photo looks unindexed while its row looks
dead. Both halves are wrong, and the row is the valuable half -- it carries the
photo's embedding and its faces, including the names somebody assigned by hand.

Deleting those rows is the obvious move and the expensive one. In one library 78 dead
rows held 234 faces, 88 of them named: an afternoon of identifying people, thrown away
to tidy up a path.

They can be re-pointed instead. A dead row is matched to a file by the identity
indexing recorded for it (the file's DocumentID), else by the name TagPup's renamer
kept in `XMP-xmpMM:PreservedFileName`, matched against the stem of the row's file name
in the same folder -- extensions ignored on both sides, since the preserved name is
usually the RAW original (.CR3) and the indexed file the JPEG made from it.

scripts/relink_renamed_photos.py runs it, on the maintenance scaffold (tagpup.services.
maintenance): a dry run is a rehearsal, and applying records one change of the journal
-- each row's path, as it was and as it is left -- which can be undone. The rows only:
no photo file is written.
"""
import os

from tagpup.core import paths
from tagpup.files import images
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session
from tagpup.services import maintenance
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos

#: What the change is recorded as.
OPERATION = "relink_renamed_photos"


def stem_of(path):
    return os.path.splitext(os.path.basename(str(path)))[0].strip().lower()


def dead_rows(conn):
    """Index rows whose file is not on disk."""
    return [path for path in store_photos.all_paths(conn) if path and not os.path.exists(path)]


def _photos_under(folder):
    found = []
    for root, _dirs, files in os.walk(folder):
        for name in sorted(files):
            if images.is_photo(name):
                found.append(os.path.join(root, name))
    return found


def _read(folder, field, exiftool_path):
    """(SourceFile, row) of every photo under `folder`, read for `field`; a batch that
    fails is read again a photo at a time."""
    found = _photos_under(folder)
    if not found:
        return []
    rows = []
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for i in range(0, len(found), 100):
            batch = found[i:i + 100]
            try:
                rows += et.get_tags(batch, tags=[field])
            except Exception:
                for one in batch:
                    try:
                        rows += et.get_tags([one], tags=[field])
                    except Exception:
                        continue
    return rows


def identities(folder, exiftool_path=None):
    """Every photo in a folder, keyed by its DocumentID.

    The better of the two signals, and the one that survives what the other does not:
    a move between folders, a rename by a tool that knows nothing about TagPup, a
    filename that collides with another photo's original. PreservedFileName only ever
    worked for renames TagPup itself performed.
    """
    by_id = {}
    for row in _read(folder, "XMP-xmpMM:DocumentID", exiftool_path):
        doc_id = row.get("XMP:DocumentID") or row.get("XMP-xmpMM:DocumentID")
        source = row.get("SourceFile")
        if not doc_id or not source:
            continue
        key = str(doc_id).strip()
        # Two files claiming one identity is a copy, not a rename; neither can be matched
        # to a row without guessing which. Stored form: this is what a row is re-pointed
        # at, and ExifTool answers with forward slashes.
        by_id[key] = None if key in by_id else paths.stored(source)
    return {k: v for k, v in by_id.items() if v}


def preserved_names(folder, exiftool_path=None):
    """Every photo under a folder, keyed by (its folder's key, the stem it was renamed from).

    Keyed by folder as well as stem: a rename never moves a file, and camera names
    repeat -- a library holds many IMG_0421s -- so a dead row may only be matched to
    a renamed file beside it. Keyed by stem alone, the last folder searched won, and
    a row's named faces could be re-pointed at a stranger's photo in another folder.
    """
    by_original = {}
    for row in _read(folder, "XMP-xmpMM:PreservedFileName", exiftool_path):
        original = row.get("XMP:PreservedFileName")
        source = row.get("SourceFile")
        if not original or not source:
            continue
        key = (paths.key(os.path.dirname(paths.stored(source))), stem_of(original))
        # Two files claiming one original cannot be told apart; leave both.
        by_original[key] = None if key in by_original else paths.stored(source)
    return {k: v for k, v in by_original.items() if v}


def merge_unambiguous(into, found):
    """Add `found` to `into`, dropping any key two different files claim.

    Folders are walked recursively, so one file can turn up from two walks -- that
    is the same claim twice. Two different files claiming one key is a copy, not a
    rename, and neither can be matched without guessing.
    """
    for key, path in found.items():
        if key in into and (into[key] is None or not paths.same(into[key], path)):
            into[key] = None
        else:
            into[key] = path


def plan_for(library, exiftool_path=None):
    """([{from, to, faces, named}] of the dead rows that can be re-pointed, [the dead
    rows that cannot]). Reads only."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        dead = dead_rows(conn)
        folders = sorted({os.path.dirname(p) for p in dead if os.path.isdir(os.path.dirname(p))})
        lookup, by_identity = {}, {}
        for folder in folders:
            merge_unambiguous(lookup, preserved_names(folder, exiftool_path))
            merge_unambiguous(by_identity, identities(folder, exiftool_path))

        # A dead row's own identity, where indexing recorded one.
        row_identity = store_photos.identities(conn)
        live = {paths.key(path) for path in store_photos.all_paths(conn) if path and os.path.exists(path)}

        moves, unmatched, claimed = [], [], set()
        for old in dead:
            # Identity first: it survives a move and a rename by any tool. The preserved
            # filename is the fallback, and only ever worked for TagPup's own renames.
            new = (by_identity.get(row_identity.get(old, ""))
                   or lookup.get((paths.key(os.path.dirname(old)), stem_of(old))))
            if not new:
                unmatched.append(old)
                continue
            key = paths.key(new)
            # Never point two rows at one file, and never collide with a row already there.
            if key in live or key in claimed:
                unmatched.append(old)
                continue
            claimed.add(key)
            faces, named = store_faces.counts_on(conn, old)
            moves.append({"from": old, "to": new, "faces": faces, "named": named})
        return moves, unmatched
    finally:
        conn.close()


def edits_for(library, moves):
    """([journal edits], [(old, new) left where they are]) for `moves`: each row found at
    its old path re-pointed at its new one while it still names the old, a skippable edit
    -- rows do not depend on each other. A row whose new name already has one is left
    where it is: re-pointing rows at a path that had rows made 233 duplicate faces. A
    move whose old path has no row has nothing to move."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        at_old = store_photos.rows_of(conn, [move["from"] for move in moves])
        at_new = store_photos.rows_of(conn, [move["to"] for move in moves])
    finally:
        conn.close()
    edits, skipped = [], []
    for move in moves:
        found = at_old.get(paths.key(move["from"]))
        if found is None:
            continue
        if paths.key(move["to"]) in at_new and not paths.same(move["from"], move["to"]):
            skipped.append((move["from"], move["to"]))
            continue
        photo_id, stored, _identity = found
        edits.append(journal.update("photos", (photo_id,), {"path": stored}, {"path": paths.stored(move["to"])},
                                    kind="relinked", skippable=True))
    return edits, skipped


def apply_moves(library, moves):
    """Re-point each row, and its faces, at the renamed file, as one change of the
    journal. Returns (rows moved, (old, new) pairs left where they were)."""
    edits, skipped = edits_for(library, moves)
    if not edits:
        return 0, skipped
    applied = journal.apply(library.path, OPERATION, edits, {"counts": {"planned": len(moves)}})
    return applied.changed, skipped


def relink(library, exiftool_path=None, apply=False):
    """Plan, and with `apply` make, the re-pointing of every dead row that a renamed file
    beside it can be matched to. A Result on the maintenance scaffold: `changed` is the
    rows re-pointed; details["reveal"] the moves and the rows left unmatched (paths)."""
    def plan(found_library):
        moves, unmatched = plan_for(found_library, exiftool_path)
        edits, occupied = edits_for(found_library, moves)
        return maintenance.Plan(
            size=len(edits),
            counts={"relinkable": len(moves), "faces": sum(m["faces"] for m in moves),
                    "named": sum(m["named"] for m in moves), "occupied": len(occupied),
                    "unmatched": len(unmatched)},
            reveal={"moves": moves, "occupied": occupied, "unmatched": unmatched},
            work=edits)

    def remaining(found_library):
        moves, unmatched = plan_for(found_library, exiftool_path)
        return {"relinkable": len(moves), "unmatched": len(unmatched)}

    return maintenance.run(library, OPERATION, plan, lambda planned: planned.work, apply=apply, remaining=remaining)
