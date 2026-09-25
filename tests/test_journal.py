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
from journal_library import JournalLibrary  # noqa: E402

from tagpup.store import db, journal, schema  # noqa: E402


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
        self.assertEqual((2, 4, True), (applied.changed, applied.rows, applied.settled), "two faces and two crops")
        self.assertEqual([], self.query("SELECT id FROM faces WHERE id IN (?, ?)", (self.ids["copy"], self.ids["named"])))
        self.assertEqual([], self.query("SELECT face_id FROM face_crops WHERE face_id IN (?, ?)",
                                        (self.ids["copy"], self.ids["named"])))
        self.assertNotIn("Maren Oakhollow", self.people_of(self.ids["finish"]), "a face's name leaves its photo")
        # The values as SQLite values: a vector and a crop as BLOBs, a box as text, prob as a real.
        types = dict(self.query("SELECT column_name, typeof(old) FROM change_rows WHERE change_id = ?"
                                " AND table_name = 'faces' AND row_key = ?", (applied.change_id, "[%d]" % self.ids["copy"])))
        self.assertEqual(("blob", "text", "real", "integer", "null"),
                         (types["embedding"], types["box"], types["prob"], types["excluded"], types["name"]))
        self.assertEqual([("blob",)], self.query("SELECT DISTINCT typeof(old) FROM change_rows"
                                                 " WHERE table_name = 'face_crops' AND column_name = 'jpeg'"))

        undone = journal.undo(self.db_path, applied.change_id)
        self.assertEqual((4, True), (undone.rows, undone.settled))
        self.assertEqual(before, self.dump())
        self.assertEqual([(applied.change_id, "test", "undone")], self.changes())

    def test_a_deleted_photo_comes_back_with_everything_it_took(self):
        before = self.dump()
        applied = self.apply([journal.delete("photos", (self.ids["finish"],), {"tags": '["Imogen Vale", "Rowan Thackeray"]'})])
        # The photo, its two faces, the one crop they had, and its vector; its people rebuilt.
        tables = dict(self.query("SELECT table_name, COUNT(DISTINCT row_key) FROM change_rows GROUP BY table_name"))
        self.assertEqual({"photos": 1, "faces": 2, "face_crops": 1, "embeddings": 1}, tables)
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
        self.execute("UPDATE changes SET schema_version = schema_version - 1 WHERE id = ?", (applied.change_id,))
        message = self.refused(lambda: journal.undo(self.db_path, applied.change_id))
        self.assertIn("change %d was made at schema %d and the library is at %d"
                      % (applied.change_id, schema.LATEST - 1, schema.LATEST), message)

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


class Crashes(JournalLibrary):
    """A crash between each step, then the next start."""

    def delete_named(self):
        return [journal.delete("faces", (self.ids["named"],), {"name": "Maren Oakhollow"})]

    def start_again(self):
        """What the next process does when it opens the library."""
        journal._settled.clear()
        schema._current.clear()
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
        self.assertEqual(["Imogen Vale", "Rowan Thackeray"], self.people_of(finish))

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
        self.assertEqual((None, 5, 3, True, True, [], 0),
                         (rehearsal.refused, rehearsal.rows, rehearsal.changed, rehearsal.exact,
                          rehearsal.derived_exact, rehearsal.differences, rehearsal.derived_stale))
        self.assertEqual(before, self.dump())
        self.assertEqual(generations, self.query("SELECT name, value FROM generations ORDER BY name"))
        self.assertEqual([], self.changes())

    def test_a_wrong_inverse_is_caught(self):
        real = journal._inverse

        def loses_the_vector(change):
            inverse = real(change)
            if inverse.action == "insert" and inverse.table == "faces":
                inverse.new.pop("embedding")
            return inverse

        with mock.patch.object(journal, "_inverse", side_effect=loses_the_vector):
            rehearsal = journal.rehearse(self.db_path, "t", self.edits())
        self.assertFalse(rehearsal.exact)
        self.assertIn("faces %d: embedding" % self.ids["copy"], rehearsal.differences)
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
        self.assertEqual((None, 5, True, True), (rehearsal.refused, rehearsal.rows, rehearsal.exact,
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
        self.assertEqual({"faces": {"delete": 1}, "face_crops": {"delete": 1}}, listed[1]["rows"])
        self.assertEqual({"counts": {"redundant": 1}, "rows": {"face_crops": 1, "faces": 1}}, listed[1]["summary"])
        self.assertEqual(schema.LATEST, listed[1]["schema_version"])
        self.assertNotIn("keys", listed[1])

    def test_one_change_lists_its_keys_and_its_values_only_when_asked(self):
        first, _second = self.two_changes()
        entry = journal.history(self.db_path, change_id=first)[0]
        self.assertEqual({"faces": [[self.ids["copy"]]], "face_crops": [[self.ids["copy"]]]}, entry["keys"])
        self.assertNotIn("values", entry)
        values = journal.history(self.db_path, change_id=first, values=True)[0]["values"]
        face = next(v for v in values if v["table"] == "faces")
        self.assertEqual(("<2048 bytes>", None), (face["old"]["embedding"], face["new"]))

    def test_pruning_keeps_the_summary_and_drops_the_values(self):
        import time
        first, second = self.two_changes()
        values = self.query("SELECT COUNT(*) FROM change_rows")[0][0]
        self.assertEqual((0, 0), journal.prunable(self.db_path))
        later = time.time() + (journal.RETENTION_DAYS + 1) * 86400
        self.assertEqual((2, values), journal.prunable(self.db_path, now=later))
        self.assertEqual((2, values), journal.prune(self.db_path, now=later))
        self.assertEqual([(first, "dedupe_faces", "pruned"), (second, "refresh_rows", "pruned")], self.changes())
        self.assertEqual([(0,)], self.query("SELECT COUNT(*) FROM change_rows"))
        self.assertEqual({"redundant": 1}, journal.history(self.db_path, change_id=first)[0]["summary"]["counts"])
        with self.assertRaisesRegex(journal.Refusal, "change %d was pruned" % first):
            journal.undo(self.db_path, first)

    def test_a_newer_change_is_never_pruned_before_an_older_one(self):
        first, second = self.two_changes()
        # The older stamped later than the newer, as a clock set back would leave them:
        # it goes with the newer, or an undo of the older could not see the newer's rows.
        self.execute("UPDATE changes SET created = '2999-01-01 00:00:00' WHERE id = ?", (first,))
        self.execute("UPDATE changes SET created = '2000-01-01 00:00:00' WHERE id = ?", (second,))
        self.assertEqual(2, journal.prunable(self.db_path, days=1)[0])
        self.execute("UPDATE changes SET created = '2000-01-01 00:00:00' WHERE id = ?", (first,))
        self.execute("UPDATE changes SET created = '2999-01-01 00:00:00' WHERE id = ?", (second,))
        self.assertEqual(1, journal.prunable(self.db_path, days=1)[0])

    def test_a_library_without_a_journal_has_no_history(self):
        bare = self.home.library("bare.db")
        conn = db.connect(bare)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.assertEqual([], journal.history(bare))
        self.assertEqual((0, 0), journal.prunable(bare))


if __name__ == "__main__":
    unittest.main()
