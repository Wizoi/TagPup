"""ExifTool stalls (a share gone away): an edit of a selection (tests/test_bulk_edits.py is the edit's own), the indexer's read
and sync's pairing read each cost one deadline and are never retried photo by photo, whatever the count; what the indexer did not
hear about is left unread, never stamped (docs/findings.md, #1473). Fictional names only.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

from tagpup.files import exiftool_session, field_values, metadata  # noqa: E402
from tagpup.services import batches, file_changes, relink_photos  # noqa: E402


class StalledSession:
    """An ExifTool session whose every read waits out its deadline, as one on a share that went away does."""
    calls = []
    timeout = 300

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_tags(self, batch, tags=None, params=None):
        StalledSession.calls.append(list(batch))
        raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")


class TheIndexAndSyncReadAShareThatStopsAnswering(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="stalled_read_")
        self.addCleanup(shutil.rmtree, folder, True)
        self.photos = []
        for name in ("Quay.jpg", "Slipway.jpg", "Beacon.jpg"):
            path = os.path.join(folder, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.photos.append(path)
        StalledSession.calls = []

    def test_the_indexers_read_waits_one_deadline_and_leaves_every_photo_unread(self):
        with mock.patch.object(metadata, "ExifToolSession", StalledSession):
            records = metadata.MetadataExtractor().batch_read(self.photos)
        self.assertEqual(1, len(StalledSession.calls), "one command for the three photos, not one more for each")
        self.assertEqual(self.photos, [record["path"] for record in records])
        # A record with no stamp is one nobody read: the indexer leaves its row as it was and reads it next time.
        self.assertEqual([(None, None)] * 3, [(record["mtime"], record["size"]) for record in records])
        self.assertTrue(all(record["read_error"] for record in records))

    def test_a_batch_refused_then_a_photo_of_it_not_answered_leaves_them_all_unread(self):
        """#1490: a batch ExifTool refuses is read again a photo at a time; the first of those it does not answer ends the
        read. It was a plain Unreadable, stamped holding nothing, and each next photo waited its own deadline."""
        class RefusesThenStalls(StalledSession):
            def get_tags(self, batch, tags=None, params=None):
                StalledSession.calls.append(list(batch))
                if len(batch) > 1:
                    raise RuntimeError("Error: one of them is no file")
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")
        with mock.patch.object(metadata, "ExifToolSession", RefusesThenStalls):
            records = metadata.MetadataExtractor().batch_read(self.photos)
        self.assertEqual(2, len(StalledSession.calls), "the batch, then one photo: no deadline more")
        self.assertEqual([(None, None, [], metadata.NOT_ANSWERED)] * 3,
                         [(r["mtime"], r["size"], r["tags"], r["unread"]) for r in records])

    def test_syncs_pairing_read_waits_one_deadline_and_pairs_nothing(self):
        with mock.patch.object(exiftool_session, "ExifToolSession", StalledSession):
            by_original, by_identity = relink_photos.claims_of(self.photos)
        self.assertEqual(1, len(StalledSession.calls))
        self.assertEqual(({}, {}), (by_original, by_identity))


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

    def test_a_batch_cancelled_while_it_reads_its_files_stops_reading_and_writes_nothing(self):
        """#1200: the read a batch plans from (about 53 ms a photo, 4.5 minutes for 5,000) could not be stopped: Cancel or
        a hand-over took effect only when it ended. It stops at the next batch of reads."""
        def cancel_at_the_first_read(_path, _n):
            batches.current().stop.set()
        self.files.on_read = cancel_at_the_first_read
        with mock.patch.object(field_values, "READ_BATCH", 10):
            done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((0, 0), (done["changed"], self.files.writes))
        self.assertEqual(1, self.files.read_calls, "one batch of reads of the six, not all of them")
        self.assertIn("Stopped as asked before anything was written", done["errors"][0]["why"])

    def test_the_edit_after_a_stalled_one_writes_the_rest(self):
        self.stall(write_at=3)
        self.tags_edit(add=["Trips/Coast"])
        self.files.on_write = None
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS - 2, 2), (done["changed"], done["unchanged"]))


if __name__ == "__main__":
    unittest.main()
