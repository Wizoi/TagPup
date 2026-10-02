"""Adopting a root is seconds, not minutes, on a library the size of photo_index (68,466 photos,
225,000 faces), and the lookups a click makes still use their indexes once it has.

The library is synthetic and made here, in a home of the test's own: rows shaped as the
indexer's are -- paths in the owner's folder shape, raw_metadata of ExifTool's fields with its
forward-slash SourceFile, tags, faces with a vector -- written in bulk through SQL, as nothing
is under test in how they are made. What is under test is `roots adopt` and the reads after it.
TAGPUP_ROOTS_SCALE (a fraction, default 1) makes it smaller where the full size is not wanted;
the timings are printed to stderr.
"""
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.store import adoption, db, faces, folders, inspection  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402
from tagpup.store import roots as store_roots  # noqa: E402

WINDOWS = os.name == "nt"

#: What photo_index holds, counted 2026-10-02.
PHOTOS = 68_466
FACES = 225_000

SCALE = float(os.environ.get("TAGPUP_ROOTS_SCALE", "1"))

#: Fields of an ExifTool read, about what the indexer keeps of one photo.
FIELDS = {"XMP:Subject": ["Activity/Sailing", "Places/Harbour"], "IPTC:Keywords": ["Activity/Sailing", "Places/Harbour"],
          "EXIF:DateTimeOriginal": "2024:06:01 10:00:00", "EXIF:Make": "Fictional", "EXIF:Model": "Cam 7",
          "EXIF:ExposureTime": "1/250", "EXIF:FNumber": "5.6", "EXIF:ISO": "200", "EXIF:FocalLength": "35.0 mm",
          "File:FileSize": "4.6 MB", "File:FileType": "JPEG", "XMP:Title": "Start line", "XMP:Description": "Start line"}


def note(text):
    sys.stderr.write("[roots at scale] %s\n" % text)


def make_library(home, photos, faces_count):
    """A library of `photos` photos in 40 folders under Training/Pictures, a few thousand beside
    it under no root, and `faces_count` faces, as the indexer's rows are shaped. Returns
    (the library, the Pictures folder)."""
    pictures = os.path.join(home.root, "Training", "Pictures")
    os.makedirs(pictures)
    db_path = home.library("photo_index.db")
    library_actions.create(db_path)
    outside = os.path.join(home.root, "Training", "Elsewhere")
    conn = db.connect(db_path)
    try:
        rows = []
        for n in range(photos):
            if n % 40 == 39:
                folder = os.path.join(outside, "Loose %d" % (n % 7))
            else:
                folder = os.path.join(pictures, "%d" % (2015 + n % 10), "Event %02d" % (n % 40))
            path = os.path.join(folder, "IMG_%06d.jpg" % n)
            raw = dict(FIELDS, SourceFile=path.replace(os.sep, "/"))
            rows.append((path, 1_700_000_000.0 + n, 4_000_000 + n, json.dumps(["Activity/Sailing", "Places/Harbour"]),
                         json.dumps(["Start line"]), json.dumps(raw), "xmp.did:%08d" % n))
        conn.executemany("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata, document_id)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        per_photo = max(1, faces_count // photos)
        conn.executemany("INSERT INTO faces (photo_id, box, embedding, name, name_source) VALUES (?, ?, ?, ?, ?)",
                         [(1 + n % photos, "[1, 2, 11, 12]", b"\x00" * 64, "Rowan Thackeray" if n % 9 == 0 else None,
                           "manual" if n % 9 == 0 else None) for n in range(per_photo * photos)])
        conn.commit()
    finally:
        conn.close()
    return Library(db_path), pictures


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class TheSizeOfPhotoIndex(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = own_home.for_class(cls, prefix="roots_scale_")
        cls.photos = int(PHOTOS * SCALE)
        cls.faces = int(FACES * SCALE)
        #: Every 40th photo is beside the root, not under it.
        cls.under_the_root = sum(1 for n in range(cls.photos) if n % 40 != 39)
        started = time.time()
        cls.library, cls.pictures = make_library(cls.home, cls.photos, cls.faces)
        note("made %d photos, %d faces in %.1f s" % (cls.photos, cls.faces, time.time() - started))

    def read_all(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            started = time.time()
            rows = store_photos.index_rows(conn, "m")
            return time.time() - started, len(rows)
        finally:
            conn.close()

    def test_adopting_it_takes_seconds_and_undoing_it_takes_seconds(self):
        machine = rl.machine()
        started = time.time()
        plain_read, rows = self.read_all()
        note("reading every row of the unconverted library: %.1f s (%d rows)" % (plain_read, rows))

        started = time.time()
        report = roots_service.adopt(self.library, "pictures", rl.ADDRESS, self.pictures, machine)
        dry = time.time() - started
        self.assertIsNone(report.refused, report.refused)
        photos = report.details["rehearsal"]["tables"]["photos"]
        self.assertEqual(self.photos, photos["rows"])
        self.assertEqual(self.under_the_root, photos["convert"])
        note("dry run: %.1f s" % dry)

        started = time.time()
        converted = roots_service.adopt(self.library, "pictures", rl.ADDRESS, self.pictures, machine, apply=True)
        total = time.time() - started
        self.assertTrue(converted.ok and not converted.refused, converted.message())
        self.assertEqual(self.under_the_root, converted.details["adopted"]["tables"]["photos"]["convert"])
        note("adopt --apply (new backup, conversion, verification, journal): %.1f s" % total)
        self.assertLess(total, 90, "adopting took minutes")

        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            started = time.time()
            problems = adoption.verify(conn, store_roots.roots_for(conn))
            note("the doctor's check of every rooted row: %.2f s" % (time.time() - started))
            self.assertEqual([], problems)
            self.assertEqual(self.photos, conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0])
            self.assertEqual(max(1, self.faces // self.photos) * self.photos,
                             conn.execute("SELECT COUNT(*) FROM faces").fetchone()[0])
            self.assertEqual(self.under_the_root, conn.execute(
                "SELECT COUNT(*) FROM photos WHERE substr(path, 1, 1) = '@'").fetchone()[0])
        finally:
            conn.close()

        rooted_read, _rows = self.read_all()
        note("reading every row of the converted library: %.1f s" % rooted_read)

        self.lookups_use_their_indexes()

        started = time.time()
        undone = journal_service.undo(self.library, converted.details["change"], apply=True)
        note("undo (rehearsal, then the conversion back): %.1f s" % (time.time() - started))
        self.assertIsNone(undone.refused, undone.refused)
        self.assertEqual([], store_roots.listing(self.library.path))
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM photos WHERE substr(path, 1, 1) = '@'").fetchone()[0])
        finally:
            conn.close()

    def lookups_use_their_indexes(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            photo = os.path.join(self.pictures, "2015", "Event 10", "IMG_000010.jpg")
            folder = os.path.join(self.pictures, "2015", "Event 10")
            lookups = {
                "a photo by its path": store_roots.sql_equals(conn, "path", photo),
                "the photos of a folder": store_roots.sql_under(conn, "path", folder),
                "the photos directly in a folder": store_roots.sql_in(conn, "path", folder),
                "the photos of the whole root": store_roots.sql_under(conn, "path", self.pictures),
            }
            for what, (where, params) in lookups.items():
                plan = " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN SELECT id FROM photos WHERE " + where,
                                                                     params))
                self.assertIn("SEARCH photos", plan, what)
                self.assertNotIn("SCAN photos", plan, what)
            where, params = faces._on_photo(conn, photo)
            plan = " | ".join(row[-1] for row in conn.execute("EXPLAIN QUERY PLAN SELECT name FROM faces WHERE " + where,
                                                                 params))
            self.assertIn("SEARCH", plan)
            self.assertNotIn("SCAN faces", plan)
            self.assertNotIn("SCAN photos", plan)
            started = time.time()
            for _ in range(200):
                store_photos.details(conn, photo)
                faces.count_for_photo(conn, photo)
            per_click = (time.time() - started) / 200
            note("a photo's details and its faces' count: %.2f ms" % (per_click * 1000))
            self.assertLess(per_click, 0.05)
            started = time.time()
            listed = store_photos.rows_under(conn, folder)
            note("the folder's rows (%d): %.0f ms" % (len(listed), (time.time() - started) * 1000))
            self.assertTrue(listed)
            self.assertTrue(folders.holds(conn, folder))
            started = time.time()
            under_root = store_photos.count_under(conn, self.pictures)
            note("counting the photos under the root (%d): %.0f ms" % (under_root, (time.time() - started) * 1000))
            self.assertEqual(self.under_the_root, under_root)
            ids = inspection.ids_and_paths(conn)
            self.assertEqual(self.photos, len(ids))
            self.assertEqual(sorted((p for _i, p in ids), key=str.lower), [p for _i, p in ids])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
