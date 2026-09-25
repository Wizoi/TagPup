"""Photo rows for tests, made by the code that makes them for real.

A row a test writes by hand agrees with the test, not with the library. #247's
fixtures held raw_metadata no read ever makes -- grouped names without their bare
copies -- and the tests passed on the bug. Here a row is what the indexer records of a
read (MetadataExtractor._structure, then store.photos.record_indexed), or what Suggest
makes of a photo it has never read (store.photos.ensure_row).
"""
from tagpup.files.metadata import MetadataExtractor
from tagpup.store import photos


def as_read(photo_path, fields, people=None):
    """The record a read of `photo_path` gives when ExifTool answers `fields`
    ({"XMP:Subject": [...], "EXIF:DateTimeOriginal": "2024:07:04 10:00:00"}). The file's
    mtime and size are read from disk when it exists."""
    return MetadataExtractor()._structure(photo_path, {"SourceFile": photo_path, **fields}, people)


def add_read(conn, photo_path, fields, people=None):
    """Record `photo_path` as the indexer does after reading `fields` from it, and
    return its id. The caller commits."""
    photos.record_indexed(conn, photo_path, as_read(photo_path, fields, people))
    return photos.ensure_row(conn, photo_path)


def add_unread(conn, photo_path):
    """The row Suggest makes for a photo the index never read -- the path and nothing
    else -- and return its id. The caller commits."""
    return photos.ensure_row(conn, photo_path)
