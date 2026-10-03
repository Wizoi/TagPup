"""The thumbnail cache (tagpup.services.thumbnails, tagpup.files.thumbs; docs/ARCHITECTURE.md, phase 9a-2).

Made on first ask, kept, keyed by the photo's id, its row's path and its file's stamp; replaced when the file
changes, deleted with its photo, never made for a damaged photo, never breaking the request when the cache
cannot be written. Photos are rows as the indexer records them (tests/view_library.py), real JPEGs on disk where a
picture is to be decoded. Fictional names only.
"""
import io
import os
import shutil
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import roots_library as rl  # noqa: E402
from PIL import Image  # noqa: E402
from view_library import ViewLibrary, make_jpeg  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core.result import NotFound  # noqa: E402
from tagpup.files import images, thumbs  # noqa: E402
from tagpup.services import damaged_photos, thumbnails  # noqa: E402
from tagpup.services import faces as face_actions  # noqa: E402
from tagpup.services import photos as photo_actions  # noqa: E402
from tagpup.store import db, photos  # noqa: E402


def entries_of(library):
    return sorted(os.path.basename(each[4]) for each in thumbs.entries(library.thumbs))


def picture(content):
    return Image.open(io.BytesIO(content))


class Cache(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.library = self.vl.library
        self.id = self.vl.photo("Regatta", "boat.jpg", taken="2024:06:01 10:00:00", real=True, size=(1000, 600))
        self.path = self.vl.path_of(self.id)

    def serve(self, photo_id=None):
        return thumbnails.serve(self.library, photo_id or self.id)

    def renders(self):
        return mock.patch.object(thumbs, "render", wraps=thumbs.render)


class FirstAsk(Cache):
    def test_the_first_ask_makes_a_300_pixel_jpeg_and_keeps_it(self):
        made = self.serve()
        self.assertEqual((thumbnails.OK, True), (made.kind, made.kept))
        found = picture(made.content)
        self.assertEqual("JPEG", found.format)
        self.assertEqual(300, max(found.size))
        self.assertEqual(1, len(entries_of(self.library)))
        self.assertTrue(entries_of(self.library)[0].startswith("%d_" % self.id))
        self.assertLess(len(made.content), 40_000)

    def test_the_second_ask_decodes_nothing(self):
        self.serve()
        with self.renders() as render:
            again = self.serve()
        self.assertEqual(0, render.call_count)
        self.assertEqual((thumbnails.OK, True), (again.kind, again.kept))

    def test_it_is_kept_under_the_library_folder_by_name_and_a_test_home_holds_its_own(self):
        self.serve()
        self.assertEqual(os.path.join(self.vl.home.data, "cache", "harbour", "thumbs"), self.library.thumbs)
        self.assertTrue(os.path.isdir(self.library.thumbs))
        self.assertTrue(self.library.thumbs.startswith(self.vl.home.root))

    def test_an_id_with_no_photo_is_a_sentence(self):
        with self.assertRaises(NotFound) as caught:
            self.serve(99999)
        self.assertIn("no photo 99999", str(caught.exception))

    def test_an_id_sqlite_cannot_hold_is_no_photo_and_not_an_overflow(self):
        for wanted in (-1, 0, 2 ** 63, 2 ** 70):
            with self.subTest(wanted=wanted), self.assertRaises(NotFound):
                thumbnails.serve(self.library, wanted)

    def test_the_url_names_the_photo_by_id_and_carries_the_stamp(self):
        self.assertEqual("/api/photo-thumb?id=7&v=1717236000.5", thumbnails.url(7, 1717236000.5))
        self.assertEqual("/api/photo-thumb?id=7", thumbnails.url(7))

    def test_the_whole_library_is_not_decoded_for_one_ask(self):
        other = self.vl.photo("Regatta", "other.jpg", real=True)
        with self.renders() as render:
            self.serve(other)
        self.assertEqual(1, render.call_count)


class TwoAtOnce(Cache):
    def test_two_requests_for_one_photo_make_it_once_and_both_get_a_whole_picture(self):
        started, release = threading.Event(), threading.Event()
        real = thumbs.render
        calls = []

        def slow(path):
            calls.append(path)
            started.set()
            release.wait(10)
            return real(path)

        answers = []
        with mock.patch.object(thumbs, "render", slow):
            first = threading.Thread(target=lambda: answers.append(self.serve()))
            first.start()
            self.assertTrue(started.wait(10))
            second = threading.Thread(target=lambda: answers.append(self.serve()))
            second.start()
            release.set()
            first.join(10)
            second.join(10)
        self.assertEqual(1, len(calls))
        self.assertEqual(2, len(answers))
        for answer in answers:
            self.assertEqual(300, max(picture(answer.content).size))
        self.assertEqual(1, len(entries_of(self.library)))
        self.assertEqual([], [name for name in os.listdir(os.path.dirname(thumbs.entry_path(
            self.library.thumbs, self.id, "00000000", 0, 0))) if name.startswith(".tmp-")])

    def test_two_writers_of_one_entry_leave_one_whole_file(self):
        data = b"x" * 1000
        done = []

        def write():
            thumbs.write(self.library.thumbs, 5, "abcdef12", 1.5, 10, data)
            done.append(1)

        threads = [threading.Thread(target=write) for _ in range(6)]
        for each in threads:
            each.start()
        for each in threads:
            each.join(10)
        self.assertEqual(6, len(done))
        self.assertEqual(data, thumbs.read(self.library.thumbs, 5, "abcdef12", 1.5, 10))
        self.assertEqual(1, len(os.listdir(os.path.join(self.library.thumbs, "000"))))


class AFileThatChanges(Cache):
    def test_a_changed_file_is_a_new_entry_and_the_old_one_is_deleted(self):
        self.serve()
        before = entries_of(self.library)
        make_jpeg(self.path, (200, 10, 10), (500, 500))
        os.utime(self.path, (1_700_000_000, 1_700_000_000))
        with self.renders() as render:
            again = self.serve()
        self.assertEqual(1, render.call_count)
        after = entries_of(self.library)
        self.assertEqual(1, len(after))
        self.assertNotEqual(before, after)
        self.assertEqual((300, 300), picture(again.content).size)

    def test_a_photo_renamed_keeps_its_id_and_is_made_once_under_its_new_name(self):
        self.serve()
        before = entries_of(self.library)
        new_path = os.path.join(os.path.dirname(self.path), "renamed.jpg")
        os.rename(self.path, new_path)
        moved, skipped = photos.move_rows(self.library.path, {self.path: new_path})
        self.assertEqual((1, []), (moved, skipped))
        with self.renders() as render:
            self.serve()
            self.serve()
        self.assertEqual(1, render.call_count)
        after = entries_of(self.library)
        self.assertEqual(1, len(after))
        self.assertTrue(after[0].startswith("%d_" % self.id))
        self.assertNotEqual(before[0].split("_")[1], after[0].split("_")[1], "the path hash changed")

    def test_a_case_only_rename_is_the_same_entry_where_the_filesystem_has_no_case(self):
        if os.name != "nt":
            self.skipTest("a filesystem with case")
        self.serve()
        folded = os.path.join(os.path.dirname(self.path), "BOAT.JPG")
        os.rename(self.path, folded)
        photos.move_rows(self.library.path, {self.path: folded})
        with self.renders() as render:
            self.serve()
        self.assertEqual(0, render.call_count)

    def test_a_photo_whose_file_is_gone_is_shown_as_it_was_last_seen(self):
        self.serve()
        os.remove(self.path)
        gone = self.serve()
        self.assertEqual(thumbnails.LAST_KNOWN, gone.kind)
        self.assertEqual(300, max(picture(gone.content).size))

    def test_a_photo_whose_file_is_gone_and_never_shown_is_a_sentence(self):
        never = self.vl.photo("Regatta", "never.jpg")
        with self.assertRaises(NotFound) as caught:
            self.serve(never)
        self.assertIn("is not there", str(caught.exception))


class ADamagedPhoto(Cache):
    def damaged_record(self):
        stat = os.stat(self.path)
        damaged_photos.remember(self.library, [(self.path, (stat.st_mtime, stat.st_size), "truncated", "ends early", 0)])

    def test_a_photo_recorded_damaged_is_a_placeholder_and_its_file_is_never_decoded(self):
        self.damaged_record()
        with self.renders() as render:
            answer = self.serve()
        self.assertEqual(0, render.call_count)
        self.assertEqual((thumbnails.DAMAGED, False, None), (answer.kind, answer.kept, answer.etag))
        self.assertEqual([], entries_of(self.library))
        self.assertEqual("JPEG", picture(answer.content).format)

    def test_it_is_decoded_again_when_its_stamp_changes(self):
        self.damaged_record()
        self.serve()
        make_jpeg(self.path, (1, 2, 3), (400, 400))
        os.utime(self.path, (1_700_000_000, 1_700_000_000))
        answer = self.serve()
        self.assertEqual(thumbnails.OK, answer.kind)
        self.assertEqual(1, len(entries_of(self.library)))

    def test_a_possibly_incomplete_copy_decodes_and_is_kept(self):
        stat = os.stat(self.path)
        damaged_photos.remember(self.library, [(self.path, (stat.st_mtime, stat.st_size), damaged_photos.INCOMPLETE,
                                                "ends in zeros", 70000)])
        self.assertEqual(thumbnails.OK, self.serve().kind)

    def test_a_file_that_does_not_decode_is_a_placeholder_not_kept_and_not_decoded_again(self):
        with open(self.path, "wb") as handle:
            handle.write(b"this is not a picture at all")
        os.utime(self.path, (1_700_000_100, 1_700_000_100))
        with self.renders() as render:
            first = self.serve()
            second = self.serve()
        self.assertEqual(1, render.call_count)
        self.assertEqual((thumbnails.DAMAGED, thumbnails.DAMAGED), (first.kind, second.kind))
        self.assertEqual([], entries_of(self.library))
        # Nothing was written to the library for it
        self.assertEqual([], damaged_photos.records(self.library))

    def test_a_file_that_cannot_be_read_is_unavailable_and_nothing_is_made_of_it(self):
        with mock.patch.object(images, "smaller_copy", side_effect=PermissionError(13, "Permission denied")):
            with self.assertRaises(thumbnails.Unavailable) as caught:
                self.serve()
        self.assertIn("could not be read just now", str(caught.exception))
        self.assertEqual([], entries_of(self.library))
        self.assertEqual([], damaged_photos.records(self.library))
        self.assertEqual(thumbnails.OK, self.serve().kind, "a moment later it is a photo again")


class ACacheThatCannotBeWritten(Cache):
    def test_a_folder_that_cannot_be_made_costs_only_the_cache(self):
        os.makedirs(os.path.dirname(self.library.thumbs), exist_ok=True)
        with open(self.library.thumbs, "w") as handle:
            handle.write("a file where the folder should be")
        answer = self.serve()
        self.assertEqual((thumbnails.OK, False, None), (answer.kind, answer.kept, answer.etag))
        self.assertEqual(300, max(picture(answer.content).size))

    def test_a_full_disk_costs_only_the_cache(self):
        with mock.patch.object(thumbs, "write", side_effect=OSError(28, "No space left on device")):
            answer = self.serve()
        self.assertFalse(answer.kept)
        self.assertEqual(300, max(picture(answer.content).size))
        with self.renders() as render:
            self.serve()
        self.assertEqual(1, render.call_count, "made again: nothing was kept")

    def test_a_write_that_fails_half_way_leaves_no_entry_and_no_temporary_file(self):
        with mock.patch("os.replace", side_effect=OSError(5, "I/O error")):
            with self.assertRaises(OSError):
                thumbs.write(self.library.thumbs, 7, "abcdef12", 1.0, 5, b"data")
        shard = os.path.join(self.library.thumbs, "000")
        self.assertEqual([], os.listdir(shard))

    def test_a_temporary_file_a_crashed_writer_left_is_swept_once_it_is_old(self):
        shard = os.path.join(self.library.thumbs, "000")
        os.makedirs(shard)
        old, young = os.path.join(shard, ".tmp-1-1-1"), os.path.join(shard, ".tmp-2-2-2")
        for each in (old, young):
            with open(each, "wb") as handle:
                handle.write(b"half")
        os.utime(old, (1_000_000, 1_000_000))
        self.assertEqual(1, thumbs.sweep_temporaries(self.library.thumbs))
        self.assertEqual([".tmp-2-2-2"], os.listdir(shard))

    def test_the_folder_gone_is_made_again(self):
        self.serve()
        shutil.rmtree(self.library.thumbs)
        self.assertTrue(self.serve().kept)


class TakingOne(Cache):
    def test_deleting_a_photo_takes_its_thumbnail(self):
        self.serve()
        self.assertEqual(1, len(entries_of(self.library)))
        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", return_value=True):
            result = photo_actions.delete(self.library, self.path)
        self.assertTrue(result.ok, result.message())
        self.assertEqual([], entries_of(self.library))

    def test_removing_a_folder_takes_the_thumbnails_of_its_photos_and_only_those(self):
        elsewhere = self.vl.photo("Harbour", "kept.jpg", real=True)
        self.serve()
        self.serve(elsewhere)
        self.assertEqual(2, len(entries_of(self.library)))
        result = face_actions.remove_folder(self.library, os.path.dirname(self.path))
        self.assertEqual(1, result.changed)
        left = entries_of(self.library)
        self.assertEqual(1, len(left))
        self.assertTrue(left[0].startswith("%d_" % elsewhere))

    def test_the_indexer_removing_photos_sweeps_the_cache(self):
        from tagpup.services.search import PhotoIndex
        gone = self.vl.photo("Regatta", "going.jpg", real=True)
        self.serve()
        self.serve(gone)
        index = PhotoIndex.__new__(PhotoIndex)
        index.db_path, index.conn = self.library.path, self.vl.conn
        with mock.patch.object(PhotoIndex, "load"):
            index.remove_paths({self.vl.path_of(gone)})
        left = entries_of(self.library)
        self.assertEqual(1, len(left))
        self.assertTrue(left[0].startswith("%d_" % self.id))

    def test_sweeping_takes_what_no_photo_owns_and_keeps_the_rest(self):
        self.serve()
        thumbs.write(self.library.thumbs, 4242, "00000000", 1.0, 1, b"orphan")
        other = self.vl.photo("Regatta", "later.jpg", real=True)
        self.serve(other)
        # an entry of a photo that is there, under a path it no longer has (written beside the real one, as a
        # rename leaves it until the photo is asked for again)
        with open(thumbs.entry_path(self.library.thumbs, self.id, "deadbeef", 2.0, 2), "wb") as handle:
            handle.write(b"stale")
        done = thumbnails.sweep(self.library)
        self.assertEqual(2, done["removed"])
        names = entries_of(self.library)
        self.assertEqual(2, len(names))
        self.assertNotIn("4242", " ".join(names))

    def test_sweeping_a_cache_that_is_not_there_is_nothing(self):
        self.assertEqual({"removed": 0, "bytes": 0, "temporaries": 0}, thumbnails.sweep(self.library))

    def test_forgetting_a_cache_that_cannot_be_cleared_does_not_raise(self):
        with mock.patch.object(thumbs, "remove", side_effect=OSError(5, "I/O")):
            self.assertEqual(0, thumbnails.forget(self.library, [self.id]))

    def test_clearing_the_cache_of_a_library_made_again_from_nothing(self):
        self.serve()
        self.assertTrue(thumbnails.clear(self.library))
        self.assertFalse(os.path.exists(self.library.thumbs))
        self.assertFalse(thumbnails.clear(self.library))


class TwoLibraries(unittest.TestCase):
    def test_two_libraries_on_one_machine_with_the_same_ids_keep_their_own(self):
        home = own_home.for_test(self)
        first = ViewLibrary(self, "harbour", home=home)
        second = ViewLibrary(self, "meadow", home=home)
        a = first.photo("Pics", "a.jpg", real=True, shade=(250, 0, 0), size=(100, 100))
        b = second.photo("Pics", "a.jpg", real=True, shade=(0, 0, 250), size=(100, 100))
        self.assertEqual(a, b, "both are photo 1")
        red = picture(thumbnails.serve(first.library, a).content).convert("RGB").getpixel((5, 5))
        blue = picture(thumbnails.serve(second.library, b).content).convert("RGB").getpixel((5, 5))
        self.assertGreater(red[0], 200)
        self.assertGreater(blue[2], 200)
        self.assertNotEqual(first.library.thumbs, second.library.thumbs)
        self.assertEqual(1, len(entries_of(first.library)))
        self.assertEqual(1, len(entries_of(second.library)))

    def test_two_libraries_holding_one_folder_each_make_their_own_entry(self):
        home = own_home.for_test(self)
        first = ViewLibrary(self, "harbour", home=home)
        second = ViewLibrary(self, "meadow", home=home)
        shared = os.path.join(home.root, "Shared", "x.jpg")
        a = first.photo("", "", real=True, at=shared)
        b = second.photo("", "", at=shared)
        thumbnails.serve(first.library, a)
        with mock.patch.object(thumbs, "render", wraps=thumbs.render) as render:
            thumbnails.serve(second.library, b)
        self.assertEqual(1, render.call_count)


@unittest.skipUnless(os.name == "nt", "the spellings are Windows paths")
class ALibraryMovedToAnotherPlace(unittest.TestCase):
    def test_a_root_moved_keeps_the_cache_valid(self):
        """Ids and row paths unchanged, only the machine's map: the entries are still the photos' own."""
        home = own_home.for_test(self)
        side = rl.Side(home, "harbour", real=2, bulk=0, outside=0)
        self.assertIsNone(side.adopt().refused)
        library = side.library
        ids = [row[0] for row in side.rows("SELECT id FROM photos ORDER BY id")]
        for photo_id in ids[:2]:
            thumbnails.serve(library, photo_id)
        before = entries_of(library)
        self.assertEqual(2, len(before))
        copy = os.path.join(home.root, "Copy", "Pictures")
        shutil.copytree(side.pictures, copy, copy_function=shutil.copy2)
        config.set_location(rl.NAME, copy)
        with mock.patch.object(thumbs, "render", wraps=thumbs.render) as render:
            again = [thumbnails.serve(library, photo_id) for photo_id in ids[:2]]
        self.assertEqual(0, render.call_count, "the stamps and the rows' paths are as they were")
        self.assertTrue(all(each.kept for each in again))
        self.assertEqual(before, entries_of(library))


class Warming(Cache):
    def setUp(self):
        super().setUp()
        self.second = self.vl.photo("Regatta", "second.jpg", real=True, shade=(5, 200, 5))
        self.third = self.vl.photo("Harbour", "third.jpg", real=True)
        self.gone = self.vl.photo("Harbour", "gone.jpg")   # a row, no file

    def test_a_dry_run_counts_and_writes_nothing(self):
        counts = thumbnails.warm(self.library)
        self.assertEqual(4, counts["photos"])
        self.assertEqual((3, 0, 1, 0), (counts["to_make"], counts["present"], counts["missing"], counts["made"]))
        self.assertEqual(3 * thumbnails.ASSUMED_BYTES, counts["estimate"])
        self.assertFalse(os.path.exists(self.library.thumbs), "not even the folder")

    def test_apply_makes_them_and_a_second_run_finds_them_there(self):
        counts = thumbnails.warm(self.library, apply=True)
        self.assertEqual((3, 0, 1), (counts["made"], counts["failed"], counts["missing"]))
        self.assertGreater(counts["bytes_made"], 0)
        self.assertEqual(3, len(entries_of(self.library)))
        again = thumbnails.warm(self.library, apply=True)
        self.assertEqual((0, 3, 0), (again["made"], again["present"], again["to_make"]))

    def test_the_estimate_is_the_average_entry_once_some_are_kept(self):
        self.serve()
        counts = thumbnails.warm(self.library)
        held, size = thumbs.usage(self.library.thumbs)
        self.assertEqual(1, held)
        self.assertEqual(2 * (size // held), counts["estimate"])

    def test_a_limit_stops_it_and_a_second_run_goes_on_from_there(self):
        first = thumbnails.warm(self.library, apply=True, limit=2)
        self.assertEqual((2, True), (first["made"], first["stopped"]))
        second = thumbnails.warm(self.library, apply=True, limit=2)
        self.assertEqual((1, 2), (second["made"], second["present"]))
        self.assertEqual(3, len(entries_of(self.library)))

    def test_a_folder_limits_it_to_the_photos_under_it(self):
        counts = thumbnails.warm(self.library, folder=os.path.join(self.vl.pictures, "Harbour"), apply=True)
        self.assertEqual(1, counts["made"])
        self.assertEqual(2, counts["photos"])

    def test_a_damaged_photo_is_counted_and_not_decoded(self):
        stat = os.stat(self.vl.path_of(self.third))
        damaged_photos.remember(self.library, [(self.vl.path_of(self.third), (stat.st_mtime, stat.st_size),
                                                "truncated", "x", 0)])
        with mock.patch.object(thumbs, "render", wraps=thumbs.render) as render:
            counts = thumbnails.warm(self.library, apply=True)
        self.assertEqual(2, render.call_count)
        self.assertEqual((2, 1), (counts["made"], counts["damaged"]))

    def test_a_file_that_does_not_decode_is_counted_damaged_and_the_run_goes_on(self):
        with open(self.vl.path_of(self.third), "wb") as handle:
            handle.write(b"not a picture")
        counts = thumbnails.warm(self.library, apply=True)
        self.assertEqual((2, 1, 1), (counts["made"], counts["damaged"], counts["missing"]))

    def test_a_cache_that_cannot_be_written_is_said(self):
        with mock.patch.object(thumbs, "write", side_effect=OSError(28, "No space left on device")):
            counts = thumbnails.warm(self.library, apply=True)
        self.assertEqual((0, 3), (counts["made"], counts["unwritable"]))

    def test_progress_is_reported_after_each_batch(self):
        seen = []
        thumbnails.warm(self.library, apply=True, progress=seen.append)
        self.assertEqual(1, len(seen))
        self.assertEqual(4, seen[0]["photos"])

    def test_a_library_that_cannot_be_read_just_now_fails_loudly(self):
        with mock.patch.object(db, "connect", side_effect=OSError("the library is not there")):
            with self.assertRaises(OSError):
                thumbnails.warm(self.library)


if __name__ == "__main__":
    unittest.main()
