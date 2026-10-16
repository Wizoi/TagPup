"""Tag a photo, read it back from the file and the row, and undo it (scenario F1).

A person tags photos three ways -- Save on one photo, Smart Rename, and a tag added to a selection (what Apply All
Suggestions asks of change_tags) -- and expects the same of each: the file holds the keywords the app writes (whole
paths, a person filed under People, no loose parts), the row says what the file says in the shape the indexer records,
the write is one change in History, and undo puts back the file and the row. Real ExifTool on JPEGs made here; the
rows are the indexer's (photo_rows.add_read), never the helper under test.

A JPEG whose file stops short takes a tag as any photo does, its picture data untouched (DECISIONS 2026-10-15: the
whole-file scan that refused it is gone, and the tests of that refusal with it -- test_writes_refuse_cut_short_photos,
and the cut-short arms of test_bulk_edits, test_rotate_is_journaled and this file); a photo ExifTool cannot write is
an error of its own in a selection, and the rest are written, journaled and undone.

Lifted: test_tag_write_is_journaled (#266), test_tag_write_follows_the_keyword_rules (#249; its mtime == compare is
store.photos.describes now), test_single_save_and_smart_rename_are_journaled (#266), test_save_photo_rename_is_journaled
(#298; its failing rename is ASaveFromThePage's), test_a_save_is_one_session, test_a_saved_row_is_the_
indexers_shape (#251) and test_a_file_undo_waits_for_newer_changes. Each was broken in a scratch copy and this file
fails: change_tags writing the loose parts of a path (tag write), save_photo opening a second session (one session),
MetadataExtractor._structure dropping the bare names (the indexer's shape), file_changes._undoable skipping the
newer-change check (undo order), save_photo's rename done outside the journal (rename after a caption).

Lifted: tests/test_tagpup_server_api.py's TestSaveMetadata and TestBulkTags (what the save and bulk-tags routes left on the
disk: a title, a tag whole and not in pieces, a deep tag, a clear, Date Taken, an add that keeps the keywords the photo
carries, a remove) -- the routes call these services and the pages' flow against the real routes is
test_pages_against_the_real_server's. Broken in a scratch copy and this file fails: change_tags dropping the keywords a
photo carries before it adds (an add keeps them), save_photo writing no Date Taken (one save), a remove that takes nothing off.

Lifted (#1475): test_service_save_photo (a save's sub-second Date Taken in the one write, the named face kept among the
photo's people, a new tag that may not be set refusing the save, the faces following a rename after a caption, a write
that fails answered in the Result and not raised, #299; its ExifTool was a stand-in, here it is real; #266, #276) and
test_save_reply_says_rename_failed (#884: a caption saved whose rename then fails says so in the reply, and the page's
fixture of those replies, tests/frontend/fixtures/save_replies.json, is written here: ASaveFromThePage). Broken in a scratch
copy and this file fails (2026-10-15): date_taken_fields writing no SubSecTimeOriginal, save_photo setting a tag without its
check, the save route dropping `rename_failed`.
"""
import json
import os
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import damaged_photos  # noqa: E402
import face_rows  # noqa: E402
import photo_rows  # noqa: E402
import test_stale_record_precondition as S  # noqa: E402
from test_a_file_holds_what_exiftool_kept import ReadFilesCase  # noqa: E402
from test_file_journal import EXIFTOOL, ExifToolSession, field_of, keywords_of, write_outside  # noqa: E402

from tagpup.files import exiftool_session, names  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import file_changes, photos as photo_actions, sync, tagging  # noqa: E402
from tagpup.store import db, photos as store_photos  # noqa: E402

PRESERVED = "XMP-xmpMM:PreservedFileName"

#: The JPEG marker the compressed picture data starts after (start of scan).
START_OF_SCAN = bytes([0xFF, 0xDA])


def picture_of(path):
    """The bytes of a JPEG from its start-of-scan marker to its end: the picture, which a write of tags never touches."""
    with open(path, "rb") as handle:
        data = handle.read()
    return data[data.index(START_OF_SCAN):]


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

    def test_a_remove_takes_off_only_what_it_names_and_a_save_with_no_tags_clears_the_photo(self):
        a, b = self.make_read("a.jpg", ("Beach", "Relay")), self.make_read("b.jpg", ("Beach",))

        result = tagging.change_tags(self.library, [a, b], [], ["Beach"], EXIFTOOL)

        self.assertEqual([], result.errors)
        self.assertEqual((["Relay"], []), (keywords_of(a), keywords_of(b)))
        self.assertEqual(["Relay"], self.indexed(a))
        self.assertTrue(tagging.save_photo(self.library, a, "", [], None, EXIFTOOL, "").ok)
        self.assertEqual([], keywords_of(a))

    def damaged(self, name, shape):
        """A photo of noise holding Beach, then damaged as a copy that stopped leaves it -- `cut` short, or with its
        second half `zeros` in a file of its full size -- and its row as the indexer leaves it after reading it."""
        path = os.path.join(self.folder, name)
        damaged_photos.whole_jpeg(path, size=(480, 360))
        write_outside(path, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]})
        with open(path, "rb") as handle:
            whole = handle.read()
        half = len(whole) // 2
        with open(path, "wb") as handle:
            handle.write(whole[:-damaged_photos.CUT] if shape == "cut" else whole[:half] + bytes(len(whole) - half))
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"]})
            conn.commit()
        finally:
            conn.close()
        return path

    def test_a_photo_cut_short_takes_a_tag_and_keeps_its_picture_and_undo_takes_it_off(self):
        for shape in ("cut", "zeros"):
            with self.subTest(shape):
                self.takes_a_tag(self.damaged("%s.jpg" % shape, shape), self.make_read("whole %s.jpg" % shape))

    def takes_a_tag(self, cut, whole):
        picture = picture_of(cut)

        result = tagging.change_tags(self.library, [cut, whole], ["Activity/Rowing"], [], EXIFTOOL)

        self.assertEqual((2, [], []), (result.changed, result.errors, result.skipped), result.message())
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(cut), "read back from the file")
        self.assertEqual(["Activity/Rowing", "Beach"], self.indexed(cut), "the row says what the file says")
        self.assertEqual(picture, picture_of(cut), "the picture data after the start of scan changed")
        self.assert_row_describes_file(cut)
        saved = tagging.save_photo(self.library, cut, "", ["Activity/Rowing", "Beach", "Places/Quay"], None, EXIFTOOL, "")
        self.assertEqual((True, 1), (saved.ok, saved.changed), saved.message())
        self.assertEqual(["Activity/Rowing", "Beach", "Places/Quay"], keywords_of(cut))
        self.assertEqual(picture, picture_of(cut))

        self.assertEqual(1, self.undo(self.last_change()).changed)
        undone = self.undo(result.details["change"])

        self.assertEqual((2, []), (undone.changed, undone.errors))
        self.assertEqual((["Beach"], ["Beach"]), (keywords_of(cut), self.indexed(cut)))
        self.assertEqual(picture, picture_of(cut))

    def test_a_photo_exiftool_cannot_write_is_its_own_error_and_the_rest_are_written_and_undone(self):
        """A read-only file (copied off a disc, say): ExifTool reads it and fails the write. A bulk edit goes on past it."""
        a, b, c = self.make_read("a.jpg"), self.make_read("b.jpg"), self.make_read("c.jpg")
        os.chmod(b, stat.S_IREAD)
        self.addCleanup(self.writable_again)
        with open(b, "rb") as handle:
            held = handle.read()
        row = self.rows("SELECT tags, mtime, size FROM photos WHERE path = ?", (b,))

        result = tagging.change_tags(self.library, [a, b, c], ["Activity/Rowing"], [], EXIFTOOL, stop_at_first_error=False)

        self.assertEqual(2, result.changed, result.message())
        self.assertEqual([b], [path for path, _why in result.errors], "the photo it failed, by itself")
        self.assertIn("ExifTool", str(result.errors[0][1]))
        for path in (a, c):
            self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(path))
            self.assertEqual(["Activity/Rowing", "Beach"], self.indexed(path))
        with open(b, "rb") as handle:
            self.assertEqual(held, handle.read(), "the file it could not write changed")
        self.assertEqual(row, self.rows("SELECT tags, mtime, size FROM photos WHERE path = ?", (b,)), "its row changed")
        change = result.details["change"]
        self.assertEqual(["done", "done"], self.states(change), "the failed file is not left in the change")
        self.assertEqual("applied", self.status(change))

        undone = self.undo(change)

        self.assertEqual((2, []), (undone.changed, undone.errors))
        for path in (a, b, c):
            self.assertEqual(["Beach"], keywords_of(path))
            self.assertEqual(["Beach"], self.indexed(path))

    def writable_again(self):
        """ExifTool's temporary file of a failed write carries the photo's read-only attribute: the home goes after."""
        for name in os.listdir(self.folder):
            os.chmod(os.path.join(self.folder, name), stat.S_IREAD | stat.S_IWRITE)

    def test_a_write_exiftool_fails_after_deleting_the_original_leaves_its_temporary_file_alone(self):
        """#1410: ExifTool can fail after deleting the original ("Error renaming temporary file after deleting
        original"), and then its temporary file is the photo's only copy: nothing here deletes it. Stood in for: the
        one command of b's write does what that failure does."""
        a, b = self.make_read("a.jpg"), self.make_read("b.jpg")
        with open(b, "rb") as handle:
            held = handle.read()
        temporary = b + "_exiftool_tmp"
        real = ExifToolSession.set_tags

        def failing_after_deleting(session, files, *args, **kwargs):
            if files == [b]:
                with open(temporary, "wb") as handle:
                    handle.write(held)
                os.remove(b)
                raise exiftool_session.ExifToolFailed(
                    1, b"", b"Error: Error renaming temporary file after deleting original - " + b.encode(), files)
            return real(session, files, *args, **kwargs)

        with mock.patch.object(ExifToolSession, "set_tags", failing_after_deleting):
            result = tagging.change_tags(self.library, [a, b], ["Activity/Rowing"], [], EXIFTOOL,
                                         stop_at_first_error=False)

        self.assertEqual(([b], 1), ([path for path, _why in result.errors], result.changed), result.message())
        self.assertIn("after deleting original", str(result.errors[0][1]))
        with open(temporary, "rb") as handle:
            self.assertEqual(held, handle.read(), "the only copy of the photo was deleted")
        self.assertEqual(["Activity/Rowing", "Beach"], keywords_of(a))
        # b's file is not what the plan read now (it is gone): a conflict, which nothing settles by writing it.
        self.assertEqual(["done", "conflict"], self.states(result.details["change"]))

    def test_one_save_is_one_change_one_session_a_row_as_the_indexer_makes_it_and_undone(self):
        a = self.make_read("a.jpg")
        db.write_with_connection(self.library.path, lambda conn: face_rows.add_face(conn, a, name="Rowan Thackeray",
                                                                                 name_source="manual"))
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
            result = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay/Pier"],
                                        "2019-06-15T10:30:00", EXIFTOOL, "")

        self.assertEqual((True, 1), (result.ok, result.changed), result.message())
        self.assertEqual("2019:06:15 10:30:00", str(field_of(a, "EXIF:DateTimeOriginal")))
        self.assertEqual((1, 2, 1), (len(sessions), len(reads), len(writes)), "sessions, reads, writes of one save")
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            self.assertEqual(["Rowan Thackeray"], face_rows.people_of(conn, a), "the named face was dropped from the people")
        finally:
            conn.close()
        change = self.last_change()
        self.assertEqual(change, result.details["change"])
        self.assertEqual(("save photo", "applied", ["done"]),
                         (self.rows("SELECT operation FROM changes WHERE id = ?", (change,))[0][0],
                          self.status(change), self.states(change)))
        self.assertEqual(["Beach", "Places/Quay/Pier"], keywords_of(a), "a deep tag whole, no level of it alone")
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (a,))[0][0])
        self.assertEqual(MetadataExtractor(exiftool_path=EXIFTOOL).batch_read([a])[0]["raw_metadata"], raw)
        self.assertEqual("Quay at dusk", raw["Description"])
        self.assertTrue(any(":" not in key for key in raw), "a read records the bare names too")
        written = os.stat(a).st_mtime_ns
        again = tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay/Pier"],
                                   "2019-06-15T10:30:00", EXIFTOOL, "")
        self.assertEqual((True, 0, None, written), (again.ok, again.changed, again.details["change"],
                                                    os.stat(a).st_mtime_ns), "a file holding it all was written")

        undone = self.undo(change)

        self.assertEqual((1, []), (undone.changed, undone.errors))
        self.assertEqual(["Beach"], keywords_of(a))
        self.assertIsNone(field_of(a, "XMP:Description"))
        self.assertEqual(["Beach"], self.indexed(a))

    def test_a_save_takes_a_date_to_the_hundredth_refuses_a_tag_it_may_not_set_and_answers_a_write_that_fails(self):
        a = self.make_read("a.jpg")
        refused = tagging.save_photo(self.library, a, "Start", ["Beach", "Places|Harbour"], None, EXIFTOOL, "")
        self.assertIn('"|"', refused.refused)
        self.assertEqual((["Beach"], None), (keywords_of(a), field_of(a, "XMP:Description")), "a refused save wrote")
        timed = tagging.save_photo(self.library, a, "Start", ["Beach"], "2019-06-15T10:30:00.25", EXIFTOOL, "")
        self.assertTrue(timed.ok, timed.message())
        self.assertEqual("25", str(field_of(a, "EXIF:SubSecTimeOriginal")), "the hundredths in the same write")
        os.chmod(a, stat.S_IREAD)
        self.addCleanup(self.writable_again)
        failed = tagging.save_photo(self.library, a, "Finish", ["Beach"], None, EXIFTOOL, "")
        # #299: the route answers from the Result: a write that fails is in it, not raised.
        self.assertEqual((False, 0, None), (failed.ok, failed.changed, failed.refused))
        self.assertIn("ExifTool", failed.message())

    def test_a_save_stopped_once_its_file_is_done_leaves_the_row_whole_as_the_indexer_makes_it(self):
        """#1368: a save's row is recorded once, whole and stamped, in the transaction that marks its file done. It was
        recorded a second time after the write, so a save stopped in between left a row of the fields written alone."""
        a = self.make_read("a.jpg")

        def stopped(step):
            if step == "file recorded":
                raise KeyboardInterrupt("the server was closed")

        with mock.patch.object(file_changes, "_reached", side_effect=stopped), self.assertRaises(KeyboardInterrupt):
            tagging.save_photo(self.library, a, "Quay at dusk", ["Beach", "Places/Quay"], "2019-06-15T10:30:00", EXIFTOOL, "")

        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (a,))[0][0])
        self.assertEqual(MetadataExtractor(exiftool_path=EXIFTOOL).batch_read([a])[0]["raw_metadata"], raw)
        self.assertEqual(["Beach", "Places/Quay"], self.indexed(a))
        self.assert_row_describes_file(a)

    def test_a_photo_given_an_identity_elsewhere_is_saved_then_renamed_elsewhere_and_sync_follows_it(self):
        """#1469: a save records its row from the file read back whole, its DocumentID column too. The column was set
        only when a DocumentID was written, so a photo another program gave one after it was indexed kept a NULL
        column; renamed by another program, sync found no row to follow it by and its row was left missing."""
        a = self.make_read("IMG_0001.jpg")
        write_outside(a, {"XMP-xmpMM:DocumentID": "xmp.did:5b2c-quay"})
        photo_id = self.rows("SELECT id FROM photos WHERE path = ?", (a,))[0][0]

        self.assertTrue(tagging.save_photo(self.library, a, "Quay at dusk", ["Beach"], None, EXIFTOOL, "").ok)

        self.assertEqual([("xmp.did:5b2c-quay",)], self.rows("SELECT document_id FROM photos WHERE path = ?", (a,)),
                         "the row took the file whole but not its identity")
        renamed = os.path.join(self.folder, "Quay.jpg")
        os.rename(a, renamed)   # another program renames it

        result = sync.sync(self.library, folder=self.folder, apply=True, exiftool_path=EXIFTOOL)

        self.assertEqual([(photo_id, renamed)], self.rows("SELECT id, path FROM photos"), result.message())

    def test_a_caption_that_renames_the_photo_is_a_change_that_undo_reverses_with_the_row(self):
        a = self.make_read("IMG_0001.jpg")
        renamed = photo_actions.smart_rename(self.library, [a], "Regatta", "{grouping} - {index}",
                                             EXIFTOOL).details["updated_paths"][a]
        db.write_with_connection(self.library.path, lambda conn: face_rows.add_face(conn, renamed, name="Rowan Thackeray",
                                                                                 name_source="manual"))
        result = tagging.save_photo(self.library, renamed, "Start", ["Beach"], None, EXIFTOOL,
                                    "{grouping} - {index} - {caption}")
        new = os.path.join(self.folder, "Regatta - 1 - Start.jpg")
        self.assertEqual((new, True), (result.details["new_path"], result.details["renamed"]))
        self.assertEqual([(new,)], self.rows("SELECT p.path FROM faces f JOIN photos p ON p.id = f.photo_id"),
                         "the face stayed at the old name")
        change = self.rows("SELECT change_id FROM change_files WHERE new_path = ?", (new,))[0][0]
        self.assertEqual(("done", [new]), (self.rows("SELECT state FROM change_files WHERE new_path = ?", (new,))[0][0],
                                           [r[0] for r in self.rows("SELECT path FROM photos")]))
        self.assertEqual(1, self.undo(change).changed)
        self.assertEqual([renamed], [r[0] for r in self.rows("SELECT path FROM photos")])
        self.assertEqual(["Regatta - 1.jpg"], os.listdir(self.folder))


#: The replies of a save the page test reads (tests/frontend/rename-failed.test.mjs).
SAVE_REPLIES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "fixtures", "save_replies.json")


@unittest.skipIf(S.EXIFTOOL is None, "ExifTool not installed")
class ASaveFromThePage(S.Fresh):
    """A caption saved on a Smart-Renamed photo whose rename then fails says so (#884): the save wrote the caption, the
    follow-up rename failed (a locked file, a held share), and the reply said only `success`. The reply carries
    `rename_failed`, and the page's test answers from the replies written here."""

    def smart_renamed(self, seed=1):
        path, photo_id = self.make("Regatta - 1.jpg", seed, ["Trips/Coast"])
        S.write_into(path, **{PRESERVED: "IMG_0001.jpg"})
        return path, self.record(photo_id)

    def test_a_locked_file_keeps_its_name_and_the_reply_says_why(self):
        path, record = self.smart_renamed()
        refused = names.RenameFailed("The file is open in another program (held by Viewer)", [])
        with mock.patch("tagpup.files.names.rename_all", side_effect=refused):
            reply = self.save(record, title="Start line")
        body = reply.get_json()
        self.assertEqual((200, True, path), (reply.status_code, body["success"], body["new_path"]), reply.data)
        self.assertIn("held by Viewer", body["rename_failed"])
        self.assertEqual("Start line", S.caption_in(path))

    def test_a_save_that_renames_or_attempts_no_rename_has_no_warning(self):
        path, record = self.smart_renamed()
        taken = os.path.join(self.folder, "Regatta - 1 - Start line.jpg")
        S.damaged_photos.whole_jpeg(taken, seed=77)       # the name the caption wants is taken: a free one is chosen
        renamed = self.save(record, title="Start line").get_json()
        self.assertNotIn("rename_failed", renamed)
        self.assertTrue(renamed["new_path"].endswith("Regatta - 1 - Start line_1.jpg"), renamed["new_path"])
        plain, plain_id = self.make("IMG_0002.jpg", 3, ["Trips/Coast"])
        reply = self.save(self.record(plain_id), title="A caption").get_json()
        self.assertNotIn("rename_failed", reply)
        self.assertEqual(plain, reply["new_path"])

    def test_the_replies_the_page_reads_are_written_for_its_test(self):
        path, record = self.smart_renamed()
        refused = names.RenameFailed("The file is open in another program (held by Viewer)", [])
        found = {}
        with mock.patch("tagpup.files.names.rename_all", side_effect=refused):
            found["rename_failed"] = self.save(record, title="Start line").get_json()
        plain, plain_id = self.make("IMG_0002.jpg", 3, ["Trips/Coast"])
        found["no_rename"] = self.save(self.record(plain_id), title="A caption").get_json()
        for each in found.values():
            each.update(mtime=1, size=1, new_path=os.path.basename(each["new_path"]))
        text = json.dumps(found, sort_keys=True, indent=1) + "\n"
        if not os.path.exists(SAVE_REPLIES) or open(SAVE_REPLIES, encoding="utf-8").read() != text:
            with open(SAVE_REPLIES, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)


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
