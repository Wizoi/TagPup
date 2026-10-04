"""Delete of a library view's selection (#674): a bulk job of op `delete` (tagpup.jobs.bulk_edits, tagpup.services.bulk_edit) that
deletes each photo as the folder view's Delete does (tagpup.services.photos.delete), and the question's answer before it -- how many,
and how many with no Recycle Bin (POST /api/library/selection/delete-check, tagpup.services.selection.where_deleted).

The Recycle Bin is the test's: `send_to_recycle_bin` removes the file and says so, and which folders have none is the test's to
say; nothing reaches the owner's Recycle Bin. Photos are real small files and rows as the indexer records them. Fictional names only.
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from fake_exiftool import Files  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import recycle_bin  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, selection  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}
ALL = {"source": {"kind": "all"}}


class Delete(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.library = self.vl.library
        self.files = Files()
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", self.files.session)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(bulk_edits.forget, self.library)
        self.sent = []
        self.binless = set()          # folders (paths.key) with no Recycle Bin: a share, as far as the test says
        self.gate = None
        bin_patch = mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle)
        bin_patch.start()
        self.addCleanup(bin_patch.stop)
        reason_patch = mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason)
        reason_patch.start()
        self.addCleanup(reason_patch.stop)
        self.vl.tree("Trips/Coast")
        self.coast = [self.vl.photo("2024 Coast", "c%02d.jpg" % n, taken="2024:06:%02d 10:00:00" % (n + 1), real=True, size=(16, 16))
                      for n in range(30)]
        self.lakes = [self.vl.photo("2023 Lakes", "l%02d.jpg" % n, taken="2023:01:%02d 10:00:00" % (n + 1), real=True, size=(16, 16))
                      for n in range(5)]

    def recycle(self, path):
        if self.gate is not None:
            self.gate()
        self.sent.append(path)
        os.remove(path)
        return True

    def reason(self, path):
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        return recycle_bin.NETWORK if paths.key(folder) in self.binless else None

    def folder(self, name):
        return paths.stored(os.path.join(self.vl.pictures, name))

    def start(self, selection_body, params=None, expect=200):
        reply = self.client.post("/library/api/library/bulk/start", json={"op": "delete", "selection": selection_body,
                                                                         "params": params if params is not None else {}})
        self.assertEqual(expect, reply.status_code, reply.get_json())
        return reply.get_json()

    def finish(self, handle):
        bulk_edits._held(self.library)[handle].thread.join(60)
        return self.client.get("/library/api/library/bulk/status", query_string={"job": handle}).get_json()

    def check(self, selection_body, expect=200, **kwargs):
        reply = self.client.post("/library/api/library/selection/delete-check", json={"selection": selection_body}, **kwargs)
        self.assertEqual(expect, reply.status_code, reply.get_json())
        return reply.get_json()

    def held(self, ids):
        found = {row[0] for row in self.vl.rows("SELECT id FROM photos")}
        return [photo_id for photo_id in ids if photo_id in found]


class Deleting(Delete):
    def test_a_selection_across_folders_goes_to_the_bin_and_its_rows_go_with_the_files(self):
        chosen = self.coast[:3] + self.lakes[:2]
        paths_of = [self.vl.path_of(photo_id) for photo_id in chosen]
        done = self.finish(self.start({"ids": chosen})["job"])
        self.assertEqual(("done", 5, 5, 0, 0), (done["state"], done["total"], done["changed"], done["skipped_missing"], done["error_count"]))
        self.assertEqual([], self.held(chosen), "the rows went with the files")
        self.assertFalse([path for path in paths_of if os.path.exists(path)])
        self.assertEqual(sorted(paths.key(path) for path in paths_of), sorted(paths.key(path) for path in self.sent))
        self.assertEqual(self.coast[3:], self.held(self.coast[3:]), "nothing else is touched")
        self.assertEqual([], self.vl.rows("SELECT id FROM changes WHERE operation LIKE 'bulk delete%'"),
                         "as the folder view's Delete: no journal change")

    def test_the_whole_library_less_the_excluded_leaves_exactly_the_excluded(self):
        kept = [self.coast[0], self.lakes[4]]
        done = self.finish(self.start({"source": {"kind": "all"}, "excluded": kept})["job"])
        self.assertEqual(33, done["changed"])
        self.assertEqual(kept, self.held(self.coast + self.lakes))

    def test_a_file_gone_already_is_counted_missing_and_its_row_stays_for_sync_to_report(self):
        os.remove(self.vl.path_of(self.coast[1]))
        done = self.finish(self.start({"ids": self.coast[:3]})["job"])
        self.assertEqual((2, 1, 0), (done["changed"], done["skipped_missing"], done["error_count"]))
        self.assertEqual([self.coast[1]], self.held(self.coast[:3]))

    def test_a_folder_with_no_recycle_bin_is_left_unless_the_owner_was_told(self):
        self.binless.add(paths.key(self.folder("2023 Lakes")))
        done = self.finish(self.start({"ids": self.coast[:2] + self.lakes})["job"])
        self.assertEqual((2, 5), (done["changed"], done["error_count"]))
        self.assertIn("deleted permanently", done["errors"][0]["why"])
        self.assertIn("on a network share", done["errors"][0]["why"])
        self.assertEqual(self.lakes, self.held(self.lakes), "not deleted for good without being asked")
        self.assertTrue(all(os.path.exists(self.vl.path_of(photo_id)) for photo_id in self.lakes))
        done = self.finish(self.start({"ids": self.lakes}, {"permanent": True})["job"])
        self.assertEqual((5, 0), (done["changed"], done["error_count"]))
        self.assertEqual([], self.held(self.lakes))

    def test_a_file_the_bin_refuses_is_an_error_and_keeps_its_row_and_the_rest_go_on(self):
        refused = paths.key(self.vl.path_of(self.coast[1]))

        def recycle(path):
            if paths.key(path) == refused:
                return False
            os.remove(path)
            return True
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            done = self.finish(self.start({"ids": self.coast[:3]})["job"])
        self.assertEqual((2, 1), (done["changed"], done["error_count"]))
        self.assertEqual([self.coast[1]], self.held(self.coast[:3]))

    def test_what_a_delete_asks_for_is_checked_up_front(self):
        for params in ({"permanent": "yes"}, {"permanent": 1}, []):
            with self.subTest(params=params):
                self.assertIn("error", self.start({"ids": self.coast[:1]}, params, expect=400))
        self.assertEqual(self.coast[:1], self.held(self.coast[:1]))
        with mock.patch.object(selection, "MAX_SELECTED", 10):
            refused = self.start(ALL, expect=400)
        self.assertIn("at most", refused["error"])
        self.assertEqual([], self.sent)

    def test_cancel_stops_after_the_chunk_under_way(self):
        reached, release = threading.Event(), threading.Event()
        calls = []

        def gate():
            calls.append(1)
            if len(calls) == 2:
                reached.set()
                release.wait(30)
        self.gate = gate
        self.addCleanup(release.set)
        handle = self.start({"ids": self.coast})["job"]          # 30 photos: two chunks of 25 and 5
        self.assertTrue(reached.wait(30))
        self.client.post("/library/api/library/bulk/cancel", json={"job": handle})
        release.set()
        done = self.finish(handle)
        self.assertEqual(("cancelled", 25), (done["state"], done["changed"]))
        self.assertEqual(self.coast[25:], self.held(self.coast))

    def test_while_a_delete_runs_another_bulk_edit_is_refused_and_a_second_delete_too(self):
        reached, release = threading.Event(), threading.Event()

        def gate():
            reached.set()
            release.wait(30)
        self.gate = gate
        self.addCleanup(release.set)
        handle = self.start({"ids": self.coast[:2]})["job"]
        self.assertTrue(reached.wait(30))
        self.assertIn("already running", self.start({"ids": self.lakes}, expect=409)["error"])
        tags = self.client.post("/library/api/library/bulk/start", json={"op": "tags", "selection": ALL, "params": {"add": ["Trips/Coast"]}})
        self.assertEqual(409, tags.status_code)
        self.assertIn("bulk delete", tags.get_json()["error"])
        release.set()
        self.assertEqual("done", self.finish(handle)["state"])

    def test_the_activity_record_names_no_photo(self):
        done = self.finish(self.start({"ids": self.coast[:2]})["job"])
        self.assertEqual("done", done["state"])
        what = self.vl.rows("SELECT changed FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1", bulk_edits.JOB)[0][0]
        self.assertIn("bulk delete", what)
        self.assertNotIn("c00", what)

    def test_the_edit_says_what_it_does_without_a_name(self):
        self.assertEqual("delete the files to the Recycle Bin", bulk_edit.prepare(self.library, "delete", {}).describe())


class Asking(Delete):
    def test_how_many_and_how_many_for_good_by_folder(self):
        self.binless.add(paths.key(self.folder("2023 Lakes")))
        found = self.check({"ids": self.coast[:3] + self.lakes})
        self.assertEqual({"total": 8, "folders": 2, "permanent": 5, "reasons": [{"reason": "on a network share", "photos": 5}]}, found)
        found = self.check({"source": {"kind": "all"}, "excluded": self.lakes})
        self.assertEqual((30, 1, 0, []), (found["total"], found["folders"], found["permanent"], found["reasons"]))
        self.assertEqual([], self.sent, "asking deletes nothing")

    def test_the_bin_is_asked_once_a_folder_never_once_a_photo(self):
        asked = []
        with mock.patch.object(recycle_bin, "no_bin_reason", side_effect=lambda path: asked.append(path)):
            self.check(ALL)
        self.assertEqual(sorted([paths.key(self.folder("2023 Lakes")), paths.key(self.folder("2024 Coast"))]),
                         sorted(paths.key(path) for path in asked))

    def test_over_the_bulk_cap_this_pc_only_and_nonsense(self):
        with mock.patch.object(selection, "MAX_SELECTED", 10):
            self.assertIn("at most", self.check(ALL, expect=400)["error"])
        self.check({"ids": self.coast[:1]}, expect=403, environ_overrides=REMOTE)
        for body in (None, {"ids": "x"}, {"source": {"kind": "nope"}}):
            with self.subTest(body=body):
                self.check(body, expect=400)


if __name__ == "__main__":
    unittest.main()
