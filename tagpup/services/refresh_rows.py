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

`rederive` is a one-off (scripts/rederive_captions.py): the rows stored before a photo's captions were read from XMP
first (DECISIONS 2026-11-03) are derived again from their own raw_metadata, no file read, as one change. So is
`redate_clips` (scripts/rederive_clip_dates.py): the clips dated before a clip's XMP date was read first (DECISIONS
2026-11-08, A5 (D) REVISED) are dated again from their own raw_metadata, no file read, as one change.
"""
import json
from collections import Counter

from tagpup.core.vocabulary import people_in_photo
from tagpup.files.metadata import MetadataExtractor
from tagpup.services import maintenance
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy


BATCH = 200

#: The change `rederive` is recorded as.
REDERIVE_OPERATION = "rederive tags and captions"

#: The change `redate_clips` is recorded as.
REDATE_CLIPS_OPERATION = "rederive clip dates"


def read_files(conn, photo_paths, exiftool_path):
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


def reread(conn, stale, exiftool_path):
    """What the files of the rows named in `stale` (paths as stored) hold, against their
    rows, on `conn`: (records, {path: what its file holds}; to_write, the paths whose row
    differs; fields, a Counter of the fields that differ; unreadable, the paths whose file
    could not be read; identities, {path: the row's document_id} of those to write). Reads
    the files; writes nothing. Sync's (tagpup.services.sync) one reading of changed rows."""
    records, to_write, fields, unreadable, identities = {}, [], Counter(), [], {}
    if stale:
        records = read_files(conn, sorted(stale), exiftool_path)
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
            fields.update(changed)
    return records, to_write, fields, unreadable, identities


def apart(library):
    """(counts, the rows apart) of the photos of `library` whose stored tags or captions are not what their stored
    raw_metadata derives now (store.photos.derived_apart), on a read-only connection: counts only, safe to print."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        photos, unread, rows = store_photos.derived_apart(conn)
    finally:
        conn.close()
    return {"photos": photos, "no raw_metadata": unread, "apart": len(rows),
            "tags": sum("tags" in differs for _id, _stored, differs in rows),
            "captions": sum("captions" in differs for _id, _stored, differs in rows),
            "shown caption": sum(json.loads(stored["captions"] or "[]")[:1] != json.loads(differs["captions"])[:1]
                                 for _id, stored, differs in rows if "captions" in differs)}, rows


def rederive(library, apply=False):
    """Make each photo's stored tags and captions what its stored raw_metadata derives now, as one change of the journal
    (maintenance.run: a rehearsal without `apply`). Each row is written only while its tags, captions and raw_metadata
    are what the plan read: one saved meanwhile (a server has the library open) is skipped and reported, as it was
    derived by the rule already. A row with no raw_metadata is left as it is, counted. The search words follow in the
    same change (journal._derive). Run again, it changes nothing."""
    def plan(_library):
        counts, rows = apart(library)
        return maintenance.Plan(size=len(rows), counts=counts, ids={"photos": [row[0] for row in rows]}, work=rows)

    def edits(planned):
        return [journal.update("photos", (photo_id,), stored, differs, kind="rederived", skippable=True)
                for photo_id, stored, differs in planned.work]

    return maintenance.run(library, REDERIVE_OPERATION, plan, edits, apply=apply)


def clips_apart(library):
    """(counts, the rows apart) of the clips of `library` whose stored `taken` or `year` is not what their stored
    raw_metadata dates now (store.photos.clip_dates_apart), on a read-only connection: counts only, safe to print."""
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        clips, rows = store_photos.clip_dates_apart(conn)
    finally:
        conn.close()
    return {"clips": clips, "apart": len(rows),
            "year": sum(stored["year"] != now["year"] for _id, stored, now in rows)}, rows


def redate_clips(library, apply=False):
    """Make each clip's stored `taken` and `year` what its stored raw_metadata and path date now, as one change of the
    journal (maintenance.run: a rehearsal without `apply`). Each row is written only while its taken, year, raw_metadata
    and path are what the plan read: one saved or moved meanwhile is skipped and reported, as it was dated by the rule
    already. The change names only the two columns, so its undo puts them back as they were and dates nothing again
    (store.journal._touched re-dates a row only for a change of its path or raw_metadata). Run again, it changes
    nothing."""
    def plan(_library):
        counts, rows = clips_apart(library)
        return maintenance.Plan(size=len(rows), counts=counts, ids={"photos": [row[0] for row in rows]}, work=rows)

    def edits(planned):
        return [journal.update("photos", (photo_id,), stored, now, kind="rederived", skippable=True)
                for photo_id, stored, now in planned.work]

    return maintenance.run(library, REDATE_CLIPS_OPERATION, plan, edits, apply=apply)


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
