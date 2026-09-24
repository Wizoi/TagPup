"""tagpup.services.tagging.save_photo: saving the photo panel.

ExifTool is stood in for, answering each read with what the file holds, and so is the
rename after a caption; the index rows are real.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402

from tagpup.services import tagging  # noqa: E402

FORMAT = "{grouping} - {index} - {caption}"


class SavingAPhoto(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("Regatta - 1.jpg")
        self.lib.add_row(self.photo, tags=["Beach"])
        self.lib.add_face(self.photo, [0, 0, 10, 8], name="Rowan Thackeray")
        self.holds = {"XMP:Subject": ["Beach"]}
        self.et = mock.MagicMock()
        self.et.get_tags.side_effect = lambda paths, tags=None: [dict(self.holds, SourceFile=paths[0])]
        session = mock.MagicMock()
        session.return_value.__enter__.return_value = self.et
        session.return_value.__exit__.return_value = False
        for patcher in (mock.patch("tagpup.files.exiftool_session.ExifToolSession", session),
                        mock.patch("tagpup.files.metadata.sync_title_to_filename", side_effect=self.rename)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.renamed_to = None

    def rename(self, photo_path, title, exiftool, rename_format):
        if not self.renamed_to:
            return photo_path
        os.rename(photo_path, self.renamed_to)
        return self.renamed_to

    def save(self, title="Start", tags=("Beach", "Relay"), date_taken=None):
        return tagging.save_photo(self.lib.library, self.photo, title, list(tags), date_taken,
                                  "exiftool", FORMAT)

    def written(self):
        return self.et.set_tags.call_args.kwargs["tags"]

    def test_the_caption_the_tags_and_the_date_go_in_one_write(self):
        result = self.save(date_taken="2019-06-15T10:30:00.25")
        self.assertEqual((result.attempted, result.changed, result.ok), (1, 1, True))
        written = self.written()
        self.assertEqual(written["XMP:Subject"], ["Beach", "Relay"])
        self.assertEqual(written["XMP:Description"], "Start")
        self.assertEqual(written["EXIF:DateTimeOriginal"], "2019:06:15 10:30:00.25")
        self.assertEqual(written["EXIF:SubSecTimeOriginal"], "25")

    def test_the_row_records_what_the_file_holds_after(self):
        self.holds = {"XMP:Subject": ["Beach", "Relay"], "XMP:Description": "Start"}
        self.save()
        tags, people, captions = self.lib.rows("SELECT tags, people, captions FROM photos")[0]
        self.assertEqual(json.loads(tags), ["Beach", "Relay"])
        self.assertEqual(json.loads(captions), ["Start"])
        self.assertEqual(json.loads(people), ["Rowan Thackeray"], "the named face was dropped")

    def test_a_new_tag_that_may_not_be_set_refuses_the_save(self):
        result = self.save(tags=["Beach", "Places|Harbour"])
        self.assertFalse(result.ok)
        self.assertIn('"|"', result.refused)
        self.et.set_tags.assert_not_called()

    def test_a_rename_after_the_caption_moves_the_row_and_its_faces(self):
        self.renamed_to = os.path.join(self.lib.photos, "Regatta - 1 - Start.jpg")
        result = self.save()
        self.assertEqual((result.details["new_path"], result.details["renamed"]), (self.renamed_to, True))
        self.assertEqual(self.lib.rows("SELECT path FROM photos"), [(self.renamed_to,)])
        self.assertEqual(self.lib.rows("SELECT photo_path FROM faces"), [(self.renamed_to,)])


if __name__ == "__main__":
    unittest.main()
