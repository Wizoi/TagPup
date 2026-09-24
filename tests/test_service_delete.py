"""tagpup.services.photos.delete: clicking Delete on a photo.

The Recycle Bin is stood in for; the service's part is what it removes from the
library, in what order, and what its Result says.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402


class DeletingAPhoto(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("finish.jpg")
        self.other = self.lib.photo("finish_2.jpg")
        for path in (self.photo, self.other):
            self.lib.add_row(path)
            self.lib.add_face(path, [0, 0, 10, 8], name="Rowan Thackeray")
            self.lib.execute("INSERT INTO embedding_cache (path, mtime, size, model_name, pretrained,"
                             " preserve_full_frame, max_aspect_ratio, force_image_size, embedding)"
                             " VALUES (?, 1.0, 1, 'm', 'p', 0, 1.0, 1, x'00')", (path,))

    def delete(self, moved=True):
        def recycle(path):
            if moved is True:
                os.remove(path)
                return True
            if isinstance(moved, Exception):
                raise moved
            return moved
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            return photos.delete(self.lib.library, self.photo)

    def counts(self, path):
        return tuple(self.lib.rows("SELECT COUNT(*) FROM %s WHERE %s = ?" % (table, column), (path,))[0][0]
                     for table, column in (("photos", "path"), ("faces", "photo_path"),
                                           ("embedding_cache", "path")))

    def test_the_photo_and_everything_the_index_held_for_it_go(self):
        result = self.delete()
        self.assertEqual((result.attempted, result.changed, result.errors), (1, 1, []))
        self.assertEqual(result.details["removed"], {"faces": 1, "photos": 1, "embedding_cache": 1})
        self.assertEqual(self.counts(self.photo), (0, 0, 0))
        self.assertFalse(os.path.exists(self.photo))

    def test_nothing_else_goes(self):
        self.delete()
        self.assertEqual(self.counts(self.other), (1, 1, 1))

    def test_a_photo_the_recycle_bin_refused_stays_in_the_library(self):
        result = self.delete(moved=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.changed, 0)
        self.assertIn("Recycle Bin", result.message())
        self.assertEqual(self.counts(self.photo), (1, 1, 1))

    def test_an_error_moving_it_is_reported_the_same_way(self):
        result = self.delete(moved=OSError("the file is open elsewhere"))
        self.assertEqual((result.changed, result.message()), (0, "the file is open elsewhere"))
        self.assertEqual(self.counts(self.photo), (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
