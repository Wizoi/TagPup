"""A clip's tag write cut short, and the next start (DECISIONS 2026-11-05, M7; scenario C2, its settle D3's rule).

What the owner lives through: a tag is added to a clip (its XMP patched in place, a box appended: in_place.patch_clip)
and the server dies, or the share stops answering, part-way. At the next start (file_changes.settle_once) the clip is
settled by its bytes against the record (v2) the journal saved before the first byte: all old, it is written again;
torn (the appended box half there), it is cut back to its old size and written again; all new, it is recorded; changed by
another program meanwhile, it is a conflict, never written over. Each ends with the clip byte for byte the original or
the clip as an uncut write leaves it, and its row following. Two writes of one clip run one after the other
(file_changes.exclusively). After a write, the player's stream of the clip carries its new size.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip), the crash
as tests/test_file_journal makes one. These are guards of B3's wiring, which the old code passed: written with it (#2547).
Fictional names.
"""
import contextlib
import io
import os
import shutil
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exiftool_fields  # noqa: E402
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402
from test_file_journal import Crash, Torn, crash_at, cut_patch  # noqa: E402

from tagpup.files import clip_patch, containers, in_place, segment_edits  # noqa: E402
import page_writes  # noqa: E402
from tagpup.services import file_changes, tagging  # noqa: E402
from tagpup.store import file_journal  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


@contextlib.contextmanager
def cut_half_way(raised=None):
    """The patch's write of the clip cut half way through its first range, then the process killed (a Crash), or, with
    `raised`, that raised as the write's (a share that stopped answering). Only the first write is cut."""
    real_write, real_bounded, cut = in_place._write, in_place._bounded, []

    def write(handle, changes):
        if cut:
            return real_write(handle, changes)
        cut.append(changes)
        at, new = changes[0]
        real_write(handle, [(at, new[:len(new) // 2])])
        raise raised or Torn("killed half way through its first range")

    def bounded(path, call):
        try:
            return real_bounded(path, call)
        except Torn as e:
            raise Crash(str(e)) from None

    with mock.patch.object(in_place, "_write", write), mock.patch.object(in_place, "_bounded", bounded):
        yield cut


class AClipPatchCutShort(tagged.ALibraryHoldingAClip):

    def setUp(self):
        super().setUp()
        self.clip, = self.index(self.clip_path)
        self.original = self.read_bytes(self.clip_path)
        shutil.copyfile(self.clip_path, os.path.join(self.home.root, "original.mp4"))

    def tag(self, *tags):
        return tagging.change_tags(self.vl.library, [self.clip_path], list(tags), [], exiftool_fields.opened(self, EXIFTOOL))

    def killed(self, cutting=None, step=None):
        with cutting if cutting is not None else contextlib.nullcontext(), \
                mock.patch.object(file_changes, "_reached", side_effect=crash_at(step) if step else None):
            with self.assertRaises(Crash):
                self.tag("Trips/Coast")
        self.assertEqual(["writing"], self.change_states(self.clip))
        (change, file_id), = self.vl.rows("SELECT change_id, id FROM change_files WHERE photo_id = ?", self.clip)
        return change, file_id

    def as_written(self, change):
        """The clip as the write of `change` leaves it uncut: its original patched on a copy outside the library."""
        row, = file_journal.files_of(self.vl.library.path, change)
        copy = os.path.join(self.home.root, "uncut.mp4")
        shutil.copyfile(os.path.join(self.home.root, "original.mp4"), copy)
        in_place.patch_clip(copy, lambda old: segment_edits.new_packet(old, row.after),
                            lambda record, before: None)
        return self.read_bytes(copy)

    def against_its_record(self, file_id):
        return in_place.held(self.clip_path, *file_journal.patch_of(self.vl.library.path, file_id))

    def start_again(self):
        with mock.patch.object(file_changes, "_settled", set()):
            return file_changes.settle_once(self.vl.library, EXIFTOOL)

    def assert_written_again(self, change):
        self.assertEqual(1, self.start_again())
        self.assertEqual((["done"], ["Trips/Coast"], ["Trips/Coast"]),
                         (self.change_states(self.clip), self.read_now(self.clip_path)[1], self.tags_of(self.clip)))
        self.assertEqual(self.as_written(change), self.read_bytes(self.clip_path), "byte for byte an uncut write")
        self.assertEqual([(len(self.read_bytes(self.clip_path)),)],
                         self.vl.rows("SELECT size FROM photos WHERE id = ?", self.clip), "its row has its new size")

    def test_killed_once_its_record_was_saved_every_byte_is_old_and_it_is_written_again(self):
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        self.assertEqual((self.original, in_place.OLD), (self.read_bytes(self.clip_path), self.against_its_record(file_id)))
        self.assert_written_again(change)

    def test_killed_half_way_through_its_box_it_is_cut_back_and_written_again(self):
        change, file_id = self.killed(cut_half_way())
        self.assertEqual(in_place.TORN, self.against_its_record(file_id))
        self.assert_written_again(change)

    def test_a_date_save_killed_half_way_through_its_first_header_range_is_torn_and_settled_by_its_bytes_at_the_next_start(self):
        saved = "2024:08:15 09:30:00"
        base = page_writes.base(self.vl.library, self.clip_path)
        with cut_half_way() as cut:
            with self.assertRaises(Crash):
                tagging.save_photo(self.vl.library, self.clip_path, "", [], saved, exiftool_fields.opened(self, EXIFTOOL), "", base=base)
        mvhd = next(box for box in containers.read(io.BytesIO(self.original)).headers if box.type == "mvhd")
        self.assertEqual(mvhd.payload + 4, cut[0][0][0], "the first range is the movie header's creation field")
        self.assertEqual(["writing"], self.change_states(self.clip))
        (file_id,), = self.vl.rows("SELECT id FROM change_files WHERE photo_id = ?", self.clip)
        self.assertEqual(in_place.TORN, self.against_its_record(file_id))
        self.assertNotEqual(self.original, self.read_bytes(self.clip_path), "its first header half written")
        self.assertEqual(1, self.start_again())
        self.assertEqual(["done"], self.change_states(self.clip))
        now = self.read_bytes(self.clip_path)
        seconds = clip_patch.seconds_of(saved)
        for box in containers.read(io.BytesIO(now)).headers:
            width = box.width
            self.assertEqual(seconds.to_bytes(width, "big"), now[box.payload + 4:box.payload + 4 + width], box.type)

    def test_killed_after_its_last_byte_it_is_recorded_and_not_written_again(self):
        change, file_id = self.killed(cut_patch())
        self.assertEqual(in_place.NEW, self.against_its_record(file_id))
        self.assertEqual(self.as_written(change), self.read_bytes(self.clip_path))
        self.assert_written_again(change)

    def test_changed_by_another_program_meanwhile_it_is_a_conflict_never_written_over(self):
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        with open(self.clip_path, "ab") as handle:
            handle.write(b"\0\0\0\x08free")
        theirs = self.read_bytes(self.clip_path)
        self.assertEqual(in_place.NEITHER, self.against_its_record(file_id))
        self.start_again()
        self.assertEqual((["conflict"], theirs, []), (self.change_states(self.clip), self.read_bytes(self.clip_path),
                                                       self.tags_of(self.clip)))

    def test_a_share_that_stops_answering_half_way_leaves_it_to_be_settled_by_its_bytes_at_the_next_start(self):
        with cut_half_way(in_place.Away("the share did not answer")):
            result = self.tag("Trips/Coast")
        self.assertIn("did not answer", result.message())
        # Answering again by the time the write is settled (_after_failure, by its bytes: torn, put back), the clip is
        # as it was and taken out of its change; one still away stays `writing` for the next start (D3's
        # test_unreadable_at_the_next_start_it_stays_writing_with_its_record_and_the_change_is_let_go).
        self.assertEqual((0, self.original, [], []), (result.changed, self.read_bytes(self.clip_path),
                                                      self.change_states(self.clip), self.tags_of(self.clip)))
        self.assertEqual(0, self.start_again(), "nothing left to settle")

    def test_two_writes_of_one_clip_run_one_after_the_other(self):
        # The first write stops once its record is saved, until the second has started and waits for it.
        saved, tick, waited = threading.Event(), threading.Event(), []
        first = threading.current_thread()

        def reached(step):
            if step == file_changes.PATCH_SAVED and threading.current_thread() is first and not saved.is_set():
                saved.set()
                for _ in range(3000):
                    if file_changes._waiting:
                        break
                    tick.wait(0.01)
                waited.append(file_changes._waiting)
        second = threading.Thread(target=lambda: saved.wait(30) and self.tag("Trips/Harbour"))
        with mock.patch.object(file_changes, "_reached", side_effect=reached):
            second.start()
            self.tag("Trips/Coast")
            second.join(60)
        self.assertEqual([1], waited, "the second waited for the first, its record saved and its bytes not yet written")
        self.assertFalse(second.is_alive())
        self.assertEqual(["Trips/Coast", "Trips/Harbour"], self.read_now(self.clip_path)[1],
                         "the second planned from the clip the first left")
        self.assertEqual(["done", "done"], self.change_states(self.clip))

    def test_the_player_streams_the_clip_a_write_left_with_its_new_size(self):
        self.assertEqual(0, len(self.tag("Trips/Coast").errors))
        written = self.read_bytes(self.clip_path)
        reply = self.client.get("/library/api/video-file", query_string={"id": self.clip},
                                headers={"Range": "bytes=0-99"})
        self.assertEqual(206, reply.status_code, (reply.headers, reply.data[:300]))
        self.assertEqual(("bytes 0-99/%d" % len(written), written[:100]),
                         (reply.headers["Content-Range"], reply.data))


if __name__ == "__main__":
    unittest.main()
