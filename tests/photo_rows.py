"""Photo rows for tests, made by the code that makes them for real.

A row a test writes by hand agrees with the test, not with the library. #247's
fixtures held raw_metadata no read ever makes -- grouped names without their bare
copies -- and the tests passed on the bug. Here a row is what the indexer records of a
read (metadata.structure, then store.photos.record_indexed), or what Suggest
makes of a photo it has never read (store.photos.ensure_row, in a folder added to the library);
what a write of the app's own leaves is what the journaled write records in the transaction that
marks the file done (store.photos.follow_fields, move_rows_in).

A photo row made by a bare statement of the test's own makes no folder row, so the folders reader finds the photo in no folder: a new test uses add_read.
tests/test_fixtures_make_photo_rows_like_the_indexer.py lists the files that still do, and only lets that list shrink.
"""
import hashlib
import os

from tagpup.core import fields, paths
from tagpup.files import field_values, images, metadata
from tagpup.store import added_folders, db, embeddings, faces_detected, folders, photos


def stamp_of(photo_path):
    """(mtime, size) of the file, as the indexer takes it before its read (metadata.stamp_before_read); (0.0, 0) for a
    path no file is at -- most fixtures name a photo and make no file -- so that its row is a row that was read. By design
    (#1496): a stamp of 0 describes no file on disk (store.photos.describes), so anything that compares the row with a
    file reads it again; the real reader leaves such a row unstamped instead, which only the fixtures cannot be."""
    stamp = metadata.stamp_before_read(photo_path)
    return (0.0, 0) if stamp == (None, None) else stamp


def as_read(photo_path, fields, people=None):
    """The record a read of `photo_path` gives when ExifTool answers `fields`
    ({"XMP:Subject": [...], "EXIF:DateTimeOriginal": "2024:07:04 10:00:00"}). The file's
    mtime and size are read from disk when it exists (stamp_of)."""
    return metadata.structure(photo_path, {"SourceFile": photo_path, **fields}, people, stamp_of(photo_path))


def read_whole(exiftool_path, photo_paths, **asked):
    """The records the index's read gives of `photo_paths` (field_values.Fields.whole, `asked` its people and bakes),
    through one Fields opened and closed for the call, as a service opens one for its operation."""
    with field_values.opened(exiftool_path) as reader:
        return reader.whole(list(photo_paths), **asked)


def add_read(conn, photo_path, fields, people=None):
    """Record the read of `fields` from `photo_path` as the indexer records it, and return its id: the row alone, no
    vector and no look for faces -- what the index still owes work (photo_fingerprints.owed). The picture's size is its
    header's (images.picture_size, as the index reads it); a path no file is at -- most fixtures make none -- records
    none. A folder the library does not hold yet is added first, as the index adds the folder it is given
    (libraries.record_added) before it records a row there (record_indexed refuses one in a folder not held). The caller
    commits."""
    record = as_read(photo_path, fields, people)
    try:
        record["width"], record["height"] = images.picture_size(photo_path)
    except OSError:
        pass
    added(conn, photo_path)
    photos.record_indexed(conn, photo_path, record)
    return photos.ensure_row(conn, photo_path)


def added(conn, photo_path):
    """Add the folder of `photo_path` (without its subfolders) when the library does not hold it yet: what an index
    of the folder records before its first row there (libraries.record_added). For a test that records a read through
    store.photos.record_indexed itself."""
    folder = os.path.dirname(paths.stored(photo_path))
    if not folders.holds(conn, folder):
        added_folders.record(conn, folder, subfolders=False)


#: images.opened and images.fingerprint as this module was imported: fingerprint_of's, beyond a test's mock of either.
_OPENED, _FINGERPRINT = images.opened, images.fingerprint

#: The vector add_indexed keeps: its value matters to no test that asks whether the index owes the photo work.
VECTOR = b"\x00\x00\x80\x3f\x00\x00\x00\x00"


def fingerprint_of(photo_path):
    """The fingerprint the index records with a vector it makes of `photo_path` (images.FINGERPRINT_KEY, the digest of
    its upright decode); for a fixture's file that does not decode, or no file -- which no index makes a vector of -- a
    digest of its path, distinct for each photo and equal to no decode. Decoded through the functions as imported, so a
    test that refuses every decode of the code under test (sync decodes nothing) can still seed its rows."""
    try:
        digest = _FINGERPRINT(_OPENED(photo_path, upright=True))
    except OSError:
        digest = hashlib.sha256(paths.key(photo_path).encode("utf-8")).digest()
    return images.FINGERPRINT_KEY, digest


def add_indexed(conn, photo_path, fields, people=None):
    """Record `photo_path` as a whole index leaves it, and return its id: the read of `fields` (add_read), a vector
    under the library's photo model stamped with the file as read, with its picture's fingerprint row (fingerprint_of),
    and a look for faces by the library's detector that found none -- the index owes it nothing
    (photo_fingerprints.owed) while its file is as read. The caller commits."""
    from tagpup.services import faces, settings
    record = as_read(photo_path, fields, people)
    photo_id = add_read(conn, photo_path, fields, people)
    held = settings.on(conn)
    embeddings.put(conn, photo_path, settings.photo_key_on(conn), record["mtime"], record["size"], VECTOR,
                   fingerprint_of(photo_path))
    faces_detected.record(conn, photo_path, faces.detector_named(held.faces),
                          (record["mtime"], record["size"]), 0)
    return photo_id


def add_unread(conn, photo_path):
    """The row Suggest makes for a photo the index never read -- the path and nothing
    else -- in a folder added to the library (store.added_folders), and return its id.
    The caller commits."""
    added_folders.record(conn, os.path.dirname(photo_path), subfolders=False)
    return photos.ensure_row(conn, photo_path)


#: The photo hold() records: one indexed before and gone from disk since. A whole sync that
#: finds a file of the folder's root removes its row (DECISIONS 2026-10-28); a sync of the
#: folder alone, or one that finds no file of it, keeps it.
HELD_BY = "indexed earlier.jpg"


def hold(conn, folder):
    """Make `folder` the library's (store.folders.holds), as indexing it did: the
    row of a photo indexed there before and deleted since, so that no photo on disk --
    nothing a scan, a rename or a count sees -- is added. A photo is written only
    through a library that holds its folder (tagpup.services.libraries.split).
    The caller commits."""
    return add_read(conn, os.path.join(folder, HELD_BY), {})


def written(db_path, photo_path, values, before=None):
    """What the journaled write records of `values` ({field: value}) written into `photo_path`
    (store.photos.follow_fields, as tagpup.services.file_changes records a file done): the file's
    stamp now, taken only where the row described the file at `before` (shares.read_stamp
    just before the write). True when the photo has a row."""
    try:
        stat = os.stat(photo_path)
    except OSError:
        stat = None
    return db.write_with_connection(
        db_path, lambda conn: photos.follow_fields(conn, photo_path, values, stat, before)) is not None


def tag(db_path, photo_path, tags, before=None):
    """What a keyword write of `tags` leaves in the photo's row (written, with the keyword fields
    the journaled write sets for them)."""
    return written(db_path, photo_path, dict(fields.keyword_fields(*fields.expand_tag_fields(tags))), before)


def move(db_path, renames):
    """What a rename leaves in the rows (store.photos.move_rows_in, in the transaction the file
    journal marks the renames done in): (moved, skipped)."""
    return db.write_with_connection(db_path, lambda conn: photos.move_rows_in(conn, renames))
