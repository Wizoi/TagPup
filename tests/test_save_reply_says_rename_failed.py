"""A caption saved on a Smart-Renamed photo whose rename then fails says so (findings #884).

The save wrote the caption, the follow-up rename failed (a locked file, a held share) and the reply said only
`success`, so the page told the owner the photo was saved and nothing of the name it kept. The reply carries
`rename_failed` now, and the page's test (tests/frontend/rename-failed.test.mjs) answers from the replies this file
writes to tests/frontend/fixtures/save_replies.json. Real ExifTool, real files, rows as the indexer makes them.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_stale_record_precondition as S  # noqa: E402

from tagpup.files import names  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "fixtures", "save_replies.json")


@unittest.skipIf(S.EXIFTOOL is None, "ExifTool not installed")
class SavingACaptionThatCannotRename(S.Fresh):
    def smart_renamed(self, seed=1):
        path, photo_id = self.make("Regatta - 1.jpg", seed, ["Trips/Coast"])
        S.write_into(path, **{"XMP-xmpMM:PreservedFileName": "IMG_0001.jpg"})
        return path, self.record(photo_id)

    def test_a_locked_file_keeps_its_name_and_the_reply_says_why(self):
        path, record = self.smart_renamed()
        refused = names.RenameFailed("The file is open in another program (held by Viewer)", [])
        with mock.patch("tagpup.files.names.rename_all", side_effect=refused):
            reply = self.save(record, title="Start line")
        body = reply.get_json()
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertTrue(body["success"])
        self.assertEqual(path, body["new_path"])
        self.assertIn("held by Viewer", body["rename_failed"])
        self.assertTrue(os.path.exists(path))
        self.assertEqual("Start line", S.caption_in(path))

    def test_a_save_that_renames_or_attempts_no_rename_has_no_warning(self):
        path, record = self.smart_renamed()
        taken = os.path.join(self.folder, "Regatta - 1 - Start line.jpg")
        S.damaged_photos.whole_jpeg(taken, seed=77)       # the name the caption wants is taken: a free one is chosen
        renamed = self.save(record, title="Start line").get_json()
        self.assertNotIn("rename_failed", renamed)
        self.assertTrue(renamed["new_path"].endswith("Regatta - 1 - Start line_1.jpg"), renamed["new_path"])
        plain, plain_id = self.make("IMG_0002.jpg", 3, ["Trips/Coast"])
        plain_record = self.record(plain_id)
        reply = self.save(plain_record, title="A caption").get_json()
        self.assertNotIn("rename_failed", reply)
        self.assertEqual(plain, reply["new_path"])

    def test_the_replies_the_page_reads_are_written_for_its_test(self):
        path, record = self.smart_renamed()
        refused = names.RenameFailed("The file is open in another program (held by Viewer)", [])
        found = {}
        with mock.patch("tagpup.files.names.rename_all", side_effect=refused):
            found["rename_failed"] = self.save(record, title="Start line").get_json()
        plain, plain_id = self.make("IMG_0002.jpg", 3, ["Trips/Coast"])
        plain_record = self.record(plain_id)
        found["no_rename"] = self.save(plain_record, title="A caption").get_json()
        for each in found.values():
            for key in ("mtime", "size"):
                each[key] = 1
        for each in found.values():
            for key in ("new_path",):
                each[key] = os.path.basename(each[key])
        text = json.dumps(found, sort_keys=True, indent=1) + "\n"
        if not os.path.exists(FIXTURE) or open(FIXTURE, encoding="utf-8").read() != text:
            with open(FIXTURE, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)


if __name__ == "__main__":
    unittest.main()
