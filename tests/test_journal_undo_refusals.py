"""The review of phase 7.5's journal: each finding, held.

- An undo that puts a deleted row back is refused, naming it, when a row it names is
  gone -- a face whose photo was deleted since, a node whose parent was -- counting the
  rows the same undo puts back; its rehearsal says refused, not exact.
- An undo whose row would break a UNIQUE constraint (tag_taxonomy.tag, photos.path) is
  refused naming the row and the column, before writing; and an IntegrityError SQLite
  raises all the same is a refusal, never a traceback.
- The maintenance scripts print their errors and exit non-zero (the refresh's script,
  in tests/test_refresh_rows_guards.py).
- journal.rehearse works for every shape of insert the journal allows.
- A refresh skips a row saved during its run instead of refusing the whole change
  (journal: skippable edits; tests/test_refresh_rows_guards.py through the script).
- One keyword spelling, in tagpup.core.vocabulary.
- journal.history of one change lists its keys without reading its values.
"""
import contextlib
import io
import json
import os
import re
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from journal_library import MODEL, JournalLibrary, jpeg, vector  # noqa: E402

import dedupe_faces as dedupe_script  # noqa: E402
import merge_duplicate_person_tags as merge_script  # noqa: E402
from tagpup.core import vocabulary  # noqa: E402
from tagpup.services import journal as library_journal  # noqa: E402
from tagpup.store import journal, people  # noqa: E402

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
        from tagpup.core.library import Library
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
                                                                "embedding": vector(9), "name": "Imogen Vale",
                                                                "name_source": "manual", "excluded": 0}),
            "a node with its tag and name": journal.insert("tag_taxonomy", {
                "tag": "Friends/Maren Oakhollow", "parent_id": self.ids["Friends"], "name": "Maren Oakhollow",
                "has_face": 1}),
            "a node without tag or name": journal.insert("tag_taxonomy", {"parent_id": self.ids["Activity"]}),
            "a crop": journal.insert("face_crops", {"face_id": self.ids["excluded"], "jpeg": jpeg(7)}),
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
        # Merging and deduplicating stay all or nothing: their rows depend on each other.
        # Recording an identity a file holds, and re-pointing a dead row at its renamed
        # file, are each one row's own business, as a refresh's rows are (phase 7.5).
        services = os.path.join(WORKSPACE_DIR, "tagpup", "services")
        using = []
        for name in sorted(os.listdir(services)):
            if name.endswith(".py"):
                with open(os.path.join(services, name), encoding="utf-8") as handle:
                    if "skippable=True" in handle.read():
                        using.append(name)
        self.assertEqual(["document_ids.py", "refresh_rows.py", "relink_photos.py"], using)


class ScriptsReportErrors(JournalLibrary):
    """#3: dedupe and merge print result.errors and exit non-zero (the refresh's script
    in tests/test_refresh_rows_guards.py)."""

    def run_script(self, module, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = module.main(["--db", self.db_path] + list(arguments))
        return code, out.getvalue()

    def dedupe_ready(self):
        self.execute("INSERT INTO faces (photo_id, box, embedding, prob, excluded)"
                     " SELECT photo_id, box, embedding, prob, 0 FROM faces WHERE id = ?", (self.ids["copy"],))

    def failed_write(self, module):
        before = self.dump()
        with mock.patch.object(journal, "_write", side_effect=RuntimeError("disk I/O error")):
            code, out = self.run_script(module, "--apply")
        self.assertEqual(1, code, out)
        self.assertIn("FAILED: the write: RuntimeError: disk I/O error", out)
        self.assertIn("Nothing was recorded", out)
        self.assertEqual(before, self.dump())

    def failed_rebuild(self, module):
        with mock.patch.object(journal, "_derive", side_effect=RuntimeError("database is locked")):
            with self.assertLogs("tagpup.store.journal", "ERROR"):
                code, out = self.run_script(module, "--apply")
        self.assertEqual(1, code, out)
        self.assertRegex(out, r"Recorded as change \d+")
        self.assertIn("FAILED: the people and dates of the photos it touched: not rebuilt yet", out)
        self.assertEqual("derived_pending", self.changes()[0][2])

    def test_merge_a_failed_write(self):
        self.failed_write(merge_script)

    def test_merge_a_failed_rebuild(self):
        self.failed_rebuild(merge_script)

    def test_dedupe_a_failed_write(self):
        self.dedupe_ready()
        self.failed_write(dedupe_script)

    def test_dedupe_a_failed_rebuild(self):
        self.dedupe_ready()
        self.failed_rebuild(dedupe_script)

    def test_a_failed_rehearsal_is_reported_too(self):
        with mock.patch.object(journal, "rehearse", side_effect=RuntimeError("disk I/O error")):
            code, out = self.run_script(merge_script)
        self.assertEqual(1, code, out)
        self.assertIn("FAILED: the rehearsal: RuntimeError: disk I/O error", out)

    def test_a_clean_apply_exits_zero(self):
        code, out = self.run_script(merge_script, "--apply")
        self.assertEqual(0, code, out)
        self.assertNotIn("FAILED", out)

    def test_recorded_copes_with_no_change(self):
        from tagpup.core.result import Result
        from tagpup.services import maintenance
        self.assertEqual("Nothing was recorded: no change was written.",
                         maintenance.recorded(Result(details={"change": None}), self.db_path))


class OneKeywordSpelling(unittest.TestCase):
    """#6: the keyword spelling extract_people looks up by, and people.follow_tree and
    photos_named_by find photos by, is vocabulary.keyword_key -- one copy."""

    def test_the_spelling(self):
        self.assertEqual("people/rowan thackeray", vocabulary.keyword_key("  People" + chr(92) + "Rowan Thackeray "))

    def test_no_copy_of_it_elsewhere(self):
        spelled = 'replace("' + chr(92) * 2 + '", "/").strip().lower()'
        found = []
        for folder in ("tagpup", "scripts"):
            for root, _dirs, names in os.walk(os.path.join(WORKSPACE_DIR, folder)):
                for name in names:
                    if name.endswith(".py"):
                        path = os.path.join(root, name)
                        with open(path, encoding="utf-8") as handle:
                            count = handle.read().count(spelled)
                        if count:
                            found.append((os.path.relpath(path, WORKSPACE_DIR), count))
        self.assertEqual([(os.path.join("tagpup", "core", "vocabulary.py"), 1)], found)
        self.assertFalse(hasattr(people, "_spelled"))


class HistoryReadsKeysOnly(JournalLibrary):
    """#7: history of one change lists its keys without loading old and new values."""

    def test_keys_without_values(self):
        applied = journal.apply(self.db_path, "dedupe_faces", [
            journal.delete("faces", (self.ids["copy"],), {}), journal.delete("faces", (self.ids["named"],), {}),
            journal.update("photos", (self.ids["start"],), {}, {"captions": '["Quay"]'})])
        with mock.patch.object(journal, "_load", side_effect=AssertionError("loaded every value")):
            entry = journal.history(self.db_path, change_id=applied.change_id)[0]
        self.assertEqual({"face_crops": [[self.ids["copy"]], [self.ids["named"]]],
                          "faces": [[self.ids["copy"]], [self.ids["named"]]],
                          "photos": [[self.ids["start"]]]}, entry["keys"])
        self.assertNotIn("values", entry)
        values = journal.history(self.db_path, change_id=applied.change_id, values=True)[0]["values"]
        self.assertEqual(5, len(values))
        self.assertTrue(re.match(r"<\d+ bytes>", values[0]["old"]["jpeg"]))


if __name__ == "__main__":
    unittest.main()
