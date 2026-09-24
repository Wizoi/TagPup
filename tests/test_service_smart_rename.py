"""tagpup.services.photos.smart_rename: Smart Rename.

What each photo's caption is, and whether a preserved name is written, is stood in for
(tagpup.files.names.read_for_renaming); the renames on disk and the index rows are real.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import photos  # noqa: E402

FORMAT = "{grouping} - {index} - {caption}"


class SmartRenaming(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)

    def rename(self, paths, captions, grouping="Regatta"):
        with mock.patch("tagpup.files.names.read_for_renaming",
                        side_effect=lambda exiftool, present: {p: captions.get(p, "") for p in present}):
            return photos.smart_rename(self.lib.library, paths, grouping, FORMAT, "exiftool")

    def names(self):
        return sorted(os.listdir(self.lib.photos))

    def test_photos_are_numbered_in_the_order_given_and_named_for_their_captions(self):
        c, a, b = (self.lib.photo(n) for n in ("c.jpg", "a.jpg", "b.jpg"))
        result = self.rename([c, a, b], {c: "Start", b: "Finish"})
        self.assertEqual(self.names(), ["Regatta - 1 - Start.jpg", "Regatta - 2.jpg",
                                        "Regatta - 3 - Finish.jpg"])
        self.assertEqual((result.attempted, result.changed, result.errors), (3, 3, []))

    def test_the_index_follows_the_files(self):
        a = self.lib.photo("a.jpg")
        self.lib.add_row(a)
        self.lib.add_face(a, [0, 0, 10, 8], name="Rowan Thackeray")
        result = self.rename([a], {a: "Start"})
        new = os.path.join(self.lib.photos, "Regatta - 1 - Start.jpg")
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(new,)])
        self.assertEqual(self.lib.rows("SELECT photo_path, name FROM faces"), [(new, "Rowan Thackeray")])
        self.assertEqual(result.details["index_rows_moved"], 1)

    def test_a_file_holding_a_new_name_is_moved_aside_with_its_row(self):
        a = self.lib.photo("a.jpg")
        occupant = self.lib.photo("Regatta - 1.jpg", content=b"another photo")
        self.lib.add_row(occupant)
        result = self.rename([a], {})
        aside = os.path.join(self.lib.photos, "Regatta - 1_conflict_1.jpg")
        self.assertEqual(result.details["moved_aside"], {occupant: aside})
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(aside,)])
        with open(aside, "rb") as handle:
            self.assertEqual(handle.read(), b"another photo")

    def test_a_photo_no_longer_on_disk_keeps_its_number_unused(self):
        a, b = self.lib.photo("a.jpg"), self.lib.photo("b.jpg")
        gone = os.path.join(self.lib.photos, "gone.jpg")
        self.rename([a, gone, b], {})
        self.assertEqual(self.names(), ["Regatta - 1.jpg", "Regatta - 3.jpg"])

    def test_a_failed_rename_is_undone_and_the_index_left_alone(self):
        a = self.lib.photo("a.jpg")
        self.lib.add_row(a)
        real_rename = os.rename
        failed = []

        def rename(src, dst):
            # The second pass, a temporary name onto the new one, fails once; putting
            # the photo back afterwards works.
            if "tmp_rename_" in os.path.basename(src) and not failed:
                failed.append(src)
                raise PermissionError("the file is open in another program")
            return real_rename(src, dst)

        with mock.patch("os.rename", side_effect=rename):
            result = self.rename([a], {a: "Start"})
        self.assertFalse(result.ok)
        self.assertIn("Every photo was put back", result.message())
        self.assertEqual(self.names(), ["a.jpg"])
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(a,)])


if __name__ == "__main__":
    unittest.main()
