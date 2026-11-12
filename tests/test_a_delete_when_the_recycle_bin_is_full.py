"""A delete of files from a place with no Recycle Bin of its own (a network share) when this PC's Recycle Bin, which keeps their
copies, is full (owner, 2026-11-12): the page asks "The Recycle Bin is full: delete permanently instead?" rather than refusing; OK
deletes those files for good, Cancel (no resend) deletes nothing, and the Bin stays the default while it has room. One confirm
path: the one of a file its own drive's Bin would not keep (recycle_bin.NotKept, `for_good`).

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


if __name__ == "__main__":
    unittest.main()
