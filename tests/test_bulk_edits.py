"""An edit of a selection of photos by id, written in one request (POST /api/library/bulk-edit, tagpup.services.bulk_edit;
docs/ARCHITECTURE.md). The selection's limit is 5,000 photos (tagpup.services.batches.MAX_BATCH).

ExifTool is a table of files (tests/fake_exiftool.py), the photos are real small JPEGs and rows as the indexer records them, the
tag tree and the journal are real. A write is paused in the middle by an event the fake sets and waits on, in a thread of the
test's own (no sleep). Fictional names only: the library is photographs of real people, many of them minors.
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from fake_exiftool import Files  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import activity, batches, bulk_edit, file_changes  # noqa: E402
from tagpup.store import job_runs  # noqa: E402
from tagpup.web import tagpup_routes  # noqa: E402

REMOTE = {"REMOTE_ADDR": "10.0.0.7"}
ALL = {"source": {"kind": "all"}}
GHOST = "%s:4000000:1" % socket.gethostname()   # a process that ended: no such id


class Crash(BaseException):
    """The process dying: nothing in the edit catches it as it does an Exception."""


class Gate:
    """Pause the writes at the `at`-th (the first one seen when `at` is None): the edit sets `reached` and waits to be released."""

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
        batches.reopen()        # a test that drained a server in this process leaves batches stopped
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.library = self.vl.library
        self.files = Files()
        patcher = mock.patch("tagpup.files.exiftool_session.ExifToolSession", self.files.session)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def post(self, body, client=None, **kwargs):
        return (client or self.client).post("/library/api/library/bulk-edit", json=body, **kwargs)

    def edit(self, op="tags", selection_body=None, params=None, expect=200):
        reply = self.post({"op": op, "selection": selection_body or ALL, "params": params or {}})
        self.assertEqual(expect, reply.status_code, reply.get_json())
        return reply.get_json()

    def tags_edit(self, add=(), remove=(), selection_body=None, expect=200):
        return self.edit("tags", selection_body, {"add": list(add), "remove": list(remove)}, expect)

    def path(self, photo_id):
        return self.vl.path_of(photo_id)

    def tags(self, photo_id):
        return self.files.tags_of(self.path(photo_id))

    def row_tags(self, photo_id):
        return json.loads(self.vl.rows("SELECT tags FROM photos WHERE id = ?", photo_id)[0][0])

    def changes(self, like):
        return self.vl.rows("SELECT id, operation, status FROM changes WHERE operation LIKE ? ORDER BY id", like)


class Tags(Bulk):
    def test_a_tags_edit_adds_and_removes_in_every_photo_and_counts_what_it_changed(self):
        done = self.tags_edit(add=["Trips/Coast"], remove=["Old/Stuff"])
        self.assertEqual((self.PHOTOS, self.PHOTOS, 0, 0), (done["total"], done["requested"], done["missing"], done["excluded"]))
        self.assertEqual((self.PHOTOS, 0, 0, 0), (done["changed"], done["unchanged"], done["skipped_missing"],
                                                  done["error_count"]))
        for photo_id in self.ids:
            self.assertEqual(["Trips/Coast"], self.tags(photo_id))
            self.assertEqual(["Trips/Coast"], self.row_tags(photo_id), "the index is told what was written")

    def test_a_photo_that_holds_it_already_is_unchanged_and_is_not_written(self):
        first = self.tags_edit(add=["Trips/Coast"])
        writes = self.files.writes
        again = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((first["changed"], again["changed"], again["unchanged"]), (self.PHOTOS, 0, self.PHOTOS))
        self.assertEqual(writes, self.files.writes)

    def test_it_adds_and_removes_against_the_files_own_tags_never_a_list(self):
        # Another program added a keyword the index has not read; an edit that replaced a list would lose it.
        self.files.keep_tags(self.path(self.ids[1]), ["Added By Another Program"])
        self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(["Added By Another Program", "Trips/Coast"], self.tags(self.ids[1]))

    def test_the_tags_are_checked_up_front_and_nothing_is_written(self):
        for bad in ("Trips\u0007Coast", "   ", "///", "Trips//Coast"):
            reply = self.edit("tags", params={"add": [bad]}, expect=400)
            self.assertTrue(reply["error"])
        self.assertEqual(0, self.files.writes)

    def test_what_is_taken_off_may_be_a_tag_the_rules_would_not_let_in(self):
        self.files.keep_tags(self.path(self.ids[2]), ["Bad\u0007Tag"])
        self.tags_edit(remove=["Bad\u0007Tag"])
        self.assertEqual([], self.tags(self.ids[2]))

    def test_an_edit_that_means_nothing_is_a_400_with_a_sentence(self):
        for op, params in (("nope", {}), ("tags", {}), ("tags", {"add": "Trips"}), ("tags", {"add": ["A"], "remove": ["A"]}),
                           ("tags", {"remove": [""]}), ("tags", "x"), ("tags", {"add": ["t%d" % n for n in range(201)]}),
                           ("time_shift", {}), ("time_shift", {"minutes": 0}), ("people", {}), ("people", {"add": ["  "]})):
            with self.subTest(op=op, params=params):
                reply = self.edit(op, params=params, expect=400)
                self.assertNotIn("Traceback", reply["error"])
        self.assertEqual(0, self.files.writes)

    def test_a_selection_of_one_photo(self):
        done = self.tags_edit(add=["Trips/Lakes"], selection_body={"ids": [self.ids[7]]})
        self.assertEqual((1, 1), (done["total"], done["changed"]))
        self.assertEqual(["Trips/Lakes"], self.tags(self.ids[7]))
        self.assertEqual([], self.tags(self.ids[8]))

    def test_a_source_minus_the_excluded_is_resolved_on_the_server(self):
        keep = self.ids[:5]
        done = self.tags_edit(add=["Trips/Lakes"], selection_body={"source": {"kind": "all"}, "excluded": keep})
        self.assertEqual((self.PHOTOS - 5, self.PHOTOS, 5), (done["total"], done["requested"], done["excluded"]))
        for photo_id in self.ids:
            self.assertEqual(photo_id not in keep, "Trips/Lakes" in self.tags(photo_id), photo_id)

    def test_ids_nobody_has_and_duplicates_are_left_out_and_said(self):
        done = self.tags_edit(add=["Trips/Lakes"], selection_body={"ids": [self.ids[0], self.ids[0], 999999, self.ids[1]]})
        self.assertEqual((2, 3, 1, 2), (done["total"], done["requested"], done["missing"], done["changed"]))

    def test_a_selection_of_only_ids_nobody_has_writes_nothing(self):
        reply = self.edit("tags", {"ids": [999998, 999999]}, {"add": ["A"]}, expect=400)
        self.assertIn("no photos", reply["error"])
        self.assertEqual(0, self.files.writes)

    def test_the_journal_has_one_change_named_for_the_edit_whatever_the_count(self):
        self.tags_edit(add=["Trips/Coast"])
        found = self.changes("bulk tags%")
        self.assertEqual(1, len(found), "60 photos are one change")
        self.assertEqual(("bulk tags", "applied"), found[0][1:])

    def test_the_cached_scans_of_folders_are_forgotten_after_it(self):
        with mock.patch.object(tagpup_routes, "forget_scans") as forget:
            self.tags_edit(add=["Trips/Coast"])
        forget.assert_called_once_with(self.library)

    def test_nothing_is_a_job_any_more(self):
        for path in ("start", "status?job=1", "current", "cancel"):
            reply = self.client.open("/library/api/library/bulk/" + path, method="POST" if path in ("start", "cancel") else "GET")
            self.assertEqual(404, reply.status_code, path)
        self.assertEqual([], self.vl.rows("SELECT id FROM job_runs"), "an edit leaves no run to claim, poll or cancel")


class TheLimit(Bulk):
    """5,000 photos at a time: the 5,000th is written, the 5,001st refuses the whole request before a file is read
    (findings #535: a select-all of 68,000 posted five megabytes of paths and held the file-changes lock for hours).

    Lifted: test_bulk_cap (3 tests, the same refusal through the folder routes bulk-tags and rename-photos). MAX_BATCH =
    5001 in a scratch copy fails test_the_limit_is_five_thousand and test_batches.Cap."""

    def test_the_limit_is_five_thousand(self):
        self.assertEqual(5000, batches.MAX_BATCH)

    def test_exactly_the_limit_is_written_and_one_more_is_refused_with_the_sentence(self):
        with mock.patch.object(batches, "MAX_BATCH", 50):
            done = self.tags_edit(add=["Trips/Coast"], selection_body={"ids": self.ids[:50]})
            self.assertEqual(50, done["changed"])
            refused = self.tags_edit(add=["Trips/Lakes"], selection_body={"ids": self.ids[:51]}, expect=400)
        self.assertEqual("Select up to 50 photos at a time (51 are selected).", refused["error"])
        self.assertNotIn("Trips/Lakes", self.tags(self.ids[0]))
        self.assertEqual(50, self.files.writes, "the refused request read and wrote nothing")

    def test_a_source_is_counted_once_the_excluded_are_out(self):
        with mock.patch.object(batches, "MAX_BATCH", 50):
            done = self.tags_edit(add=["Trips/Coast"], selection_body={"source": {"kind": "all"}, "excluded": self.ids[:10]})
            self.assertEqual((50, 10), (done["changed"], done["excluded"]), "60 less 10 is 50: within the limit")
            refused = self.tags_edit(add=["Trips/Lakes"], selection_body={"source": {"kind": "all"}, "excluded": self.ids[:9]}, expect=400)
        self.assertEqual("Select up to 50 photos at a time (51 are selected).", refused["error"])

    def test_the_question_before_a_delete_is_limited_the_same(self):
        with mock.patch.object(batches, "MAX_BATCH", 10):
            reply = self.client.post("/library/api/library/selection/delete-check", json={"selection": ALL})
        self.assertEqual(400, reply.status_code)
        self.assertIn("Select up to 10 photos at a time", reply.get_json()["error"])

    def test_a_tally_is_not_limited(self):
        with mock.patch.object(batches, "MAX_BATCH", 10):
            reply = self.client.post("/library/api/library/selection/tally", json={"selection": ALL})
        self.assertEqual((200, self.PHOTOS), (reply.status_code, reply.get_json()["total"]))


class OneAtATime(Bulk):
    def test_an_edit_that_fails_leaves_the_library_free_for_the_next(self):
        self.files.cannot_start = FileNotFoundError("ExifTool is not where the library says it is")
        self.tags_edit(add=["Trips/Coast"], expect=500)
        self.assertEqual(0, batches.running_here())
        self.files.cannot_start = None
        self.tags_edit(add=["Trips/Coast"])


class Interrupted(Bulk):
    """The request runs to its end on the server whether or not the page waits (a closed tab changes nothing). A process that dies
    part-way leaves the files it wrote written and journaled; the rest are planned, and the next change of photo files finishes
    them (file_changes.settle: the edit the person asked for is finished)."""

    def test_a_process_dying_in_the_middle_leaves_a_change_that_the_next_settle_finishes(self):
        def die(_path, n):
            if n == 30:
                raise Crash()
        self.files.on_write = die
        edit = bulk_edit.prepare(self.library, "tags", {"add": ["Trips/Coast"]})
        with self.assertRaises(Crash):
            bulk_edit.run(self.library, edit, self.ids, "exiftool")
        self.assertEqual(0, batches.running_here(), "the line is free for the next batch")
        done = [photo_id for photo_id in self.ids if "Trips/Coast" in self.tags(photo_id)]
        self.assertEqual(29, len(done), "the files written before it are written")
        change = self.changes("bulk tags%")
        self.assertEqual(1, len(change))
        self.assertNotEqual("applied", change[0][2], "and the change says it is not finished")
        self.files.on_write = None
        self.assertEqual(1, file_changes.settle(self.library, "exiftool"))
        self.assertEqual(self.PHOTOS, len([photo_id for photo_id in self.ids if "Trips/Coast" in self.tags(photo_id)]))
        self.assertEqual("applied", self.changes("bulk tags%")[0][2])
        for photo_id in self.ids:
            self.assertEqual(1, self.files.writes_of[paths.key(self.path(photo_id))], "no photo is written twice")

    def test_an_error_that_is_not_one_photos_is_a_500_with_a_fixed_sentence_naming_no_path(self):
        secret = "C:\\Users\\Someone\\Pictures\\Private Folder\\clip.jpg is locked by a program"
        self.files.cannot_start = FileNotFoundError(secret)
        reply = self.tags_edit(add=["Trips/Coast"], expect=500)
        self.assertIn("FileNotFoundError", reply["error"])
        self.assertNotIn("Private Folder", reply["error"])
        self.assertEqual(0, self.files.writes)

    def test_a_page_that_stops_waiting_changes_nothing_the_edit_is_done_when_the_request_returns(self):
        # The test client answers when the request is done, as the server does whether or not anyone is reading: what the request
        # wrote is in the files, the rows and the journal by then, with nothing left to settle.
        self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(0, file_changes.settle(self.library, "exiftool"))
        self.assertEqual("applied", self.changes("bulk tags%")[0][2])


class WhilePhotosMove(Bulk):
    def test_a_photo_deleted_before_the_write_is_skipped_and_counted_and_the_others_are_written(self):
        order = self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]
        os.remove(self.path(order[40]))
        self.vl.conn.execute("DELETE FROM photos WHERE id = ?", (order[41],))
        self.vl.conn.commit()
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS - 1, 1, self.PHOTOS - 2), (done["total"], done["skipped_missing"], done["changed"]),
                         "a row that is gone is not in the selection; a file that is gone is counted missing")
        self.assertEqual(["Trips/Coast"], self.tags(order[59]), "the photos after the missing one are written")

    def test_a_file_edited_by_another_program_between_the_plan_and_the_write_is_reported_and_not_overwritten(self):
        target = self.path(self.ids[10])

        def other_program(path, n):
            # The second read of the file is the write's own check, after the plan was committed.
            if paths.key(path) == paths.key(target) and n == 2:
                self.files.keep_tags(target, ["Changed By Another Program"])
        self.files.on_read = other_program
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((1, self.PHOTOS - 1), (done["error_count"], done["changed"]))
        self.assertEqual(self.ids[10], done["errors"][0]["id"])
        self.assertIn("changed since it was read", done["errors"][0]["why"])
        self.assertEqual(["Changed By Another Program"], self.files.tags_of(target), "never overwritten")

    def test_a_file_replaced_by_another_is_written_as_the_file_it_now_is(self):
        replaced = self.path(self.ids[4])
        self.files.fields[paths.key(replaced)] = {"EXIF:DateTimeOriginal": self.taken[self.ids[4]]}
        self.files.keep_tags(replaced, ["From The Replacement"])
        self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(["From The Replacement", "Trips/Coast"], self.files.tags_of(replaced))


class WhatGoesWrong(Bulk):
    def test_an_unreadable_file_is_an_error_entry_and_the_others_go_on(self):
        self.files.unreadable.add(paths.key(self.path(self.ids[5])))
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((1, self.PHOTOS - 1, 0), (done["error_count"], done["changed"], done["skipped_missing"]))
        self.assertEqual(self.ids[5], done["errors"][0]["id"])
        self.assertEqual("p005.jpg", done["errors"][0]["name"])
        self.assertTrue(done["errors"][0]["why"])

    def test_a_write_that_fails_does_not_stop_the_others(self):
        self.files.fails.add(paths.key(self.path(self.ids[2])))
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((1, self.PHOTOS - 1), (done["error_count"], done["changed"]))
        self.assertIn("locked", done["errors"][0]["why"])
        self.assertEqual(["Trips/Coast"], self.tags(self.ids[59]), "the photos after the failure are written")

    def test_only_the_first_fifty_errors_are_named_and_all_are_counted(self):
        self.files.unreadable.update(paths.key(self.path(photo_id)) for photo_id in self.ids[:55])
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((55, 50, self.PHOTOS - 55), (done["error_count"], len(done["errors"]), done["changed"]))

    def test_a_name_and_a_reason_are_kept_to_two_hundred_characters(self):
        target = self.path(self.ids[3])

        def fails(path, _n):
            if paths.key(path) == paths.key(target):
                raise RuntimeError("y" * 500)
        self.files.on_write = fails
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(200, len(done["errors"][0]["why"]))

    def test_photos_on_a_share_that_does_not_answer_are_errors_not_sent_to_exiftool_and_the_others_are_written(self):
        away = {paths.key(self.path(photo_id)) for photo_id in self.ids[:6]}
        real = shares.stamp_of

        def stamp_of(path):
            return shares.UNANSWERED if paths.key(path) in away else real(path)
        with mock.patch.object(shares, "stamp_of", stamp_of):
            done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((6, self.PHOTOS - 6), (done["error_count"], done["changed"]))
        self.assertIn("share that did not answer", done["errors"][0]["why"])
        for key in away:
            self.assertNotIn(key, self.files.reads, "ExifTool was never asked to read a file on the share that is away")

    def test_a_whole_share_away_is_every_photo_an_error_at_once_and_nothing_is_read(self):
        with mock.patch.object(shares, "stamp_of", return_value=shares.UNANSWERED):
            done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS, 0), (done["error_count"], done["changed"]))
        self.assertEqual({}, self.files.reads)

    def test_a_root_this_machine_does_not_place_is_the_gates_409_before_the_route_runs(self):
        with mock.patch("tagpup.web.roots_gate.problem_of", return_value="This machine does not place the root pictures."):
            reply = self.post({"op": "tags", "selection": ALL, "params": {"add": ["A"]}})
        self.assertEqual((409, True), (reply.status_code, reply.get_json()["roots_problem"]))
        self.assertEqual(0, self.files.writes)

    def test_the_route_answers_this_pc_only(self):
        reply = self.post({"op": "tags", "selection": ALL, "params": {"add": ["A"]}}, environ_overrides=REMOTE)
        self.assertEqual(403, reply.status_code)
        self.assertEqual(0, self.files.writes)


class ADeadExifTool(Bulk):
    """ExifTool that stalls costs one deadline for the whole edit, not one for each photo (tests/test_bulk_edits_stalled_exiftool.py
    holds the rest)."""

    def test_the_edit_is_one_session_for_the_60_photos(self):
        self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(1, len(self.files.sessions))


class People(Bulk):
    def setUp(self):
        super().setUp()
        # Rowan is in the tree once; Wren twice, under one people root: where to file her is a question.
        self.vl.tree("People/Friends/Wren Halloway", face_root="People")
        self.rowan = ["Rowan Thackeray"]

    def test_a_person_is_filed_as_the_chip_files_them_and_no_node_is_made(self):
        nodes = self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy")
        done = self.edit("people", {"ids": self.ids[:4]}, {"add": self.rowan})
        self.assertEqual(4, done["changed"])
        for photo_id in self.ids[:4]:
            self.assertIn("People/Rowan Thackeray", self.tags(photo_id))
        self.assertEqual(nodes, self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy"))
        self.assertEqual(("bulk people", "applied"), self.changes("bulk people%")[0][1:])

    def test_a_name_the_tree_does_not_hold_is_filed_under_the_one_people_root_without_making_a_node(self):
        nodes = self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy")
        self.edit("people", {"ids": self.ids[:2]}, {"add": ["Cora Ingersoll"]})
        self.assertIn("People/Cora Ingersoll", self.tags(self.ids[0]))
        self.assertEqual(nodes, self.vl.rows("SELECT COUNT(*) FROM tag_taxonomy"))

    def test_a_keyword_that_only_shares_the_persons_leaf_is_not_them_and_the_person_is_added(self):
        # By the id of their node alone (#1394): it was "already named by their leaf, whatever the path", and not written.
        self.files.keep_tags(self.path(self.ids[1]), ["Family/Rowan Thackeray"])
        done = self.edit("people", {"ids": [self.ids[1], self.ids[2]]}, {"add": self.rowan})
        self.assertEqual((2, 0), (done["changed"], done["unchanged"]))
        self.assertEqual(["Family/Rowan Thackeray", "People/Rowan Thackeray"], self.tags(self.ids[1]))
        self.assertEqual(["People/Rowan Thackeray"], self.tags(self.ids[2]))

    def test_a_person_filed_in_two_places_is_refused_up_front_with_a_sentence(self):
        reply = self.edit("people", params={"add": ["Wren Halloway"]}, expect=400)
        self.assertIn("More than one person is called", reply["error"])
        # Given the path, it is no question.
        done = self.edit("people", {"ids": self.ids[:1]}, {"add": ["People/Friends/Wren Halloway"]})
        self.assertEqual(1, done["changed"])
        self.assertIn("People/Friends/Wren Halloway", self.tags(self.ids[0]))

    def test_a_blank_name_is_refused(self):
        for params in ({"add": [""]}, {"add": ["   "]}, {"remove": ["\t"]}):
            self.edit("people", params=params, expect=400)

    def node(self, tag):
        return self.vl.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", tag)[0][0]

    def test_two_people_called_alike_are_two_people_an_edit_adds_by_id(self):
        """A name two people have is refused (which one?); their ids are two persons, and a file that names the first by its
        leaf still gets the second."""
        self.vl.tree("People/Family/Sam", "People/Friends/Sam", face_root="People")
        reply = self.edit("people", params={"add": ["Sam"]}, expect=400)
        self.assertIn("More than one person is called", reply["error"])
        family, friends = self.node("People/Family/Sam"), self.node("People/Friends/Sam")
        self.files.keep_tags(self.path(self.ids[0]), ["People/Family/Sam"])
        done = self.edit("people", {"ids": self.ids[:2]}, {"add_ids": [friends]})
        self.assertEqual(2, done["changed"])
        self.assertEqual(["People/Family/Sam", "People/Friends/Sam"], sorted(self.tags(self.ids[0])))
        self.assertEqual(["People/Friends/Sam"], self.tags(self.ids[1]))
        again = self.edit("people", {"ids": self.ids[:2]}, {"add_ids": [friends]})
        self.assertEqual((0, 2), (again["changed"], again["unchanged"]), "a person the file names by their node is not added again")
        self.edit("people", {"ids": self.ids[:1]}, {"remove_ids": [family]})
        self.assertEqual(["People/Friends/Sam"], self.tags(self.ids[0]), "only the one picked is taken off")

    def test_an_id_that_is_nobodys_or_a_groups_is_refused_before_a_photo_is_touched(self):
        self.vl.tree("People/Family/Sam", face_root="People")
        group = self.node("People/Family")
        self.edit("people", params={"add_ids": [99999]}, expect=404)
        self.edit("people", params={"add_ids": [group]}, expect=400)
        for bad in ("7", 1.5, True, None):
            self.edit("people", params={"add_ids": [bad]}, expect=400)
        self.assertEqual(["Old/Stuff"], self.tags(self.ids[0]), "the file is as it was")

    def test_taking_a_person_off_takes_every_way_the_tree_files_them(self):
        self.files.keep_tags(self.path(self.ids[1]), ["People/Wren Halloway", "People/Friends/Wren Halloway", "Trips/Coast"])
        self.edit("people", {"ids": [self.ids[1]]}, {"remove": ["Wren Halloway"]})
        self.assertEqual(["Trips/Coast"], self.tags(self.ids[1]))


class RunsAnOlderVersionRecorded(Bulk):
    """Until this version an edit was a JOB, recorded in `job_runs` as 'bulk edit' with its counts. Those rows stay (no migration):
    the Activity page still lists them, a restart's abandoned one included, and nothing else reads them."""

    def old_run(self, outcome, counts):
        found = self.vl.conn.execute(
            "INSERT INTO job_runs (job, library, started, outcome, changed, owner) VALUES ('bulk edit', 'library', ?, ?, ?, ?)",
            (job_runs.stamp(time.time() - 3600), outcome, json.dumps(counts), GHOST if outcome == "running" else None)).lastrowid
        self.vl.conn.commit()
        return found

    def test_the_activity_page_lists_them_whatever_became_of_them(self):
        done = self.old_run("done", {"op": "tags", "job": 5, "total": 60, "done": 60, "changed": 60, "state": "done",
                                     "what": "bulk tags: add 1 tag(s), 60 of 60 photos"})
        abandoned = self.old_run("running", {"op": "tags", "job": 6, "total": 60, "done": 25, "changed": 25, "state": "running",
                                             "what": "bulk tags: add 1 tag(s), 25 of 60 photos"})
        bare = self.old_run("failed", {})
        listed = {entry["id"]: entry for entry in activity.timeline(self.library) if entry["kind"] == "job"}
        self.assertEqual({done, abandoned, bare}, set(listed))
        self.assertEqual("done", listed[done]["outcome"])
        self.assertEqual("bulk tags: add 1 tag(s), 60 of 60 photos", listed[done]["what"])
        self.assertEqual("bulk edit", listed[bare]["what"], "a run with no sentence is named for its job")
        self.assertEqual(200, self.client.get("/library/api/activity/jobs").status_code)

    def test_a_new_edit_is_not_refused_by_an_old_run_and_leaves_no_run_of_its_own(self):
        self.old_run("running", {"op": "tags", "job": 6, "total": 60, "done": 25, "changed": 25, "state": "running"})
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(self.PHOTOS, done["changed"])
        self.assertEqual(1, self.vl.rows("SELECT COUNT(*) FROM job_runs")[0][0])

    def test_naming_faces_is_not_held_back_by_an_old_run(self):
        from tagpup.jobs import naming_faces
        self.old_run("running", {"op": "tags", "job": 6, "total": 60, "done": 25, "changed": 25, "state": "running"})
        self.assertEqual([], naming_faces.other_work(self.library))


if __name__ == "__main__":
    unittest.main()
