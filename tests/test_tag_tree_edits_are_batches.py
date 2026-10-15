"""A rename of a tag or a person rewrites every photo FIRST and moves the tree AFTER (tagpup.services.tags._retag, the one place
that orders the two), so a batch that was stopped (Cancel, an update), cancelled while it waited or refused because the line was
full leaves the tree as it was and says so, with the change it wrote; making the same edit again finishes the rest. 60 photos
carry the tag: three chunks of the batch model (tagpup.services.batches). Fictional names only.
"""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_batches as B  # noqa: E402
import test_bulk_edits as T  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import batches, tags  # noqa: E402
from tagpup.store import taxonomy  # noqa: E402
from tagpup.web import lifecycle, tuner_routes  # noqa: E402

OLD, NEW = "Trips/Coast", "Trips/Shore"


class Rename(T.Bulk):
    def setUp(self):
        super().setUp()
        self.tags_edit(add=[OLD], remove=["Old/Stuff"])       # every photo carries exactly the tag
        self.addCleanup(batches.reopen)
        self.node = self.vl.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", OLD)[0][0]

    def rename(self, client=None):
        return (client or self.client).post("/library/api/taxonomy/rename", json={"tag_id": self.node, "new_name": "Shore"})

    def in_tree(self):
        return [tag for tag in (OLD, NEW) if taxonomy.find(self.library.path, tag)]

    def holding(self, tag):
        return len([each for each in self.ids if self.tags(each) == [tag]])

    def test_cancelled_after_the_first_chunk_leaves_the_tree_and_the_second_try_finishes_the_rest(self):
        before = self.files.writes          # the writes of setUp; the fake counts them all

        def cancel(_path, n):
            if n == before + 10:
                client = self.app.test_client()
                ticket = client.get("/api/activity/batches").get_json()["batches"][0]["ticket"]
                self.assertEqual(200, client.post("/api/activity/batches/cancel", json={
                    "library": self.library.name, "ticket": ticket}).status_code)
        self.files.on_write = cancel
        reply = self.rename()
        self.assertEqual(400, reply.status_code)
        said = reply.get_json()["error"]
        self.assertIn("Stopped as asked after 25 of 60 photos", said)
        self.assertIn("recorded as change", said)
        self.assertIn("make the same edit again to finish the rest", said)
        self.assertEqual([OLD], self.in_tree(), "the tree is as it was")
        self.assertEqual((25, 35), (self.holding("Trips/Shore"), self.holding(OLD)))
        self.files.on_write = None
        done = self.rename()
        self.assertEqual(200, done.status_code, done.get_json())
        self.assertEqual([NEW], self.in_tree(), "the second try moved the tree")
        self.assertEqual((60, 0), (self.holding("Trips/Shore"), self.holding(OLD)))
        self.assertEqual(before + 60, self.files.writes, "no photo was written twice")

    def test_a_photo_that_cannot_be_written_means_not_renamed_and_the_reply_names_it(self):
        stuck = self.path(self.ids[7])
        self.files.fails.add(paths.key(stuck))
        reply = self.rename()
        body = reply.get_json()
        self.assertEqual((200, False, False), (reply.status_code, body["success"], body["renamed"]))
        self.assertIn("could not be rewritten", body["error"])
        self.assertEqual([stuck], [each["path"] for each in body["failed"]])
        self.assertEqual(1, body["failed_count"])
        self.assertEqual([OLD], self.in_tree(), "the tree keeps the old name")
        self.files.fails.clear()
        self.assertEqual(200, self.rename().status_code)
        self.assertEqual([NEW], self.in_tree())

    def test_a_photo_whose_file_is_gone_does_not_hold_the_tree_back(self):
        os.remove(self.path(self.ids[7]))
        body = self.rename().get_json()
        self.assertTrue(body["success"], body)
        self.assertEqual(([NEW], self.PHOTOS - 1), (self.in_tree(), self.holding("Trips/Shore")))

    def test_a_move_with_no_photo_left_still_moves_the_branch(self):
        self.vl.tree("Old/Stuff/Deep")
        node = self.vl.rows("SELECT id FROM tag_taxonomy WHERE tag = 'Old/Stuff'")[0][0]     # setUp took it off every photo
        done = tags.delete(self.library, node, "move", OLD, "exiftool")
        self.assertTrue(done.ok, done.message())
        self.assertEqual((None, True), (taxonomy.find(self.library.path, "Old/Stuff"),
                                        bool(taxonomy.find(self.library.path, OLD + "/Deep"))), "the branch went on under the target")

    def test_cancelled_while_it_waits_writes_nothing_and_leaves_the_tree(self):
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def first():
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                started.set()
                release.wait(30)
        holder = threading.Thread(target=first)
        holder.start()
        self.assertTrue(started.wait(30))
        before = self.files.writes
        waiting, answered = B.in_thread(lambda: self.rename(self.app.test_client()))
        B.wait_until(lambda: batches.running_here() == 2, "the rename waits")
        queued = [each for each in batches.listing(self.library) if each["state"] == "queued"][0]
        self.assertTrue(batches.cancel(self.library, queued["ticket"]))
        waiting.join(30)
        release.set()
        holder.join(30)
        self.assertEqual(400, answered[0].status_code)
        self.assertIn("cancelled before it began", answered[0].get_json()["error"])
        self.assertEqual(([OLD], before), (self.in_tree(), self.files.writes))

    def test_a_full_line_refuses_it_and_leaves_the_tree(self):
        release, started = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def first():
            with batches.claim(self.library, "bulk tags", self.PHOTOS):
                started.set()
                release.wait(30)
        holder = threading.Thread(target=first)
        holder.start()
        self.assertTrue(started.wait(30))
        with mock.patch.object(batches, "MOST_WAITING", 0):
            reply = self.rename()
        release.set()
        holder.join(30)
        self.assertEqual(400, reply.status_code)
        self.assertIn("photo batches are ahead of this one", reply.get_json()["error"])
        self.assertEqual([OLD], self.in_tree())

    def test_a_hand_over_stops_it_at_a_chunk_and_the_tree_waits_for_the_next_start(self):
        gate = T.Gate(self.files.writes + 1)      # the first write of the rename (setUp wrote the photos once)

        def slow(path, n):
            gate(path, n)
            threading.Event().wait(0.02)
        self.files.on_write = slow
        self.addCleanup(gate.release.set)
        writing, answered = B.in_thread(lambda: self.rename(self.app.test_client()))
        self.assertTrue(gate.reached.wait(30))
        moving = lifecycle.Lifecycle()
        draining, drained = B.in_thread(lambda: moving.drain(seconds=30, lease=60))
        B.wait_until(lambda: [each["state"] for each in batches.listing(self.library)] == ["stopping"], "asked to stop")
        gate.release.set()
        writing.join(30)
        draining.join(30)
        self.assertEqual([{"drained": True}], drained)
        self.assertEqual(400, answered[0].status_code)
        self.assertEqual(([OLD], 25), (self.in_tree(), self.holding("Trips/Shore")))
        moving.resume()
        self.files.on_write = None
        self.assertEqual(200, self.rename().status_code)
        self.assertEqual(([NEW], 60), (self.in_tree(), self.holding("Trips/Shore")))


class PersonRename(T.Bulk):
    NAME, NEW = "Wren Halloway", "Wren Halloway-Vale"

    def setUp(self):
        super().setUp()
        self.edit("people", params={"add": [self.NAME]})
        self.addCleanup(batches.reopen)

    def holding(self, tag):
        return len([each for each in self.ids if tag in self.tags(each)])

    def test_a_photo_that_cannot_be_written_means_not_renamed_for_a_person_too(self):
        stuck = self.path(self.ids[3])
        self.files.fails.add(paths.key(stuck))
        result = tags.rename_person(self.library, self.NAME, self.NEW, "exiftool")
        self.assertEqual((False, 0), (result.ok, result.changed))
        self.assertEqual([stuck], [each["path"] for each in result.details["failed_photos"]])
        self.assertTrue(taxonomy.find(self.library.path, "People/" + self.NAME))
        with self.app.app_context():
            reply = tuner_routes.not_renamed(result).get_json()
        self.assertEqual((False, False), (reply["success"], reply["renamed"]))

    def test_stopped_after_the_first_chunk_the_person_keeps_their_node_until_it_is_made_again(self):
        first_write = self.files.writes
        self.files.on_write = lambda path, n: batches.stop_all() if n == first_write + 10 else None
        result = tags.rename_person(self.library, self.NAME, self.NEW, "exiftool")
        self.assertIn("Stopped as asked after 25 of 60 photos", result.refused)
        self.assertTrue(taxonomy.find(self.library.path, "People/" + self.NAME), "the node is where it was")
        self.assertIsNone(taxonomy.find(self.library.path, "People/" + self.NEW))
        self.assertEqual(25, self.holding("People/" + self.NEW))
        batches.reopen()
        self.files.on_write = None
        done = tags.rename_person(self.library, self.NAME, self.NEW, "exiftool")
        self.assertIsNone(done.refused)
        self.assertTrue(taxonomy.find(self.library.path, "People/" + self.NEW))
        self.assertIsNone(taxonomy.find(self.library.path, "People/" + self.NAME))
        self.assertEqual(60, self.holding("People/" + self.NEW))


if __name__ == "__main__":
    unittest.main()
