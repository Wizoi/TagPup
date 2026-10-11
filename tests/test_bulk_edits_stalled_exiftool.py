"""An edit of a selection when ExifTool stalls (tests/test_bulk_edits.py is the edit's own): a stalled ExifTool costs the edit one
deadline and is never retried photo by photo, whatever the count. Fictional names only.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

from tagpup.files import exiftool_session, field_values  # noqa: E402
from tagpup.services import file_changes  # noqa: E402


class AStalledExifTool(T.Bulk):
    def stall(self, read=False, write_at=None):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")
        if read:
            def hook(_path, _n):
                raise stalled
            self.files.on_read = hook
        if write_at is not None:
            def writing(_path, n):
                if n == write_at:
                    raise stalled
            self.files.on_write = writing

    def order(self):
        return self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]

    def test_read_does_not_retry_photo_by_photo_after_a_timeout(self):
        calls = []

        class Stalled:
            timeout = 60

            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer")
        found = field_values.read(Stalled(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(1, len(calls))
        self.assertEqual(3, len(found))
        self.assertTrue(all(isinstance(each, field_values.Unreadable) and "did not answer in 60 s" in str(each) for each in found.values()))

    def test_read_still_retries_singly_a_batch_one_bad_path_made_exiftool_refuse(self):
        calls = []

        class Refuses:
            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                if len(batch) > 1:
                    raise RuntimeError("one of them is no file")
                return [{"SourceFile": batch[0], "File:MIMEType": "image/jpeg"}]
        found = field_values.read(Refuses(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(4, len(calls))
        self.assertFalse(any(isinstance(each, Exception) for each in found.values()))

    def test_a_read_that_times_out_costs_the_edit_one_timeout_and_writes_nothing(self):
        self.stall(read=True)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS, 0), (done["error_count"], done["changed"]))
        self.assertEqual(1, self.files.read_calls, "one command for the 60 photos, not one for each")
        self.assertEqual(0, self.files.writes)
        self.assertIn("did not answer", done["errors"][0]["why"])
        self.assertTrue(file_changes._one_at_a_time.acquire(blocking=False), "the lock is released")
        file_changes._one_at_a_time.release()

    def test_a_write_that_times_out_leaves_the_rest_unwritten_each_an_error_and_no_other_deadline_is_waited_for(self):
        self.stall(write_at=3)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((2, self.PHOTOS - 2), (done["changed"], done["error_count"]))
        self.assertEqual(3, self.files.writes, "after the stall no write was begun: the session stays dead")
        order = self.order()
        self.assertEqual(2, len([photo_id for photo_id in order if "Trips/Coast" in self.tags(photo_id)]))
        self.assertEqual("applied", self.changes("bulk tags%")[0][2], "what was written is one change, finished")

    def test_the_edit_after_a_stalled_one_writes_the_rest(self):
        self.stall(write_at=3)
        self.tags_edit(add=["Trips/Coast"])
        self.files.on_write = None
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS - 2, 2), (done["changed"], done["unchanged"]))


if __name__ == "__main__":
    unittest.main()
