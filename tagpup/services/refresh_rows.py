"""Re-read a photo's file and record what it holds in its row: what sync (tagpup.services.sync) does with a
row whose stamp no longer describes its file, and the CLI with the photos an identity was given.

A row goes stale when its file changed under it and the indexer did not notice: another program's keyword write, an
edit. `reread` reads those files the way the indexer does and says which rows differ; `edits_for` makes the change
that records what they hold: tags, people, captions, raw_metadata, mtime, size, and the DocumentID where the row has
none. The files are only read, never written (no DocumentID is minted); embeddings and faces are untouched.

The refresh that planned this over a whole library (`refresh_rows`, the MCP server's tool) is deleted with the server's
write tools (DECISIONS 2026-10-28, THE MCP SERVER ONLY READS); kr-track's journal holds the one it applied (change 2,
schema 10), listed in History and never undone: the library is at schema 35 and `journal.why_not` refuses on the
version (tagpup.services.journal.RETIRED).
"""
import json
from collections import Counter

from tagpup.core.vocabulary import people_in_photo
from tagpup.store import journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy


BATCH = 200


def read_files(conn, photo_paths, fields):
    """{path: what its file holds}, read the way the indexer reads, of the library open
    on `conn`. Files are only read: no DocumentID is minted.

    With the ExifTool the apps use: without one named, this looked on PATH, and where
    ExifTool is not there every file read as unreadable and nothing was refreshed. The
    library's people are read once for the run; they were read again for every photo.
    """
    known = store_taxonomy.people_vocabulary(conn=conn)
    records = {}
    for start in range(0, len(photo_paths), BATCH):
        batch = photo_paths[start:start + BATCH]
        for record in fields.whole(batch, people=known):
            # People from the keywords AND the photo's named faces, as every writer
            # of the column now records them; the read alone knows only the
            # keywords, and would take off everyone identified by face.
            record["people"] = people_in_photo(record["tags"], store_faces.face_refs(record["path"], conn=conn), known)
            records[record["path"]] = record
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
    if not store_photos.describes(mtime, size, (record["mtime"], record["size"])):
        changed.append("mtime/size")
    if not doc_id and record.get("document_id"):
        changed.append("document_id")
    return changed, json.loads(captions or "[]"), (mtime, size)


def reread(conn, stale, fields):
    """What the files of the rows named in `stale` (paths as stored) hold, against their
    rows, on `conn`: (records, {path: what its file holds}; to_write, the paths whose row
    differs; differing, a Counter of the fields that differ; unreadable, the paths whose file
    could not be read; identities, {path: the row's document_id} of those to write). Reads
    the files; writes nothing. Sync's (tagpup.services.sync) one reading of changed rows."""
    records, to_write, differing, unreadable, identities = {}, [], Counter(), [], {}
    if stale:
        records = read_files(conn, sorted(stale), fields)
    for path in sorted(stale):
        record = records.get(path)
        if record is None or not record.get("raw_metadata"):
            unreadable.append(path)
            continue
        stored = store_photos.row_as_recorded(conn, path)
        changed, _old_captions, _now = differences(conn, path, record, stored)
        if changed:
            to_write.append(path)
            identities[path] = stored[6]
            differing.update(changed)
    return records, to_write, differing, unreadable, identities


def edits_for(records, to_write, found, identities, ids):
    """The fix, as one change: each row in `to_write` from its file's `records`. `found` is each row as the plan read
    it ({"mtime", "size", "tags", "captions", "raw_metadata"}), `identities` each row's
    document_id, `ids` each row's id, all by path as stored.

    Each row is written only while it still has what the plan found before the files were
    read (`found`): one the app saved while this run was reading already describes
    something newer, and writing the older read over it would take the save back. That
    row is skipped (the edits are skippable) and reported, and the rest are written: the
    rows do not depend on each other, and refusing the whole change for one save threw
    away minutes of reading, on a library in use perhaps every time. The next sync reads
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
