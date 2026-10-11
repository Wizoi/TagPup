"""A bulk edit when ExifTool stalls or a write fails (tests/test_bulk_edits.py is the job's own): a stalled ExifTool costs a chunk
one minute and is never retried photo by photo, a slow claim blocks no status, and no exception's text is kept. Fictional names only.
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import exiftool_session, field_values  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402


class WhatTheJobKeeps(T.Bulk):
    def test_a_slow_claim_blocks_neither_a_status_nor_a_cancel(self):
        release = threading.Event()
        entered = threading.Event()
        real = bulk_edits.runs_service.claim

        def slow(*args, **kwargs):
            entered.set()
            release.wait(30)
            return real(*args, **kwargs)
        self.addCleanup(release.set)
        outcome = []
        with mock.patch.object(bulk_edits.runs_service, "claim", slow):
            starter = threading.Thread(target=lambda: outcome.append(bulk_edits.start(
                self.library, bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]}), self.ids, "exiftool")))
            starter.start()
            self.assertTrue(entered.wait(30))
            asked = []
            other = threading.Thread(target=lambda: asked.append((bulk_edits.status(self.library, 777),
                                                                  bulk_edits.running(self.library))))
            other.start()
            other.join(10)
            self.assertFalse(other.is_alive(), "a status waited for the claim")
            self.assertEqual([(None, 0)], asked)
            release.set()
            starter.join(60)
        bulk_edits._held(self.library)[outcome[0].handle].thread.join(60)

    def test_a_job_that_is_over_keeps_nothing_in_the_cache_folder(self):
        for op, params in (("tags", {"add": ["Trips/Coast"]}), ("people", {"add": ["Rowan Thackeray"]})):
            handle = self.finish(self.start(op, params=params)["job"])["job"]
            self.assertFalse(os.path.isdir(self.library.bulk_jobs) and os.listdir(self.library.bulk_jobs), op)
            self.assertEqual("done", self.status(handle)["state"])
        gate = T.Gate(self.files.writes + 2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        cancelled = self.tags_job(add=["Trips/Lakes"])["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": cancelled})
        gate.release.set()
        self.finish(cancelled)
        self.assertFalse(os.path.isdir(self.library.bulk_jobs) and os.listdir(self.library.bulk_jobs))

    def test_no_exception_text_is_written_to_the_library_and_an_error_is_capped(self):
        secret = "C:\\Users\\Someone\\Pictures\\Private Folder\\clip.jpg is locked by a program"
        self.files.cannot_start = FileNotFoundError(secret)
        handle = self.finish(self.tags_job(add=["Trips/Coast"])["job"])["job"]
        seen = self.status(handle)
        run = self.vl.rows("SELECT note, changed FROM job_runs WHERE id = ?", handle)[0]
        for kept in (seen["message"], run[0], run[1]):
            self.assertNotIn("Private Folder", kept)
        self.assertIn("FileNotFoundError", seen["message"])
        self.assertIn("FileNotFoundError", run[0])
        self.files.cannot_start = None
        target = self.path(self.ids[3])

        def fails(path, _n):
            if paths.key(path) == paths.key(target):
                raise RuntimeError("y" * 500)
        self.files.on_write = fails
        done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(200, len(done["errors"][0]["why"]))
        self.assertLessEqual(len(done["errors"][0]["name"]), 200)


class AStalledExifTool(T.Bulk):
    def stall(self, read=False, write_at=None):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s (a read); its process was killed.")
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

    def test_a_chunk_gets_its_own_session_with_a_one_minute_deadline(self):
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertTrue(self.files.sessions)
        self.assertEqual({60}, {each.get("timeout") for each in self.files.sessions if "timeout" in each})
        self.assertEqual(3, len([each for each in self.files.sessions if each.get("timeout") == 60]), "one for each chunk")

    def test_a_read_that_times_out_costs_a_chunk_one_timeout_writes_nothing_and_the_rule_stops_the_job(self):
        self.stall(read=True)
        with mock.patch.object(bulk_edits, "CHUNK", 5):
            done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(("failed", 25, 25, 0), (done["state"], done["done"], done["error_count"], done["changed"]))
        self.assertEqual(5, self.files.read_calls, "one command for each chunk, not one for each photo")
        self.assertEqual(0, self.files.writes)
        self.assertIn("did not answer", done["errors"][0]["why"])
        self.assertTrue(file_changes._one_at_a_time.acquire(blocking=False), "the lock is released")
        file_changes._one_at_a_time.release()

    def test_a_write_that_times_out_leaves_the_rest_of_the_chunk_unwritten_and_the_next_chunk_goes_on(self):
        first = self.order()[:25]
        self.stall(write_at=3)
        done = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(("done", 23), (done["state"], done["error_count"]))
        self.assertEqual(23, len([photo_id for photo_id in first if "Trips/Coast" not in self.tags(photo_id)]),
                         "two written, the third timed out, the others were not tried")
        self.assertEqual(3 + 35, self.files.writes, "after the stall the chunk began no write: the next chunks wrote their 35")
        for photo_id in self.order()[25:]:
            self.assertIn("Trips/Coast", self.tags(photo_id))

    def test_a_cancel_after_a_stalled_chunk_is_honoured_at_once(self):
        reached, release = threading.Event(), threading.Event()
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 60s; its process was killed.")

        def hook(_path, _n):
            reached.set()
            release.wait(30)
            raise stalled
        self.files.on_read = hook
        self.addCleanup(release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(reached.wait(30))
        self.post("cancel", {"job": handle})
        release.set()
        done = self.finish(handle)
        self.assertEqual(("cancelled", 25, 1), (done["state"], done["done"], self.files.read_calls))


if __name__ == "__main__":
    unittest.main()
