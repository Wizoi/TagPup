"""Delete a photo (F8): the Recycle Bin, a place with none, a long name, Just look. The real Recycle Bin is never used:
SHFileOperation is stood in for, and the test home's Downloads holds the copies.

Lifted: test_service_delete (the service: what leaves the library and in what order), the delete tests of
test_just_look_delete_and_rename_rules (#520-#525) and of test_just_look_bin_and_long_names (#528-#531), which keep the
rest (held-or-ignored, the file-only check, Smart Rename failing part-way). Mutation proofs: the index rows left; the rows
forgotten though the Bin failed; the long name not judged; a link to a share not followed; SUBST, a removable drive and
"unsure" not read as no Bin; the membership answer never permanent; the reply not saying through this PC, or why. The
trailing-separator refusal of recycle_bin.problem is caught by no test, as before (the route refuses it first; a test of
the function would send a file to the real Bin when it broke).

A photo deleted from a place with no Recycle Bin goes through this PC (#694, the owner's decision): copied under
<Downloads>\\TagPup deleted from shares\\<server>\\<share>\\<path>, the copy checked (size and hash), the COPY sent to this PC's
Recycle Bin, and only then the original deleted. Any step that fails leaves the original. (tagpup.files.recycle_bin.delete_file,
the one owner, used by Organize's delete and the bulk Delete.) And it is refused, nothing copied, where the copy could not be
kept: this PC's Recycle Bin too small or set to delete at once (#703), a path too long for the Bin (#704), a Downloads folder
synced to the cloud (#706).

The share is a folder of this test's home that the test says has no Recycle Bin (no real share is needed); the Downloads folder
is the test home's (own_home sets TAGPUP_DOWNLOADS), and so is the Recycle Bin's size and settings (TAGPUP_RECYCLE_BIN, which
own_home sets to a large empty Bin); the Recycle Bin is the test's: nothing reaches, or reads, the owner's.
"""
import ctypes
import os
import shutil
import string
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import web_client  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

import damaged_photos  # noqa: E402
import test_just_look_edits as jl  # noqa: E402

from tagpup.core import paths, processes  # noqa: E402
from tagpup.files import recycle_bin  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402


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
        done = self.client.post("/library/api/library/bulk-edit", json={"op": "delete", "selection": {"source": {"kind": "all"}},
                                                                       "params": {"token": asked["token"]}}).get_json()
        self.assertEqual((5, 0), (done["changed"], done["error_count"]))
        self.assertEqual(3, len([each for each in self.binned if "TagPup deleted from shares" in each]))

    def test_no_room_for_the_copies_is_said_before_anything_is_asked(self):
        usage = shutil._ntuple_diskusage(10 ** 12, 10 ** 12 - 1000, 1000)
        with mock.patch.object(shutil, "disk_usage", return_value=usage):
            asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share}}).get_json()
        self.assertIn("not room", asked["no_room"])
        self.assertEqual([], self.binned)



MB = 1 << 20


class TheBinMustKeepIt(Share):
    """#703: Windows makes room in a full Recycle Bin by purging its OLDEST items for good, so a copy is made only when the Bin of
    the Downloads volume will keep it, and every copy before it."""

    def bin(self, capacity_mb, used_mb, nuke=0):
        recycle_bin.forget_bin()
        patcher = mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "%d,%d,%d" % (capacity_mb, used_mb, nuke)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(recycle_bin.forget_bin)

    def test_the_test_home_reads_no_real_bin(self):
        self.assertTrue(os.environ.get("TAGPUP_RECYCLE_BIN"))
        with mock.patch.object(recycle_bin, "_query_bin", side_effect=AssertionError("the owner's Bin was asked")):
            recycle_bin.delete_file(self.photo)

    def test_a_bin_that_would_overflow_refuses_naming_the_sizes_and_copies_nothing(self):
        self.bin(capacity_mb=1000, used_mb=990)
        with self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(self.photo)
        said = str(raised.exception)
        self.assertIn("Recycle Bin", said)
        self.assertIn("1,000 MB", said)
        self.assertIn("990 MB", said)
        self.assertIn("oldest", said)
        self.assertTrue(os.path.exists(self.photo))
        self.assertEqual([], self.binned)
        self.assertFalse(os.path.exists(os.path.dirname(self.mirror())), "nothing was copied")

    def test_the_margin_is_kept_and_a_bin_with_room_takes_it(self):
        self.bin(capacity_mb=1000, used_mb=900)
        self.assertIsNotNone(recycle_bin.room_for_copies(60 * MB, MB), "1,000 MB less a 5 % margin is 950: 960 does not fit")
        self.assertIsNone(recycle_bin.room_for_copies(40 * MB, MB))
        recycle_bin.delete_file(self.photo)
        self.assertEqual(1, len(self.binned))

    def test_what_this_job_put_in_the_bin_counts_without_asking_windows_again(self):
        self.bin(capacity_mb=1000, used_mb=0)
        with mock.patch.object(recycle_bin, "_bin_size", wraps=recycle_bin._bin_size) as asked:
            recycle_bin.room_for_copies(MB, MB, fresh=True)
            recycle_bin.note_binned(recycle_bin.mirror_root(), 900 * MB)
            self.assertIsNotNone(recycle_bin.room_for_copies(60 * MB, MB), "the 900 MB it sent are counted")
            self.assertEqual(1, asked.call_count, "the Bin is asked once, not once a photo")

    def test_a_bin_set_to_delete_at_once_is_no_bin(self):
        self.bin(capacity_mb=1000, used_mb=0, nuke=1)
        with self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(self.photo)
        self.assertIn("deletes files at once", str(raised.exception))
        self.assertTrue(os.path.exists(self.photo))
        self.assertEqual([], self.binned)

    def test_a_photo_larger_than_the_whole_bin_is_refused(self):
        self.bin(capacity_mb=1, used_mb=0)
        big = os.path.join(self.share, "2019 Regatta", "big.jpg")
        make_jpeg(big)
        with open(big, "ab") as handle:
            handle.write(b"\0" * (2 * MB))
        with self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(big)
        self.assertIn("larger than", str(raised.exception))
        self.assertTrue(os.path.exists(big))

    def test_the_bins_settings_are_read_for_the_downloads_volume(self):
        recycle_bin.forget_bin()
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": ""}), \
                mock.patch.object(recycle_bin, "_volume_guid", return_value="{11111111-2222-3333-4444-555555555555}"), \
                mock.patch.object(recycle_bin, "_registry_value", side_effect=lambda key, name: {"MaxCapacity": 2000, "NukeOnDelete": 0}[name]) as read, \
                mock.patch.object(recycle_bin, "_query_bin", return_value=500 * MB):
            self.assertEqual((2000 * MB, False), recycle_bin._bin_settings("C:\\"))
            self.assertIn("{11111111-2222-3333-4444-555555555555}", read.call_args_list[0][0][0])
            self.assertEqual(500 * MB, recycle_bin._bin_size("C:\\"))
        recycle_bin.forget_bin()


class TooLong(Share):
    """#704: the Recycle Bin (SHFileOperation) takes no path of 260 characters or more."""

    def test_a_mirror_path_of_260_or_more_is_refused_up_front_naming_the_length_and_the_folder(self):
        deep = os.path.join(self.share, *(["a" * 40] * 6), "IMG_0002.jpg")
        make_jpeg(deep)
        self.assertGreaterEqual(len(recycle_bin.mirror_of(deep)) + len(".partial"), 260)
        with self.assertRaises(OSError) as raised:
            recycle_bin.delete_file(deep)
        said = str(raised.exception)
        self.assertIn("characters", said)
        self.assertIn(str(len(recycle_bin.mirror_of(deep))), said)
        self.assertIn(recycle_bin.mirror_root(), said)
        self.assertNotIn("Recycle Bin would not", said, "not blamed on the Bin")
        self.assertTrue(os.path.exists(deep))
        self.assertEqual([], self.binned)


class Leftovers(Share):
    """#706."""

    def test_a_partial_copy_a_crash_left_is_taken_away_and_the_name_is_not_bumped(self):
        os.makedirs(os.path.dirname(self.mirror()))
        for left in (self.mirror() + ".partial", self.mirror().replace(".jpg", " (2).jpg") + ".partial"):
            with open(left, "wb") as handle:
                handle.write(b"half a photo")
        recycle_bin.delete_file(self.photo)
        self.assertEqual(paths.key(self.mirror()), paths.key(self.binned[0][0]), "its own name, not (2) or (3)")
        self.assertEqual([], [name for name in os.listdir(os.path.dirname(self.mirror())) if name.endswith(".partial")])

    def test_a_downloads_folder_synced_to_the_cloud_is_refused(self):
        with mock.patch.dict(os.environ, {"OneDrive": self.home.root, "OneDriveConsumer": "", "OneDriveCommercial": ""}):
            with self.assertRaises(OSError) as raised:
                recycle_bin.delete_file(self.photo)
            self.assertIn("OneDrive", str(raised.exception))
        self.assertTrue(os.path.exists(self.photo))
        self.assertEqual([], self.binned)
        with mock.patch.dict(os.environ, {"OneDrive": os.path.join(self.home.root, "Elsewhere")}):
            recycle_bin.delete_file(self.photo)
        self.assertEqual(1, len(self.binned))


class AskedFirst(ThroughTheOwners):
    def test_delete_check_asks_the_bin_counts_the_paths_too_long_and_refuses_what_would_overflow(self):
        recycle_bin.forget_bin()
        self.addCleanup(recycle_bin.forget_bin)
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "1,1,0"}):
            asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share}}).get_json()
        self.assertIn("Recycle Bin", asked["no_room"])
        self.assertEqual(0, asked["too_long"])
        deep = self.vl.photo(os.path.join("Share", *(["b" * 40] * 6)), "deep.jpg", real=True, size=(16, 16))
        asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share + [deep]}}).get_json()
        self.assertEqual((4, 1, None), (asked["through_this_pc"], asked["too_long"], asked["no_room"]))

    def test_the_copies_are_checked_again_before_copying_the_bin_fills_during_a_long_edit(self):
        asked = self.client.post("/library/api/library/selection/delete-check", json={"selection": {"ids": self.on_share}}).get_json()
        recycle_bin.forget_bin()
        self.addCleanup(recycle_bin.forget_bin)
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "1,1,0"}):
            done = self.client.post("/library/api/library/bulk-edit", json={"op": "delete", "selection": {"ids": self.on_share},
                                                                           "params": {"token": asked["token"]}}).get_json()
        self.assertEqual((0, 3), (done["changed"], done["error_count"]))
        self.assertIn("Recycle Bin", done["errors"][0]["why"])
        self.assertEqual(self.on_share, self.held(self.on_share))
        self.assertEqual([], self.binned)


class DeletingAPhoto(unittest.TestCase):
    """The service (tagpup.services.photos.delete): what it removes from the library, in what order, what its Result says. The
    Recycle Bin is stood in for. Lifted from test_service_delete; the four tests are two, every assertion kept."""

    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo, self.other = self.lib.photo("finish.jpg"), self.lib.photo("finish_2.jpg")
        for path in (self.photo, self.other):
            self.lib.add_row(path)
            self.lib.add_face(path, [0, 0, 10, 8], name="Rowan Thackeray")
            self.lib.execute("INSERT INTO embeddings (photo_id, model, mtime, size, vector)"
                             " SELECT id, 'm|p|cropped|1.0|1', 1.0, 1, x'00' FROM photos WHERE path = ?", (path,))

    def delete(self, moved=True):
        def recycle(path):
            if moved is True:
                os.remove(path)
                return True
            if isinstance(moved, Exception):
                raise moved
            return moved
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            return photo_actions.delete(self.lib.library, self.photo)

    def counts(self, path):
        # Faces by their photo's path, and the faces that point at no photo row at all: a face whose photo row went while
        # it stayed would otherwise count as gone.
        return tuple(self.lib.rows(sql, (path,))[0][0] for sql in (
            "SELECT COUNT(*) FROM photos WHERE path = ?",
            "SELECT COUNT(*) FROM faces f LEFT JOIN photos p ON p.id = f.photo_id WHERE p.path = ? OR p.id IS NULL",
            "SELECT COUNT(*) FROM embeddings e LEFT JOIN photos p ON p.id = e.photo_id WHERE p.path = ? OR p.id IS NULL"))

    def test_a_photo_the_bin_refused_or_could_not_move_stays_in_the_library_and_one_it_took_takes_its_index_rows_only(self):
        refused = self.delete(moved=False)
        self.assertEqual((False, 0), (refused.ok, refused.changed))
        self.assertIn("Recycle Bin", refused.message())
        failed = self.delete(moved=OSError("the file is open elsewhere"))
        self.assertEqual((0, "the file is open elsewhere"), (failed.changed, failed.message()))
        self.assertEqual((1, 1, 1), self.counts(self.photo), "kept whole")
        result = self.delete()
        self.assertEqual((1, 1, []), (result.attempted, result.changed, result.errors))
        self.assertEqual({"faces": 1, "photos": 1}, result.details["removed"])
        self.assertEqual((0, 0, 0), self.counts(self.photo))
        self.assertFalse(os.path.exists(self.photo))
        self.assertEqual((1, 1, 1), self.counts(self.other), "nothing else goes")


def refuses_to_send(*_args):
    raise AssertionError("something was sent to the Recycle Bin")


def unc_of(path):
    r"""\\localhost\C$\... for a local file, or None when the administrative share is not there."""
    unc = "\\\\localhost\\" + path[0] + "$" + path[2:]
    return unc if os.path.exists(unc) else None


class JustLookDelete(jl.Base):
    """Delete in a folder the library does not hold (Just look, #520-#525, #528-#531): only an existing regular photo FILE goes
    to the Bin, the place is judged truly, the reply says where it restores to. Lifted from test_just_look_delete_and_rename_rules
    and test_just_look_bin_and_long_names; the Bin is stood in for, and the test home's Downloads holds the copy."""

    PHRASES = {recycle_bin.NETWORK: "network share", recycle_bin.REMOVABLE: "removable drive",
               recycle_bin.SUBST: "substituted", recycle_bin.NO_BIN: "without a Recycle Bin"}

    def short(self, path):
        buffer = ctypes.create_unicode_buffer(300)
        ctypes.windll.kernel32.GetShortPathNameW(path, buffer, 300)
        return buffer.value or path

    def test_only_an_existing_regular_photo_file_is_sent_to_the_bin_in_a_folder_held_or_not(self):
        texts = [os.path.join(folder, "notes.txt") for folder in (self.lighthouse, self.regatta)]
        for text in texts:
            with open(text, "w") as handle:
                handle.write("not a photo")
        for path in (self.lighthouse, texts[0], self.short(texts[0]), self.loose[0] + os.sep,
                     os.path.join(self.lighthouse, "gone.jpg"),
                     self.regatta, texts[1], self.short(texts[1]), self.held[0] + os.sep):
            with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=refuses_to_send):
                reply = self.post("/photo/delete", {"path": path})
            self.assertIn(reply.status_code, (400, 409), (path, reply.data))
            self.assertFalse(reply.get_json()["success"])
        self.assertTrue(all(os.path.exists(path) for path in (self.lighthouse, self.regatta, *texts)))
        self.assertEqual(3, len([n for n in os.listdir(self.lighthouse) if n.endswith(".jpg")]))
        self.assertEqual(2, self.rows("SELECT COUNT(*) FROM photos")[0][0])
        self.assert_library_unchanged()
        for path in (self.lighthouse, texts[0], os.path.join(self.lighthouse, "missing.jpg")):   # the function itself
            with self.assertRaises(ValueError, msg=path):
                recycle_bin.send_to_recycle_bin(path)
        self.assertTrue(os.path.exists(texts[0]))

    def test_a_photo_file_goes_to_the_bin_and_the_reply_says_so(self):
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
            body = self.post("/photo/delete", {"path": self.loose[0]}).get_json()
            self.assertEqual(200, self.post("/photo/delete", {"path": self.held[0]}).status_code)
        self.assertFalse(body["through_this_pc"])
        self.assertIn("Recycle Bin", body["message"])

    def test_the_folder_says_before_the_delete_whether_it_would_be_permanent_and_why(self):
        self.assertFalse(library_actions.membership(self.library, self.lighthouse)["permanent_delete"])
        unc = unc_of(self.lighthouse)
        if unc is not None:
            self.assertTrue(library_actions.membership(self.library, unc)["permanent_delete"])
        for reason in self.PHRASES:
            with mock.patch.object(recycle_bin, "no_bin_reason", return_value=reason):
                found = library_actions.membership(self.library, self.lighthouse)
            self.assertEqual((True, reason), (found["permanent_delete"], found["permanent_reason"]))
        with mock.patch.object(recycle_bin, "no_bin_reason", return_value=None):
            found = library_actions.membership(self.library, self.lighthouse)
        self.assertEqual((False, None), (found["permanent_delete"], found["permanent_reason"]))

    def test_a_unc_path_to_local_storage_goes_through_this_pc_and_the_reply_says_where_it_restores_to(self):
        unc = unc_of(self.loose[0])
        if unc is None:
            self.skipTest("the administrative share \\\\localhost\\%s$ is not available" % self.loose[0][0])
        binned = []
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: binned.append(p) or os.remove(p) or True):
            reply = self.post("/photo/delete", {"path": unc})
        self.assertEqual(200, reply.status_code, reply.data)
        body = reply.get_json()
        self.assertTrue(body["through_this_pc"], body)
        self.assertNotIn("permanently", body["message"])
        self.assertIn("TagPup deleted from shares", body["message"])
        self.assertEqual([recycle_bin.mirror_of(unc)], binned, "the copy, under the test home's Downloads, went to the Bin")
        self.assertFalse(os.path.exists(self.loose[0]))
        self.assert_library_unchanged()

    def test_the_reply_names_the_true_reason_for_each(self):
        downloads = recycle_bin.downloads_folder()
        for n, (reason, phrase) in enumerate(self.PHRASES.items()):
            photo = self.loose[n % len(self.loose)]
            if not os.path.exists(photo):
                damaged_photos.whole_jpeg(photo, seed=n)
            # The original's place has no Bin; the Downloads folder, where the copy goes, has one.
            judged = lambda path, reason=reason: None if paths.key(path).startswith(paths.key(downloads)) else reason  # noqa: E731
            with mock.patch.object(recycle_bin, "no_bin_reason", side_effect=judged), \
                    mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
                body = self.post("/photo/delete", {"path": photo}).get_json()
            self.assertTrue(body["through_this_pc"], body)
            self.assertIn(phrase, body["message"], reason)
            if reason != recycle_bin.NETWORK:
                self.assertNotIn("network share", body["message"], reason)
            self.assertEqual(reason, body["no_bin_reason"])

    def test_the_8_3_name_of_a_longer_extension_is_refused(self):
        real = os.path.join(self.lighthouse, "holiday.jpgold")
        with open(real, "wb") as handle:
            handle.write(b"x")
        short = self.short(real)
        if short.lower() == real.lower():
            self.skipTest("this volume makes no 8.3 names")
        self.assertTrue(short.lower().endswith(".jpg"), short)
        self.assertIsNotNone(recycle_bin.problem(short))
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=AssertionError("sent")):
            reply = self.post("/photo/delete", {"path": short})
        self.assertIn(reply.status_code, (400, 409), reply.data)
        self.assertTrue(os.path.exists(real))

    def test_a_file_on_a_subst_drive_is_permanent_though_its_drive_is_a_fixed_disk(self):
        letter = next((c for c in reversed(string.ascii_uppercase) if not os.path.exists(c + ":\\")), None)
        started = processes.run(["subst", letter + ":", self.lighthouse], capture_output=True, text=True)
        if started.returncode != 0:
            self.skipTest("subst is not available: %s" % (started.stderr or started.stdout).strip())
        try:
            on_subst = letter + ":\\" + os.path.basename(self.loose[0])
            self.assertTrue(os.path.isfile(on_subst))
            self.assertEqual(recycle_bin.SUBST, recycle_bin.no_bin_reason(on_subst))
            self.assertFalse(recycle_bin.goes_to_bin(on_subst))
            self.assertTrue(recycle_bin.goes_to_bin(self.loose[0]), "the same file by its own path")
            with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=lambda p: os.remove(p) or True):
                body = self.post("/photo/delete", {"path": on_subst}).get_json()
            self.assertTrue(body["through_this_pc"], body)
            self.assertIn("substituted", body["message"])
        finally:
            processes.run(["subst", letter + ":", "/D"], capture_output=True, text=True)


class WhichPlacesGoToTheBin(unittest.TestCase):
    """goes_to_bin is right for a SUBST drive, a volume mounted in a folder, an extended-prefix spelling, a link to a share and every drive
    type but a fixed disk (#529); when unsure it says permanent. Lifted from test_just_look_bin_and_long_names, eight tests as a table."""

    def judge(self, path, real=None, mount="C:\\", kind=3, device="\\Device\\HarddiskVolume3"):
        with mock.patch.object(recycle_bin, "_real", return_value=real or path), \
                mock.patch.object(recycle_bin, "_mount_point", return_value=mount), \
                mock.patch.object(recycle_bin, "_drive_type", return_value=kind), \
                mock.patch.object(recycle_bin, "_dos_device", return_value=device):
            return recycle_bin.no_bin_reason(path)

    def test_only_a_fixed_local_disk_with_a_real_volume_goes_to_the_bin(self):
        no_bin = {4: recycle_bin.NETWORK, 2: recycle_bin.REMOVABLE, 5: recycle_bin.NO_BIN, 6: recycle_bin.NO_BIN,
                  0: recycle_bin.NO_BIN, 1: recycle_bin.NO_BIN}
        cases = [(("C:\\Photos\\a.jpg",), None)]
        cases += [(("Z:\\a.jpg",), reason, {"mount": "Z:\\", "kind": kind}) for kind, reason in no_bin.items()]
        cases += [(("Z:\\a.jpg",), recycle_bin.SUBST, {"mount": "Z:\\", "device": "\\??\\C:\\Temp\\x"}),
                  (("C:\\mnt\\usb\\a.jpg",), recycle_bin.REMOVABLE, {"mount": "C:\\mnt\\usb\\", "kind": 2}),
                  (("C:\\mnt\\disk\\a.jpg",), None, {"mount": "C:\\mnt\\disk\\", "kind": 3}),
                  (("C:\\links\\a.jpg",), recycle_bin.NETWORK, {"real": "\\\\server\\share\\a.jpg"}),
                  (("\\\\?\\C:\\Photos\\a.jpg",), None, {"real": "\\\\?\\C:\\Photos\\a.jpg"}),
                  (("C:\\a.jpg",), recycle_bin.NO_BIN, {"device": "\\Device\\Strange"}),
                  (("C:\\a.jpg",), recycle_bin.NO_BIN, {"device": None})]
        for case in cases:
            (path,), want, *more = case
            self.assertEqual(want, self.judge(path, **(more[0] if more else {})), case)
        self.assertEqual(recycle_bin.NETWORK, recycle_bin.no_bin_reason("\\\\?\\UNC\\server\\share\\a.jpg"))
        with mock.patch.object(recycle_bin, "_mount_point", side_effect=OSError("no")):
            self.assertEqual(recycle_bin.NO_BIN, recycle_bin.no_bin_reason("C:\\Photos\\a.jpg"))
        self.assertFalse(recycle_bin.goes_to_bin("\\\\server\\share\\a.jpg"))
        self.assertFalse(recycle_bin.goes_to_bin("\\\\?\\UNC\\server\\share\\a.jpg"))
        with mock.patch("ctypes.windll.kernel32.GetDriveTypeW", return_value=4, create=True):
            self.assertFalse(recycle_bin.goes_to_bin("Z:\\Photos\\a.jpg"), "a mapped network drive")


if __name__ == "__main__":
    unittest.main()
