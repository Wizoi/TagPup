"""Re-read the photos whose index rows no longer describe their files, and record them.

Rows go stale in ways the indexer never notices, because it only re-reads a file
whose mtime or size changed:

* bulk keyword writes recorded only some of the fields they wrote, so a removed tag
  lingered in raw_metadata's IPTC:Keywords and came back on the next re-derivation;
* they also left mtime and size as they were, so every folder scan re-read those
  photos with ExifTool, every time;
* ExifTool's answers were decoded as cp1252 instead of UTF-8, so captions holding
  "ü" were stored as "Ã¼" from files that were right all along;
* a tag write on a row the index had never read (one Suggest made, path only) recorded
  the keywords as its whole raw_metadata and stamped it with the file's mtime and size
  (#247), so the scan trusted a row with no Date Taken. A read always records each field
  under its bare name as well (`Subject` beside `XMP:Subject`); a row holding none is
  "never read".

This finds rows where the file on disk disagrees with the row -- mtime or size
differ, the stored text shows UTF-8-read-as-cp1252, the tags re-derived from
raw_metadata are not the row's tags, or the people lack someone the row's own data
names -- reads those files the way the indexer does, and records what they hold: tags,
people, captions, raw_metadata, mtime, size, and the DocumentID where the row has none.
The files are only read, never written (no DocumentID is minted); embeddings and faces
are untouched. Rows that only list a caption more than once are fixed from the row.

scripts/refresh_rows_from_files.py and the MCP server's tool both call `refresh_rows`,
on the maintenance scaffold (tagpup.services.maintenance). Planning reads the files, so
a dry run takes as long as the reading.
"""
import json
import os
import re
from collections import Counter

from tagpup.core import paths
from tagpup.core.vocabulary import extract_people, extract_tags, people_in_photo
from tagpup.files.metadata import MetadataExtractor
from tagpup.services import maintenance
from tagpup.store import db, journal
from tagpup.store import faces as store_faces
from tagpup.store import photos as store_photos
from tagpup.store import taxonomy as store_taxonomy


#: UTF-8 bytes decoded as cp1252: "ü" -> "Ã¼", "é" -> "Ã©", "–" -> "â€“", nbsp -> "Â ".
MOJIBAKE = re.compile("Ã[\u0080-ÿ]|Â[\u0080-ÿ ]|â€")

BATCH = 200


def is_garbled(stored_json):
    """Does a stored JSON value hold UTF-8 that was read as cp1252?

    Searched decoded: rows are written with json.dumps, which escapes non-ASCII,
    so the characters never appear in the stored text itself.
    """
    if not stored_json:
        return False
    try:
        text = json.dumps(json.loads(stored_json), ensure_ascii=False)
    except Exception:
        text = stored_json
    return bool(MOJIBAKE.search(text))


def never_read(raw_json):
    """Does a row's raw_metadata hold only what writes recorded, never a read of its file?
    ExifTool's fields are recorded by a read under both names, `XMP:Subject` and
    `Subject`; the writes record only the first."""
    try:
        keys = json.loads(raw_json or "{}")
    except Exception:
        return False
    return bool(keys) and all(":" in key for key in keys)


def why_stale(row):
    """The reasons a row may not describe its file, [] if it looks right, or None if its
    file is gone."""
    path, mtime, size, tags_json, captions_json, raw_json = row
    reasons = []
    try:
        stat = os.stat(path)
    except OSError:
        return None   # file gone: a job for relink or remove, not this
    if not store_photos.describes(mtime, size, (stat.st_mtime, stat.st_size)):
        reasons.append("mtime/size")
    if is_garbled(captions_json) or is_garbled(raw_json):
        reasons.append("garbled text")
    if never_read(raw_json):
        reasons.append("never read")
    try:
        stored = set(json.loads(tags_json or "[]"))
        derived = set(extract_tags(json.loads(raw_json or "{}")))
        if stored != derived:
            reasons.append("keywords disagree")
    except Exception:
        reasons.append("keywords disagree")
    return reasons


def distinct_captions(captions_json):
    """The stored captions with repeats removed, or None if there were none to remove.

    The extractor listed each caption once per field it appeared in, prefixed and
    bare; the stored list is exactly that output, so dropping repeats from it is
    exactly what the corrected extraction gives -- no file needs reading.
    """
    try:
        captions = json.loads(captions_json or "[]")
    except Exception:
        return None
    distinct = list(dict.fromkeys(captions))
    return distinct if len(distinct) != len(captions) else None


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
    """(rows whose file must be re-read, {path: captions} fixable from the row alone),
    each by the path as stored, of the library open on `conn`.

    `seen`, if given, is filled with each stale row's (mtime, size) as found here --
    before any file is read. `found`, if given, with each named row's columns as found
    here: {"mtime", "size", "tags", "captions", "raw_metadata"}, what the write checks
    the row still has. `ids`, if given, is filled with the id of each row either kind
    names.
    """
    stale, captions_only = {}, {}
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
            continue
        fixed = distinct_captions(row[4])
        if fixed is not None:
            captions_only[row[0]] = fixed
            if found is not None:
                found[row[0]] = {"captions": row[4]}
            if ids is not None:
                ids[row[0]] = row[7]
    return stale, captions_only


def read_files(conn, photo_paths, exiftool_path, progress=None):
    """{path: what its file holds}, read the way the indexer reads, of the library open
    on `conn`. Files are only read: no DocumentID is minted.

    With the ExifTool the apps use: without one named, this looked on PATH, and where
    ExifTool is not there every file read as unreadable and nothing was refreshed. The
    library's people are read once for the run; they were read again for every photo.
    """
    extractor = MetadataExtractor(exiftool_path=exiftool_path, mint_identities=False)
    known = store_taxonomy.people_vocabulary(conn=conn)
    records = {}
    for start in range(0, len(photo_paths), BATCH):
        batch = photo_paths[start:start + BATCH]
        for record in extractor.batch_read(batch, people=known):
            # People from the keywords AND the photo's named faces, as every writer
            # of the column now records them; the extractor alone knows only the
            # keywords, and would take off everyone identified by face.
            record["people"] = people_in_photo(record["raw_metadata"], record["tags"],
                                               store_faces.face_names(record["path"], conn=conn), known)
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


def _planner(exiftool_path, folder, examples, progress):
    def plan(library):
        records, to_write, fields, unreadable, shown, ids = {}, [], Counter(), [], [], {}
        found, identities = {}, {}
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            stale, captions_only = find_stale(conn, folder, ids=ids, found=found)
            reasons = dict(Counter(r for rs in stale.values() for r in rs).most_common())
            if progress is not None:
                progress("found", {"stale": len(stale), "reasons": reasons,
                                   "captions_only": len(captions_only)})
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
        finally:
            conn.close()
        return maintenance.Plan(
            size=len(to_write) + len(captions_only),
            counts={"stale": len(stale), "reasons": reasons, "captions_only": len(captions_only),
                    "to_write": len(to_write), "fields": dict(fields.most_common()),
                    "unreadable": len(unreadable)},
            ids={"stale": [ids[p] for p in sorted(stale)], "to_write": [ids[p] for p in to_write],
                 "captions_only": [ids[p] for p in sorted(captions_only)],
                 "unreadable": [ids[p] for p in unreadable]},
            reveal={"examples": shown},
            work=(records, to_write, captions_only, found, identities, ids))
    return plan


def _edits(planned):
    """Both kinds of fix, as one change.

    Each row is written only while it still has what the plan found before the files were
    read (`found`): one the app saved while this run was reading already describes
    something newer, and writing the older read over it would take the save back. That
    row is skipped (the edits are skippable) and reported, and the rest are written: the
    rows do not depend on each other, and refusing the whole change for one save threw
    away minutes of reading, on a library in use perhaps every time. The next run reads
    the skipped row again. The DocumentID is recorded only where the row has none, as the
    indexer does.
    """
    records, to_write, captions_only, found, identities, ids = planned.work
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
    for path, fixed in captions_only.items():
        edits.append(journal.update("photos", (ids[path],), {"captions": found[path]["captions"]},
                                    {"captions": json.dumps(fixed)}, kind="captions", skippable=True))
    return edits


def refresh_rows(library, exiftool_path, apply=False, folder=None, examples=5, progress=None):
    """Plan, and with `apply` make, the refresh of every row of `library` (or of those
    under `folder`) that no longer describes its file, reading the files with ExifTool at
    `exiftool_path`. A Result, on the maintenance scaffold: `changed` is rows changed,
    from their files (details["changed"]["from_files"]) and captions alone ("captions"). Applied
    as one change of the journal, undoable, of the rows written; a row changed after this
    run read it is left as it is and listed in `skipped`.

    `examples` caps the captions shown under details["reveal"]["examples"]. `progress`,
    if given, is called progress(stage, counts): "found" once the rows are sorted, with
    counts of what was found, then "read" after each batch of files, with "done" and
    "total".
    """
    return maintenance.run(library, "refresh_rows", _planner(exiftool_path, folder, examples, progress),
                           _edits, apply=apply, kinds=("from_files", "captions"))
