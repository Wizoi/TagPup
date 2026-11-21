"""A clip's tag save undone from History (DECISIONS 2026-11-05, M7): the undo is the same patch of the clip's XMP the save
was.

What the owner lives through: a tag is added to a clip, then History's Undo is pressed. The clip goes back to what it
held and its row follows; a clip whose QuickTime Subject is a Microsoft Xtra value that is not text (14 live .mp4
clips, counted 2026-11-07) goes back to an empty XMP:Subject and no tag, that value never read as one (#2662). (An undo the writer would refuse
is refused in its rehearsal, the change left applied, #2546: its one producer, writing clips turned off, went with the
switch, DECISIONS 2026-11-07.)

A clip's XMP box of any size is saved (owner, 2026-11-12): one holding a Premiere packet of about 200 KB, as the 1 live
box over 64 KB holds, is tagged and undone, the clip byte for byte as before; one whose patch is more than the journal
keeps (file_journal.PATCH_CAP: its record and the old bytes) is refused with its own sentence, nothing written.

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.AClipInTheLibrary). Fictional names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.services import journal  # noqa: E402
from tagpup.store import file_journal  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


class AClipTagUndone(tagged.ALibraryHoldingAClip):

    def tagged(self, photo_id, tag):
        reply = self.post("photos/write", {"op": "tags", "changes": {self.clip_path: {"add": [tag]}}})
        self.assertEqual(200, reply.status_code, reply.get_json())
        (change,), = self.vl.rows("SELECT DISTINCT change_id FROM change_files WHERE photo_id = ?", photo_id)
        return change

    def undo(self, change, apply=True):
        return journal.undo(self.vl.library, change, apply=apply, exiftool_path=EXIFTOOL)

    def states(self, change):
        status, = self.vl.rows("SELECT status FROM changes WHERE id = ?", change)
        return status[0], [state for state, in self.vl.rows("SELECT state FROM change_files WHERE change_id = ?", change)]

    def test_the_first_tag_of_a_clip_whose_quicktime_subject_is_not_text_is_undone(self):
        tagged.with_a_quicktime_subject_that_is_not_text(self.clip_path)
        clip, = self.index(self.clip_path)
        before = self.read_now(self.clip_path)
        self.assertEqual(([], []), before, "no tag, no XMP:Subject")
        change = self.tagged(clip, "Trips/Harbour")
        self.assertEqual((["Trips/Harbour"], ["Trips/Harbour"]), self.read_now(self.clip_path))
        rehearsal = self.undo(change, apply=False)
        self.assertEqual([], rehearsal.details["rehearsal"]["differences"], "the rehearsal says it can be put back")
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(before, self.read_now(self.clip_path), "its XMP:Subject is empty again, and no tag is read")
        self.assertEqual([], self.tags_of(clip), "and its row follows")
        self.assertEqual(("undone", ["undone"]), self.states(change))

    def with_a_premiere_packet(self, events):
        """The clip given a Premiere packet with `events` saves in its History (tests/test_xmp_packet_edits_what_it_owns.
        premiere) in a uuid box at its end; its row, as the indexer reads it, and its bytes."""
        with open(self.clip_path, "ab") as handle:
            handle.write(tagged.patched.xmp_box(tagged.patched.shapes.premiere(events)))
        clip, = self.index(self.clip_path)
        return clip, self.read_bytes(self.clip_path)

    def test_a_clip_whose_xmp_is_over_64_kb_is_tagged_and_undone_byte_for_byte(self):
        clip, before = self.with_a_premiere_packet(515)
        change = self.tagged(clip, "Trips/Harbour")
        self.assertEqual((["Trips/Harbour"], ["Trips/Harbour"]), self.read_now(self.clip_path))
        self.assertEqual(len(before), len(self.read_bytes(self.clip_path)), "the keyword took the packet's padding")
        undone = self.undo(change)
        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(before, self.read_bytes(self.clip_path), "the clip is byte for byte as it was")
        self.assertEqual([], self.tags_of(clip))
        self.assertEqual(("undone", ["undone"]), self.states(change))

    def test_a_clip_whose_patch_is_more_than_the_journal_keeps_is_refused_and_nothing_is_written(self):
        clip, before = self.with_a_premiere_packet(1600)
        reply = self.post("photos/write", {"op": "tags", "changes": {self.clip_path: {"add": ["Trips/Harbour"]}}})
        (error,) = reply.get_json()["errors"]
        self.assertIn(file_journal.PATCH_TOO_LARGE.split(" (")[0], error["why"])
        self.assertIn("at most 1,024 KB is kept). Nothing was written.", error["why"])
        self.assertEqual(before, self.read_bytes(self.clip_path), "not a byte written")
        self.assertEqual(([], []), (self.tags_of(clip), self.change_states(clip)), "its row withdrawn, no tag")


if __name__ == "__main__":
    unittest.main()
