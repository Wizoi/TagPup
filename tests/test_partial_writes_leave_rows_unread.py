"""A write that records part of a file never stamps a row nobody read from its file.

Suggest makes a row for a photo the index never read (store.photos.ensure_row): the
path, and nothing else -- mtime and size empty, so the folder scan and the refresh read
the file. A tag write on such a row recorded the keyword fields as the row's whole raw
metadata and stamped it with the file's mtime and size: the row claimed to match its
file, and was trusted from then on, with no Date Taken (docs/findings.md, "Photos of a
folder showed without their Date Taken"). 107 rows of one folder held only the three
keyword fields.

Each writer here runs on a real photo carrying a Date Taken, through the service the
page calls, with real ExifTool. After it, the row is either still unread (mtime empty),
or holds what its file holds, Date Taken and all. A row read from its file is still
stamped, so the scan need not read it again.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image  # noqa: E402

import own_home  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import exiftool_session  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.services import tagging  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
requires_exiftool = unittest.skipUnless(EXIFTOOL, "ExifTool not installed")

TAKEN = "2019:06:15 10:30:00"


def make_photo(folder, name, keywords=()):
    """A small JPEG carrying a Date Taken, a camera and `keywords`, written by ExifTool."""
    path = os.path.join(folder, name)
    Image.new("RGB", (8, 6), (90, 120, 150)).save(path)
    fields = {"EXIF:DateTimeOriginal": TAKEN, "EXIF:Make": "Quayside Optics"}
    if keywords:
        fields.update({"XMP:Subject": list(keywords), "IPTC:Keywords": list(keywords)})
    with exiftool_session.ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=fields, params=["-overwrite_original"])
    return path


class Photos(unittest.TestCase):
    """A library in a home of its own, a folder of real photos beside it."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour Day")
        os.makedirs(self.folder)

    def suggested(self, path):
        """The row Suggest makes for a photo the index never read."""
        conn = db.connect(self.db_path)
        try:
            store_photos.ensure_row(conn, path)
            conn.commit()
        finally:
            conn.close()

    def row(self, path):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            mtime, size, raw, taken = conn.execute(
                "SELECT mtime, size, raw_metadata, taken FROM photos WHERE path = ?", (path,)).fetchone()
        finally:
            conn.close()
        return mtime, size, json.loads(raw or "{}"), taken


@requires_exiftool
class PartialWritesOnAnUnreadRow(Photos):

    def assertUnreadOrWhole(self, path):
        mtime, size, raw, taken = self.row(path)
        if mtime is None:
            self.assertIsNone(size)
            return
        # Stamped: then it must hold what the file holds, its Date Taken first.
        stat = os.stat(path)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        self.assertEqual(raw.get("EXIF:DateTimeOriginal"), TAKEN,
                         "the row claims to match its file but holds only %s" % sorted(raw))
        self.assertIsNotNone(taken)

    def assertUnread(self, path):
        mtime, size, raw, _taken = self.row(path)
        self.assertEqual((mtime, size), (None, None),
                         "a partial write stamped a row never read from its file (it holds %s)" % sorted(raw))

    def test_apply_all(self):
        path = make_photo(self.folder, "a.jpg")
        self.suggested(path)
        result = tagging.add_tags(self.library, {path: ["Activity/Sailing"]}, EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_add_to_all_selected(self):
        path = make_photo(self.folder, "b.jpg")
        self.suggested(path)
        result = tagging.change_tags(self.library, [path], ["Activity/Sailing"], [], EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_renaming_a_tag(self):
        path = make_photo(self.folder, "c.jpg", keywords=["Activity/Sailing"])
        self.suggested(path)
        result = tagging.replace_tag(self.library, [path], "Activity/Sailing", "Activity/Rowing", EXIFTOOL)
        self.assertEqual((result.ok, result.changed), (True, 1))
        self.assertUnread(path)

    def test_a_photo_not_carrying_the_renamed_tag(self):
        """Its row is told what the file holds, and nothing was written to the file."""
        path = make_photo(self.folder, "d.jpg", keywords=["Activity/Rowing"])
        self.suggested(path)
        tagging.replace_tag(self.library, [path], "Activity/Sailing", "Activity/Kayaking", EXIFTOOL)
        self.assertUnread(path)

    def test_writing_suggestions(self):
        tagged = make_photo(self.folder, "e.jpg")
        captioned = make_photo(self.folder, "f.jpg")
        for path in (tagged, captioned):
            self.suggested(path)
        result = tagging.write_suggestions(
            self.library, [(tagged, ["Activity/Sailing"], None), (captioned, [], "Harbour at dusk")], EXIFTOOL,
            nobackup=True)
        self.assertEqual((result.ok, result.changed), (True, 2))
        self.assertUnread(tagged)
        self.assertUnread(captioned)

    def test_rotating(self):
        path = make_photo(self.folder, "g.jpg")
        self.suggested(path)
        result = photo_actions.rotate(self.library, path, "left", EXIFTOOL)
        self.assertTrue(result.ok)
        self.assertUnread(path)

    def test_saving_one_photo(self):
        """The save reads the file back whole, so it may record it all."""
        path = make_photo(self.folder, "h.jpg")
        self.suggested(path)
        result = tagging.save_photo(self.library, path, "", ["Activity/Sailing"], None, EXIFTOOL, "")
        self.assertTrue(result.ok)
        self.assertUnreadOrWhole(path)

    def test_shifting_date_taken(self):
        """The shift reads the files back whole too."""
        path = make_photo(self.folder, "i.jpg")
        self.suggested(path)
        result = photo_actions.shift_date_taken(self.library, [path], 0, EXIFTOOL)
        self.assertTrue(result.ok)
        self.assertUnreadOrWhole(path)


@requires_exiftool
class PartialWritesOnARowReadFromItsFile(Photos):
    """A row read from its file is stamped by the write, as before, or every photo
    tagged in bulk is read again on every scan."""

    def indexed(self, path):
        stat = os.stat(path)
        raw = {"SourceFile": path, "EXIF:DateTimeOriginal": TAKEN, "DateTimeOriginal": TAKEN,
               "EXIF:Make": "Quayside Optics", "Make": "Quayside Optics"}
        conn = db.connect(self.db_path)
        try:
            conn.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata)"
                         " VALUES (?, ?, ?, '[]', '[]', ?)", (path, stat.st_mtime, stat.st_size, json.dumps(raw)))
            store_photos.date_photos(conn)
            conn.commit()
        finally:
            conn.close()

    def test_apply_all_stamps_a_row_that_was_read(self):
        path = make_photo(self.folder, "j.jpg")
        self.indexed(path)
        tagging.add_tags(self.library, {path: ["Activity/Sailing"]}, EXIFTOOL)
        mtime, size, raw, taken = self.row(path)
        stat = os.stat(path)
        self.assertEqual((mtime, size), (stat.st_mtime, stat.st_size))
        self.assertEqual((raw["EXIF:DateTimeOriginal"], raw["XMP:Subject"]), (TAKEN, ["Activity/Sailing"]))
        self.assertIsNotNone(taken)


if __name__ == "__main__":
    unittest.main()
