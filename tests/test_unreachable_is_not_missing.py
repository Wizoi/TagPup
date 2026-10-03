"""A file on a drive or share that does not answer is not "missing" (docs/findings.md, the follow-up review
of #566): Windows reports a sleeping server, a share name that is not there and an unplugged drive letter with
the same "not found" it uses for a deleted file, at once and not after a wait. A photo called missing is left
out of a bulk write and made read-only in the page, so a sleeping NAS must not be told so. And the gzip
hook honours q=0 in Accept-Encoding."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.services import damaged_photos, tagging  # noqa: E402
from tagpup.web import app as web_app  # noqa: E402


def _not_found(_path):
    raise FileNotFoundError(2, "not found")


class AFileOnADriveThatDoesNotAnswer(unittest.TestCase):
    def test_a_drive_that_is_not_there_does_not_make_the_file_missing(self):
        with mock.patch("tagpup.services.damaged_photos.os.stat", side_effect=_not_found), \
                mock.patch("tagpup.services.damaged_photos.os.path.exists", return_value=False):
            self.assertIs(damaged_photos.stamp_of("Q:\\Pictures\\a.jpg"), damaged_photos.CANNOT_READ)

    def test_a_file_deleted_from_a_drive_that_answers_is_missing(self):
        with mock.patch("tagpup.services.damaged_photos.os.stat", side_effect=_not_found), \
                mock.patch("tagpup.services.damaged_photos.os.path.exists", return_value=True):
            self.assertIsNone(damaged_photos.stamp_of("Q:\\Pictures\\a.jpg"))

    def test_a_path_with_no_drive_is_judged_by_the_file_alone(self):
        with mock.patch("tagpup.services.damaged_photos.os.stat", side_effect=_not_found):
            self.assertIsNone(damaged_photos._stat_state("pictures/a.jpg"))

    def test_a_bulk_write_does_not_leave_such_a_photo_out_as_missing(self):
        paths = ["Q:\\Pictures\\a.jpg", "Q:\\Pictures\\b.jpg"]
        with mock.patch("tagpup.services.damaged_photos.os.stat", side_effect=_not_found), \
                mock.patch("tagpup.services.damaged_photos.os.path.exists", return_value=False):
            present, gone = tagging.leave_out_missing(paths)
        self.assertEqual((present, gone), (paths, []))

    def test_a_bulk_write_leaves_out_the_file_that_is_really_gone(self):
        paths = ["Q:\\Pictures\\a.jpg"]
        with mock.patch("tagpup.services.damaged_photos.os.stat", side_effect=_not_found), \
                mock.patch("tagpup.services.damaged_photos.os.path.exists", return_value=True):
            present, gone = tagging.leave_out_missing(paths)
        self.assertEqual(present, [])
        self.assertEqual([path for path, _ in gone], paths)


class TheGzipHookHonoursQualityValues(unittest.TestCase):
    def test_what_each_header_accepts(self):
        accepts = web_app._accepts_gzip
        self.assertTrue(accepts("gzip, deflate, br"))
        self.assertTrue(accepts("GZIP"))
        self.assertTrue(accepts("deflate, gzip;q=0.5"))
        self.assertTrue(accepts("x-gzip"))
        self.assertFalse(accepts("gzip;q=0"))
        self.assertFalse(accepts("identity;q=1, gzip;q=0"))
        self.assertFalse(accepts("gzip;q=0.0"))
        self.assertFalse(accepts("br"))
        self.assertFalse(accepts(""))
        self.assertFalse(accepts("gzip;q=nonsense"))


if __name__ == "__main__":
    unittest.main()
