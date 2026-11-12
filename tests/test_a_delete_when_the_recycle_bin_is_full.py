"""A delete of files from a place with no Recycle Bin of its own (a network share) when this PC's Recycle Bin, which keeps their
copies, is full (owner, 2026-11-12): the page asks "The Recycle Bin is full: delete permanently instead?" rather than refusing; OK
deletes those files for good, Cancel (no resend) deletes nothing, and the Bin stays the default while it has room. One confirm
path: the one of a file its own drive's Bin would not keep (recycle_bin.NotKept, `for_good`). A file on a LOCAL drive whose own Bin
is nearly full is asked of the same way (owner, 2026-11-12): never refused, never sent to the Bin unweighed.

The Recycle Bin is the test's: `send_to_recycle_bin` removes the copy and says so, which folders have none is the test's to say,
and the Bin's size is the test home's (TAGPUP_RECYCLE_BIN) or stood in; nothing reaches the owner's Recycle Bin. Photos are real
small files and rows as the indexer records them. Fictional names only.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import recycle_bin  # noqa: E402

FULL = {"TAGPUP_RECYCLE_BIN": "10,10,0"}     # a Bin of 10 MB holding 10 MB: full
EMPTIED = {"TAGPUP_RECYCLE_BIN": "10,0,0"}  # the same Bin, emptied in Explorer


class BinFull(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.share = os.path.join(self.vl.pictures, "Harbour Share")
        self.binned = []
        recycle_bin.forget_bin()
        self.addCleanup(recycle_bin.forget_bin)
        bin_patch = mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle)
        bin_patch.start()
        self.addCleanup(bin_patch.stop)
        reason_patch = mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason)
        reason_patch.start()
        self.addCleanup(reason_patch.stop)
        self.on_share = [self.vl.photo("Harbour Share", "pier%d.jpg" % n, taken="2024:03:0%d 10:00:00" % (n + 1), real=True,
                                       size=(16, 16)) for n in range(2)]
        self.on_disk = [self.vl.photo("Home Folder", "garden%d.jpg" % n, taken="2024:04:0%d 10:00:00" % (n + 1), real=True,
                                      size=(16, 16)) for n in range(2)]

    def reason(self, path):
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.share)) else None

    def recycle(self, path):
        self.binned.append(path)
        os.remove(path)
        return True

    def held(self, ids):
        found = {row[0] for row in self.vl.rows("SELECT id FROM photos")}
        return [photo_id for photo_id in ids if photo_id in found]

    def delete_one(self, path, for_good=False):
        body = {"path": path, "for_good": True} if for_good else {"path": path}
        return self.client.post("/library/api/photo/delete", json=body)

    def check(self):
        reply = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def start(self, params):
        return self.client.post("/library/api/library/bulk-edit", json={"op": "delete", "selection": {"ids": self.on_share},
                                                                         "params": params})

    def test_the_panels_delete_asks_and_only_ok_deletes_it_for_good(self):
        path = self.vl.path_of(self.on_share[0])
        with mock.patch.dict(os.environ, FULL):
            reply = self.delete_one(path)
            self.assertEqual(400, reply.status_code, reply.get_json())
            body = reply.get_json()
            self.assertEqual(([os.path.basename(path)], 1), (body.get("not_kept"), body.get("bin_full")))
            self.assertIn("is full", body["error"])
            self.assertTrue(os.path.exists(path), "asked, not deleted: Cancel (no resend) leaves it")
            self.assertEqual(self.on_share, self.held(self.on_share))
            done = self.delete_one(path, for_good=True)
        self.assertEqual(200, done.status_code, done.get_json())
        said = done.get_json()
        self.assertEqual((True, False), (said["for_good"], said["through_this_pc"]))
        self.assertIn("Deleted for good, as you confirmed", said["message"])
        self.assertIn("is full", said["message"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual([], self.binned, "no copy was made or binned")
        self.assertEqual(self.on_share[1:], self.held(self.on_share))

    def test_a_bulk_delete_bins_what_the_bin_still_keeps_and_deletes_the_rest_for_good_once_confirmed(self):
        files = [self.vl.path_of(each) for each in self.on_share]
        size = max(os.path.getsize(each) for each in files)
        capacity = 1 << 30
        # Room for one copy and a half, BIN_MARGIN kept: the two copies together do not fit; the first does.
        room = (capacity, int(capacity * (1 - recycle_bin.BIN_MARGIN)) - int(1.5 * size), False)
        with mock.patch.object(recycle_bin, "_test_bin", return_value=room):
            asked = self.check()
            self.assertEqual((None, 2, 2, 0), (asked["no_room"], asked["for_good"]["count"], asked["bin_full"],
                                               asked["through_this_pc"]))
            self.assertEqual(sorted(os.path.basename(each) for each in files), asked["for_good"]["names"])
            refused = self.start({"token": asked["token"]})
            self.assertEqual(409, refused.status_code, "a start that did not confirm is refused, nothing begun")
            self.assertEqual(self.on_share, self.held(self.on_share))
            done = self.start({"token": asked["token"], "for_good": True}).get_json()
        self.assertEqual((2, 1, 0), (done["changed"], done["for_good"], done["error_count"]), done)
        self.assertEqual(1, len(self.binned), "the Bin keeps what it has room for: for_good is leave, never force")
        self.assertEqual([], self.held(self.on_share))
        self.assertFalse([each for each in files if os.path.exists(each)])


    def test_a_photo_on_a_local_drive_whose_own_bin_is_nearly_full_asks_and_only_ok_deletes_it_for_good(self):
        path = self.vl.path_of(self.on_disk[0])
        with mock.patch.dict(os.environ, FULL):
            reply = self.delete_one(path)
            self.assertEqual(400, reply.status_code, reply.get_json())
            body = reply.get_json()
            self.assertEqual(([os.path.basename(path)], 1), (body.get("not_kept"), body.get("bin_full")))
            self.assertIn("is full", body["error"])
            self.assertTrue(os.path.exists(path), "asked, not deleted: Cancel (no resend) leaves it")
            self.assertEqual([], self.binned, "never sent to the Bin unweighed")
            self.assertEqual(self.on_disk, self.held(self.on_disk))
            done = self.delete_one(path, for_good=True)
        self.assertEqual(200, done.status_code, done.get_json())
        said = done.get_json()
        self.assertEqual((True, False), (said["for_good"], said["through_this_pc"]))
        self.assertIn("Deleted for good, as you confirmed", said["message"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual([], self.binned, "deleted where it was, no Bin touched")
        self.assertEqual(self.on_disk[1:], self.held(self.on_disk))

    def test_a_local_photo_goes_to_its_bin_while_the_bin_has_room(self):
        path = self.vl.path_of(self.on_disk[0])
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "1024,1,0"}):
            reply = self.delete_one(path)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertFalse(reply.get_json()["for_good"])
        self.assertEqual([path], self.binned)

    def test_a_bin_emptied_after_the_panel_said_it_was_full_takes_the_next_delete(self):
        # Delete -> "The Recycle Bin is full" -> Cancel -> the owner empties the Bin in Explorer -> Delete again at once, well
        # within the ten minutes the Bin's size is remembered: the Bin is asked again, and the photo goes to it.
        for path in (self.vl.path_of(self.on_disk[0]), self.vl.path_of(self.on_share[0])):
            with self.subTest(on_share=path.startswith(self.share)):
                self.binned.clear()
                recycle_bin.forget_bin()        # a scenario of its own: the Bin not yet asked
                with mock.patch.dict(os.environ, FULL):
                    asked = self.delete_one(path)
                self.assertEqual(1, asked.get_json().get("bin_full"), asked.get_json())
                with mock.patch.dict(os.environ, EMPTIED):
                    reply = self.delete_one(path)
                self.assertEqual(200, reply.status_code, reply.get_json())
                self.assertFalse(reply.get_json()["for_good"], "binned: not 'full' over an empty Bin")
                self.assertEqual(1, len(self.binned))
                self.assertFalse(os.path.exists(path))

    def test_a_bulk_start_weighs_the_bin_as_it_is_now_not_as_its_question_found_it(self):
        selection = {"ids": self.on_disk}
        files = [self.vl.path_of(each) for each in self.on_disk]
        with mock.patch.dict(os.environ, FULL):
            asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": selection}).get_json()
        self.assertEqual((2, 2), (asked["for_good"]["count"], asked["bin_full"]))
        # The owner empties the Bin and only then presses OK, within the ten minutes: the start sees the Bin as it is now, and
        # deletes nothing for good it was not told of.
        with mock.patch.dict(os.environ, EMPTIED):
            refused = self.client.post("/library/api/library/bulk-edit", json={
                "op": "delete", "selection": selection, "params": {"token": asked["token"], "for_good": True}})
            self.assertEqual(409, refused.status_code, refused.get_json())
            self.assertIn("Recycle Bin", refused.get_json()["error"])
            self.assertTrue(all(os.path.exists(each) for each in files), "nothing deleted for good over an emptied Bin")
            again = self.client.post("/library/api/library/selection/delete-check", json={"selection": selection}).get_json()
            self.assertEqual((0, 0), (again["for_good"]["count"], again["bin_full"]))
            done = self.client.post("/library/api/library/bulk-edit", json={
                "op": "delete", "selection": selection, "params": {"token": again["token"]}}).get_json()
        self.assertEqual((2, 0, 0), (done["changed"], done["for_good"], done["error_count"]), done)
        self.assertEqual(2, len(self.binned))

    def test_the_bulk_question_names_local_photos_when_their_bin_is_nearly_full_and_only_a_confirmed_start_deletes_them(self):
        files = [self.vl.path_of(each) for each in self.on_disk]
        selection = {"ids": self.on_disk}
        with mock.patch.dict(os.environ, FULL):
            asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": selection}).get_json()
            self.assertEqual((2, 2, 0), (asked["for_good"]["count"], asked["bin_full"], asked["through_this_pc"]), asked)
            refused = self.client.post("/library/api/library/bulk-edit", json={
                "op": "delete", "selection": selection, "params": {"token": asked["token"]}})
            self.assertEqual(409, refused.status_code, "a start that did not confirm is refused, nothing begun")
            self.assertTrue(all(os.path.exists(each) for each in files))
            done = self.client.post("/library/api/library/bulk-edit", json={
                "op": "delete", "selection": selection, "params": {"token": asked["token"], "for_good": True}}).get_json()
        self.assertEqual((2, 2, 0), (done["changed"], done["for_good"], done["error_count"]), done)
        self.assertEqual([], self.binned)
        self.assertFalse([each for each in files if os.path.exists(each)])


if __name__ == "__main__":
    unittest.main()
