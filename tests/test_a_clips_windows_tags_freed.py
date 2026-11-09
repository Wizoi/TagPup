"""A clip's Windows Tags moved into its XMP, its Microsoft Xtra freed in the same patch, and undone from History
(DECISIONS 2026-11-09).

What the owner lives through: a clip tagged long ago in Windows Photo Gallery holds its Tags in the WM/Category entry of
its moov/udta/Xtra box, which TagPup never reads as tags (DECISIONS 2026-11-07). The one-time move reads that Category
(fields.CLIP_CATEGORY, asked as Microsoft:Category and answered by ExifTool as QuickTime:Category: fields.read_key) beside
the clip's keyword fields, plans its tags with the Category's added and the Category cleared, and journals it: the tags
land in the clip's XMP, the Xtra's four type bytes become `free` in the same patch, the clip's row reads the tags, and
the journal records the Category it held and the [] it holds. History's Undo gives the Xtra back (the four bytes) and
takes the tags off again, the clip as before but for the XMP box the move appended. A second move finds nothing to free
and writes nothing; a clip with no Xtra, or an empty one, writes nothing and says so (no change, nothing changed).

A real one-second clip made by the machine's ffmpeg, given an Xtra as Windows writes one
(tests/test_a_clip_patched_in_place.with_windows_tags), indexed by the indexer's own read, written through
file_changes.write_fields and undone through Flask's test client in a home of its own
(tests/test_a_clip_tagged_through_the_journal.ALibraryHoldingAClip). Skipped, saying so, without ffmpeg or ExifTool.
Fictional tags only.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_a_clip_patched_in_place as patched  # noqa: E402
from test_a_clip_tagged_through_the_journal import EXIFTOOL, ALibraryHoldingAClip  # noqa: E402

from tagpup.core import fields  # noqa: E402
from tagpup.files import containers  # noqa: E402
from tagpup.services import bulk_edit, file_changes  # noqa: E402

WINDOWS_TAGS = patched.WINDOWS_TAGS
#: What the move reads of each clip: its Windows Tags and its keyword fields.
READ = [fields.CLIP_CATEGORY, *fields.keyword_fields([], [])]
OPERATION = bulk_edit.OPERATIONS[bulk_edit.TAGS]


def moved(_path, held):
    """The move's plan of one clip from what it holds: its tags with its Windows Tags added, its Category cleared."""
    tags = list(held.get("XMP:Subject", []))
    tags += [tag for tag in held.get(fields.CLIP_CATEGORY, []) if tag not in tags]
    return file_changes.Plan(after={**patched.keywords(tags), fields.CLIP_CATEGORY: []})


class AClipsWindowsTagsFreed(ALibraryHoldingAClip):

    def move(self):
        """The move of the clip's Windows Tags through the journal; its Result."""
        return file_changes.write_fields(self.vl.library, OPERATION, EXIFTOOL, [self.clip_path], READ, moved)

    def windows_tags(self):
        """(the XMP:Subject, the Windows Tags) ExifTool reads of the clip now, the Category asked as the move asks it."""
        return patched.windows_tags_read(self.clip_path)

    def xtra(self):
        with open(self.clip_path, "rb") as handle:
            return containers.read(handle).xtra

    def journaled(self, change):
        """(fields_before, fields_after, state) of the change's one file, as the journal recorded them."""
        (before, after, state), = self.vl.rows(
            "SELECT fields_before, fields_after, state FROM change_files WHERE change_id = ?", change)
        return json.loads(before), json.loads(after), state

    def undone(self, change):
        """History's Undo of `change` as the dialog sends it: its rehearsal, then the undo itself."""
        rehearsal = self.post("history/%d/undo" % change, {})
        self.assertEqual(200, rehearsal.status_code, rehearsal.data)
        self.assertEqual([], rehearsal.get_json()["differences"], "the rehearsal says it can be put back")
        reply = self.post("history/%d/undo" % change, {"apply": True})
        self.assertEqual(200, reply.status_code, reply.data)
        self.assertEqual((1, []), (reply.get_json()["changed"], reply.get_json()["errors"]))

    def assert_nothing_written(self, result, before):
        self.assertEqual((0, [], None), (result.changed, result.errors, result.details["change"]),
                         "no change: nothing to free, and the result says nothing was written")
        self.assertEqual(before, self.read_bytes(self.clip_path))
        self.assertEqual([], self.vl.rows("SELECT id FROM change_files"), "nothing journaled")

    def test_windows_tags_move_into_xmp_the_xtra_is_freed_journaled_and_undone_from_history(self):
        patched.with_windows_tags(self.clip_path, WINDOWS_TAGS)
        clip, = self.index(self.clip_path)
        original = self.read_bytes(self.clip_path)
        xtra = self.xtra()
        self.assertEqual(([], list(WINDOWS_TAGS)), self.windows_tags(), "Windows' Tags, no XMP")
        self.assertEqual([], self.tags_of(clip), "the index reads no tag of the Xtra")

        result = self.move()
        self.assertEqual((1, []), (result.changed, result.errors))
        change = result.details["change"]
        self.assertEqual((list(WINDOWS_TAGS), None), self.windows_tags(), "the tags in XMP, Windows' Tags gone")
        self.assertIsNone(self.xtra(), "no Xtra left")
        after = self.read_bytes(self.clip_path)
        self.assertEqual(b"free", after[xtra.offset + 4:xtra.offset + 8], "the Xtra's four type bytes freed")
        self.assertEqual(original[:xtra.offset + 4] + original[xtra.offset + 8:],
                         after[:xtra.offset + 4] + after[xtra.offset + 8:len(original)],
                         "no other byte the clip held changed; the XMP appended after them")
        self.assertEqual((list(WINDOWS_TAGS), list(WINDOWS_TAGS)), self.read_now(self.clip_path))
        self.assertEqual(sorted(WINDOWS_TAGS), sorted(self.tags_of(clip)), "the row reads the tags")
        before, written, state = self.journaled(change)
        self.assertEqual(("done", list(WINDOWS_TAGS), []),
                         (state, before[fields.CLIP_CATEGORY], written[fields.CLIP_CATEGORY]),
                         "the journal records the Windows Tags the clip held and the none it holds")
        self.assertEqual(([], list(WINDOWS_TAGS)), (before["XMP:Subject"], written["XMP:Subject"]))

        again = self.move()
        self.assertEqual((0, [], None), (again.changed, again.errors, again.details["change"]),
                         "a second move finds nothing to free and writes nothing")
        self.assertEqual(after, self.read_bytes(self.clip_path))

        self.undone(change)
        self.assertEqual(([], list(WINDOWS_TAGS)), self.windows_tags(), "the undo gives Windows' Tags back")
        self.assertEqual(xtra, self.xtra(), "the Xtra where it was, its size as it was")
        self.assertEqual(original, self.read_bytes(self.clip_path)[:len(original)],
                         "every byte the clip held is as it was; the XMP box the move appended stays, emptied")
        self.assertEqual([], self.tags_of(clip), "the tags go from the row too")
        self.assertEqual("undone", self.journaled(change)[2])

    def test_a_windows_tag_a_read_spells_otherwise_is_given_back_by_the_undo_not_marked_a_conflict(self):
        """A Windows tag ExifTool answers otherwise than the Xtra spells it ("1.50", answered as the JSON number 1.5 and
        kept by the journal as "1.5") beside one ending in a blank (which ExifTool keeps): the undo finds the freed
        Xtra's texts as a read gives them (fields.same_read) and gives it back. Matched by list equality, the undo found
        no box holding them and marked the file a conflict (#2802). None of the 7,293 live texts is either (counted
        2026-11-09); this fails on the code before the fix."""
        patched.with_windows_tags(self.clip_path, ("Trips/Coast ", "1.50"))
        self.index(self.clip_path)
        original = self.read_bytes(self.clip_path)
        result = self.move()
        self.assertEqual((1, []), (result.changed, result.errors))
        change = result.details["change"]
        self.assertEqual(["Trips/Coast ", "1.5"], self.journaled(change)[0][fields.CLIP_CATEGORY],
                         "the journal keeps the texts as ExifTool answered them")
        self.undone(change)
        self.assertEqual("undone", self.journaled(change)[2], "given back, not a conflict")
        self.assertEqual(original, self.read_bytes(self.clip_path)[:len(original)], "the Xtra as it was")

    def test_a_clip_with_no_xtra_writes_nothing_and_says_so(self):
        self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        self.assert_nothing_written(self.move(), before)

    def test_a_clip_with_an_empty_xtra_writes_nothing_and_its_xtra_stays(self):
        patched.with_windows_tags(self.clip_path, ())
        self.index(self.clip_path)
        before = self.read_bytes(self.clip_path)
        self.assertIsNotNone(self.xtra(), "an empty Xtra, as 10 live clips hold")
        self.assert_nothing_written(self.move(), before)
        self.assertIsNotNone(self.xtra(), "and it stays an Xtra")


if __name__ == "__main__":
    unittest.main()
