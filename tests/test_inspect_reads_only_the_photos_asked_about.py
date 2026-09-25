"""inspect.photos(folder=..., tag=...) reads the tags of the folder's photos only.

It narrowed by folder, then read every photo's tags in the library to filter thirty of
them by tag (docs/findings.md, #185).
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import inspect  # noqa: E402
from tagpup.store import db, inspection, schema  # noqa: E402

HARBOUR = r"D:\Pictures\Harbour"
SCHOOL = r"D:\Pictures\School"


class PhotosByFolderAndTag(unittest.TestCase):
    def setUp(self):
        home = own_home.for_test(self)
        self.db_path = home.library("library.db")
        schema.ensure(self.db_path)
        self.library = Library(self.db_path)
        conn = db.connect(self.db_path)
        try:
            self.ids = {}
            for folder in (HARBOUR, SCHOOL):
                for n, tags in enumerate((["Places/Harbour/Pier"], ["Activity/Running"], [])):
                    path = os.path.join(folder, "IMG_%04d.jpg" % n)
                    self.ids[path] = photo_rows.add_read(conn, path, {"XMP:Subject": tags})
            conn.commit()
        finally:
            conn.close()

    def test_a_folder_and_a_tag_read_the_folders_photos_only(self):
        with mock.patch.object(inspection, "tags_of_every_photo",
                               side_effect=AssertionError("read every photo's tags")):
            found = inspect.photos(self.library, folder=HARBOUR, tag="Places/Harbour", reveal=True)
        self.assertEqual([os.path.join(HARBOUR, "IMG_0000.jpg")], found["paths"])

    def test_a_tag_alone_still_finds_it_in_every_folder(self):
        found = inspect.photos(self.library, tag="Places/Harbour", reveal=True)
        self.assertEqual(sorted([os.path.join(HARBOUR, "IMG_0000.jpg"), os.path.join(SCHOOL, "IMG_0000.jpg")]),
                         sorted(found["paths"]))


if __name__ == "__main__":
    unittest.main()
