"""Tag a photo, read it back from the file and the row, and undo it (scenario F1).

A person tags photos three ways -- Save on one photo, Smart Rename, and a tag added to a selection (what Apply All
Suggestions asks of change_tags) -- and expects the same of each: the file holds the keywords the app writes (whole
paths, a person filed under People, no loose parts), the row says what the file says in the shape the indexer records,
the write is one change in History, and undo puts back the file and the row. Real ExifTool on JPEGs made here; the
rows are the indexer's (photo_rows.add_read), never the helper under test.

Lifted: test_tag_write_is_journaled (#266), test_tag_write_follows_the_keyword_rules (#249; its mtime == compare is
store.photos.describes now), test_single_save_and_smart_rename_are_journaled (#266), test_save_photo_rename_is_journaled
(#298; its failing rename is test_save_reply_says_rename_failed's), test_a_save_is_one_session, test_a_saved_row_is_the_
indexers_shape (#251) and test_a_file_undo_waits_for_newer_changes. Each was broken in a scratch copy and this file
fails: change_tags writing the loose parts of a path (tag write), save_photo opening a second session (one session),
MetadataExtractor._structure dropping the bare names (the indexer's shape), file_changes._undoable skipping the
newer-change check (undo order), save_photo's rename done outside the journal (rename after a caption).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_a_file_holds_what_exiftool_kept import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, ExifToolSession, field_of, keywords_of, write_outside  # noqa: E402

from tagpup.files import exiftool_session  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import photos as photo_actions, tagging  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

PRESERVED = "XMP-xmpMM:PreservedFileName"


def as_list(value):
    return value if isinstance(value, list) else ([value] if value else [])


class TaggingAPhoto(ReadFilesCase):
    def row_stamp(self, path):
        return self.rows("SELECT mtime, size FROM photos WHERE path = ?", (path,))[0]

    def assert_row_describes_file(self, path):
        stat = os.stat(path)
        self.assertTrue(store_photos.describes(*self.row_stamp(path), (stat.st_mtime, stat.st_size)),
                        "the row does not describe the file it was saved from")

    def test_a_tag_added_to_a_selection_is_written_by_the_rules_and_undone(self):
        for row in [(1, "People", "People", None, 1), (2, "People/Rowan Thackeray", "Rowan Thackeray", 1, 1)]:
            self.execute("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face) VALUES (?, ?, ?, ?, ?)", row)
        a, b = self.make_read("a.jpg", ("Places/Harbour",)), self.make_read("b.jpg", ("Beach", "Relay"))
        captions = {path: field_of(path, "XMP:Description") for path in (a, b)}

        result = tagging.change_tags(self.library, [a, b], ["Activity/Rowing", "People/Rowan Thackeray"], [], EXIFTOOL)

        self.assertEqual([], result.errors)
        with ExifToolSession(executable=EXIFTOOL) as et:
            found = et.get_tags([a], tags=["XMP:Subject", "XMP:HierarchicalSubject"])[0]
        self.assertEqual({"Places/Harbour", "Activity/Rowing", "People/Rowan Thackeray"},
                         set(as_list(found.get("XMP:HierarchicalSubject"))))
        for loose in ("Activity", "Rowing", "People", "Rowan Thackeray"):
            self.assertNotIn(loose, as_list(found.get("XMP:Subject")), "a path was also written in parts")
        self.assertEqual(sorted(keywords_of(a)), self.indexed(a), "the row says what the file says")
        self.assertIn("People/Rowan Thackeray", self.indexed(a))
        self.assertEqual(["Activity/Rowing", "Beach", "People/Rowan Thackeray", "Relay"], keywords_of(b))
        self.assert_row_describes_file(a)
        change = self.last_change()
        self.assertEqual([(tagging.ADD_TO_ALL, "applied")],
                         self.rows("SELECT operation, status FROM changes WHERE id = ?", (change,)))
        self.assertEqual(["done", "done"], self.states(change))
        self.assertEqual([], [name for name in os.listdir(self.folder) if name.endswith("_original")],
                         "ExifTool kept a copy beside a file: the journal is the way back")

        undone = self.undo(change)

        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual(["Places/Harbour"], keywords_of(a))
        self.assertEqual(["Beach", "Relay"], keywords_of(b))
        self.assertEqual(["Beach", "Relay"], self.indexed(b))
        self.assertEqual(captions, {path: field_of(path, "XMP:Description") for path in (a, b)})
        self.assert_row_describes_file(b)

    def test_a_photo_cut_short_is_skipped_and_said(self):
        a, b = self.make_read("a.jpg"), self.make_read("b.jpg")
        with open(b, "rb") as handle:
            whole = handle.read()
        with open(b, "wb") as handle:
            handle.write(whole[:-20])
        result = tagging.change_tags(self.library, [a, b], ["Activity/Rowing"], [], EXIFTOOL)
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(a))
        self.assertEqual(["Beach"], keywords_of(b))
        self.assertEqual((1, [b]), (result.details["skipped_damaged"], [path for path, _why in result.skipped]))

    def test_one_save_is_one_change_one_session_a_row_as_the_indexer_makes_it_and_undone(self):
        a = self.make_read("a.jpg")
        real = exiftool_session.ExifToolSession
        sessions, reads, writes = [], [], []

        def counted(*args, **kwargs):
            session = real(*args, **kwargs)
            sessions.append(session)
            get, put = real.get_tags, real.set_tags
            session.get_tags = lambda *a, **k: reads.append(a) or get(session, *a, **k)
            session.set_tags = lambda *a, **k: writes.append(a) or put(session, *a, **k)
            return session

        with mock.patch.object(exiftool_session, "ExifToolSession", side_effect=counted):
            result = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"],
                                        "2019-06-15T10:30:00", EXIFTOOL, "")

        self.assertEqual((True, 1), (result.ok, result.changed), result.message())
        self.assertEqual((1, 2, 1), (len(sessions), len(reads), len(writes)), "sessions, reads, writes of one save")
        change = self.last_change()
        self.assertEqual(change, result.details["change"])
        self.assertEqual(("save photo", "applied", ["done"]),
                         (self.rows("SELECT operation FROM changes WHERE id = ?", (change,))[0][0],
                          self.status(change), self.states(change)))
        self.assertEqual(["Beach", "Places/Quay"], keywords_of(a))
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (a,))[0][0])
        self.assertEqual(MetadataExtractor(exiftool_path=EXIFTOOL).batch_read([a])[0]["raw_metadata"], raw)
        self.assertEqual("Quay at dusk", raw["Description"])
        self.assertTrue(any(":" not in key for key in raw), "a read records the bare names too")
        written = os.stat(a).st_mtime_ns
        again = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"],
                                   "2019-06-15T10:30:00", EXIFTOOL, "")
        self.assertEqual((True, 0, None, written), (again.ok, again.changed, again.details["change"],
                                                    os.stat(a).st_mtime_ns), "a file holding it all was written")

        undone = self.undo(change)

        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertIsNone(field_of(a, "XMP:Description"))
        self.assertEqual(["Beach"], self.indexed(a))

    def test_a_caption_that_renames_the_photo_is_a_change_that_undo_reverses_with_the_row(self):
        a = self.make_read("IMG_0001.jpg")
        renamed = photo_actions.smart_rename(self.library, [a], "Regatta", "{grouping} - {index}",
                                             EXIFTOOL).details["updated_paths"][a]
        result = tagging.save_photo(self.library, renamed, "Start", ["Beach"], None, EXIFTOOL,
                                    "{grouping} - {index} - {caption}")
        new = os.path.join(self.folder, "Regatta - 1 - Start.jpg")
        self.assertEqual((new, True), (result.details["new_path"], result.details["renamed"]))
        change = self.rows("SELECT change_id FROM change_files WHERE new_path = ?", (new,))[0][0]
        self.assertEqual(("done", [new]), (self.rows("SELECT state FROM change_files WHERE new_path = ?", (new,))[0][0],
                                           [r[0] for r in self.rows("SELECT path FROM photos")]))
        self.assertEqual(1, self.undo(change).changed)
        self.assertEqual([renamed], [r[0] for r in self.rows("SELECT path FROM photos")])
        self.assertEqual(["Regatta - 1.jpg"], os.listdir(self.folder))


class TheUndoOrder(ReadFilesCase):
    """A file undo waits for a newer change of the same photo, naming it, and then goes through in turn."""

    def change_named(self, operation):
        return self.rows("SELECT MAX(id) FROM changes WHERE operation = ?", (operation,))[0][0]

    def test_a_tag_change_after_the_kept_name_refuses_the_undo_until_it_is_undone(self):
        a = self.make_read("a.jpg")
        kept = photo_actions.preserve_names(self.library, [a], EXIFTOOL).details["change"]
        tagged = self.add([a]).details["change"]
        refused = self.undo(kept)
        self.assertEqual(0, refused.changed)
        self.assertIn("change %d (add to all selected)" % tagged, refused.message())
        self.assertEqual("a.jpg", field_of(a, PRESERVED))
        self.assertEqual((1, 1), (self.undo(tagged).changed, self.undo(kept).changed))
        self.assertIsNone(field_of(a, PRESERVED))

    def test_smart_rename_keeps_each_original_name_as_its_own_change_that_waits_for_the_rename(self):
        a, b = self.make_read("IMG_0001.jpg"), self.make_read("IMG_0002.jpg")
        write_outside(b, {PRESERVED: "DSC_0100.JPG"})
        result = photo_actions.smart_rename(self.library, [a, b], "Regatta", "{grouping} - {index}", EXIFTOOL)
        self.assertEqual((True, 2), (result.ok, result.changed))
        kept, renamed = self.change_named("smart rename: original names"), self.change_named("smart rename")
        files = self.rows("SELECT path, fields_before, fields_after, state FROM change_files WHERE change_id = ?",
                          (kept,))
        self.assertEqual([(a, {PRESERVED: []}, {PRESERVED: ["IMG_0001.jpg"]}, "done")],
                         [(p, json.loads(x), json.loads(y), s) for p, x, y, s in files])
        moved = result.details["updated_paths"]
        self.assertEqual("DSC_0100.JPG", field_of(moved[b], PRESERVED), "a name kept already was written over")
        refused = self.undo(kept)
        self.assertEqual(0, refused.changed)
        self.assertIn("change %d (smart rename)" % renamed, refused.message())
        self.assertEqual((2, 1), (self.undo(renamed).changed, self.undo(kept).changed))
        self.assertIsNone(field_of(a, PRESERVED))


if __name__ == "__main__":
    unittest.main()
