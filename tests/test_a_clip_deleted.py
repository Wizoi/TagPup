"""A clip is deleted as a photo is (C4; DECISIONS 2026-11-08, A5, cut B): a clip of up to recycle_bin.MOST_CLIP_BYTES
goes to the Recycle Bin -- from a share, through this PC: copied under the Downloads mirror, the copy checked and recycled,
then the original deleted -- and leaves the library with everything it had: its row, its vector and frames, its poster. A
larger clip is deleted for good, on any drive, only once the owner confirmed a warning naming it (DECISIONS 2026-11-02 item
7, cut C): no copy is made of it, nothing of it reaches a Bin, its bytes are counted in no copy, and the photos and smaller
clips beside it go to the Bin as always.
On a local drive a file goes to its own drive's Recycle Bin. One larger than that Bin keeps, or on a drive whose Bin keeps
nothing, would be deleted for good (Windows, asked without a question, deletes it and says it recycled it): the question
names it, and it is deleted for good only when the owner confirmed that; a start that did not is refused, nothing touched
(DECISIONS 2026-11-08).

What the owner lives through: a day in the library view holds photos and clips together, on a share (no Recycle Bin of its
own); a Delete of the day asks first how much will be copied through this PC (the delete-check), then deletes them. A clip's
copy can take minutes, so a bulk delete lets other writers in before and after each clip, as it does after each chunk of
photos: a clip is a chunk of its own. Meanwhile the clip may be opened again, from this page or another, in any view: the
server, which alone knows which files a delete is working on, refuses a range of that clip (409, "This video is being
deleted.") and plays every other, until the delete has ended, done or failed.

A real one-second clip made by the machine's ffmpeg (as tests/test_a_clip_is_not_turned.py makes it); rows as the
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
import corpus  # noqa: E402
import own_home  # noqa: E402
import web_client  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files import recycle_bin, shares  # noqa: E402
from tagpup.services import file_changes, thumbnails  # noqa: E402
from tagpup.services import photos as photo_service  # noqa: E402
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
        corpus.clip(self.clip_path, ["-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
                                     "-t", "1", "-pix_fmt", "yuv420p", "-metadata", "creation_time=2021-07-04T18:30:00Z"],
                    TOOLS.ffmpeg)
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
        # The clip is weighed by its delete (recycle_bin.delete_file: is it over MOST_CLIP_BYTES?) and again for its copy.
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


class AClipOverTheLimit(APhotoAndAClip):
    """The clip, on the share, is over recycle_bin.MOST_CLIP_BYTES (the limit set to its size less a byte, as a 9 GB clip is
    over the real one): it is named in the delete's warning and deleted for good only once confirmed -- removed where it is,
    never copied through this PC, nothing of it sent to a Bin -- and the photos and smaller clips beside it go as always."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(recycle_bin, "MOST_CLIP_BYTES", os.path.getsize(self.clip_path) - 1)
        patcher.start()
        self.addCleanup(patcher.stop)
        copier = mock.patch.object(recycle_bin.shutil, "copy2", side_effect=shutil.copy2)
        self.copied = copier.start()
        self.addCleanup(copier.stop)

    def copies_of_the_clip(self):
        return [call for call in self.copied.call_args_list if paths.key(call.args[0]) == paths.key(self.clip_path)]

    def test_the_question_names_it_to_go_for_good_and_counts_no_copy_of_it(self):
        asked = self.asked(self.photo, self.clip)
        self.assertEqual({"count": 1, "names": ["jetty.mov"]}, asked["for_good"])
        self.assertEqual(os.path.getsize(self.photo_path), asked["copy_bytes"], "the photo's copy alone: the clip makes none")
        self.assertEqual(1, asked["through_this_pc"], "the photo goes through this PC; the clip does not")
        self.assertEqual(2, asked["total"])
        self.assert_clip_untouched()

    def test_unconfirmed_it_deletes_nothing_and_says_why(self):
        reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(400, reply.status_code, reply.get_json())
        self.assertEqual(["jetty.mov"], reply.get_json()["not_kept"], "named, for the page's warning")
        said = reply.get_json()["error"]
        self.assertIn("would be deleted for good", said)
        self.assertIn("a video of", said, "the sentence says why: its size")
        self.assertIn("Nothing was deleted", said)
        asked = self.asked(self.photo, self.clip)
        unconfirmed = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": [self.photo, self.clip]},
                                                      "params": {"token": asked["token"]}})
        self.assertEqual(409, unconfirmed.status_code, "a start that did not confirm is not what was asked")
        self.assertTrue(os.path.exists(self.photo_path), "nothing of the day was deleted")
        self.assert_clip_untouched()
        self.assertEqual([], self.copies_of_the_clip(), "no copy of it was begun")

    def test_confirmed_from_its_panel_it_is_deleted_for_good_with_no_copy_and_said_so(self):
        db.write_with_connection(self.vl.path, lambda conn: embeddings.put_by_id(
            conn, self.clip, MODEL, 1.0, 1, VECTOR, None, frames=FRAMES))
        reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        said = reply.get_json()
        self.assertTrue(said["for_good"])
        self.assertFalse(said["through_this_pc"], "it did not go through this PC")
        self.assertTrue(said["message"].startswith("Deleted for good, as you confirmed"), said["message"])
        self.assertNotIn("Recycle Bin of its drive", said["message"], "not its Bin's doing: its size's")
        self.assert_gone(self.clip_path, self.clip)
        self.assertEqual([(0,)], self.vl.rows("SELECT COUNT(*) FROM embedding_frames WHERE photo_id = ?", self.clip))
        self.assertEqual([], self.copied.call_args_list, "no copy was made")
        self.assertEqual([], self.binned(), "nothing went to a Bin")
        self.assertTrue(os.path.exists(self.photo_path), "the photo beside it is left")

    def test_a_day_of_a_photo_a_small_clip_and_a_big_one_sends_two_to_the_bin_and_the_big_one_for_good(self):
        # The big one: the clip with more after it, indexed as itself; the clip is now within the limit, the big one over it.
        big_path = os.path.join(self.folder, "breakwater.mov")
        with open(self.clip_path, "rb") as clip, open(big_path, "wb") as big:
            big.write(clip.read() + b"\0" * 4096)
        big = self.vl.photo("Harbour", "breakwater.mov", at=big_path)
        recycle_bin.MOST_CLIP_BYTES = os.path.getsize(self.clip_path)          # undone by setUp's patcher
        ids = [self.photo, self.clip, big]
        asked = self.asked(*ids)
        self.assertEqual({"count": 1, "names": ["breakwater.mov"]}, asked["for_good"])
        self.assertEqual(os.path.getsize(self.photo_path) + os.path.getsize(self.clip_path), asked["copy_bytes"])
        self.assertEqual(2, asked["through_this_pc"])
        reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                "params": {"token": asked["token"], "for_good": True}})
        done = reply.get_json()
        self.assertEqual(200, reply.status_code, done)
        self.assertEqual((3, 0, 1), (done["changed"], done["error_count"], done["for_good"]), done["errors"])
        self.assertEqual({"pier.jpg", "jetty.mov"}, set(self.binned()), "the photo and the small clip, through this PC")
        self.assertEqual([], [call for call in self.copied.call_args_list if paths.key(call.args[0]) == paths.key(big_path)],
                         "the big one copied never")
        for path, photo_id in ((self.photo_path, self.photo), (self.clip_path, self.clip), (big_path, big)):
            self.assert_gone(path, photo_id)

    def test_a_question_that_did_not_name_it_never_deletes_it_for_good(self):
        recycle_bin.MOST_CLIP_BYTES = os.path.getsize(self.clip_path)       # within the limit when asked
        asked = self.asked(self.photo, self.clip)
        self.assertEqual(0, asked["for_good"]["count"])
        recycle_bin.MOST_CLIP_BYTES = os.path.getsize(self.clip_path) - 1   # over it by the start
        ids = [self.photo, self.clip]
        # A start confirming for good what the question did not name: the token covers the set, refused whole.
        reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids},
                                                "params": {"token": asked["token"], "for_good": True}})
        self.assertEqual(409, reply.status_code, reply.get_json())
        self.assertTrue(os.path.exists(self.photo_path), "nothing was deleted")
        self.assert_clip_untouched()
        # The start as the question was answered: the photo goes, the clip is refused at its own delete, untouched.
        reply = self.post("library/bulk-edit", {"op": "delete", "selection": {"ids": ids}, "params": {"token": asked["token"]}})
        done = reply.get_json()
        self.assertEqual(200, reply.status_code, done)
        self.assertEqual((1, 1, 0), (done["changed"], done["error_count"], done["for_good"]), done["errors"])
        self.assertIn("would be deleted for good", done["errors"][0]["why"])
        self.assert_gone(self.photo_path, self.photo)
        self.assert_clip_untouched()
        self.assertEqual([], self.copies_of_the_clip())

    def test_a_for_good_said_of_a_clip_within_the_limit_still_goes_to_the_bin_through_this_pc(self):
        recycle_bin.MOST_CLIP_BYTES = os.path.getsize(self.clip_path)
        reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertFalse(reply.get_json()["for_good"])
        self.assertTrue(reply.get_json()["through_this_pc"])
        self.assertEqual(["jetty.mov"], self.binned(), "its copy went to the Bin: a page cannot make a delete permanent")

    def test_a_clip_whose_size_cannot_be_read_is_left_with_its_row_and_a_plain_sentence(self):
        real = os.path.getsize

        def getsize(path):
            if paths.key(path) == paths.key(self.clip_path):
                raise OSError(53, "The network path was not found")
            return real(path)

        with mock.patch.object(recycle_bin.os.path, "getsize", side_effect=getsize):
            reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertNotEqual(200, reply.status_code)
        error = reply.get_json()["error"]
        self.assertIn("Could not tell how large the video is", error)
        self.assertIn("Nothing was deleted", error)
        self.assertNotIn("Errno", error, "a sentence, not the raw error")
        self.assert_clip_untouched()
        self.assertFalse(recycle_bin.being_deleted(self.clip_path), "failed: released")

    def test_a_clip_held_open_elsewhere_is_left_with_its_row_and_a_plain_sentence(self):
        real = os.remove

        def remove(path):
            if paths.key(path) == paths.key(self.clip_path):
                self.during = self.client.get("/library/api/video-file", query_string={"id": self.clip},
                                              headers={"Range": "bytes=0-99"})
                raise PermissionError(32, "The process cannot access the file because it is being used by another process")
            real(path)

        with mock.patch.object(recycle_bin.os, "remove", side_effect=remove):
            reply = self.post("photo/delete", {"path": self.clip_path, "for_good": True})
        self.assertEqual(409, self.during.status_code, "the server's player refused it while its delete was under way")
        self.assertNotEqual(200, reply.status_code)
        self.assertIn("it is still where it was", reply.get_json()["error"])
        self.assert_clip_untouched()
        self.assertFalse(recycle_bin.being_deleted(self.clip_path), "failed: released")


class AClipBeingDeleted(APhotoAndAClip):
    """A delete of the clip is under way -- its copy through this PC is held part-way -- and the player asks for a range of it,
    and of another clip, as a page does when either is opened (the asks are made from inside the held copy: no thread)."""

    def setUp(self):
        super().setUp()
        self.other_path = os.path.join(self.folder, "slipway.mov")
        shutil.copyfile(self.clip_path, self.other_path)
        self.other = self.vl.photo("Harbour", "slipway.mov", at=self.other_path)
        self.during = {}

    def ranges(self):
        return {name: self.client.get("/library/api/video-file", query_string={"id": photo_id}, headers={"Range": "bytes=0-99"})
                for name, photo_id in (("clip", self.clip), ("other", self.other))}

    def copy_asking(self, fail=False):
        real = shutil.copy2

        def copy2(source, target, *args, **kwargs):
            if paths.key(source) == paths.key(self.clip_path):
                self.during = self.ranges()
                if fail:
                    raise OSError(28, "There is not enough space on the disk")
            return real(source, target, *args, **kwargs)
        return mock.patch.object(recycle_bin.shutil, "copy2", side_effect=copy2)

    def assert_refused_alone(self):
        self.assertEqual({"clip", "other"}, set(self.during), "the player asked while the copy was held")
        refused = self.during["clip"]
        self.assertEqual(409, refused.status_code, refused.data[:300])
        self.assertEqual("This video is being deleted.", refused.get_json()["error"])
        self.assertEqual(206, self.during["other"].status_code, "another clip plays as always")

    def test_a_clip_under_its_panel_delete_is_refused_alone_and_released_once_deleted(self):
        with self.copy_asking():
            reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assert_refused_alone()
        self.assert_gone(self.clip_path, self.clip)
        self.assertFalse(recycle_bin.being_deleted(self.clip_path), "deleted: no longer under delete")
        self.assertEqual(404, self.ranges()["clip"].status_code, "asked again: its file is not there, not 'being deleted'")

    def test_a_clip_under_a_bulk_delete_is_refused_alone_and_plays_again_when_its_delete_fails(self):
        with self.copy_asking(fail=True):
            _asked, done = self.delete_all(self.photo, self.clip)
        self.assert_refused_alone()
        self.assertEqual((1, 1), (done["changed"], done["error_count"]), done["errors"])
        self.assert_clip_untouched()
        self.assertFalse(recycle_bin.being_deleted(self.clip_path), "failed: released as well")
        after = self.ranges()
        self.assertEqual((206, 206), (after["clip"].status_code, after["other"].status_code), "the clip plays again")

    def test_a_range_already_streaming_when_the_delete_begins_sends_no_more_of_it(self):
        # Another page was playing the clip: its answer is under way when the delete begins, and reads no piece after.
        piece = 1024
        patcher = mock.patch.object(photo_service, "VIDEO_READ", piece)
        patcher.start()
        self.addCleanup(patcher.stop)
        streaming = self.client.get("/library/api/video-file", query_string={"id": self.clip}, headers={"Range": "bytes=0-"},
                                    buffered=False)
        self.assertEqual(206, streaming.status_code)
        self.assertGreater(os.path.getsize(self.clip_path), 4 * piece, "the clip is several pieces long")
        real, streamed = shutil.copy2, []

        def copy2(source, target, *args, **kwargs):
            streamed.append(b"".join(streaming.response))
            return real(source, target, *args, **kwargs)

        with mock.patch.object(recycle_bin.shutil, "copy2", side_effect=copy2):
            reply = self.post("photo/delete", {"path": self.clip_path})
        streaming.close()
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(1, len(streamed))
        self.assertLessEqual(len(streamed[0]), piece, "at most the piece read before the delete began: none after it")
        self.assert_gone(self.clip_path, self.clip)


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
            self.assertEqual(["jetty.mov"], reply.get_json()["not_kept"], "the reply names it, for the page to ask")
            self.assertNotIn("for_good", reply.get_json(), "for_good is the success's flag alone: one key, one shape")
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
        said = reply.get_json()["error"]
        self.assertIn("deleted for good", said, "the sentence says what changed: what would go for good")
        self.assertIn("nothing was deleted", said)
        self.assertNotIn("came into it or left it", said, "no photo came into the selection or left it")
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

    def test_an_empty_file_on_a_drive_whose_bin_deletes_at_once_is_named_and_not_said_to_be_recycled(self):
        # A Bin set to delete at once keeps nothing, an empty file not either: Windows would delete it and say it recycled it.
        empty_path = os.path.join(self.folder, "rope.jpg")
        open(empty_path, "wb").close()
        empty = self.vl.photo("Harbour", "rope.jpg", at=empty_path)
        self.assertEqual([(0,)], self.vl.rows("SELECT size FROM photos WHERE id = ?", empty), "the index has its size, 0")
        with mock.patch.dict(os.environ, {"TAGPUP_RECYCLE_BIN": "1024,0,1"}):
            self.assertEqual({"count": 1, "names": ["rope.jpg"]}, self.asked(empty)["for_good"])
            reply = self.post("photo/delete", {"path": empty_path})
            self.assertEqual(["rope.jpg"], reply.get_json()["not_kept"], reply.get_json())
            self.assert_kept(empty_path, empty)
        self.assertEqual([], self.binned(), "nothing was sent to a Bin that keeps nothing")

    def test_a_file_its_drives_bin_can_keep_goes_straight_there(self):
        self.assertEqual({"count": 0, "names": []}, self.asked(self.clip)["for_good"])
        reply = self.post("photo/delete", {"path": self.clip_path})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertFalse(reply.get_json()["for_good"])
        self.assert_gone(self.clip_path, self.clip)
        self.assertEqual(["jetty.mov"], self.binned(), "the clip itself, not a copy, went to the Bin")


if __name__ == "__main__":
    unittest.main()
