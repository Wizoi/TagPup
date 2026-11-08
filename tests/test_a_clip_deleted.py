"""A clip is deleted as a photo is (F12; DECISIONS 2026-11-08, A5, cut B): a clip of up to recycle_bin.MOST_CLIP_BYTES
goes to the Recycle Bin -- from a share, through this PC: copied under the Downloads mirror, the copy checked and recycled,
then the original deleted -- and leaves the library with everything it had: its row, its vector and frames, its poster. A
larger clip is refused with its size, its bytes counted in no copy, and the photos beside it are deleted as always.
On a local drive a file goes to its own drive's Recycle Bin. One larger than that Bin keeps, or on a drive whose Bin keeps
nothing, would be deleted for good (Windows, asked without a question, deletes it and says it recycled it): the question
names it, and it is deleted for good only when the owner confirmed that; a start that did not is refused, nothing touched
(DECISIONS 2026-11-08).

What the owner lives through: a day in the library view holds photos and clips together, on a share (no Recycle Bin of its
own); a Delete of the day asks first how much will be copied through this PC (the delete-check), then deletes them. A clip's
copy can take minutes, so a bulk delete lets other writers in before and after each clip, as it does after each chunk of
photos: a clip is a chunk of its own.

A real one-second clip made by the machine's ffmpeg (as tests/test_a_clip_is_not_turned_or_dated.py makes it); rows as the
indexer records a read (tests/view_library.py, which gives a .mov its kind 'video'). The share is a folder of this test's
home that the test says has no Recycle Bin, the Recycle Bin is stood in for (the copy is taken away), and the mirror is a
folder of the home: nothing reaches the owner's Bin or Downloads. Flask's test client in a home of its own. Skipped, saying
so, without ffmpeg or ExifTool. Fictional names only.
"""
import hashlib
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

from tagpup import config  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import file_changes, thumbnails  # noqa: E402
from tagpup.store import db, embeddings  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
TOOLS = config.locate_ffmpeg()
MODEL = embeddings.model_key("ViT-T", "tiny", True, 1.4, 512)
VECTOR = b"\x00\x00\x80\x3f" * 4
FRAMES = [bytes([n]) * 16 for n in range(1, 5)]


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


@unittest.skipIf(TOOLS.problem, "ffmpeg is not installed on this machine (winget install Gyan.FFmpeg): %s" % TOOLS.problem)
@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class APhotoAndAClip(unittest.TestCase):
    """A photo and a clip in one folder of a library: the setting of each scenario below."""

    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.folder = os.path.join(self.vl.pictures, "Harbour")
        self.photo_path = os.path.join(self.folder, "pier.jpg")
        make_jpeg(self.photo_path, size=(32, 32))
        self.photo = self.vl.photo("Harbour", "pier.jpg", at=self.photo_path)
        self.clip_path = os.path.join(self.folder, "jetty.mov")
        made = processes.run([TOOLS.ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                              "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2021-07-04T18:30:00Z",
                              self.clip_path], capture_output=True)
        self.assertEqual(0, made.returncode, made.stderr)
        self.clip = self.vl.photo("Harbour", "jetty.mov", at=self.clip_path)
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip),
                         "the clip's row is of kind video, as the indexer records it")
        self.clip_bytes = digest(self.clip_path)
        self.events = []           # ("bin", name, the copy's digest) as each copy went to the Bin; ("turn",) at each let-go
        for patcher in (mock.patch("tagpup.web.state.exiftool", return_value=EXIFTOOL),
                        mock.patch.object(recycle_bin, "no_bin_reason", side_effect=self.reason),
                        mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=self.recycle),
                        mock.patch.object(recycle_bin, "mirror_root", return_value=os.path.join(self.home.root, "Mirror"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def reason(self, path):
        # The photos' folder is a share, as far as the test says; this PC's mirror folder is not.
        return recycle_bin.NETWORK if paths.key(path).startswith(paths.key(self.folder)) else None

    def recycle(self, path):
        self.events.append(("bin", os.path.basename(path), digest(path)))
        os.remove(path)
        return True

    def binned(self):
        return [event[1] for event in self.events if event[0] == "bin"]

    def post(self, route, body):
        return self.client.post("/library/api/" + route, json=body)

    def asked(self, *ids):
        reply = self.post("library/selection/delete-check", {"selection": {"ids": list(ids)}})
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def delete_all(self, *ids):
        asked = self.asked(*ids)
        reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": list(ids)},
                                                "params": {"token": asked["token"]}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return asked, reply.get_json()

    def assert_gone(self, path, photo_id):
        self.assertFalse(os.path.exists(path), "%s is deleted" % os.path.basename(path))
        self.assertEqual([], self.vl.rows("SELECT id FROM photos WHERE id = ?", photo_id), "and its row with it")

    def assert_clip_untouched(self):
        self.assertTrue(os.path.exists(self.clip_path), "the clip is where it was")
        self.assertEqual(self.clip_bytes, digest(self.clip_path), "the clip's file is byte for byte as it was")
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip), "its row stays")
        self.assertNotIn("jetty.mov", self.binned(), "no copy of it went to the Bin")

    def posters(self, photo_id):
        root = self.vl.library.thumbs
        return sum(1 for _folder, _dirs, names in os.walk(root) for name in names if name.startswith("%d_" % photo_id))


class AClipOnAShare(APhotoAndAClip):
    def test_a_day_of_a_photo_and_a_clip_is_deleted_through_this_pc_the_clip_copied_whole(self):
        sizes = os.path.getsize(self.photo_path) + os.path.getsize(self.clip_path)
        asked, done = self.delete_all(self.photo, self.clip)
        self.assertEqual(sizes, asked["copy_bytes"], "the question counts the copies of the photo and the clip")
        self.assertEqual(2, asked["through_this_pc"])
        self.assertEqual((2, 0), (done["changed"], done["error_count"]), done["errors"])
        self.assert_gone(self.photo_path, self.photo)
        self.assert_gone(self.clip_path, self.clip)
        [clip_copy] = [event for event in self.events if event[0] == "bin" and event[1] == "jetty.mov"]
        self.assertEqual(self.clip_bytes, clip_copy[2], "the clip's copy reached the Bin, whole")

    def test_a_bulk_delete_looks_at_each_file_on_the_share_once_and_a_photo_is_not_weighed_as_a_clip(self):
        # A look at a file on a share is a round trip; the size the delete needs is the one its first look found.
        looked, real_stamp, real_size = [], shares.stamp_of, os.path.getsize

        def stamp_of(path):
            looked.append(os.path.basename(path))
            return real_stamp(path)

        def getsize(path):
            looked.append(("size", os.path.basename(path)))
            return real_size(path)

        asked = self.asked(self.photo, self.clip)
        with mock.patch.object(shares, "stamp_of", side_effect=stamp_of), \
                mock.patch.object(recycle_bin.os.path, "getsize", side_effect=getsize):
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": [self.photo, self.clip]},
                                                    "params": {"token": asked["token"]}})
        self.assertEqual((2, 0), (reply.get_json()["changed"], reply.get_json()["error_count"]), reply.get_json())
        self.assertEqual(1, looked.count("pier.jpg"), looked)
        self.assertEqual(1, looked.count("jetty.mov"), looked)
        self.assertEqual(1, looked.count(("size", "pier.jpg")), "the photo's size, once, for its copy: %s" % looked)
        # The clip is weighed by the delete's question of the file (photos.delete, recycle_bin.problem) and again for its copy.
        self.assertEqual(2, looked.count(("size", "jetty.mov")), "the clip weighed, then its copy's size: %s" % looked)

    def test_a_clip_deleted_from_its_panel_takes_its_vector_frames_and_poster_with_it(self):
        db.write_with_connection(self.vl.path, lambda conn: embeddings.put_by_id(
            conn, self.clip, MODEL, 1.0, 1, VECTOR, None, frames=FRAMES))
        thumbnails.serve(self.vl.library, self.clip, config.locate_ffmpeg)
        self.assertEqual([(1,)], self.vl.rows("SELECT COUNT(*) FROM embeddings WHERE photo_id = ?", self.clip))
        self.assertEqual([(4,)], self.vl.rows("SELECT COUNT(*) FROM embedding_frames WHERE photo_id = ?", self.clip))
        self.assertGreater(self.posters(self.clip), 0, "its poster is in the cache")
        reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assert_gone(self.clip_path, self.clip)
        self.assertEqual([(0,)], self.vl.rows("SELECT COUNT(*) FROM embeddings WHERE photo_id = ?", self.clip))
        self.assertEqual([(0,)], self.vl.rows("SELECT COUNT(*) FROM embedding_frames WHERE photo_id = ?", self.clip),
                         "no frame outlives the clip")
        self.assertEqual(0, self.posters(self.clip), "its poster went with it")
        self.assertTrue(os.path.exists(self.photo_path), "the photo beside it is left")

    def test_a_clip_over_the_limit_is_refused_with_its_size_and_the_photo_beside_it_is_deleted(self):
        size, photo_size = os.path.getsize(self.clip_path), os.path.getsize(self.photo_path)
        with mock.patch.object(recycle_bin, "MOST_CLIP_BYTES", size - 1):
            reply = self.post("photo/delete", {"path": self.clip_path})
            self.assertEqual(400, reply.status_code, reply.get_json())
            self.assertIn("larger than", reply.get_json()["error"])
            self.assert_clip_untouched()
            asked, done = self.delete_all(self.photo, self.clip)
        self.assertEqual(photo_size, asked["copy_bytes"], "the question counts the photo's copy, never the clip's")
        self.assertEqual((1, 1), (done["changed"], done["error_count"]), done["errors"])
        [error] = done["errors"]
        self.assertEqual(self.clip, error["id"])
        self.assertIn("larger than", error["why"])
        self.assert_gone(self.photo_path, self.photo)
        self.assert_clip_untouched()

    def test_a_clip_whose_original_will_not_go_after_its_copy_is_binned_is_said_to_be_there_twice_as_a_file(self):
        real = os.remove

        def remove(path):
            if paths.key(path) == paths.key(self.clip_path):
                raise PermissionError(13, "The file is in use")
            real(path)

        with mock.patch.object(recycle_bin.os, "remove", side_effect=remove):
            reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertNotEqual(200, reply.status_code)
        said = reply.get_json()["error"]
        self.assertIn("the file is there twice", said, "a clip is no photo: the sentence names neither")
        self.assertEqual(["jetty.mov"], self.binned(), "its copy is in the Bin")
        self.assertEqual(self.clip_bytes, digest(self.clip_path), "and the original is where it was")
        self.assertEqual([("video",)], self.vl.rows("SELECT kind FROM photos WHERE id = ?", self.clip), "with its row")

    def test_a_bulk_delete_lets_other_writers_in_before_and_after_each_clip(self):
        second = os.path.join(self.folder, "slipway.mov")
        shutil.copyfile(self.clip_path, second)
        other = self.vl.photo("Harbour", "slipway.mov", at=second)
        real = file_changes.between_chunks

        def turn(batch):
            self.events.append(("turn",))
            return real(batch)

        with mock.patch.object(file_changes, "between_chunks", side_effect=turn):
            _asked, done = self.delete_all(self.photo, self.clip, other)
        self.assertEqual((3, 0), (done["changed"], done["error_count"]), done["errors"])
        self.assertEqual({"pier.jpg", "jetty.mov", "slipway.mov"}, set(self.binned()))
        kinds = [event[0] for event in self.events]
        followed = [n for n, event in enumerate(self.events)
                    if event[0] == "bin" and event[1].endswith(".mov") and "bin" in kinds[n + 1:]]
        self.assertTrue(followed, "two clips: at least one is followed by another file")
        for n in followed:
            self.assertEqual("turn", kinds[n + 1], "the lock is let go of after %s, before the next file" % self.events[n][1])
        self.assertEqual("pier.jpg", self.binned()[0], "the photo goes first, so a clip follows it")
        preceded = [n for n, event in enumerate(self.events)
                    if event[0] == "bin" and event[1].endswith(".mov") and "bin" in kinds[:n]]
        self.assertTrue(preceded, "a clip follows another file")
        for n in preceded:
            self.assertEqual("turn", kinds[n - 1], "the lock is let go of before %s, after the file ahead of it" % self.events[n][1])


class AFileOnALocalDrive(APhotoAndAClip):
    """The folder is on a local drive with a Recycle Bin of its own: each file goes straight to that Bin, or, where that Bin
    would not keep it, for good once the owner confirmed it."""

    def reason(self, path):
        return None

    def assert_kept(self, path, photo_id):
        self.assertTrue(os.path.exists(path), "%s is where it was" % os.path.basename(path))
        self.assertEqual([(photo_id,)], self.vl.rows("SELECT id FROM photos WHERE id = ?", photo_id), "its row stays")

    def test_a_clip_larger_than_its_drives_bin_is_deleted_from_its_panel_only_once_confirmed_and_said_to_be_for_good(self):
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "0,0,0"}):     # a Bin of 0 MB: every file is larger
            reply = self.post("photo/delete", {"path": self.clip_path})
            self.assertNotEqual(200, reply.status_code, reply.get_json())
            self.assertEqual(["jetty.mov"], reply.get_json()["for_good"], "the reply names it, for the page to ask")
            self.assertIn("would be deleted for good", reply.get_json()["error"])
            self.assert_kept(self.clip_path, self.clip)
            reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertTrue(reply.get_json()["for_good"])
        self.assertIn("Deleted for good", reply.get_json()["message"])
        self.assertNotIn("Moved to the Recycle Bin", reply.get_json()["message"])
        self.assert_gone(self.clip_path, self.clip)
        self.assertEqual([], self.binned(), "nothing was sent to a Bin that would not keep it")

    def test_a_bulk_delete_names_the_files_it_would_delete_for_good_and_deletes_them_only_when_confirmed(self):
        ids = [self.photo, self.clip]
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "0,0,0"}):
            asked = self.asked(*ids)
            self.assertEqual({"count": 2, "names": ["jetty.mov", "pier.jpg"]}, asked["for_good"])
            unconfirmed = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                          "params": {"token": asked["token"]}})
            self.assertEqual(409, unconfirmed.status_code, "a start that did not confirm is not what was asked")
            self.assert_kept(self.clip_path, self.clip)
            self.assert_kept(self.photo_path, self.photo)
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                    "params": {"token": asked["token"], "for_good": True}})
        done = reply.get_json()
        self.assertEqual((2, 0, 2), (done["changed"], done["error_count"], done["for_good"]), done["errors"])
        self.assert_gone(self.clip_path, self.clip)
        self.assert_gone(self.photo_path, self.photo)
        self.assertEqual([], self.binned())

    def test_a_stale_question_that_warned_of_nothing_deletes_nothing_for_good(self):
        asked = self.asked(self.photo, self.clip)                          # the Bin keeps them when the question is asked
        self.assertEqual(0, asked["for_good"]["count"])
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "0,0,1"}):  # and is set to delete at once by the start
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": [self.photo, self.clip]},
                                                    "params": {"token": asked["token"]}})
        done = reply.get_json()
        self.assertEqual((0, 2), (done["changed"], done["error_count"]), done["errors"])
        for error in done["errors"]:
            self.assertIn("set to delete files at once", error["why"])
        self.assert_kept(self.clip_path, self.clip)
        self.assert_kept(self.photo_path, self.photo)
        self.assertEqual([], self.binned())

    def between(self):
        """A Bin, as the drive's settings say it, that keeps the photo but not the clip: the test's stand-in volume."""
        photo, clip = os.path.getsize(self.photo_path), os.path.getsize(self.clip_path)
        kept = (photo + clip) // 2
        self.assertTrue(photo < kept < clip, (photo, kept, clip))
        return mock.patch.object(recycle_bin, "_test_bin", return_value=(kept, 0, False))

    def test_a_bin_set_smaller_between_the_question_and_ok_deletes_nothing(self):
        # The question named the clip alone as deleted for good, and the owner confirmed it; by the start the drive's Bin was
        # set to delete at once, so the photo would be deleted for good too, never named: the start is refused, whole.
        ids = [self.photo, self.clip]
        with self.between():
            asked = self.asked(*ids)
        self.assertEqual({"count": 1, "names": ["jetty.mov"]}, asked["for_good"])
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "0,0,1"}):
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                    "params": {"token": asked["token"], "for_good": True}})
        self.assertEqual(409, reply.status_code, reply.get_json())
        self.assert_kept(self.clip_path, self.clip)
        self.assert_kept(self.photo_path, self.photo)
        self.assertEqual([], self.binned())

    def test_a_file_grown_past_its_bin_since_the_question_is_not_deleted_for_good(self):
        # The index still has the photo's old size, so the start's question agrees with the first; the photo itself grew
        # past what the Bin keeps. Only the clip was named and confirmed: the photo is refused, untouched.
        ids = [self.photo, self.clip]
        with self.between():
            asked = self.asked(*ids)
            self.assertEqual({"count": 1, "names": ["jetty.mov"]}, asked["for_good"])
            with open(self.photo_path, "ab") as grown:
                grown.write(b"\0" * os.path.getsize(self.clip_path))
            reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                    "params": {"token": asked["token"], "for_good": True}})
        done = reply.get_json()
        self.assertEqual(200, reply.status_code, done)
        self.assertEqual((1, 1, 1), (done["changed"], done["error_count"], done["for_good"]), done["errors"])
        self.assertEqual(self.photo, done["errors"][0]["id"])
        self.assertIn("would be deleted for good", done["errors"][0]["why"])
        self.assert_gone(self.clip_path, self.clip)
        self.assert_kept(self.photo_path, self.photo)
        self.assertEqual([], self.binned())

    def test_a_delete_that_says_for_good_of_a_file_its_bin_keeps_sends_it_to_the_bin(self):
        # A page cannot make a delete permanent by saying so: only a file its Bin would not keep is deleted for good.
        reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertFalse(reply.get_json()["for_good"])
        self.assertNotIn("Deleted for good", reply.get_json().get("message") or "")
        self.assertEqual(["jetty.mov"], self.binned(), "the clip went to its Bin")
        asked = self.asked(self.photo)
        self.assertEqual(0, asked["for_good"]["count"])
        reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": [self.photo]},
                                                "params": {"token": asked["token"], "for_good": True}})
        done = reply.get_json()
        self.assertEqual(200, reply.status_code, done)
        self.assertEqual((1, 0), (done["changed"], done["for_good"]), done["errors"])
        self.assertEqual(["jetty.mov", "pier.jpg"], self.binned(), "the photo went to its Bin")

    def test_a_file_its_drives_bin_can_keep_goes_straight_there(self):
        self.assertEqual({"count": 0, "names": []}, self.asked(self.clip)["for_good"])
        reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertFalse(reply.get_json()["for_good"])
        self.assert_gone(self.clip_path, self.clip)
        self.assertEqual(["jetty.mov"], self.binned(), "the clip itself, not a copy, went to the Bin")


if __name__ == "__main__":
    unittest.main()
