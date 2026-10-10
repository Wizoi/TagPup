"""Pairing a missing row with the file it became: what sync uses when it finds files new where rows are missing.

Renaming a photo outside TagPup leaves its index row behind: the row still names the old
file, which no longer exists, so the photo looks unindexed while its row looks dead. The row
is the valuable half -- it carries the photo's embedding and its faces, including the names
somebody assigned by hand.

A dead row is matched to a file by the identity indexing recorded for it (the file's
DocumentID), else by the name TagPup's renamer kept in `XMP-xmpMM:PreservedFileName`,
matched against the stem of the row's file name in the same folder -- extensions ignored on
both sides, since the preserved name is usually the RAW original (.CR3) and the indexed file
the JPEG made from it. `claims_of` reads the files, `pair` matches, `edits_for` makes the
journaled re-pointing (sync applies it; a row whose new name already has one is left). The
folder-wide command that used this on its own is gone (owner, 2026-10-10).
"""
import os

from tagpup.core import paths
# Looked up at call time, as exiftool_session.ExifToolSession, so a test standing in for
# ExifTool there reaches this too.
from tagpup.files import exiftool_session
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos

#: The two fields a dead row is matched to a file by.
IDENTITY = "XMP-xmpMM:DocumentID"
PRESERVED = "XMP-xmpMM:PreservedFileName"


def stem_of(path):
    return os.path.splitext(os.path.basename(str(path)))[0].strip().lower()


def _read_files(found, fields, exiftool_path):
    """(SourceFile, row) of each photo in `found`, read for `fields`; a batch that fails
    is read again a photo at a time. Only reads."""
    if not found:
        return []
    rows = []
    with exiftool_session.ExifToolSession(executable=exiftool_path) as et:
        for i in range(0, len(found), 100):
            batch = found[i:i + 100]
            try:
                rows += et.get_tags(batch, tags=list(fields))
            except Exception:
                for one in batch:
                    try:
                        rows += et.get_tags([one], tags=list(fields))
                    except Exception:
                        continue
    return rows


def read_files(found, fields, exiftool_path=None):
    """(SourceFile, row) of each photo in `found`, read for `fields` (_read_files, public)."""
    return _read_files(list(found), list(fields), exiftool_path)


def _by_identity(rows):
    """{DocumentID: the file claiming it} of what ExifTool answered, an identity two files
    claim left out."""
    by_id = {}
    for row in rows:
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


def _by_original(rows):
    """{(folder key, original stem): the file claiming it} of what ExifTool answered, a
    key two files claim left out."""
    by_original = {}
    for row in rows:
        original = row.get("XMP:PreservedFileName")
        source = row.get("SourceFile")
        if not original or not source:
            continue
        key = (paths.key(os.path.dirname(paths.stored(source))), stem_of(original))
        # Two files claiming one original cannot be told apart; leave both.
        by_original[key] = None if key in by_original else paths.stored(source)
    return {k: v for k, v in by_original.items() if v}


def claims_of(files, exiftool_path=None):
    """(preserved names, identities) of `files`, as preserved_names and identities give
    them for a folder, read in one pass: what sync matches its missing rows against among
    the files it found new (tagpup.services.sync). Only reads."""
    rows = _read_files(list(files), [IDENTITY, PRESERVED], exiftool_path)
    return _by_original(rows), _by_identity(rows)


def pair(dead, lookup, by_identity, row_identity, live):
    """([(old, new)], [old unmatched]): each dead row (path as stored) matched to a file,
    by its identity (`row_identity`, {path: document_id}) in `by_identity`, else by its
    stem in its own folder in `lookup` (preserved_names). A file a row already names
    (`live`, their keys) or another dead row has been matched to is not matched again."""
    pairs, unmatched, claimed = [], [], set()
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
        pairs.append((old, new))
    return pairs, unmatched


def moves_with_faces(conn, pairs):
    """[{from, to, faces, named}] of (old, new) pairs: the faces each row takes with it."""
    moves = []
    for old, new in pairs:
        faces, named = store_faces.counts_on(conn, old)
        moves.append({"from": old, "to": new, "faces": faces, "named": named})
    return moves


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


