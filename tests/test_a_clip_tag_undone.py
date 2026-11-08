"""A clip's tag save undone from History (DECISIONS 2026-11-05, M7): the undo is the same patch of the clip's XMP the save
was, and it is refused, in its rehearsal, wherever the save's writer would refuse it.

What the owner lives through: a tag is added to a clip, then History's Undo is pressed. The clip goes back to what it
held and its row follows; a clip whose QuickTime Subject is a Microsoft Xtra value that is not text (14 live .mp4
clips, counted 2026-11-07) goes back to an empty XMP:Subject and no tag, that value never read as one (#2662). After an update with writing clips
turned off, the undo is refused before anything is written, the change still `applied` and nothing marked a conflict, so
it can be undone once writing clips is on again (#2546: it was marked a conflict, `undone`, never to be retried).

Real ffmpeg clips and the real ExifTool (tests/test_a_clip_tagged_through_the_journal.AClipInTheLibrary). Fictional names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_tagged_through_the_journal as tagged  # noqa: E402

from tagpup.files import images  # noqa: E402
from tagpup.services import journal  # noqa: E402

EXIFTOOL = tagged.EXIFTOOL


class AClipTagUndone(tagged.ALibraryHoldingAClip):

    def tagged(self, photo_id, tag):
        reply = self.post("photos/bulk-tags", {"paths": [self.clip_path], "add_tags": [tag]})
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

    def test_with_writing_clips_turned_off_its_undo_is_refused_in_the_rehearsal_and_the_change_stays_applied(self):
        photo, clip = self.index(self.photo_path, self.clip_path)
        reply = self.post("photos/bulk-tags", {"paths": [self.photo_path, self.clip_path], "add_tags": ["Trips/Coast"]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        (change,), = self.vl.rows("SELECT DISTINCT change_id FROM change_files WHERE photo_id = ?", clip)
        written = self.read_bytes(self.clip_path), self.read_bytes(self.photo_path)
        with tagged.writing_clips(False):
            rehearsal = self.undo(change, apply=False)
            self.assertIn(images.CLIP_NOT_EDITED, rehearsal.refused or "", "the rehearsal names it and why")
            undone = self.undo(change)
        self.assertTrue(undone.refused, "nothing written, not even the photo beside it")
        self.assertEqual(written, (self.read_bytes(self.clip_path), self.read_bytes(self.photo_path)))
        self.assertEqual(("applied", ["done", "done"]), self.states(change), "no conflict, no `undone`")
        self.assertEqual(2, self.undo(change).changed, "undone once writing clips is on again")


if __name__ == "__main__":
    unittest.main()
