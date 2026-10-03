"""A bulk edit of a selection by photo id, as a job (tagpup.jobs.bulk_edits, tagpup.services.bulk_edit; the routes
/api/library/bulk/*; docs/ARCHITECTURE.md, phase 9d-1).

ExifTool is a table of files (tests/fake_exiftool.py), the photos are real small JPEGs and rows as the indexer records them, the
tag tree and the journal are real. A job runs on a thread; a test joins it (no sleep), and pauses it in the middle of a write by
an event the fake sets and waits on. Fictional names only: the library is photographs of real people, many of them minors.
"""
import os
import socket
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
from tagpup.jobs import bulk_edits  # noqa: E402
from tagpup.services import bulk_edit, file_changes, libraries  # noqa: E402
from tagpup.store import damaged_files, db  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}
ALL = {"source": {"kind": "all"}}
GHOST = "%s:4000000:1" % socket.gethostname()   # a process that ended: no such id


class Crash(BaseException):
    """The process dying: nothing in the job catches it as it does an Exception."""


class Gate:
    """Pause the writes at the `at`-th (the first one seen when `at` is None): the job sets `reached` and waits to be released."""

    def __init__(self, at):
        self.at, self.reached, self.release = at, threading.Event(), threading.Event()

    def __call__(self, _path, n):
        if self.at is None or n == self.at:
            self.reached.set()
            if not self.release.wait(30):
                raise AssertionError("the test never released the write")


class Bulk(unittest.TestCase):
    PHOTOS = 60

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
        self.vl.tree("Trips/Coast", "Trips/Lakes", "Old/Stuff", "People/Wren Halloway", "People/Rowan Thackeray", face_root="People")
        self.ids, self.taken = [], {}
        for n in range(self.PHOTOS):
            taken = "2024:06:%02d 10:%02d:00" % (n % 28 + 1, n % 60)
            tags = ["Old/Stuff"] if n % 3 == 0 else []
            photo_id = self.vl.photo("2024 %s" % "AB"[n % 2], "p%03d.jpg" % n, taken=taken, tags=tags, real=True, size=(16, 16))
            self.files.hold(self.vl.path_of(photo_id), EXIF__DateTimeOriginal=taken)
            self.files.keep_tags(self.vl.path_of(photo_id), tags)
            self.ids.append(photo_id)
            self.taken[photo_id] = taken

    # ---- asking ------------------------------------------------------------------------------------

    def post(self, path, body, **kwargs):
        return self.client.post("/library/api/library/bulk/" + path, json=body, **kwargs)

    def start(self, op="tags", selection=None, params=None, expect=200):
        reply = self.post("start", {"op": op, "selection": selection or ALL, "params": params or {}})
        self.assertEqual(expect, reply.status_code, reply.get_json())
        return reply.get_json()

    def tags_job(self, add=(), remove=(), selection=None, expect=200):
        return self.start("tags", selection, {"add": list(add), "remove": list(remove)}, expect)

    def shift_job(self, minutes=90, selection=None, expect=200):
        return self.start("time_shift", selection, {"minutes": minutes}, expect)

    def status(self, handle, **query):
        reply = self.client.get("/library/api/library/bulk/status", query_string=dict(job=handle, **query))
        return reply.get_json()

    def finish(self, handle):
        """Wait for the job's thread to end; its status."""
        bulk_edits._held(self.library)[handle].thread.join(60)
        return self.status(handle)

    def run_tags(self, add=(), remove=(), selection=None):
        return self.finish(self.tags_job(add, remove, selection)["job"])

    def path(self, photo_id):
        return self.vl.path_of(photo_id)

    def tags(self, photo_id):
        return self.files.tags_of(self.path(photo_id))

    def row_tags(self, photo_id):
        import json
        return json.loads(self.vl.rows("SELECT tags FROM photos WHERE id = ?", photo_id)[0][0])

    def changes(self, like):
        return self.vl.rows("SELECT id, operation, status FROM changes WHERE operation LIKE ? ORDER BY id", like)

    def crash(self):
        """The process dies: no thread of the job is left, its row says running and is owned by a process that is gone."""
        bulk_edits._jobs.clear()
        self.vl.conn.execute("UPDATE job_runs SET owner = ? WHERE outcome = 'running'", (GHOST,))
        self.vl.conn.commit()


class Tags(Bulk):
    def test_a_tags_job_adds_and_removes_in_every_photo_and_counts_what_it_changed(self):
        reply = self.tags_job(add=["Trips/Coast"], remove=["Old/Stuff"])
        self.assertEqual((self.PHOTOS, self.PHOTOS, 0, 0), (reply["total"], reply["requested"], reply["missing"], reply["excluded"]))
        done = self.finish(reply["job"])
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS), (done["state"], done["total"], done["done"]))
        self.assertEqual((self.PHOTOS, 0, 0, 0, 0), (done["changed"], done["unchanged"], done["skipped_missing"],
                                                     done["skipped_damaged"], done["error_count"]))
        for photo_id in self.ids:
            self.assertEqual(["Trips/Coast"], self.tags(photo_id))
            self.assertEqual(["Trips/Coast"], self.row_tags(photo_id), "the index is told what was written")

    def test_a_photo_that_holds_it_already_is_unchanged_and_is_not_written(self):
        first = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        writes = self.files.writes
        again = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual((first["changed"], again["changed"], again["unchanged"]), (self.PHOTOS, 0, self.PHOTOS))
        self.assertEqual(writes, self.files.writes)

    def test_it_adds_and_removes_against_the_files_own_tags_never_a_list(self):
        # Another program added a keyword the index has not read; a job that replaced a list would lose it.
        other = self.path(self.ids[1])
        self.files.keep_tags(other, ["Added By Another Program"])
        self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual(["Added By Another Program", "Trips/Coast"], self.tags(self.ids[1]))

    def test_the_tags_are_checked_up_front_and_nothing_is_started_or_written(self):
        for bad in ("Trips\u0007Coast", "   ", "///", "Trips//Coast"):
            reply = self.start("tags", params={"add": [bad]}, expect=400)
            self.assertTrue(reply["error"])
        self.assertEqual(0, self.files.writes)
        self.assertEqual([], self.vl.rows("SELECT id FROM job_runs"))

    def test_what_is_taken_off_may_be_a_tag_the_rules_would_not_let_in(self):
        self.files.keep_tags(self.path(self.ids[2]), ["Bad\u0007Tag"])
        self.finish(self.tags_job(remove=["Bad\u0007Tag"])["job"])
        self.assertEqual([], self.tags(self.ids[2]))

    def test_an_edit_that_means_nothing_is_a_400_with_a_sentence(self):
        for op, params in (("nope", {}), ("tags", {}), ("tags", {"add": "Trips"}), ("tags", {"add": ["A"], "remove": ["A"]}),
                           ("tags", {"remove": [""]}), ("tags", "x"), ("tags", {"add": ["t%d" % n for n in range(201)]}),
                           ("time_shift", {}), ("time_shift", {"minutes": 0}), ("time_shift", {"minutes": "5"}),
                           ("time_shift", {"minutes": True}), ("time_shift", {"minutes": 1.5}), ("people", {}),
                           ("people", {"add": ["  "]})):
            with self.subTest(op=op, params=params):
                reply = self.start(op, params=params, expect=400)
                self.assertNotIn("Traceback", reply["error"])
        self.assertEqual(0, self.files.writes)

    def test_a_selection_of_one_photo(self):
        done = self.finish(self.tags_job(add=["Trips/Lakes"], selection={"ids": [self.ids[7]]})["job"])
        self.assertEqual((1, 1), (done["total"], done["changed"]))
        self.assertEqual(["Trips/Lakes"], self.tags(self.ids[7]))
        self.assertEqual([], self.tags(self.ids[8]))

    def test_a_source_minus_the_excluded_is_resolved_on_the_server(self):
        keep = self.ids[:5]
        reply = self.tags_job(add=["Trips/Lakes"], selection={"source": {"kind": "all"}, "excluded": keep})
        self.assertEqual((self.PHOTOS - 5, self.PHOTOS, 5), (reply["total"], reply["requested"], reply["excluded"]))
        self.finish(reply["job"])
        for photo_id in self.ids:
            self.assertEqual(photo_id not in keep, "Trips/Lakes" in self.tags(photo_id), photo_id)

    def test_ids_nobody_has_and_duplicates_are_left_out_and_said(self):
        reply = self.tags_job(add=["Trips/Lakes"], selection={"ids": [self.ids[0], self.ids[0], 999999, self.ids[1]]})
        self.assertEqual((2, 3, 1), (reply["total"], reply["requested"], reply["missing"]))
        self.assertEqual(2, self.finish(reply["job"])["changed"])

    def test_a_selection_of_only_ids_nobody_has_starts_nothing(self):
        reply = self.start("tags", {"ids": [999998, 999999]}, {"add": ["A"]}, expect=400)
        self.assertIn("no photos", reply["error"])
        self.assertEqual([], self.vl.rows("SELECT id FROM job_runs"))

    def test_a_selection_above_200000_is_refused_naming_how_many(self):
        with mock.patch("tagpup.services.selection.MAX_SELECTED", 50):
            reply = self.start("tags", ALL, {"add": ["A"]}, expect=400)
        self.assertIn("60 photos", reply["error"])
        self.assertEqual([], self.vl.rows("SELECT id FROM job_runs"))

    def test_the_journal_has_a_change_for_each_chunk_named_after_the_job(self):
        reply = self.tags_job(add=["Trips/Coast"])
        self.finish(reply["job"])
        found = self.changes("bulk tags (job %d)" % reply["job"])
        self.assertEqual(3, len(found), "60 photos are three chunks of 25")
        self.assertEqual({"applied"}, {status for _id, _op, status in found})


class Progress(Bulk):
    def test_the_status_of_a_running_job_is_the_in_memory_counter_and_asks_the_library_nothing(self):
        gate = Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        asked = mock.Mock(side_effect=AssertionError("the status asked the library"))
        with mock.patch.object(bulk_edits.runs_service, "get", asked), mock.patch.object(bulk_edits.bulk_edit, "read_state", asked):
            seen = self.status(handle)
        asked.assert_not_called()
        self.assertEqual(("running", self.PHOTOS), (seen["state"], seen["total"]))
        self.assertEqual(25, seen["done"], "the first chunk of 25 is settled; the second is being written")
        self.assertEqual(25, seen["changed"])
        self.assertIsNotNone(seen["eta_seconds"])
        gate.release.set()
        self.assertEqual("done", self.finish(handle)["state"])

    def test_the_library_records_the_run_for_the_activity_page_with_numbers_and_a_sentence(self):
        reply = self.tags_job(add=["Trips/Coast"], remove=["Old/Stuff"])
        self.finish(reply["job"])
        run = self.vl.rows("SELECT job, library, outcome, changed FROM job_runs WHERE id = ?", reply["job"])[0]
        self.assertEqual(("bulk edit", "library", "done"), run[:3])
        import json
        counts = json.loads(run[3])
        self.assertEqual((self.PHOTOS, self.PHOTOS, "done"), (counts["changed"], counts["done"], counts["state"]))
        self.assertIn("bulk tags: add 1 and remove 1 tag(s)", counts["what"])
        self.assertNotIn("Coast", counts["what"], "no tag or person is named in the Activity page's text")
        from tagpup.services import activity
        listed = [entry for entry in activity.timeline(self.library) if entry["kind"] == "job"]
        self.assertEqual(1, len(listed))
        self.assertEqual((reply["job"], "done", self.PHOTOS), (listed[0]["id"], listed[0]["outcome"], listed[0]["counts"]["changed"]))
        self.assertEqual(counts["what"], listed[0]["what"])
        self.assertEqual(200, self.client.get("/library/api/activity/jobs").status_code, "the Activity page lists the run")

    def test_the_run_is_written_to_the_library_at_most_every_two_seconds(self):
        calls = []
        real = bulk_edits.runs_service.progress

        def counted(library, run_id, counts):
            calls.append(counts["done"])
            return real(library, run_id, counts)
        now = [1000.0]
        with mock.patch.object(bulk_edits.runs_service, "progress", counted), \
                mock.patch.object(bulk_edits.time, "monotonic", lambda: now[0]):
            self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        # The start, the first chunk (nothing was recorded before it), and no more while the clock does not move.
        self.assertEqual([0, 25], calls)

    def test_a_server_update_waits_for_a_running_job(self):
        from tagpup.web import lifecycle
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        self.assertIn("1 bulk edit(s)", lifecycle.long_work())
        gate.release.set()
        self.finish(handle)
        self.assertEqual([], lifecycle.long_work())


class OneAtATime(Bulk):
    def test_a_second_start_is_a_409_naming_the_one_running_and_a_third_after_it_ends_is_fine(self):
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        refused = self.tags_job(add=["Trips/Lakes"], expect=409)
        self.assertIn("already running", refused["error"])
        self.assertIn("bulk tags", refused["error"])
        self.assertEqual(409, self.post("resume", {"job": handle}).status_code)
        gate.release.set()
        self.finish(handle)
        self.finish(self.tags_job(add=["Trips/Lakes"])["job"])

    def test_two_starts_at_once_begin_one(self):
        outcomes = []
        gate = threading.Barrier(2)

        def attempt():
            with self.app.test_client() as client:
                gate.wait(10)
                outcomes.append(client.post("/library/api/library/bulk/start", json={
                    "op": "tags", "selection": ALL, "params": {"add": ["Trips/Coast"]}}).status_code)
        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        self.assertEqual([200, 409], sorted(outcomes))
        for job in list(bulk_edits._held(self.library).values()):
            job.thread.join(60)

    def test_another_process_running_one_refuses_this_one(self):
        self.vl.conn.execute("INSERT INTO job_runs (job, library, started, outcome, owner) VALUES"
                             " ('bulk edit', 'library', '2026-10-03 10:00:00', 'running', ?)", (socket.gethostname() + ":1:1",))
        self.vl.conn.commit()
        with mock.patch("tagpup.store.file_journal.owner_alive", return_value=True):
            refused = self.tags_job(add=["Trips/Coast"], expect=409)
        self.assertIn("another TagPup process", refused["error"])


class Cancelling(Bulk):
    def test_cancel_stops_after_the_chunk_under_way_and_says_how_many_were_done(self):
        gate = Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        reply = self.post("cancel", {"job": handle}).get_json()
        self.assertTrue(reply["cancelling"])
        self.assertEqual("running", reply["state"], "it stops after the chunk, not in the middle of it")
        gate.release.set()
        done = self.finish(handle)
        self.assertEqual(("cancelled", 50, 50), (done["state"], done["done"], done["changed"]))
        self.assertIn("50 of 60", done["message"])
        self.assertEqual(50, sum(1 for photo_id in self.ids if "Trips/Coast" in self.tags(photo_id)))
        run = self.vl.rows("SELECT outcome FROM job_runs WHERE id = ?", handle)
        self.assertEqual("done", run[0][0], "a cancelled run is a finished one; its counts say how far")

    def test_cancel_after_the_end_is_no_error_and_changes_nothing(self):
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.finish(handle)
        reply = self.post("cancel", {"job": handle})
        self.assertEqual(200, reply.status_code)
        self.assertEqual(("done", False), (reply.get_json()["state"], reply.get_json()["cancelling"]))

    def test_cancel_of_a_job_nobody_has_is_a_404_and_of_nonsense_a_400(self):
        self.assertEqual(404, self.post("cancel", {"job": 424242}).status_code)
        self.assertEqual(400, self.post("cancel", {"job": "x"}).status_code)
        self.assertEqual(400, self.post("cancel", {}).status_code)

    def test_a_cancelled_tags_job_can_simply_be_started_again(self):
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": handle})
        gate.release.set()
        self.finish(handle)
        again = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual("done", again["state"])
        self.assertTrue(all("Trips/Coast" in self.tags(photo_id) for photo_id in self.ids))
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code, "tags are not resumed; they are started again")


class WhilePhotosMove(Bulk):
    def test_a_photo_deleted_in_the_middle_is_skipped_and_counted_and_the_later_ones_are_written(self):
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        order = self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]
        gone_file, gone_row = order[40], order[41]
        os.remove(self.path(gone_file))
        self.vl.conn.execute("DELETE FROM photos WHERE id = ?", (gone_row,))
        self.vl.conn.commit()
        gate.release.set()
        done = self.finish(handle)
        self.assertEqual(("done", 2, self.PHOTOS - 2), (done["state"], done["skipped_missing"], done["changed"]))
        self.assertEqual(self.PHOTOS, done["done"])
        self.assertEqual(["Trips/Coast"], self.tags(order[59]), "the photos after the missing one are written")

    def test_a_photo_renamed_meanwhile_is_found_by_its_id(self):
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        order = self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]
        moved = order[50]
        old = self.path(moved)
        new = os.path.join(os.path.dirname(old), "renamed meanwhile.jpg")
        os.rename(old, new)
        self.files.fields[paths.key(new)] = self.files.fields.pop(paths.key(old))
        from tagpup.store import photos as store_photos
        db.write_with_connection(self.vl.path, lambda conn: store_photos.move_rows_in(conn, {old: new}))
        gate.release.set()
        done = self.finish(handle)
        self.assertEqual((self.PHOTOS, 0), (done["changed"], done["skipped_missing"]))
        self.assertEqual(["Trips/Coast"], self.files.tags_of(new))

    def test_a_file_edited_by_another_program_between_the_plan_and_the_write_is_reported_and_not_overwritten(self):
        target = self.path(self.ids[10])

        def other_program(path, n):
            # The second read of the file is the write's own check, after the plan was committed.
            if paths.key(path) == paths.key(target) and n == 2:
                self.files.keep_tags(target, ["Changed By Another Program"])
        self.files.on_read = other_program
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("done", 1, self.PHOTOS - 1), (done["state"], done["error_count"], done["changed"]))
        self.assertEqual(self.ids[10], done["errors"][0]["id"])
        self.assertIn("changed since it was read", done["errors"][0]["why"])
        self.assertEqual(["Changed By Another Program"], self.files.tags_of(target), "never overwritten")

    def test_a_file_replaced_by_another_is_written_as_the_file_it_now_is(self):
        replaced = self.path(self.ids[4])
        self.files.fields[paths.key(replaced)] = {"EXIF:DateTimeOriginal": self.taken[self.ids[4]]}
        self.files.keep_tags(replaced, ["From The Replacement"])
        self.run_tags(add=["Trips/Coast"])
        self.assertEqual(["From The Replacement", "Trips/Coast"], self.files.tags_of(replaced))


class WhatGoesWrong(Bulk):
    def test_a_damaged_photo_is_skipped_and_counted_and_the_rest_are_written(self):
        victim = self.path(self.ids[3])
        held = self.tags(self.ids[3])
        stat = os.stat(victim)
        db.write_with_connection(self.vl.path, lambda conn: damaged_files.record(
            conn, victim, (stat.st_mtime, stat.st_size), "truncated", "ends early"))
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual((1, self.PHOTOS - 1, 0), (done["skipped_damaged"], done["changed"], done["error_count"]))
        self.assertEqual(held, self.tags(self.ids[3]), "nothing is written into a damaged photo")

    def test_an_unreadable_file_is_an_error_entry_and_the_others_go_on(self):
        self.files.unreadable.add(paths.key(self.path(self.ids[5])))
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("done", 1, self.PHOTOS - 1, 0), (done["state"], done["error_count"], done["changed"], done["skipped_missing"]))
        self.assertEqual(self.ids[5], done["errors"][0]["id"])
        self.assertEqual("p005.jpg", done["errors"][0]["name"])
        self.assertTrue(done["errors"][0]["why"])

    def test_a_write_that_fails_does_not_stop_the_others(self):
        self.files.fails.add(paths.key(self.path(self.ids[2])))
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("done", 1, self.PHOTOS - 1), (done["state"], done["error_count"], done["changed"]))
        self.assertIn("locked", done["errors"][0]["why"])
        self.assertEqual(["Trips/Coast"], self.tags(self.ids[59]), "the photos after the failure are written")

    def test_only_the_first_fifty_errors_are_named_and_all_are_counted(self):
        self.files.unreadable.update(paths.key(self.path(photo_id)) for photo_id in self.ids[:55])
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual((55, 50, self.PHOTOS - 55), (done["error_count"], len(done["errors"]), done["changed"]))
        self.assertEqual("done", done["state"])

    def test_exiftool_that_cannot_start_stops_the_job_as_failed_with_the_sentence(self):
        self.files.cannot_start = FileNotFoundError("ExifTool is not where the library says it is")
        done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("failed", 0, 0), (done["state"], done["changed"], done["done"]))
        self.assertIn("ExifTool is not where the library says", done["message"])
        self.assertEqual(1, self.files.starts, "it did not go on to ask again for every chunk")
        run = self.vl.rows("SELECT outcome, note FROM job_runs")[0]
        self.assertEqual("failed", run[0])

    def test_five_chunks_in_a_row_in_which_nothing_could_be_done_stop_the_job(self):
        self.files.unreadable.update(paths.key(self.path(photo_id)) for photo_id in self.ids)
        with mock.patch.object(bulk_edits, "CHUNK", 5):
            done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("failed", 25), (done["state"], done["done"]))
        self.assertIn("5 chunks in a row", done["message"])
        self.assertEqual(25, done["error_count"])

    def test_a_chunk_refused_as_a_whole_is_its_photos_as_errors_and_a_good_chunk_after_it_is_written(self):
        real = libraries.refuse_writes
        calls = []

        def refuse_the_first(result, library, photo_paths, damaged_ok=False):
            calls.append(1)
            if len(calls) == 1:
                result.refuse("Can't check E:: try again. A photo there was found damaged, and whether it still is could not be told.")
                return True
            return real(result, library, photo_paths, damaged_ok)
        with mock.patch.object(libraries, "refuse_writes", refuse_the_first):
            done = self.run_tags(add=["Trips/Coast"])
        self.assertEqual(("done", 25, self.PHOTOS - 25), (done["state"], done["error_count"], done["changed"]))
        self.assertIn("Can't check", done["errors"][0]["why"])

    def test_a_library_behind_is_a_409_before_anything_starts(self):
        with mock.patch("tagpup.services.library_view.store.ready", return_value=False):
            self.assertEqual(409, self.post("start", {"op": "tags", "selection": ALL, "params": {"add": ["A"]}}).status_code)
        self.assertEqual([], self.vl.rows("SELECT id FROM job_runs"))

    def test_a_library_at_schema_18_is_a_sentence_and_nothing_is_started(self):
        import own_home
        from test_migrations import at_version

        from tagpup.core.library import Library
        from tagpup.store import schema
        from tagpup.web import app as web
        from tagpup.web import libraries as web_libraries
        home = own_home.for_test(self)
        path = home.library("behind.db")
        at_version(path, schema.LATEST - 1)
        app = web.create_app("tagpup", startup=Library(path))
        app.testing = True
        with mock.patch.object(web_libraries.library_actions, "bring_up_to_date", side_effect=RuntimeError("locked")):
            reply = app.test_client().post("/behind/api/library/bulk/start", json={
                "op": "tags", "selection": ALL, "params": {"add": ["A"]}})
        self.assertEqual(409, reply.status_code)
        self.assertIn("has not been brought up to date", reply.get_json()["error"])

    def test_a_root_this_machine_does_not_place_is_the_gates_409_before_the_route_runs(self):
        with mock.patch("tagpup.web.roots_gate.problem_of", return_value="This machine does not place the root pictures."):
            reply = self.post("start", {"op": "tags", "selection": ALL, "params": {"add": ["A"]}})
            status = self.client.get("/library/api/library/bulk/status?job=1")
        self.assertEqual((409, True), (reply.status_code, reply.get_json()["roots_problem"]))
        self.assertEqual(409, status.status_code)
        self.assertEqual(0, self.files.writes)

    def test_the_four_routes_answer_this_pc_only(self):
        for path, body in (("start", {"op": "tags", "selection": ALL, "params": {"add": ["A"]}}), ("cancel", {"job": 1}),
                           ("resume", {"job": 1})):
            self.assertEqual(403, self.post(path, body, environ_overrides=REMOTE).status_code, path)
        reply = self.client.get("/library/api/library/bulk/status?job=1", environ_overrides=REMOTE)
        self.assertEqual(403, reply.status_code)
        self.assertEqual(0, self.files.writes)

    def test_a_job_of_nobody_is_a_404_and_a_bad_id_a_400(self):
        self.assertEqual(404, self.client.get("/library/api/library/bulk/status?job=777").status_code)
        self.assertEqual(400, self.client.get("/library/api/library/bulk/status?job=x").status_code)
        self.assertEqual(400, self.client.get("/library/api/library/bulk/status").status_code)
        self.assertEqual(404, self.post("resume", {"job": 777}).status_code)


class People(Bulk):
    def setUp(self):
        super().setUp()
        # Rowan is in the tree once; Wren twice, under one people root: where to file her is a question.
        self.vl.tree("People/Friends/Wren Halloway", face_root="People")
        self.rowan = ["Rowan Thackeray"]

    def test_a_person_is_filed_as_the_chip_files_them_and_no_node_is_made(self):
        nodes = self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy")
        done = self.finish(self.start("people", selection={"ids": self.ids[:4]}, params={"add": self.rowan})["job"])
        self.assertEqual(4, done["changed"])
        for photo_id in self.ids[:4]:
            self.assertIn("People/Rowan Thackeray", self.tags(photo_id))
        self.assertEqual(nodes, self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy"))

    def test_a_name_the_tree_does_not_hold_is_filed_under_the_one_people_root_without_making_a_node(self):
        nodes = self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy")
        self.finish(self.start("people", selection={"ids": self.ids[:2]}, params={"add": ["Cora Ingersoll"]})["job"])
        self.assertIn("People/Cora Ingersoll", self.tags(self.ids[0]))
        self.assertEqual(nodes, self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy"))

    def test_a_person_the_file_already_names_by_their_leaf_is_not_added_again_whatever_the_path(self):
        held = self.path(self.ids[1])
        self.files.keep_tags(held, ["Family/Rowan Thackeray"])
        done = self.finish(self.start("people", selection={"ids": [self.ids[1], self.ids[2]]}, params={"add": self.rowan})["job"])
        self.assertEqual((1, 1), (done["changed"], done["unchanged"]))
        self.assertEqual(["Family/Rowan Thackeray"], self.tags(self.ids[1]))
        self.assertEqual(["People/Rowan Thackeray"], self.tags(self.ids[2]))

    def test_a_person_filed_in_two_places_is_refused_up_front_with_a_sentence(self):
        reply = self.start("people", params={"add": ["Wren Halloway"]}, expect=400)
        self.assertIn("more than one place", reply["error"])
        # Given the path, it is no question.
        done = self.finish(self.start("people", selection={"ids": self.ids[:1]}, params={"add": ["People/Friends/Wren Halloway"]})["job"])
        self.assertEqual(1, done["changed"])
        self.assertIn("People/Friends/Wren Halloway", self.tags(self.ids[0]))

    def test_a_blank_name_is_refused(self):
        for params in ({"add": [""]}, {"add": ["   "]}, {"remove": ["\t"]}):
            self.start("people", params=params, expect=400)

    def test_taking_a_person_off_takes_every_way_the_tree_files_them(self):
        both = self.path(self.ids[1])
        self.files.keep_tags(both, ["People/Wren Halloway", "People/Friends/Wren Halloway", "Trips/Coast"])
        self.finish(self.start("people", selection={"ids": [self.ids[1]]}, params={"remove": ["Wren Halloway"]})["job"])
        self.assertEqual(["Trips/Coast"], self.tags(self.ids[1]))


class TimeShift(Bulk):
    def shifted(self, taken, minutes=90):
        from tagpup.core import dates
        return dates.shifted(taken, minutes)

    def test_a_shift_moves_the_date_taken_once_in_every_photo_and_says_which(self):
        reply = self.shift_job(90)
        done = self.finish(reply["job"])
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS), (done["state"], done["changed"], done["done"]))
        for photo_id in self.ids:
            self.assertEqual(self.shifted(self.taken[photo_id]), self.files.taken_of(self.path(photo_id)))
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])
        shown = self.status(reply["job"], ids="1")
        self.assertEqual(sorted(self.ids), shown["shifted_ids"])

    def test_a_photo_with_no_date_taken_is_unchanged_and_both_date_fields_move_together(self):
        bare = self.path(self.ids[0])
        self.files.fields[paths.key(bare)] = {}
        both = self.path(self.ids[1])
        self.files.fields[paths.key(both)]["EXIF:CreateDate"] = self.taken[self.ids[1]]
        done = self.finish(self.shift_job(30)["job"])
        self.assertEqual((self.PHOTOS - 1, 1), (done["changed"], done["unchanged"]))
        self.assertEqual({}, self.files.fields[paths.key(bare)])
        self.assertEqual(self.shifted(self.taken[self.ids[1]], 30), self.files.fields[paths.key(both)]["EXIF:CreateDate"])
        self.assertEqual(self.shifted(self.taken[self.ids[1]], 30), self.files.taken_of(both))

    def test_the_cursor_and_the_list_are_kept_in_the_cache_folder_and_the_list_goes_when_it_is_done(self):
        reply = self.shift_job(5)
        self.finish(reply["job"])
        self.assertIsNone(bulk_edit.read_ids(self.library, reply["job"]))
        self.assertEqual("done", bulk_edit.read_state(self.library, reply["job"])["state"])

    def test_a_restart_in_the_middle_leaves_it_abandoned_and_resume_shifts_nothing_twice(self):
        # The process dies as the 30th file is written: one chunk done, the second under way.
        def die(_path, n):
            if n == 30:
                raise Crash()
        self.files.on_write = die
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            handle = self.shift_job(90)["job"]
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        self.files.on_write = None
        seen = self.status(handle)
        self.assertEqual(("abandoned", True), (seen["state"], seen["resumable"]))
        self.assertIn("closed before this finished", seen["message"])
        self.assertIn("no photo already shifted is shifted again", seen["message"])
        self.assertEqual(25, seen["done"], "the first chunk is settled")
        resumed = self.post("resume", {"job": handle})
        self.assertEqual(200, resumed.status_code, resumed.get_json())
        self.assertEqual(handle, resumed.get_json()["job"])
        done = self.finish(handle)
        self.assertEqual(("done", self.PHOTOS), (done["state"], done["done"]))
        for photo_id in self.ids:
            key = paths.key(self.path(photo_id))
            self.assertEqual(self.shifted(self.taken[photo_id]), self.files.taken_of(self.path(photo_id)), photo_id)
            self.assertEqual(1, self.files.writes_of[key], "photo %d was written %d times" % (photo_id, self.files.writes_of[key]))
        self.assertEqual(done["changed"], self.PHOTOS)
        self.assertEqual(sorted(self.ids), self.status(handle, ids="1")["shifted_ids"])

    def test_a_restart_after_a_file_was_written_and_before_its_row_is_still_shifted_once(self):
        written = []
        real = file_changes._reached

        def die_after_the_file(step):
            real(step)
            if step == "file written":
                written.append(1)
                if len(written) == 30:
                    raise Crash()
        with mock.patch.object(file_changes, "_reached", die_after_the_file), \
                mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            handle = self.shift_job(90)["job"]
            bulk_edits._held(self.library)[handle].thread.join(60)
            self.crash()
            self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
            self.finish(handle)
        for photo_id in self.ids:
            self.assertEqual(self.shifted(self.taken[photo_id]), self.files.taken_of(self.path(photo_id)))
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])

    def test_a_restart_before_the_chunk_planned_anything_retries_the_whole_chunk(self):
        doomed = paths.key(self.path(self.ids[40]))

        def die_reading(path, n):
            if paths.key(path) == doomed:
                raise Crash()
        self.files.on_read = die_reading
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            handle = self.shift_job(90)["job"]
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        self.files.on_read = None
        self.post("resume", {"job": handle})
        done = self.finish(handle)
        self.assertEqual(self.PHOTOS, done["changed"])
        for photo_id in self.ids:
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])

    def test_a_cancelled_shift_resumes_from_where_it_stopped(self):
        gate = Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.shift_job(90)["job"]
        self.assertTrue(gate.reached.wait(30))
        self.post("cancel", {"job": handle})
        gate.release.set()
        stopped = self.finish(handle)
        self.assertEqual(("cancelled", True, 50), (stopped["state"], stopped["resumable"], stopped["done"]))
        self.post("resume", {"job": handle})
        done = self.finish(handle)
        self.assertEqual(("done", self.PHOTOS, self.PHOTOS), (done["state"], done["done"], done["changed"]))
        for photo_id in self.ids:
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])

    def test_resume_is_refused_for_a_job_that_is_done_running_or_gone_and_for_one_whose_list_is_lost(self):
        handle = self.shift_job(5)["job"]
        self.finish(handle)
        self.assertEqual(400, self.post("resume", {"job": handle}).status_code, "it is done")
        gate = Gate(self.files.writes + 30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        second = self.shift_job(5)["job"]
        self.assertTrue(gate.reached.wait(30))
        self.assertEqual(409, self.post("resume", {"job": second}).status_code, "it is running")
        self.post("cancel", {"job": second})
        gate.release.set()
        self.finish(second)
        bulk_edit.forget_ids(self.library, second)
        lost = self.post("resume", {"job": second})
        self.assertEqual(400, lost.status_code)
        self.assertIn("cannot be resumed", lost.get_json()["error"])

    def test_a_failed_shift_that_stopped_part_way_can_be_resumed_when_exiftool_is_back(self):
        calls = []

        def starts(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise FileNotFoundError("ExifTool went away")
            return self.files.session()
        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", starts):
            handle = self.shift_job(45)["job"]
            failed = self.finish(handle)
        self.assertEqual(("failed", 25, True), (failed["state"], failed["done"], failed["resumable"]))
        self.assertIn("ExifTool went away", failed["message"])
        self.assertEqual(200, self.post("resume", {"job": handle}).status_code)
        self.assertEqual("done", self.finish(handle)["state"])
        for photo_id in self.ids:
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])


class AfterARestart(Bulk):
    def test_the_status_of_a_job_the_process_did_not_run_comes_from_what_the_library_kept(self):
        gate = Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        gate.release.set()
        before = self.finish(handle)
        bulk_edits._jobs.clear()
        after = self.status(handle)
        for key in ("state", "total", "done", "changed", "unchanged", "error_count", "skipped_missing"):
            self.assertEqual(before[key], after[key], key)

    def test_a_tags_job_abandoned_by_a_restart_says_so_how_far_it_got_and_is_started_again(self):
        def die(_path, n):
            if n == 30:
                raise Crash()
        self.files.on_write = die
        with mock.patch.object(bulk_edits.Job, "_end", lambda self_, *a, **k: None):
            handle = self.tags_job(add=["Trips/Coast"])["job"]
            bulk_edits._held(self.library)[handle].thread.join(60)
        self.crash()
        self.files.on_write = None
        seen = self.status(handle)
        self.assertEqual(("abandoned", False), (seen["state"], seen["resumable"]))
        self.assertIn("closed before this finished", seen["message"])
        again = self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual("done", again["state"])
        self.assertEqual(handle, self.vl.rows("SELECT id FROM job_runs WHERE outcome = 'abandoned'")[0][0])
        for photo_id in self.ids:
            self.assertIn("Trips/Coast", self.tags(photo_id))
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))])

    def test_the_job_goes_on_when_the_page_is_closed_or_another_library_is_open(self):
        gate = Gate(2)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        from tagpup.services import libraries as library_actions
        other = self.home.library("elsewhere.db")
        library_actions.create(other)
        self.assertEqual(404, self.client.get("/elsewhere/api/library/bulk/status", query_string={"job": handle}).status_code)
        gate.release.set()
        self.assertEqual("done", self.finish(handle)["state"])
        self.assertEqual("done", self.status(handle)["state"])


class TheLock(Bulk):
    def test_a_single_photo_save_waiting_for_the_lock_is_let_in_between_chunks(self):
        from tagpup.services import tagging
        gate = Gate(30)
        self.files.on_write = gate
        self.addCleanup(gate.release.set)
        handle = self.tags_job(add=["Trips/Coast"])["job"]
        self.assertTrue(gate.reached.wait(30))
        saved = threading.Event()
        target = self.path(self.ids[0])

        def save():
            tagging.save_photo(self.library, target, "", ["Saved By Hand"], None, "exiftool", "{}")
            saved.set()
        thread = threading.Thread(target=save)
        thread.start()
        while not file_changes.waiting():
            if not thread.is_alive():
                break
            threading.Event().wait(0.002)
        self.assertFalse(saved.is_set(), "the job holds the lock for the chunk it is writing")
        # The next write that the job makes is its third chunk's first: by then the save has been let in.
        later = Gate(None)
        mine = paths.key(target)
        # Chunk 2 is writes 26 to 50; the save is the 51st; the job's first write after it is its third chunk's.
        self.files.on_write = lambda path, n: later(path, n) if paths.key(path) != mine and n > 50 else None
        gate.release.set()
        self.assertTrue(later.reached.wait(30), "the job went on to the next chunk")
        self.assertTrue(saved.is_set(), "the save was done between the chunks, not after the job")
        later.release.set()
        thread.join(30)
        self.assertEqual("done", self.finish(handle)["state"])

    def test_the_lock_is_held_for_a_chunk_and_not_the_job(self):
        held = []
        real = bulk_edit.run_chunk

        def watched(*args, **kwargs):
            out = real(*args, **kwargs)
            held.append(file_changes._one_at_a_time._is_owned())
            return out
        with mock.patch.object(bulk_edits.bulk_edit, "run_chunk", watched):
            self.finish(self.tags_job(add=["Trips/Coast"])["job"])
        self.assertEqual([False, False, False], held)


if __name__ == "__main__":
    unittest.main()
