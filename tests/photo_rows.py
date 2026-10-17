"""Photo rows for tests, made by the code that makes them for real.

A row a test writes by hand agrees with the test, not with the library. #247's
fixtures held raw_metadata no read ever makes -- grouped names without their bare
copies -- and the tests passed on the bug. Here a row is what the indexer records of a
read (MetadataExtractor._structure, then store.photos.record_indexed), or what Suggest
makes of a photo it has never read (store.photos.ensure_row, in a folder added to the library);
what a write of the app's own leaves is what the journaled write records in the transaction that
marks the file done (store.photos.follow_fields, move_rows_in).

A photo row made by a bare statement of the test's own makes no folder row, so the folders reader finds the photo in no folder: a new test uses add_read.
tests/test_fixtures_make_photo_rows_like_the_indexer.py lists the files that still do, and only lets that list shrink.
"""
import os

from tagpup.core import fields
from tagpup.files.metadata import MetadataExtractor
from tagpup.store import added_folders, db, photos


def stamp_of(photo_path):
    """(mtime, size) of the file, as the indexer takes it before its read (MetadataExtractor._stamp); (0.0, 0) for a
    path no file is at -- most fixtures name a photo and make no file -- so that its row is a row that was read. By design
    (#1496): a stamp of 0 describes no file on disk (store.photos.describes), so anything that compares the row with a
    file reads it again; the real reader leaves such a row unstamped instead, which only the fixtures cannot be."""
    stamp = MetadataExtractor._stamp(photo_path)
    return (0.0, 0) if stamp == (None, None) else stamp


def as_read(photo_path, fields, people=None):
    """The record a read of `photo_path` gives when ExifTool answers `fields`
    ({"XMP:Subject": [...], "EXIF:DateTimeOriginal": "2024:07:04 10:00:00"}). The file's
    mtime and size are read from disk when it exists (stamp_of)."""
    return MetadataExtractor()._structure(photo_path, {"SourceFile": photo_path, **fields}, people, stamp_of(photo_path))


def add_read(conn, photo_path, fields, people=None):
    """Record `photo_path` as the indexer does after reading `fields` from it, and
    return its id. The caller commits."""
    photos.record_indexed(conn, photo_path, as_read(photo_path, fields, people))
    return photos.ensure_row(conn, photo_path)


def add_unread(conn, photo_path):
    """The row Suggest makes for a photo the index never read -- the path and nothing
    else -- in a folder added to the library (store.added_folders), and return its id.
    The caller commits."""
    added_folders.record(conn, os.path.dirname(photo_path), subfolders=False)
    return photos.ensure_row(conn, photo_path)


#: The photo hold() records: one indexed before and gone from disk since, whose row
#: stays until the owner removes it (sync reports a missing file, never removes it).
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
    stamp now, taken only where the row described the file at `before` (embeddings.stamp_of
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
