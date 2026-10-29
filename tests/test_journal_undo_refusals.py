"""The review of phase 7.5's journal: each finding, held.

- An undo that puts a deleted row back is refused, naming it, when a row it names is
  gone -- a face whose photo was deleted since, a node whose parent was -- counting the
  rows the same undo puts back; its rehearsal says refused, not exact.
- An undo whose row would break a UNIQUE constraint (tag_taxonomy.tag, photos.path) is
  refused naming the row and the column, before writing; and an IntegrityError SQLite
  raises all the same is a refusal, never a traceback.
- The maintenance services carry their errors in the Result: held once, on the scaffold they all run on
  (tests/test_maintenance.py).
- journal.rehearse works for every shape of insert the journal allows.
- A sync skips a row saved while it reads the files instead of refusing the whole change
  (journal: skippable edits; tests/test_sync.py, ChangedFiles).
- One keyword spelling, in tagpup.core.vocabulary: tests/test_vocabulary.py.
- journal.history of one change lists its keys without reading its values.
- A face pass recorded before a retiring of the old detector's faces is not undone while the retiring stands: its faces
  stand in for the old faces the retiring deleted, and the retiring is never undone (#1979; each live library holds one,
  photo_index 3247 after 2,499 kept passes, counted 2026-10-26).
- The deletion of the old detector's rows (MTCNN's settings and its detection records) is not undone from History: the
  command is deleted with the model, and undone it would put back settings no validation knows any more (one applied
  change in each live journal: photo_index 4871, kr-track 371, renton_parkrun 1235, counted read-only 2026-10-28).
"""
import json
import os
import re
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import FACE_MODEL  # noqa: E402
from journal_library import MODEL, JournalLibrary, vector  # noqa: E402

from tagpup.core import validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as library_journal  # noqa: E402
from tagpup.store import db, faces_detected, journal  # noqa: E402

SECRETS = ("Rowan Thackeray", "Imogen Vale", "Maren Oakhollow", "regatta_00")


class Checked(JournalLibrary):
    def refused(self, work):
        """The Refusal `work` raises: nothing written, no values named."""
        before, changes = self.dump(), self.changes()
        with self.assertRaises(journal.Refusal) as caught:
            work()
        self.assertEqual(before, self.dump(), "a refusal wrote")
        self.assertEqual(changes, self.changes(), "a refusal recorded a change")
        for secret in SECRETS + (self.home.root,):
            self.assertNotIn(secret, str(caught.exception), "a refusal names values")
        return str(caught.exception)

    def undo_refused(self, change_id):
        """The undo's refusal, and its rehearsal's: the same, and refused, not exact."""
        rehearsal = journal.rehearse_undo(self.db_path, change_id)
        self.assertIsNotNone(rehearsal.refused, "the rehearsal called it %s" % ("exact" if rehearsal.exact else "inexact"))
        self.assertFalse(rehearsal.exact)
        message = self.refused(lambda: journal.undo(self.db_path, change_id))
        self.assertEqual(message, rehearsal.refused)
        return message


class RowsAnUndoNames(Checked):
    """#1: every foreign key of a row put back resolves."""

    def test_a_face_whose_photo_was_deleted_since(self):
        start = self.ids["start"]
        applied = journal.apply(self.db_path, "dedupe_faces", [journal.delete("faces", (self.ids["copy"],), {})])
        # The app deletes the photo, and its other face, afterwards.
        self.execute("DELETE FROM faces WHERE photo_id = ?", (start,))
        self.execute("DELETE FROM photos WHERE id = ?", (start,))
        message = self.undo_refused(applied.change_id)
        self.assertIn("faces %d: photo_id names photos %d, which is not there" % (self.ids["copy"], start), message)
        self.assertNotIn("face_crops", message, "the crop's face is put back by the same undo")

    def test_a_node_whose_parent_was_deleted_since(self):
        node, parent = self.ids["Friends/Imogen Vale"], self.ids["Friends"]
        applied = journal.apply(self.db_path, "t", [journal.delete("tag_taxonomy", (node,), {})])
        self.execute("DELETE FROM tag_taxonomy WHERE id = ?", (parent,))
        message = self.undo_refused(applied.change_id)
        self.assertIn("tag_taxonomy %d: parent_id names tag_taxonomy %d, which is not there" % (node, parent), message)

    def test_rows_the_same_undo_puts_back_count(self):
        before = self.dump()
        applied = journal.apply(self.db_path, "t", [
            journal.delete("tag_taxonomy", (self.ids["Friends/Imogen Vale"],), {}),
            journal.delete("tag_taxonomy", (self.ids["Friends"],), {}),
            journal.delete("photos", (self.ids["finish"],), {})])
        rehearsal = journal.rehearse_undo(self.db_path, applied.change_id)
        self.assertEqual((None, True), (rehearsal.refused, rehearsal.exact))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_a_row_written_forward_naming_nothing_is_refused_too(self):
        message = self.refused(lambda: journal.apply(self.db_path, "t", [
            journal.insert("faces", {"photo_id": 9999, "box": "[]"})]))
        self.assertIn("a new row of faces: photo_id names photos 9999, which is not there", message)
        self.assertIn("names photos 9999", journal.rehearse(self.db_path, "t", [
            journal.insert("faces", {"photo_id": 9999, "box": "[]"})]).refused)

    def test_the_constraints_are_read_from_the_schema(self):
        conn = self.connect_readonly()
        try:
            self.assertIn((("photo_id",), "photos", ("id",)), journal._foreign_keys(conn, "faces"))
            self.assertIn((("parent_id",), "tag_taxonomy", ("id",)), journal._foreign_keys(conn, "tag_taxonomy"))
            self.assertIn((("face_id",), "faces", ("id",)), journal._foreign_keys(conn, "face_crops"))
            self.assertIn((("tag",), ("BINARY",)), journal._uniques(conn, "tag_taxonomy"))
            self.assertIn((("path",), ("BINARY",)), journal._uniques(conn, "photos"))
            self.assertEqual([], journal._uniques(conn, "embeddings"), "its key is the journal's own check")
        finally:
            conn.close()

    def connect_readonly(self):
        from tagpup.store import db
        return db.connect(db.readonly_uri(self.db_path), uri=True)


class UniqueValues(Checked):
    """#2: a row put back that would break a UNIQUE constraint."""

    def test_a_node_spelled_like_one_removed_made_again(self):
        bare = self.ids["Rowan Thackeray"]
        applied = journal.apply(self.db_path, "merge_duplicate_person_tags",
                                [journal.delete("tag_taxonomy", (bare,), {})])
        again = self.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Rowan Thackeray', 'Rowan Thackeray')")
        message = self.undo_refused(applied.change_id)
        self.assertIn("tag_taxonomy %d: tag is taken by tag_taxonomy %d" % (bare, again), message)

    def test_a_photo_whose_path_has_a_new_row(self):
        prize = self.ids["prize"]
        path = self.query("SELECT path FROM photos WHERE id = ?", (prize,))[0][0]
        applied = journal.apply(self.db_path, "t", [journal.delete("photos", (prize,), {})])
        again = self.execute("INSERT INTO photos (path, tags) VALUES (?, '[]')", (path,))
        message = self.undo_refused(applied.change_id)
        self.assertIn("photos %d: path is taken by photos %d" % (prize, again), message)

    def test_an_integrity_error_all_the_same_is_a_refusal(self):
        bare = self.ids["Rowan Thackeray"]
        applied = journal.apply(self.db_path, "t", [journal.delete("tag_taxonomy", (bare,), {})])
        self.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Rowan Thackeray', 'Rowan Thackeray')")
        with mock.patch.object(journal, "_blocked", return_value=[]):
            rehearsal = journal.rehearse_undo(self.db_path, applied.change_id)
            message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
            forward = self.refused(lambda: journal.apply(self.db_path, "t", [
                journal.insert("tag_taxonomy", {"tag": "Activity", "name": "Activity"})]))
            rehearsed = journal.rehearse(self.db_path, "t", [
                journal.insert("tag_taxonomy", {"tag": "Activity", "name": "Activity"})])
        self.assertIn("UNIQUE constraint failed: tag_taxonomy.tag", rehearsal.refused)
        self.assertIn("the database refused a row: UNIQUE constraint failed: tag_taxonomy.tag", message)
        self.assertIn("UNIQUE constraint failed: tag_taxonomy.tag", forward)
        self.assertIn("UNIQUE constraint failed: tag_taxonomy.tag", rehearsed.refused)

    def test_the_services_undo_answers_a_refusal_not_a_traceback(self):
        bare = self.ids["Rowan Thackeray"]
        applied = journal.apply(self.db_path, "t", [journal.delete("tag_taxonomy", (bare,), {})])
        self.execute("INSERT INTO tag_taxonomy (tag, name) VALUES ('Rowan Thackeray', 'Rowan Thackeray')")
        for apply in (False, True):
            result = library_journal.undo(Library(self.db_path), applied.change_id, apply=apply)
            self.assertIn("tag is taken", result.refused)


class InsertsRehearsed(Checked):
    """#4: every shape of insert the journal allows, rehearsed as well as applied."""

    def shapes(self):
        start, prize = self.ids["start"], self.ids["prize"]
        raw = {"XMP:Subject": ["People/Rowan Thackeray"], "EXIF:DateTimeOriginal": "2024:06:03 12:00:00"}
        photo = {"path": os.path.join(self.home.root, "Harbour Regatta", "regatta_004.jpg"), "mtime": 1727000004.5,
                 "size": 4812349, "tags": json.dumps(["People/Rowan Thackeray"]), "captions": "[]",
                 "raw_metadata": json.dumps(raw)}
        return {
            "a photo, keyed by SQLite": journal.insert("photos", photo),
            "a photo with its key": journal.insert("photos", dict(photo, id=500)),
            "a face, keyed by SQLite": journal.insert("faces", {"photo_id": prize, "box": "[1, 2, 3, 4]",
                                                                "name_source": "manual", "excluded": 0}),
            "a face's vector under a second model": journal.insert("face_embeddings", {
                "face_id": self.ids["excluded"], "model": validation.MTCNN_FACENET, "vector": vector(9)}),
            "a node with its tag and name": journal.insert("tag_taxonomy", {
                "tag": "Friends/Maren Oakhollow", "parent_id": self.ids["Friends"], "name": "Maren Oakhollow",
                "has_face": 1}),
            "a node without tag or name": journal.insert("tag_taxonomy", {"parent_id": self.ids["Activity"]}),
            "a vector under a second model": journal.insert("embeddings", {
                "photo_id": start, "model": MODEL + "-b", "mtime": 1.5, "size": 4, "vector": vector(11)}),
            "a suggestion": journal.insert("suggestions", {"photo_id": start, "tags": "[]", "people": "[]",
                                                           "title": "Start", "raw": "{}", "before_consensus": 0,
                                                           "model": MODEL, "created": "2026-09-25 10:00:00"}),
        }

    def test_each_shape_is_rehearsed_exact_and_writes_nothing(self):
        before = self.dump()
        for shape, edit in self.shapes().items():
            with self.subTest(shape):
                rehearsal = journal.rehearse(self.db_path, "t", [edit])
                self.assertEqual((None, True, True, []),
                                 (rehearsal.refused, rehearsal.exact, rehearsal.derived_exact, rehearsal.differences))
                self.assertEqual(before, self.dump())
                self.assertEqual([], self.changes())

    def test_each_shape_is_applied_and_undone(self):
        before = self.dump()
        for shape, edit in self.shapes().items():
            with self.subTest(shape):
                applied = journal.apply(self.db_path, "t", [edit])
                self.assertEqual(1, applied.rows)
                journal.undo(self.db_path, applied.change_id)
                self.assertEqual(before, self.dump())

    def test_a_photo_inserted_gets_its_people_and_its_date(self):
        edit = self.shapes()["a photo, keyed by SQLite"]
        applied = journal.apply(self.db_path, "t", [edit])
        photo = self.query("SELECT id, year FROM photos WHERE path = ?", (edit.values["path"],))[0]
        self.assertEqual(2024, photo[1])
        self.assertEqual(["Rowan Thackeray"], self.people_of(photo[0]))
        journal.undo(self.db_path, applied.change_id)


class AFacePassBeforeARetiring(Checked):
    """#1979: a library holding a face pass and then a retiring of the old detector's faces, as each live library does."""

    def face_pass(self):
        """A face pass's change: a new face in a photo, as the pass journals one."""
        return journal.apply(self.db_path, library_journal.FACE_PASS % FACE_MODEL, [journal.insert("faces", {
            "photo_id": self.ids["prize"], "box": "[10, 20, 110, 140]", "name_source": None, "excluded": 0})]).change_id

    def retiring(self):
        """The retiring's change as the live journals hold it (change 3247 on photo_index): recorded under the name of a
        command since deleted, which no code makes any more."""
        conn = db.connect(self.db_path)
        try:
            change_id = journal.record(conn, "retire faces (%s)" % FACE_MODEL, [], {"newest_face": self.ids["named"]})
            conn.commit()
            return change_id
        finally:
            conn.close()

    def test_the_pass_before_the_retiring_is_not_undone_and_nothing_is_written(self):
        passed = self.face_pass()
        retired = self.retiring()
        sentence = library_journal.PASS_STANDS_IN % retired
        for apply in (False, True):
            with self.subTest(apply=apply):
                before, changes = self.dump(), self.changes()
                result = library_journal.undo(self.library, passed, apply=apply)
                self.assertEqual(sentence, result.refused)
                self.assertEqual((before, changes), (self.dump(), self.changes()), "a refused undo wrote")
        self.assertEqual(sentence, self.refused(lambda: library_journal.undo_rows(self.library, passed)))
        listed = library_journal.refusals(self.library, [{"id": passed}, {"id": retired}])
        self.assertEqual(sentence, listed[passed], "History offers the pass's undo")
        self.assertIn("are not put back", listed[retired])

    def test_a_pass_after_the_retiring_is_undone(self):
        self.retiring()
        passed = self.face_pass()
        self.assertIsNone(library_journal.refusals(self.library, [{"id": passed}])[passed])
        self.assertFalse(library_journal.undo(self.library, passed, apply=True).refused)


class TheOldDetectorsRowsDeletion(Checked):
    """The owner opens History on a library where the deleted `delete-old-detector-rows` ran, and the change offers no
    Undo; the rehearsal and the undo say why, and write nothing."""

    #: The change's name as the live journals hold it (a command since deleted, which no code makes any more).
    OPERATION = "delete old detector rows (mtcnn)"
    #: MTCNN's detector name as the index recorded it: its settings, sorted.
    MTCNN = faces_detected.detector({"confidence_threshold": 0.85, "min_face_size": 20, "mtcnn_thresholds": [0.6, 0.7, 0.7]})

    def deletion(self):
        """The change as the command made it: one of MTCNN's settings rows, as the old stamp wrote it, and a photo's
        detection record under MTCNN's name, as the index wrote it, each deleted and recorded whole."""
        record = {"detector": self.MTCNN, "mtime": 1.0, "size": 10, "found": 0, "at": "2026-10-01 10:00:00"}
        conn = db.connect(self.db_path)
        try:
            conn.execute("INSERT INTO settings (key, value) VALUES ('faces.min_face_size', '20')")
            conn.execute("INSERT INTO faces_detected (photo_id, detector, mtime, size, found, at) VALUES (?, ?, ?, ?, ?, ?)",
                         (self.ids["prize"],) + tuple(record.values()))
            conn.commit()
        finally:
            conn.close()
        return journal.apply(self.db_path, self.OPERATION, [
            journal.delete("settings", ("faces.min_face_size",), {"value": "20"}, kind="settings"),
            journal.delete("faces_detected", (self.ids["prize"],), record, kind="faces_detected")]).change_id

    def left(self):
        return (self.query("SELECT key FROM settings WHERE key = 'faces.min_face_size'"),
                self.query("SELECT photo_id FROM faces_detected"), self.dump(), self.changes())

    def test_its_undo_is_refused_and_nothing_is_written(self):
        deleted = self.deletion()
        before = self.left()
        self.assertEqual(([], []), before[:2])
        for apply in (False, True):
            with self.subTest(apply=apply):
                result = library_journal.undo(self.library, deleted, apply=apply)
                self.assertIsNotNone(result.refused, "the undo would put back settings no validation knows")
                self.assertIn("change %d (%s)" % (deleted, self.OPERATION), result.refused)
                self.assertIn("are not put back", result.refused)
                self.assertEqual(before, self.left(), "a refused undo wrote")
        said = library_journal.undo(self.library, deleted).refused
        self.assertEqual(said, self.refused(lambda: library_journal.undo_rows(self.library, deleted)))
        self.assertEqual(said, library_journal.refusals(self.library, [{"id": deleted}])[deleted], "History offers its undo")


class SkippableEdits(JournalLibrary):
    """#5, in the journal: a skippable edit whose row changed is left out and listed."""

    def test_a_skippable_row_changed_since_is_skipped_and_the_rest_recorded(self):
        start, prize = self.ids["start"], self.ids["prize"]
        captions = {pid: self.query("SELECT captions FROM photos WHERE id = ?", (pid,))[0][0] for pid in (start, prize)}
        edits = [journal.update("photos", (pid,), {"captions": captions[pid]}, {"captions": '["Quay"]'},
                                kind="captions", skippable=True) for pid in (start, prize)]
        self.execute("UPDATE photos SET captions = '[\"Saved\"]' WHERE id = ?", (start,))
        before = self.dump()
        rehearsal = journal.rehearse(self.db_path, "refresh_rows", edits)
        self.assertEqual((None, True, [["photos %d" % start, "not what the plan read: captions changed"]]),
                         (rehearsal.refused, rehearsal.exact, rehearsal.skipped))
        applied = journal.apply(self.db_path, "refresh_rows", edits)
        self.assertEqual((1, [("photos %d" % start, "not what the plan read: captions changed")]),
                         (applied.changed, applied.skipped))
        self.assertEqual({"photos": [[prize]]}, journal.history(self.db_path, change_id=applied.change_id)[0]["keys"])
        self.assertEqual([('["Saved"]',)], self.query("SELECT captions FROM photos WHERE id = ?", (start,)))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_a_gone_row_is_skipped_too(self):
        edits = [journal.update("photos", (self.ids["prize"],), {}, {"captions": "[]"}, skippable=True)]
        self.execute("DELETE FROM photos WHERE id = ?", (self.ids["prize"],))
        applied = journal.apply(self.db_path, "t", edits)
        self.assertEqual((None, [("photos %d" % self.ids["prize"], "gone since the plan read it")]),
                         (applied.change_id, applied.skipped))

    def test_only_the_refresh_skips(self):
        # Merging stays all or nothing: its rows depend on each other.
        # Recording an identity a file holds, and re-pointing a dead row at its renamed
        # file, are each one row's own business, as a refresh's rows are (phase 7.5); so is unnaming a face whose person was taken off its
        # photo (face_people, #908): a face renamed meanwhile is the owner's newer decision and the others go on.
        # So is removing the row of a file a sync confirmed gone (sync, DECISIONS 2026-10-28): a row another sync or
        # Remove Folder took meanwhile is already gone, and the other removals go on.
        services = os.path.join(WORKSPACE_DIR, "tagpup", "services")
        using = []
        for name in sorted(os.listdir(services)):
            if name.endswith(".py"):
                with open(os.path.join(services, name), encoding="utf-8") as handle:
                    if "skippable=True" in handle.read():
                        using.append(name)
        self.assertEqual(["face_people.py", "refresh_rows.py", "relink_photos.py", "sync.py"], using)


class HistoryReadsKeysOnly(JournalLibrary):
    """#7: history of one change lists its keys without loading old and new values."""

    def test_keys_without_values(self):
        applied = journal.apply(self.db_path, "dedupe_faces", [
            journal.delete("faces", (self.ids["copy"],), {}), journal.delete("faces", (self.ids["named"],), {}),
            journal.update("photos", (self.ids["start"],), {}, {"captions": '["Quay"]'})])
        with mock.patch.object(journal, "_load", side_effect=AssertionError("loaded every value")):
            entry = journal.history(self.db_path, change_id=applied.change_id)[0]
        self.assertEqual({"face_embeddings": [[self.ids["copy"], FACE_MODEL], [self.ids["named"], FACE_MODEL]],
                          "faces": [[self.ids["copy"]], [self.ids["named"]]],
                          "photos": [[self.ids["start"]]]}, entry["keys"])
        self.assertNotIn("values", entry)
        values = journal.history(self.db_path, change_id=applied.change_id, values=True)[0]["values"]
        self.assertEqual(5, len(values))
        self.assertTrue(re.match(r"<\d+ bytes>", values[0]["old"]["vector"]))


if __name__ == "__main__":
    unittest.main()
