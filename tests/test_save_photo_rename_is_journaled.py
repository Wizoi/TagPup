"""A caption saved on a Smart-Renamed photo renames the file after it (docs/findings.md, #298).

The rename went through os.rename in tagpup.files.metadata, outside the file journal: it was not in
History and could not be undone. It is a journaled rename now (tagpup.services.file_changes.rename),
the rename of Smart Rename. Nothing is stood in for but ExifTool; the file is renamed for real.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.files import names  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402

FORMAT = "{grouping} - {index} - {caption}"


class SavingACaptionThatRenames(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("Regatta - 1.jpg")
        self.lib.add_row(self.photo, tags=["Beach"])
        self.holds = {"XMP:Subject": ["Beach"], "XMP:PreservedFileName": ["IMG_0001.jpg"]}
        self.et = mock.MagicMock()
        self.et.get_tags.side_effect = lambda paths, tags=None: [dict(self.holds, SourceFile=paths[0])]
        self.et.set_tags.side_effect = lambda paths, tags=None, params=None: self.holds.update(tags or {})
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = self.et
        session.return_value.__exit__.return_value = False
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.renamed_to = os.path.join(self.lib.photos, "Regatta - 1 - Start.jpg")

    def save(self):
        return tagging.save_photo(self.lib.library, self.photo, "Start", ["Beach"], None, "exiftool", FORMAT)

    def journaled_renames(self):
        return self.lib.rows("SELECT path, new_path, state FROM change_files WHERE new_path IS NOT NULL")

    def test_the_rename_is_in_the_journal(self):
        result = self.save()
        self.assertEqual((result.details["new_path"], result.details["renamed"]), (self.renamed_to, True))
        self.assertTrue(os.path.exists(self.renamed_to))
        self.assertEqual(self.journaled_renames(), [(self.photo, self.renamed_to, "done")])
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(self.renamed_to,)])

    def test_the_rename_can_be_undone(self):
        self.save()
        change = self.lib.rows("SELECT change_id FROM change_files WHERE new_path IS NOT NULL")[0][0]
        undone = file_changes.undo(self.lib.library, change, "exiftool", apply=True)
        self.assertEqual(undone.changed, 1)
        self.assertTrue(os.path.exists(self.photo))
        self.assertFalse(os.path.exists(self.renamed_to))
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(self.photo,)])

    def test_a_rename_that_fails_leaves_the_file_and_says_so(self):
        with mock.patch("tagpup.files.names.rename_all", side_effect=names.RenameFailed("in use", [])):
            result = self.save()
        self.assertEqual((result.details["new_path"], result.details["renamed"]), (self.photo, False))
        self.assertIn("in use", result.details["rename_failed"])
        self.assertTrue(os.path.exists(self.photo))


if __name__ == "__main__":
    unittest.main()
