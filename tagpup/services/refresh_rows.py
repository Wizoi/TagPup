"""Re-read the photos whose index rows no longer describe their files, and record them.

A row goes stale when its file changed under it and the indexer did not notice: a bulk
keyword write that left mtime and size as they were, a row Suggest made from a path alone
(no mtime, no size), a people column short of someone the row's own data names. This
finds the rows whose mtime or size differ from the file's, or whose people lack someone
the row's own data names, reads those files the way the indexer does, and records what
they hold: tags, people, captions, raw_metadata, mtime, size, and the DocumentID where the
row has none. The files are only read, never written (no DocumentID is minted); embeddings
and faces are untouched.

Rows that show other signs -- text decoded as cp1252, tags that disagree with the keywords
in raw_metadata, a row no read ever made, a caption listed twice -- are not looked for
here: none exists in the three libraries (counted 2026-10-10), and if one appears the owner
fixes it by hand (the doctor does not look for these; its checks are in tagpup.store.checks).

The MCP server's tool calls `refresh_rows`,
on the maintenance scaffold (tagpup.services.maintenance). Planning reads the files, so
a dry run takes as long as the reading.
"""
import json
import os
from collections import Counter

from tagpup.core import paths
from tagpup.core.vocabulary import extract_people, people_in_photo
from tagpup.files.metadata import MetadataExtractor
from tagpup.services import maintenance
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy


BATCH = 200


def why_stale(row):
    """The reasons a row may not describe its file, [] if it looks right, or None if its
    file is gone."""
    path, mtime, size = row[:3]
    try:
        stat = os.stat(path)
    except OSError:
        return None   # file gone: a job for sync or remove, not this
    return [] if store_photos.describes(mtime, size, (stat.st_mtime, stat.st_size)) else ["mtime/size"]


def people_checker(conn):
    """A test for rows whose people lack someone their own data names.

    A row's people are its keyword people and the names on its faces
    (tagpup.core.vocabulary.people_in_photo). Rows written before either rule reached every writer
    miss some: 741 names on 597 rows of one library -- mostly face names, then people
    under Pets, Family and Friends. None of the other reasons notices, since the file
    and the tags agree; only the people column is short. The taxonomy and the face
    names are read once, not per row.
    """
    vocabulary = store_taxonomy.people_vocabulary(conn=conn)
    named = store_faces.names_by_photo_key(conn)

    def missing(path, raw_json, tags_json, people_json):
        try:
            stored = set(json.loads(people_json or "[]"))
            wanted = set(extract_people(json.loads(raw_json or "{}"), json.loads(tags_json or "[]"),
                                        vocabulary))
        except Exception:
            return False
        wanted |= named.get(paths.key(path), set())
        return bool(wanted - stored)

    return missing


def find_stale(conn, folder=None, seen=None, ids=None, found=None):
    """{path as stored: the reasons its row may not describe its file} of the rows whose file must
    be re-read, of the library open on `conn`.

    `seen`, if given, is filled with each stale row's (mtime, size) as found here --
    before any file is read. `found`, if given, with each named row's columns as found
    here: {"mtime", "size", "tags", "captions", "raw_metadata"}, what the write checks
    the row still has. `ids`, if given, is filled with the id of each row either kind
    names.
    """
    stale = {}
    missing_people = people_checker(conn)
    for row in store_photos.rows_to_check(conn, folder):
        reasons = why_stale(row[:6])
        if reasons is not None and missing_people(row[0], row[5], row[3], row[6]):
            reasons.append("people incomplete")
        if reasons:
            stale[row[0]] = reasons   # re-reading the file fixes its captions too
            if seen is not None:
                seen[row[0]] = (row[1], row[2])
            if found is not None:
                found[row[0]] = {"mtime": row[1], "size": row[2], "tags": row[3], "captions": row[4],
                                 "raw_metadata": row[5]}
            if ids is not None:
                ids[row[0]] = row[7]
    return stale


def read_files(conn, photo_paths, exiftool_path, progress=None):
    """{path: what its file holds}, read the way the indexer reads, of the library open
    on `conn`. Files are only read: no DocumentID is minted.

    With the ExifTool the apps use: without one named, this looked on PATH, and where
    ExifTool is not there every file read as unreadable and nothing was refreshed. The
    library's people are read once for the run; they were read again for every photo.
    """
    extractor = MetadataExtractor(exiftool_path=exiftool_path)
    known = store_taxonomy.people_vocabulary(conn=conn)
    records = {}
    for start in range(0, len(photo_paths), BATCH):
        batch = photo_paths[start:start + BATCH]
        for record in extractor.batch_read(batch, people=known):
            # People from the keywords AND the photo's named faces, as every writer
            # of the column now records them; the extractor alone knows only the
            # keywords, and would take off everyone identified by face.
            record["people"] = people_in_photo(record["raw_metadata"], record["tags"],
                                               store_faces.face_refs(record["path"], conn=conn), known)
            records[record["path"]] = record
        if progress is not None:
            progress("read", {"done": min(start + BATCH, len(photo_paths)), "total": len(photo_paths)})
    return records


def differences(conn, path, record, stored=None):
    """(the fields where `record`, read from the file, differs from the row stored under
    `path`, the row's captions, the row's (mtime, size)). `stored` is the row as
    store.photos.row_as_recorded gives it, when it has been read already."""
    tags, people, captions, raw_json, mtime, size, doc_id = stored or store_photos.row_as_recorded(conn, path)
    changed = []
    if sorted(json.loads(tags or "[]")) != sorted(record["tags"]):
        changed.append("tags")
    if sorted(json.loads(people or "[]")) != sorted(record["people"]):
        changed.append("people")
    if json.loads(captions or "[]") != record["captions"]:
        changed.append("captions")
    if json.loads(raw_json or "{}") != record["raw_metadata"]:
        changed.append("raw_metadata")
    if (mtime, size) != (record["mtime"], record["size"]):
        changed.append("mtime/size")
    if not doc_id and record.get("document_id"):
        changed.append("document_id")
    return changed, json.loads(captions or "[]"), (mtime, size)


def reread(conn, stale, exiftool_path, examples=0, progress=None):
    """What the files of the rows named in `stale` (paths as stored) hold, against their
    rows, on `conn`: (records, {path: what its file holds}; to_write, the paths whose row
    differs; fields, a Counter of the fields that differ; unreadable, the paths whose file
    could not be read; shown, up to `examples` (path, old captions, new captions);
    identities, {path: the row's document_id} of those to write). Reads the files; writes
    nothing. The refresh's and sync's (tagpup.services.sync) one reading of stale rows."""
    records, to_write, fields, unreadable, shown, identities = {}, [], Counter(), [], [], {}
    if stale:
        records = read_files(conn, sorted(stale), exiftool_path, progress)
    for path in sorted(stale):
        record = records.get(path)
        if record is None or not record.get("raw_metadata"):
            unreadable.append(path)
            continue
        stored = store_photos.row_as_recorded(conn, path)
        changed, old_captions, _now = differences(conn, path, record, stored)
        if changed:
            to_write.append(path)
            identities[path] = stored[6]
            fields.update(changed)
            if "captions" in changed and len(shown) < examples:
                shown.append((path, old_captions, record["captions"]))
    return records, to_write, fields, unreadable, shown, identities


def _planner(exiftool_path, folder, examples, progress):
    def plan(library):
        ids, found = {}, {}
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            stale = find_stale(conn, folder, ids=ids, found=found)
            reasons = dict(Counter(r for rs in stale.values() for r in rs).most_common())
            if progress is not None:
                progress("found", {"stale": len(stale), "reasons": reasons})
            records, to_write, fields, unreadable, shown, identities = reread(
                conn, stale, exiftool_path, examples, progress)
        finally:
            conn.close()
        return maintenance.Plan(
            size=len(to_write),
            counts={"stale": len(stale), "reasons": reasons,
                    "to_write": len(to_write), "fields": dict(fields.most_common()),
                    "unreadable": len(unreadable)},
            ids={"stale": [ids[p] for p in sorted(stale)], "to_write": [ids[p] for p in to_write],
                 "unreadable": [ids[p] for p in unreadable]},
            reveal={"examples": shown},
            work=(records, to_write, found, identities, ids))
    return plan


def _edits(planned):
    return edits_for(*planned.work)


def edits_for(records, to_write, found, identities, ids):
    """The fix, as one change: each row in `to_write` from its file's `records`. `found` is each row as the plan read
    it ({"mtime", "size", "tags", "captions", "raw_metadata"}), `identities` each row's
    document_id, `ids` each row's id, all by path as stored.

    Each row is written only while it still has what the plan found before the files were
    read (`found`): one the app saved while this run was reading already describes
    something newer, and writing the older read over it would take the save back. That
    row is skipped (the edits are skippable) and reported, and the rest are written: the
    rows do not depend on each other, and refusing the whole change for one save threw
    away minutes of reading, on a library in use perhaps every time. The next run reads
    the skipped row again. The DocumentID is recorded only where the row has none, as the
    indexer does.
    """
    edits = []
    for path in to_write:
        record, before = records[path], found[path]
        values = {"tags": json.dumps(record["tags"]), "captions": json.dumps(record["captions"]),
                  "raw_metadata": json.dumps(record["raw_metadata"]),
                  "mtime": record["mtime"], "size": record["size"]}
        expect = dict(before)
        if identities.get(path) is None and record.get("document_id"):
            values["document_id"] = record["document_id"]
            expect["document_id"] = None
        edits.append(journal.update("photos", (ids[path],), expect, values, kind="from_files", skippable=True))
    return edits


def refresh_rows(library, exiftool_path, apply=False, folder=None, examples=5, progress=None):
    """Plan, and with `apply` make, the refresh of every row of `library` (or of those
    under `folder`) that no longer describes its file, reading the files with ExifTool at
    `exiftool_path`. A Result, on the maintenance scaffold: `changed` is rows changed,
    from their files (details["changed"]["from_files"]). Applied
    as one change of the journal, undoable, of the rows written; a row changed after this
    run read it is left as it is and listed in `skipped`.

    `examples` caps the captions shown under details["reveal"]["examples"]. `progress`,
    if given, is called progress(stage, counts): "found" once the rows are sorted, with
    counts of what was found, then "read" after each batch of files, with "done" and
    "total".
    """
    return maintenance.run(library, "refresh_rows", _planner(exiftool_path, folder, examples, progress),
                           _edits, apply=apply, kinds=("from_files",))
