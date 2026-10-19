"""The journal: a change applied and undone restores every table it touched byte for byte;
every refusal names what stands in the way and writes nothing; a crash at any step is
settled at the next start; and the rehearsal catches an undo that does not restore.

docs/ARCHITECTURE.md, phase 7.5. Rows are shaped like the owner's (tests/journal_library.py).
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import FACENET  # noqa: E402
import migration_names  # noqa: E402
from journal_library import JournalLibrary, jpeg, vector  # noqa: E402

from tagpup.store import db, faces, journal, schema  # noqa: E402


class Crash(BaseException):
    """The process dying: not an Exception, so nothing on the way out catches it."""


def crash_at(step):
    def reached(where):
        if where == step:
            raise Crash(step)
    return reached


class ForwardThenUndo(JournalLibrary):
    def apply(self, edits, operation="test"):
        return journal.apply(self.db_path, operation, edits, {"counts": {"n": len(edits)}})

    def test_deleted_faces_come_back_with_their_crops_byte_for_byte(self):
        before = self.dump()
        applied = self.apply([journal.delete("faces", (self.ids["copy"],), {"name": None}),
                              journal.delete("faces", (self.ids["named"],), {"name": "Maren Oakhollow"})])
        self.assertEqual((2, 6, True), (applied.changed, applied.rows, applied.settled),
                         "two faces, their two vectors and two crops")
        self.assertEqual([], self.query("SELECT id FROM faces WHERE id IN (?, ?)", (self.ids["copy"], self.ids["named"])))
        self.assertEqual([], self.query("SELECT face_id FROM face_crops WHERE face_id IN (?, ?)",
                                        (self.ids["copy"], self.ids["named"])))
        self.assertEqual([], self.query("SELECT face_id FROM face_embeddings WHERE face_id IN (?, ?)",
                                        (self.ids["copy"], self.ids["named"])))
        self.assertNotIn("Maren Oakhollow", self.people_of(self.ids["finish"]), "a face's name leaves its photo")
        # The values as SQLite values: a vector and a crop as BLOBs, a box as text, prob as a real.
        types = dict(self.query("SELECT column_name, typeof(old) FROM change_rows WHERE change_id = ?"
                                " AND table_name = 'faces' AND row_key = ?", (applied.change_id, "[%d]" % self.ids["copy"])))
        self.assertEqual(("text", "real", "integer", "null"),
                         (types["box"], types["prob"], types["excluded"], types["name"]))
        for table, column in (("face_crops", "jpeg"), ("face_embeddings", "vector")):
            self.assertEqual([("blob",)], self.query("SELECT DISTINCT typeof(old) FROM change_rows"
                                                     " WHERE table_name = ? AND column_name = ?", (table, column)))

        undone = journal.undo(self.db_path, applied.change_id)
        self.assertEqual((6, True), (undone.rows, undone.settled))
        self.assertEqual(before, self.dump())
        self.assertEqual([(applied.change_id, "test", "undone")], self.changes())

    def test_a_deleted_photo_comes_back_with_everything_it_took(self):
        before = self.dump()
        applied = self.apply([journal.delete("photos", (self.ids["finish"],), {"tags": '["Friends/Imogen Vale", "People/Rowan Thackeray"]'})])
        # The photo, its two faces, their vectors, the one crop they had, and its vector; its people rebuilt.
        tables = dict(self.query("SELECT table_name, COUNT(DISTINCT row_key) FROM change_rows GROUP BY table_name"))
        self.assertEqual({"photos": 1, "faces": 2, "face_embeddings": 2, "face_crops": 1, "embeddings": 1}, tables)
        self.assertEqual([], self.query("SELECT * FROM photo_people WHERE photo_id = ?", (self.ids["finish"],)))
        self.assertEqual([], self.query("SELECT photo_id FROM embeddings WHERE photo_id = ?", (self.ids["finish"],)))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_a_suggestion_goes_and_comes_back_with_its_photo(self):
        before = self.dump()
        applied = self.apply([journal.delete("photos", (self.ids["prize"],), {})])
        self.assertEqual([("suggestions",)], self.query(
            "SELECT DISTINCT table_name FROM change_rows WHERE table_name = 'suggestions'"))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_an_update_records_the_columns_it_changed_and_its_dates_follow(self):
        before = self.dump()
        start = self.ids["start"]
        raw = json.loads(self.query("SELECT raw_metadata FROM photos WHERE id = ?", (start,))[0][0])
        raw["EXIF:DateTimeOriginal"] = "2019:05:04 08:00:00"
        tags = self.query("SELECT tags FROM photos WHERE id = ?", (start,))[0][0]
        applied = self.apply([journal.update("photos", (start,), {"tags": tags},
                                             {"tags": tags, "raw_metadata": json.dumps(raw), "size": 4812399})])
        self.assertEqual([("raw_metadata",), ("size",)], self.query(
            "SELECT column_name FROM change_rows WHERE change_id = ? ORDER BY column_name", (applied.change_id,)),
            "tags did not change, so it is not recorded")
        self.assertEqual([(2019,)], self.query("SELECT year FROM photos WHERE id = ?", (start,)))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_a_tree_node_removed_takes_its_person_off_the_photos_and_the_undo_puts_them_back(self):
        before = self.dump()
        finish = self.ids["finish"]
        self.assertIn("Imogen Vale", self.people_of(finish))
        applied = self.apply([journal.delete("tag_taxonomy", (self.ids["Friends/Imogen Vale"],),
                                             {"tag": "Friends/Imogen Vale"})])
        self.assertEqual(["Rowan Thackeray", "Maren Oakhollow"], self.people_of(finish),
                         "her keyword names nobody without her node")
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_an_inserted_row_is_given_its_key_and_the_undo_takes_it_away(self):
        before = self.dump()
        applied = self.apply([journal.insert("tag_taxonomy", {"tag": "Activity/Rowing", "parent_id": self.ids["Activity"],
                                                              "name": "Rowing", "has_face": 0})])
        node = self.query("SELECT id FROM tag_taxonomy WHERE tag = 'Activity/Rowing'")[0][0]
        self.assertEqual([("[%d]" % node,)], self.query("SELECT DISTINCT row_key FROM change_rows"))
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_nothing_to_change_records_nothing(self):
        tags = self.query("SELECT tags FROM photos WHERE id = ?", (self.ids["start"],))[0][0]
        applied = self.apply([journal.update("photos", (self.ids["start"],), {}, {"tags": tags})])
        self.assertEqual((None, 0), (applied.change_id, applied.changed))
        self.assertEqual([], self.changes())


class Refusals(JournalLibrary):
    """Each refusal names the row, the newer change or the schema, and writes nothing."""

    def refused(self, work):
        before, changes = self.dump(), self.changes()
        with self.assertRaises(journal.Refusal) as caught:
            work()
        self.assertEqual(before, self.dump(), "a refusal wrote")
        self.assertEqual(changes, self.changes(), "a refusal recorded a change")
        for secret in ("Rowan Thackeray", "Imogen Vale", "Maren Oakhollow", self.home.root):
            self.assertNotIn(secret, str(caught.exception), "a refusal names values")
        return str(caught.exception)

    def delete_copy(self):
        return journal.apply(self.db_path, "dedupe_faces", [journal.delete("faces", (self.ids["copy"],), {"name": None})])

    # ---- Forward -------------------------------------------------------------------------

    def test_a_row_changed_since_the_plan(self):
        edits = [journal.delete("faces", (self.ids["copy"],), {"name": None, "excluded": 0}),
                 journal.delete("faces", (self.ids["kept"],), {"name": "Rowan Thackeray"})]
        self.execute("UPDATE faces SET name = 'Imogen Vale', excluded = 1 WHERE id = ?", (self.ids["copy"],))
        message = self.refused(lambda: journal.apply(self.db_path, "t", edits))
        self.assertIn("faces %d is not what the plan read: name, excluded changed" % self.ids["copy"], message)

    def test_a_row_gone_since_the_plan(self):
        self.execute("DELETE FROM faces WHERE id = ?", (self.ids["copy"],))
        message = self.refused(self.delete_copy)
        self.assertIn("faces %d is gone" % self.ids["copy"], message)

    def test_a_node_with_nodes_under_it(self):
        message = self.refused(lambda: journal.apply(self.db_path, "t", [
            journal.delete("tag_taxonomy", (self.ids["Friends"],), {"tag": "Friends"})]))
        self.assertIn("tag_taxonomy %d has tag_taxonomy %d under it" % (self.ids["Friends"], self.ids["Friends/Imogen Vale"]),
                      message)

    def test_a_node_and_everything_under_it_may_go_together(self):
        before = self.dump()
        applied = journal.apply(self.db_path, "t", [
            journal.delete("tag_taxonomy", (self.ids["Friends/Imogen Vale"],), {}),
            journal.delete("tag_taxonomy", (self.ids["Friends"],), {})])
        journal.undo(self.db_path, applied.change_id)
        self.assertEqual(before, self.dump())

    def test_an_insert_whose_key_is_taken(self):
        message = self.refused(lambda: journal.apply(self.db_path, "t", [
            journal.insert("suggestions", {"photo_id": self.ids["prize"], "before_consensus": 0})]))
        self.assertIn("suggestions %d is already there" % self.ids["prize"], message)

    def test_a_derived_table_is_never_journaled(self):
        with self.assertRaisesRegex(ValueError, "photo_people is not a table a change writes: it is derived"):
            journal.apply(self.db_path, "t", [journal.delete("photo_people", (self.ids["start"], 0), {})])
        self.assertEqual([], self.changes())

    def test_a_column_the_table_has_not(self):
        with self.assertRaisesRegex(ValueError, "faces has no column colour"):
            journal.apply(self.db_path, "t", [journal.update("faces", (self.ids["copy"],), {}, {"colour": "red"})])

    # ---- Undo ----------------------------------------------------------------------------

    def test_undo_of_a_row_changed_since(self):
        start = self.ids["start"]
        applied = journal.apply(self.db_path, "t", [journal.update("photos", (start,), {}, {"captions": '["Quay"]'})])
        self.execute("UPDATE photos SET captions = '[\"Harbour\"]' WHERE id = ?", (start,))
        message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
        self.assertIn("photos %d is not what change %d left: captions changed" % (start, applied.change_id), message)

    def test_undo_of_a_delete_whose_row_is_back(self):
        applied = self.delete_copy()
        self.execute("INSERT INTO faces (id, photo_id, box) VALUES (?, ?, '[]')", (self.ids["copy"], self.ids["start"]))
        message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
        self.assertIn("faces %d is there again" % self.ids["copy"], message)

    def test_undo_under_a_newer_change_of_the_same_rows(self):
        start = self.ids["start"]
        first = journal.apply(self.db_path, "first", [journal.update("photos", (start,), {}, {"captions": '["Quay"]'})])
        second = journal.apply(self.db_path, "second", [journal.update("photos", (start,), {}, {"captions": '["Pier"]'})])
        message = self.refused(lambda: journal.undo(self.db_path, first.change_id))
        self.assertIn("change %d (second), applied after it, changed the same rows: undo it first" % second.change_id,
                      message)
        journal.undo(self.db_path, second.change_id)
        journal.undo(self.db_path, first.change_id)
        self.assertEqual([('["Start line"]',)], self.query("SELECT captions FROM photos WHERE id = ?", (start,)))

    def test_a_newer_change_of_other_rows_is_no_obstacle(self):
        first = self.delete_copy()
        journal.apply(self.db_path, "other", [journal.update("photos", (self.ids["prize"],), {}, {"captions": "[]"})])
        journal.undo(self.db_path, first.change_id)
        self.assertEqual([(self.ids["copy"],)], self.query("SELECT id FROM faces WHERE id = ?", (self.ids["copy"],)))

    def test_undo_once_the_schema_has_moved_on(self):
        applied = self.delete_copy()
        self.execute("UPDATE changes SET schema_version = 9 WHERE id = ?", (applied.change_id,))
        message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
        self.assertIn("change %d was made at schema 9 and the library is at %d" % (applied.change_id, schema.LATEST),
                      message)
        self.assertIn("before the baseline", message)

    def test_undo_once_only_an_exempt_migration_has_run_since(self):
        applied = self.delete_copy()
        with migration_names.ahead():   # an additive step that touches no journaled table, run on opening
            self.assertEqual(1, len(schema.ensure(self.db_path)))
            journal.undo(self.db_path, applied.change_id)
        self.assertEqual([(self.ids["copy"],)], self.query("SELECT id FROM faces WHERE id = ?", (self.ids["copy"],)))

    def test_undo_twice(self):
        applied = self.delete_copy()
        journal.undo(self.db_path, applied.change_id)
        self.assertIn("change %d is undone" % applied.change_id,
                      self.refused(lambda: journal.undo(self.db_path, applied.change_id)))

    def test_undo_of_a_change_there_is_not(self):
        self.assertIn("there is no change 41", self.refused(lambda: journal.undo(self.db_path, 41)))

    def test_undo_of_an_insert_something_now_depends_on(self):
        applied = journal.apply(self.db_path, "t", [journal.insert("faces", {"photo_id": self.ids["prize"], "box": "[]"})])
        face = self.query("SELECT MAX(id) FROM faces")[0][0]
        self.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, x'ffd8ffd9')", (face,))
        message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
        self.assertIn("faces %d has rows in face_crops made since" % face, message)


class AFacePassFolderUndone(JournalLibrary):
    """The new face model's pass gives a folder's photos new faces, each with its crop and its vector under the new
    model's key and no other, as ONE journaled change, through its own writer (tagpup.store.faces.give_vectors: the rows
    written, then recorded as stored by journal.record_inserted). The owner undoes the folder: the faces go with the
    crop and the vector the same change gave them, and come back the same when written again. A row of another
    change under such a face (a vector a later run added under another model) still refuses the undo, and says so.
    The whole pass is scenario P8 (tests/test_the_face_pass.py)."""
    NEW_MODEL = "scrfd-adaface"

    def the_pass_gives_a_face(self):
        """(change id, face id) of the pass's change that gave the photo "prize" a face, its crop and its vector."""
        photo = self.query("SELECT path FROM photos WHERE id = ?", (self.ids["prize"],))[0][0]

        def work(conn):
            db.begin(conn, immediate=True)
            change_id = journal.record(conn, "face pass (%s)" % self.NEW_MODEL, [], {"folder": "Harbour Regatta"})
            (face,) = faces.give_vectors(conn, change_id, photo, self.NEW_MODEL, [],
                                         [([40, 40, 140, 160], 0.93, vector(7), jpeg(7))], {"new": 1})
            return change_id, face
        return db.write_with_connection(self.db_path, work)

    def test_the_folder_undone_takes_its_faces_crops_and_vectors_and_written_again_gives_them_back(self):
        before = self.dump()
        change_id, face = self.the_pass_gives_a_face()
        given = self.dump()
        self.assertEqual(1, len(self.query("SELECT 1 FROM face_embeddings WHERE face_id = ?", (face,))))
        rehearsal = journal.rehearse_undo(self.db_path, change_id)
        self.assertEqual((None, True), (rehearsal.refused, rehearsal.exact), "undone and written again, the rows are as given")
        self.assertEqual(given, self.dump(), "a rehearsal writes nothing")
        undone = journal.undo(self.db_path, change_id)
        self.assertEqual(3, undone.rows)
        self.assertEqual(before, self.dump())
        for table in ("face_crops", "face_embeddings"):
            self.assertEqual([], self.query("SELECT 1 FROM %s WHERE face_id = ?" % table, (face,)))

    def test_a_vector_another_change_added_since_still_refuses_the_undo(self):
        change_id, face = self.the_pass_gives_a_face()
        journal.apply(self.db_path, "vectors", [journal.insert("face_embeddings", {"face_id": face, "model": FACENET,
                                                                                    "vector": vector(8)})])
        before = self.dump()
        with self.assertRaises(journal.Refusal) as caught:
            journal.undo(self.db_path, change_id)
        self.assertIn("faces %d has rows in face_embeddings made since" % face, str(caught.exception))
        self.assertNotIn("face_crops", str(caught.exception), "its own crop stands in no way")
        self.assertEqual(before, self.dump(), "a refusal wrote")


class Crashes(JournalLibrary):
    """A crash between each step, then the next start."""

    def delete_named(self):
        return [journal.delete("faces", (self.ids["named"],), {"name": "Maren Oakhollow"})]

    def start_again(self):
        """What the next process does when it opens the library."""
        journal._settled.clear()
        schema.forget()
        schema.ensure(self.db_path)

    def test_after_the_write_before_the_commit(self):
        before = self.dump()
        with mock.patch.object(journal, "_reached", side_effect=crash_at("forward written")):
            with self.assertRaises(Crash):
                journal.apply(self.db_path, "t", self.delete_named())
        self.start_again()
        self.assertEqual(before, self.dump())
        self.assertEqual([], self.changes())

    def test_after_the_commit_before_the_derived_rebuild(self):
        finish = self.ids["finish"]
        with mock.patch.object(journal, "_reached", side_effect=crash_at("forward committed")):
            with self.assertRaises(Crash):
                journal.apply(self.db_path, "t", self.delete_named())
        self.assertEqual([(1, "t", "derived_pending")], self.changes())
        self.assertIn("Maren Oakhollow", self.people_of(finish), "not rebuilt yet")
        self.start_again()
        self.assertEqual([(1, "t", "applied")], self.changes())
        self.assertEqual(["Rowan Thackeray", "Imogen Vale"], self.people_of(finish))

    def test_a_derived_rebuild_that_fails_is_finished_at_start(self):
        with mock.patch.object(journal, "_derive", side_effect=RuntimeError("database is locked")):
            with self.assertLogs("tagpup.store.journal", "ERROR"):
                applied = journal.apply(self.db_path, "t", self.delete_named())
        self.assertFalse(applied.settled)
        self.assertEqual([(1, "t", "derived_pending")], self.changes())
        self.assertEqual(1, journal.settle(self.db_path))
        self.assertEqual([(1, "t", "applied")], self.changes())

    def test_during_the_undo_before_its_commit(self):
        applied = journal.apply(self.db_path, "t", self.delete_named())
        after = self.dump()
        with mock.patch.object(journal, "_reached", side_effect=crash_at("undo written")):
            with self.assertRaises(Crash):
                journal.undo(self.db_path, applied.change_id)
        self.start_again()
        self.assertEqual(after, self.dump())
        self.assertEqual([(1, "t", "applied")], self.changes())

    def test_after_the_undo_commits_before_its_derived_rebuild(self):
        before = self.dump()
        applied = journal.apply(self.db_path, "t", self.delete_named())
        with mock.patch.object(journal, "_reached", side_effect=crash_at("undo committed")):
            with self.assertRaises(Crash):
                journal.undo(self.db_path, applied.change_id)
        self.assertEqual([(1, "t", "derived_pending")], self.changes())
        self.start_again()
        self.assertEqual([(1, "t", "undone")], self.changes())
        self.assertEqual(before, self.dump())

    def test_a_pending_change_is_not_undone_until_it_is_finished(self):
        with mock.patch.object(journal, "_reached", side_effect=crash_at("forward committed")):
            with self.assertRaises(Crash):
                journal.apply(self.db_path, "t", self.delete_named())
        with self.assertRaisesRegex(journal.Refusal, "change 1 is not finished"):
            journal.undo(self.db_path, 1)

    def test_every_step_is_tried(self):
        self.assertEqual(("forward written", "forward committed", "undo written", "undo committed"), journal.STEPS)


class Rehearsals(JournalLibrary):
    def edits(self):
        return [journal.delete("faces", (self.ids["copy"],), {"name": None}),
                journal.delete("faces", (self.ids["named"],), {"name": "Maren Oakhollow"}),
                journal.update("photos", (self.ids["start"],), {}, {"captions": '["Quay"]'})]

    def test_a_rehearsal_writes_nothing_and_finds_the_undo_exact(self):
        before = self.dump()
        generations = self.query("SELECT name, value FROM generations ORDER BY name")
        rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertEqual((None, 7, 3, True, True, [], 0),
                         (rehearsal.refused, rehearsal.rows, rehearsal.changed, rehearsal.exact,
                          rehearsal.derived_exact, rehearsal.differences, rehearsal.derived_stale))
        self.assertEqual(before, self.dump())
        self.assertEqual(generations, self.query("SELECT name, value FROM generations ORDER BY name"))
        self.assertEqual([], self.changes())

    def test_a_wrong_inverse_is_caught(self):
        real = journal._inverse

        def zeroes_the_vector(change):
            inverse = real(change)
            if inverse.action == "insert" and inverse.table == "face_embeddings":
                inverse.new["vector"] = bytes(len(inverse.new["vector"]))
            return inverse

        with mock.patch.object(journal, "_inverse", side_effect=zeroes_the_vector):
            rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertFalse(rehearsal.exact)
        self.assertIn("face_embeddings %d/%s: vector" % (self.ids["copy"], FACENET), rehearsal.differences)
        self.assertEqual([], self.changes())

    def test_a_wrong_derived_rebuild_is_caught(self):
        real = journal._derive
        calls = []

        def forgets_on_the_way_back(conn, changes):
            calls.append(1)
            return 0 if len(calls) == 3 else real(conn, changes)

        with mock.patch.object(journal, "_derive", side_effect=forgets_on_the_way_back):
            rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertTrue(rehearsal.exact)
        self.assertFalse(rehearsal.derived_exact)

    def test_a_photo_left_stale_by_another_writer_is_not_blamed_on_the_undo(self):
        self.execute("DELETE FROM photo_people WHERE photo_id = ?", (self.ids["start"],))
        rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertEqual((True, True, 1), (rehearsal.exact, rehearsal.derived_exact, rehearsal.derived_stale))

    def test_a_rehearsal_that_would_be_refused_says_why(self):
        self.execute("DELETE FROM faces WHERE id = ?", (self.ids["copy"],))
        rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertIn("faces %d is gone" % self.ids["copy"], rehearsal.refused)

    def test_an_undo_is_rehearsed_too(self):
        applied = journal.apply(self.db_path, "t", self.edits())
        after = self.dump()
        rehearsal = journal.rehearse_undo(self.db_path, applied.change_id)
        self.assertEqual((None, 7, True, True), (rehearsal.refused, rehearsal.rows, rehearsal.exact,
                                                 rehearsal.derived_exact))
        self.assertEqual(after, self.dump())
        self.assertEqual([(1, "t", "applied")], self.changes())
        self.execute("UPDATE photos SET captions = '[]' WHERE id = ?", (self.ids["start"],))
        self.assertIn("captions changed", journal.rehearse_undo(self.db_path, applied.change_id).refused)


class HistoryAndPruning(JournalLibrary):
    def two_changes(self):
        first = journal.apply(self.db_path, "dedupe_faces", [journal.delete("faces", (self.ids["copy"],), {})],
                              {"counts": {"redundant": 1}})
        second = journal.apply(self.db_path, "refresh_rows",
                               [journal.update("photos", (self.ids["start"],), {}, {"captions": '["Quay"]'})])
        return first.change_id, second.change_id

    def test_history_lists_changes_newest_first_with_counts_only(self):
        first, second = self.two_changes()
        listed = journal.history(self.db_path)
        self.assertEqual([(second, "refresh_rows", "applied"), (first, "dedupe_faces", "applied")],
                         [(c["id"], c["operation"], c["status"]) for c in listed])
        self.assertEqual({"faces": {"delete": 1}, "face_crops": {"delete": 1}, "face_embeddings": {"delete": 1}},
                         listed[1]["rows"])
        self.assertEqual({"counts": {"redundant": 1}, "rows": {"face_crops": 1, "face_embeddings": 1, "faces": 1}},
                         listed[1]["summary"])
        self.assertEqual(schema.LATEST, listed[1]["schema_version"])
        self.assertNotIn("keys", listed[1])

    def test_one_change_lists_its_keys_and_its_values_only_when_asked(self):
        first, _second = self.two_changes()
        entry = journal.history(self.db_path, change_id=first)[0]
        self.assertEqual({"faces": [[self.ids["copy"]]], "face_crops": [[self.ids["copy"]]],
                          "face_embeddings": [[self.ids["copy"], FACENET]]}, entry["keys"])
        self.assertNotIn("values", entry)
        values = journal.history(self.db_path, change_id=first, values=True)[0]["values"]
        vector = next(v for v in values if v["table"] == "face_embeddings")
        self.assertEqual(("<2048 bytes>", None), (vector["old"]["vector"], vector["new"]))


class TheSchemaMovedOn(unittest.TestCase):
    """A change made at an older schema is undone unless a migration since could have changed what
    its rows mean: one that is not additive, or that touches a table a change can name. The steps are synthetic
    (tests/migration_names.py): the real ones below the baseline are gone, so a change made below it is refused."""

    def test_a_gap_of_only_additive_migrations_that_touch_no_journaled_table_is_exempt(self):
        first, second = migration_names.step(), migration_names.step(version=schema.LATEST + 2)
        with migration_names.ahead(first, second):
            self.assertIsNone(journal.schema_gap_blocker(migration_names.BASE, second.version))
            self.assertIsNone(journal.schema_gap_blocker(second.version, second.version))

    def test_a_migration_that_touches_only_derived_tables_blocks_no_undo(self):
        """Every undo rebuilds the derived tables from the rows it wrote, so a step that makes or fills one changes
        nothing an older change's rows mean."""
        step = migration_names.step(touches=("photo_tags", "folders", "photo_folder"))
        self.assertLessEqual({"photo_tags", "folders", "photo_folder"}, set(journal.DERIVED))
        with migration_names.ahead(step):
            self.assertIsNone(journal.schema_gap_blocker(migration_names.BASE, step.version))

    def test_a_change_made_below_the_baseline_is_refused_in_a_sentence_that_says_why(self):
        # Every journal change of rows made before schema 32 is refused (DECISIONS 2026-10-15); one made at 32 is refused
        # across migration 33, which destroys (a column dropped); one made at 33 is not.
        for version in (9, 14, 20, 31):
            found = journal.schema_gap_blocker(version, schema.LATEST)
            self.assertIn("before the baseline, schema %d" % schema.BASELINE, found)
        self.assertIn("migration 33, face vectors in face_embeddings, is destructive",
                      journal.schema_gap_blocker(schema.BASELINE, schema.LATEST))
        self.assertIsNone(journal.schema_gap_blocker(schema.LATEST, schema.LATEST))

    def test_a_gap_with_a_migration_that_is_not_additive_refuses_naming_it(self):
        fake = schema.Migration(schema.LATEST + 1, "rewrites", lambda conn: None, schema.DESTRUCTIVE, "rewrites", (), ())
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS + (fake,)):
            found = journal.schema_gap_blocker(schema.LATEST, schema.LATEST + 1)
        self.assertIn("migration %d" % (schema.LATEST + 1), found)
        self.assertIn("destructive", found)

    def test_a_migration_that_is_not_in_the_list_refuses(self):
        later = migration_names.step(version=schema.LATEST + 2)   # the one between is missing from the list
        with migration_names.ahead(later):
            self.assertIn("migration %d is not known" % (migration_names.BASE + 1),
                          journal.schema_gap_blocker(migration_names.BASE, later.version))
        self.assertIn("is not applied here", journal.schema_gap_blocker(schema.LATEST + 1, schema.LATEST))

    def test_a_fake_additive_migration_touching_a_journaled_table_is_not_exempt(self):
        fake = schema.Migration(schema.LATEST + 1, "x", lambda conn: None, schema.ADDITIVE, "adds nothing",
                                ("tag_taxonomy",), ())
        with mock.patch.object(schema, "MIGRATIONS", schema.MIGRATIONS + (fake,)):
            self.assertIn("touches tag_taxonomy", journal.schema_gap_blocker(schema.LATEST, schema.LATEST + 1))

    def test_the_history_lists_a_change_made_at_the_baseline_as_undoable_and_one_before_it_as_not(self):
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        applied = journal.apply(path, "add a node", [journal.insert("tag_taxonomy", {"tag": "Fresh", "name": "Fresh"})])
        self.assertEqual({applied.change_id: []}, journal.refusals(path, [applied.change_id]))
        conn = db.connect(path)
        try:
            conn.execute("UPDATE changes SET schema_version = 31 WHERE id = ?", (applied.change_id,))
            conn.commit()
        finally:
            conn.close()
        said = journal.refusals(path, [applied.change_id])[applied.change_id]
        self.assertIn("made at schema 31 and the library is at %d" % schema.LATEST, said[0])
        self.assertIn("before the baseline", said[0])

    def test_a_migration_is_never_undone_whatever_the_gap(self):
        from tagpup.services import journal as journal_service
        from tagpup.core.library import Library
        home = own_home.for_test(self)
        path = home.library("harbour.db")
        schema.ensure(path)
        steps = migration_names.two_steps()
        with migration_names.ahead(*steps):
            self.assertEqual(2, len(schema.ensure(path)))
            library = Library(path)
            listed = journal.history(path, limit=100)
            migrations = [e for e in listed if e["operation"].startswith("migration ")]
            self.assertEqual(2, len(migrations))
            reasons = journal_service.refusals(library, migrations)
            for entry in migrations:
                self.assertIn("a migration is not undone", reasons[entry["id"]], entry)
                self.assertEqual(["change %d (%s): a migration is not undone" % (entry["id"], entry["operation"])],
                                 journal.refusals(path, [entry["id"]])[entry["id"]])
                result = journal_service.undo(library, entry["id"], apply=True)
                self.assertIn("a migration is not undone", result.refused)
            after = journal.history(path, limit=100)
        self.assertEqual([], [e["id"] for e in after if e["status"] == "undone"], "nothing was marked undone")


if __name__ == "__main__":
    unittest.main()
