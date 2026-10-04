"""A photo deleted from a place with no Recycle Bin goes through this PC (#694, the owner's decision): copied under
<Downloads>\\TagPup deleted from shares\\<server>\\<share>\\<path>, the copy checked (size and hash), the COPY sent to this PC's
Recycle Bin, and only then the original deleted. Any step that fails leaves the original. (tagpup.files.recycle_bin.delete_file,
the one owner, used by Organize's delete and the bulk Delete.)

The share is a folder of this test's home that the test says has no Recycle Bin (no real share is needed); the Downloads folder
is the test home's (own_home sets TAGPUP_DOWNLOADS); the Recycle Bin is the test's: nothing reaches the owner's.
"""
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.files import recycle_bin  # noqa: E402
from tagpup.jobs import bulk_edits  # noqa: E402


class Share(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.share = os.path.join(self.home.root, "nas", "Pictures")
        self.photo = os.path.join(self.share, "2019 Regatta", "IMG_0001.jpg")
        make_jpeg(self.photo)
        with open(self.photo, "rb") as handle:
            self.bytes = handle.read()
        self.binned = []           # (path, whether the original was still there, the copy's bytes) as each went to the Bin
        bin_patch = mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle)
        bin_patch.start()
        self.addCleanup(bin_patch.stop)
        reason_patch = mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason)
        reason_patch.start()
        self.addCleanup(reason_patch.stop)

    def reason(self, path):
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.share)) else None

    def recycle(self, path):
        with open(path, "rb") as handle:
            held = handle.read()
        self.binned.append((path, os.path.exists(self.photo), held))
        os.remove(path)
        return True

    def mirror(self):
        return recycle_bin.mirror_of(self.photo)


class Through(Share):
    def test_the_downloads_folder_is_the_test_homes_and_the_mirror_follows_the_original_path(self):
        self.assertEqual(os.path.join(self.home.root, "Downloads"), recycle_bin.downloads_folder())
        self.assertEqual(os.path.join(self.home.root, "Downloads", "TagPup deleted from shares", "server", "share", "a", "b.jpg"),
                         recycle_bin.mirror_of("\\\\server\\share\\a\\b.jpg"))
        self.assertEqual(os.path.join(self.home.root, "Downloads", "TagPup deleted from shares", "E", "Card", "b.jpg"),
                         recycle_bin.mirror_of("E:\\Card\\b.jpg"))
        self.assertEqual(recycle_bin.mirror_of("\\\\server\\share\\a\\b.jpg"), recycle_bin.mirror_of("\\\\?\\UNC\\server\\share\\a\\b.jpg"))

    def test_the_known_folder_is_asked_when_no_test_says_otherwise(self):
        with mock.patch.dict(os.environ, {"TAGPUP_DOWNLOADS": ""}), \
                mock.patch.object(recycle_bin, "_known_downloads", return_value="D:\\Moved\\Downloads"):
            self.assertEqual("D:\\Moved\\Downloads", recycle_bin.downloads_folder())
        with mock.patch.dict(os.environ, {"TAGPUP_DOWNLOADS": "", "USERPROFILE": "C:\\Users\\wren"}), \
                mock.patch.object(recycle_bin, "_known_downloads", side_effect=OSError("no")):
            self.assertEqual(os.path.join("C:\\Users\\wren", "Downloads"), recycle_bin.downloads_folder())

    def test_the_copy_is_checked_before_the_original_goes_and_only_the_copy_goes_to_the_bin(self):
        went = recycle_bin.delete_file(self.photo)
        self.assertEqual((True, recycle_bin.NETWORK), (went["through_this_pc"], went["reason"]))
        [(path, original_there, held)] = self.binned
        self.assertEqual(paths.key(self.mirror()), paths.key(path), "the copy went to the Bin, under the mirror of its path")
        self.assertTrue(original_there, "the original was still there when the copy went to the Bin")
        self.assertEqual(self.bytes, held, "the copy holds what the original held")
        self.assertFalse(os.path.exists(self.photo), "and then the original went")

    def test_a_photo_of_a_place_with_a_bin_goes_to_its_bin_as_ever(self):
        local = os.path.join(self.home.root, "Local", "IMG_0002.jpg")
        make_jpeg(local)
        went = recycle_bin.delete_file(local)
        self.assertFalse(went["through_this_pc"])
        self.assertEqual([paths.key(local)], [paths.key(each[0]) for each in self.binned])

    def test_a_copy_that_fails_or_differs_keeps_the_original_and_leaves_no_copy(self):
        for how in ("fails", "differs"):
            with self.subTest(how=how):
                if how == "fails":
                    patcher = mock.patch.object(shutil, "copy2", side_effect=OSError("the share went away"))
                else:
                    patcher = mock.patch.object(recycle_bin, "_hash", side_effect=["a" * 64, "b" * 64])
                with patcher, self.assertRaises(OSError) as raised:
                    recycle_bin.delete_file(self.photo)
                self.assertIn("left where it is", str(raised.exception))
                self.assertTrue(os.path.exists(self.photo))
                self.assertEqual([], self.binned)
                self.assertFalse(os.path.exists(self.mirror()))
                self.assertFalse(os.path.exists(self.mirror() + ".partial"))

    def test_this_pcs_bin_refusing_the_copy_keeps_the_original(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", return_value=False), self.assertRaises(OSError):
            recycle_bin.delete_file(self.photo)
        self.assertTrue(os.path.exists(self.photo))
        self.assertFalse(os.path.exists(self.mirror()), "the copy is taken away again")

    def test_a_failure_after_the_copy_is_in_the_bin_keeps_the_original_and_says_it_is_there_twice(self):
        real_remove = os.remove

        def remove(path):
            if paths.key(path) == paths.key(self.photo):
                raise PermissionError("the share is read-only")
            return real_remove(path)
        with mock.patch.object(os, "remove", side_effect=remove), self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(self.photo)
        self.assertTrue(os.path.exists(self.photo), "the original stays")
        self.assertEqual(1, len(self.binned), "and a copy is in this PC's Recycle Bin")
        self.assertIn("twice", str(raised.exception))

    def test_no_room_on_this_pcs_drive_refuses_and_copies_nothing(self):
        usage = shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - 1000, 1000)
        with mock.patch.object(shutil, "disk_usage", return_value=usage), self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(self.photo)
        self.assertIn("not room", str(raised.exception))
        self.assertTrue(os.path.exists(self.photo))
        self.assertEqual([], self.binned)
        with mock.patch.object(shutil, "disk_usage", return_value=shutil._ntuple_diskusage(10 ** 12, 0, 2 ** 31)):
            self.assertIsNone(recycle_bin.room_for(2 ** 29), "half a GB, with 1 GB spare, fits in 2 GB")
            self.assertIsNotNone(recycle_bin.room_for(2 ** 30 + 1), "a byte over does not")

    def test_a_name_already_in_the_mirror_is_kept_and_the_copy_takes_another(self):
        os.makedirs(os.path.dirname(self.mirror()))
        with open(self.mirror(), "wb") as handle:
            handle.write(b"an earlier copy")
        recycle_bin.delete_file(self.photo)
        self.assertEqual(b"an earlier copy", open(self.mirror(), "rb").read())
        self.assertTrue(self.binned[0][0].endswith("IMG_0001 (2).jpg"), self.binned[0][0])


class ThroughTheOwners(unittest.TestCase):
    """Organize's delete and the bulk Delete both go through it: one owner."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.library = self.vl.library
        self.addCleanup(bulk_edits.forget, self.library)
        self.share = os.path.join(self.vl.pictures, "Share")
        self.binned = []
        bin_patch = mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle)
        bin_patch.start()
        self.addCleanup(bin_patch.stop)
        reason_patch = mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason)
        reason_patch.start()
        self.addCleanup(reason_patch.stop)
        self.on_share = [self.vl.photo("Share", "s%d.jpg" % n, taken="2024:01:0%d 10:00:00" % (n + 1), real=True, size=(16, 16))
                         for n in range(3)]
        self.local = [self.vl.photo("Local", "l%d.jpg" % n, taken="2024:02:0%d 10:00:00" % (n + 1), real=True, size=(16, 16))
                      for n in range(2)]

    def reason(self, path):
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.share)) else None

    def recycle(self, path):
        self.binned.append(path)
        os.remove(path)
        return True

    def held(self, ids):
        found = {row[0] for row in self.vl.rows("SELECT id FROM photos")}
        return [photo_id for photo_id in ids if photo_id in found]

    def test_organizes_delete_of_a_photo_on_a_share_goes_through_this_pc_and_says_where_it_restores_to(self):
        path = self.vl.path_of(self.on_share[0])
        reply = self.client.post("/library/api/photo/delete", json={"path": path})
        self.assertEqual(200, reply.status_code, reply.get_json())
        body = reply.get_json()
        self.assertTrue(body["through_this_pc"])
        self.assertNotIn("permanently", body["message"])
        self.assertIn("TagPup deleted from shares", body["message"])
        self.assertIn("not back to", body["message"])
        self.assertFalse(os.path.exists(path))
        self.assertEqual([paths.key(recycle_bin.mirror_of(path))], [paths.key(each) for each in self.binned])
        self.assertEqual([], self.held(self.on_share[:1]))

    def test_the_bulk_delete_asks_where_and_how_much_and_goes_through_this_pc(self):
        asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"source": {"kind": "all"}}}).get_json()
        self.assertEqual((5, 3), (asked["total"], asked["through_this_pc"]))
        self.assertEqual([{"reason": "on a network share", "photos": 3}], asked["reasons"])
        self.assertEqual(sum(os.path.getsize(self.vl.path_of(each)) for each in self.on_share), asked["copy_bytes"])
        self.assertEqual(recycle_bin.mirror_root(), asked["restores_to"])
        self.assertIsNone(asked["no_room"])
        reply = self.client.post("/library/api/library/bulk/start", json={"op": "delete", "selection": {"source": {"kind": "all"}},
                                                                         "params": {"token": asked["token"]}}).get_json()
        bulk_edits._held(self.library)[reply["job"]].thread.join(60)
        done = self.client.get("/library/api/library/bulk/status", query_string={"job": reply["job"]}).get_json()
        self.assertEqual((5, 0), (done["changed"], done["error_count"]))
        self.assertEqual(3, len([each for each in self.binned if "TagPup deleted from shares" in each]))

    def test_no_room_for_the_copies_is_said_before_anything_is_asked(self):
        usage = shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - 1000, 1000)
        with mock.patch.object(shutil, "disk_usage", return_value=usage):
            asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share}}).get_json()
        self.assertIn("not room", asked["no_room"])
        self.assertEqual([], self.binned)


if __name__ == "__main__":
    unittest.main()
