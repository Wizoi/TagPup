"""The maintenance scaffold: a dry run plans, rehearses and changes nothing; an apply
records one change in the library's journal, copies nothing, writes what the plan said
only while every row is what the plan read, and counts what the writes changed.

tagpup.services.maintenance is what the maintenance scripts and the MCP server's write
tools both run on (docs/ARCHITECTURE.md, phase 7). Each operation is tested here through
its service: removing duplicate faces, merging duplicate person tags, and re-reading
rows from their files. The scripts' own tests check what they print.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import duplicate_faces, maintenance, person_tags, refresh_rows  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402
from face_rows import add_face  # noqa: E402
import test_refresh_rows_from_files as refresh_fixture  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"
OTHER = r"D:\Pictures\Regatta\finish.jpg"


class LibraryCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="maintenance_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        self.library = Library(self.db)
        schema.ensure(self.db)

    def execute(self, sql, params=()):
        conn = db.connect(self.db)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def backups(self):
        folder = self.library.backups
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []


class TheScaffold(LibraryCase):
    """run() with an operation of the test's own: it captions photos."""

    def operation(self, size):
        ids = [self.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')",
                            (r"D:\Pictures\Regatta\%d.jpg" % n,)) for n in range(size)]
        self.planned = []

        def plan(library):
            return maintenance.Plan(size=size, counts={"things": size}, ids={"things": ids},
                                    reveal={"names": ["Rowan Thackeray"]}, work=ids)

        def edits(planned):
            self.planned.append(planned.work)
            return [journal.update("photos", (i,), {"captions": "[]"}, {"captions": '["Harbour"]'})
                    for i in planned.work]

        return plan, edits

    def captions(self):
        return [c for (c,) in self.query("SELECT captions FROM photos ORDER BY id")]

    def changes(self):
        return self.query("SELECT id, operation, status FROM changes ORDER BY id")

    def test_a_dry_run_rehearses_and_writes_nothing(self):
        plan, edits = self.operation(3)
        result = maintenance.run(self.library, "test_op", plan, edits)
        self.assertEqual(["[]"] * 3, self.captions())
        self.assertEqual([], self.changes())
        self.assertEqual([], self.backups())
        self.assertEqual((3, 0), (result.attempted, result.changed))
        self.assertTrue(result.details["dry_run"])
        self.assertIsNone(result.details["change"])
        self.assertEqual({"things": 3}, result.details["counts"])
        self.assertEqual({"things": [1, 2, 3]}, result.details["ids"])
        self.assertEqual({"names": ["Rowan Thackeray"]}, result.details["reveal"])
        rehearsal = result.details["rehearsal"]
        self.assertEqual((3, True, True, None), (rehearsal["rows"], rehearsal["exact"], rehearsal["derived_exact"],
                                                 rehearsal["refused"]))

    def test_an_apply_records_one_change_and_copies_nothing(self):
        plan, edits = self.operation(3)
        with mock.patch.object(db, "backup", wraps=db.backup) as backup:
            result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        backup.assert_not_called()
        self.assertEqual([], self.backups())
        self.assertEqual(['["Harbour"]'] * 3, self.captions())
        change = result.details["change"]
        self.assertEqual([(change, "test_op", "applied")], self.changes())
        self.assertEqual([(3,)], self.query("SELECT COUNT(*) FROM change_rows WHERE change_id = ?", (change,)))
        self.assertFalse(result.details["dry_run"])
        self.assertEqual((3, 3), (result.attempted, result.changed))

    def test_changed_is_what_the_write_changed_not_the_plan(self):
        plan, _edits = self.operation(3)
        self.execute("UPDATE photos SET captions = '[\"Harbour\"]' WHERE id = 1")

        def edits(planned):
            # The first already holds what the edit writes: nothing to change there.
            return [journal.update("photos", (i,), {}, {"captions": '["Harbour"]'}) for i in planned.work]

        result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        self.assertEqual((3, 2), (result.attempted, result.changed))
        self.assertEqual([(2,)], self.query("SELECT COUNT(*) FROM change_rows"))

    def test_an_empty_plan_is_neither_rehearsed_nor_written(self):
        plan, edits = self.operation(0)
        for apply in (False, True):
            result = maintenance.run(self.library, "test_op", plan, edits, apply=apply)
            self.assertIsNone(result.details["change"])
            self.assertNotIn("rehearsal", result.details)
        self.assertEqual([], self.planned)
        self.assertEqual([], self.changes())

    def test_a_refused_plan_is_neither_rehearsed_nor_written(self):
        def plan(library):
            return maintenance.Plan(size=2, refused="no tree")
        asked = []
        result = maintenance.run(self.library, "test_op", plan, lambda planned: asked.append(planned), apply=True)
        self.assertEqual("no tree", result.refused)
        self.assertEqual([], asked)
        self.assertEqual([], self.changes())

    def test_a_row_changed_since_the_plan_refuses_the_whole_change(self):
        plan, edits = self.operation(3)

        def plan_then_the_app_saves(library):
            planned = plan(library)
            self.execute("UPDATE photos SET captions = '[\"Quay\"]' WHERE id = 2")
            return planned

        for apply in (False, True):
            result = maintenance.run(self.library, "test_op", plan_then_the_app_saves, edits, apply=apply)
            self.assertIn("photos 2 is not what the plan read: captions changed", result.refused)
            self.assertNotIn("Quay", result.refused, "a refusal names columns, never values")
            self.assertEqual(0, result.changed)
            self.assertIsNone(result.details["change"])
        self.assertEqual(["[]", '["Quay"]', "[]"], self.captions())
        self.assertEqual([], self.changes())

    def test_an_apply_whose_write_fails_says_so_and_writes_nothing(self):
        import sqlite3
        plan, edits = self.operation(2)
        with mock.patch.object(journal, "_write", side_effect=sqlite3.OperationalError("disk I/O error")):
            result = maintenance.run(self.library, "test_op", plan, edits, apply=True)
        self.assertFalse(result.ok)
        self.assertEqual(0, result.changed)
        self.assertIn("disk I/O error", result.errors[0][1])
        self.assertEqual((["[]"] * 2, []), (self.captions(), self.changes()))

    def test_remaining_is_read_after_the_write(self):
        plan, edits = self.operation(2)
        result = maintenance.run(self.library, "test_op", plan, edits, apply=True,
                                 remaining=lambda library: {"uncaptioned": self.captions().count("[]")})
        self.assertEqual({"uncaptioned": 0}, result.details["remaining"])


class DedupeFaces(LibraryCase):
    def face(self, photo=PHOTO, **columns):
        conn = db.connect(self.db)
        try:
            face_id = add_face(conn, photo, box="[1, 2, 3, 4]", **columns)
            conn.commit()
            return face_id
        finally:
            conn.close()

    def face_ids(self):
        return {face_id for (face_id,) in self.query("SELECT id FROM faces")}

    def seed(self):
        self.kept = self.face(name="Rowan Thackeray", name_source="manual")
        self.copy = self.face()
        self.other_kept = self.face(photo=OTHER)
        self.other_copy = self.face(photo=OTHER)
        self.disputed = [self.face(photo=r"D:\Pictures\Regatta\prize.jpg", name="Rowan Thackeray"),
                         self.face(photo=r"D:\Pictures\Regatta\prize.jpg", name="Imogen Vale")]

    def test_a_dry_run_names_the_copies_and_changes_nothing(self):
        self.seed()
        before = self.face_ids()
        result = duplicate_faces.dedupe_faces(self.library)
        self.assertEqual(before, self.face_ids())
        self.assertEqual([], self.query("SELECT id FROM changes"))
        self.assertTrue(result.details["rehearsal"]["exact"])
        self.assertEqual([self.copy, self.other_copy], sorted(result.details["ids"]["redundant"]))
        self.assertEqual([sorted(self.disputed)], result.details["ids"]["disputed"])
        self.assertEqual({"redundant": 2, "redundant_named": 0, "redundant_excluded": 0, "disputed": 1},
                         result.details["counts"])
        self.assertEqual((2, 0), (result.attempted, result.changed))

    def test_an_apply_removes_what_the_plan_named_as_one_change(self):
        self.seed()
        planned = set(duplicate_faces.dedupe_faces(self.library).details["ids"]["redundant"])
        before = self.face_ids()
        result = duplicate_faces.dedupe_faces(self.library, apply=True)
        self.assertEqual(before - planned, self.face_ids())
        self.assertEqual((2, 2), (result.attempted, result.changed))
        self.assertEqual([], self.backups())
        self.assertEqual([(result.details["change"], "dedupe_faces", "applied")],
                         self.query("SELECT id, operation, status FROM changes"))
        self.assertEqual({"redundant": 0, "disputed": 1}, result.details["remaining"])

    def test_a_face_gone_before_the_write_refuses_the_change(self):
        # It deleted the other copies and counted one fewer; the plan no longer holds, so
        # nothing is written, and a second run plans again.
        self.seed()
        real = duplicate_faces._plan

        def plan_then_someone_deletes(library):
            planned = real(library)
            self.execute("DELETE FROM faces WHERE id = ?", (self.copy,))
            return planned

        with mock.patch.object(duplicate_faces, "_plan", plan_then_someone_deletes):
            result = duplicate_faces.dedupe_faces(self.library, apply=True)
        self.assertEqual((2, 0), (result.attempted, result.changed))
        self.assertIn("faces %d is gone" % self.copy, result.refused)
        self.assertIn(self.other_copy, self.face_ids())


class MergeDuplicatePersonTags(LibraryCase):
    def seed(self):
        root = self.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('People', 'People', 1)")
        self.execute("INSERT INTO tag_taxonomy (tag, name, has_face, parent_id)"
                     " VALUES ('People/Rowan Thackeray', 'Rowan Thackeray', 1, ?)", (root,))
        # Not flagged as holding faces, as every bare duplicate in the owner's libraries
        # is: a flagged root-level node is a people root (store.taxonomy.people_roots).
        self.bare = self.execute("INSERT INTO tag_taxonomy (tag, name, has_face)"
                                 " VALUES ('Rowan Thackeray', 'Rowan Thackeray', 0)")
        self.execute("INSERT INTO tag_taxonomy (tag, name, has_face) VALUES ('Sailing', 'Sailing', 0)")
        self.tags = json.dumps(["People/Rowan Thackeray", "Rowan Thackeray"])
        self.photo = self.execute("INSERT INTO photos (path, tags, captions, raw_metadata)"
                                  " VALUES (?, ?, '[]', '{}')", (PHOTO, self.tags))
        self.execute("INSERT INTO photos (path, tags, captions, raw_metadata)"
                     " VALUES (?, '[\"Sailing\"]', '[]', '{}')", (OTHER,))

    def nodes(self):
        return {tag for (tag,) in self.query("SELECT tag FROM tag_taxonomy")}

    def test_a_dry_run_names_the_bare_tag_and_changes_nothing(self):
        self.seed()
        before = self.nodes()
        result = person_tags.merge_duplicate_person_tags(self.library)
        self.assertEqual(before, self.nodes())
        self.assertEqual([], self.query("SELECT id FROM changes"))
        self.assertTrue(result.details["rehearsal"]["exact"])
        self.assertEqual({"duplicates": 1, "affected_photos": 1}, result.details["counts"])
        self.assertEqual({"duplicates": [self.bare], "affected_photos": [self.photo]}, result.details["ids"])
        self.assertEqual([("Rowan Thackeray", "People/Rowan Thackeray")], result.details["reveal"]["duplicates"])

    def test_an_apply_removes_the_node_and_leaves_the_photos(self):
        self.seed()
        result = person_tags.merge_duplicate_person_tags(self.library, apply=True)
        self.assertEqual(self.nodes(), {"People", "People/Rowan Thackeray", "Sailing"})
        self.assertEqual([(self.tags,)], self.query("SELECT tags FROM photos WHERE path = ?", (PHOTO,)))
        self.assertEqual((1, 1), (result.attempted, result.changed))
        self.assertEqual([], self.backups())
        self.assertEqual([("tag_taxonomy", "delete", 1)], self.query(
            "SELECT table_name, action, COUNT(DISTINCT row_key) FROM change_rows WHERE change_id = ?"
            " GROUP BY table_name, action", (result.details["change"],)))
        self.assertEqual({"duplicates": 0}, result.details["remaining"])

    def test_a_library_without_a_tree_is_refused(self):
        bare = os.path.join(self.dir, "no_tree.db")
        conn = db.connect(bare)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT, tags TEXT)")
        conn.commit()
        conn.close()
        result = person_tags.merge_duplicate_person_tags(Library(bare), apply=True)
        self.assertIn("no tag_taxonomy table", result.refused)
        self.assertIsNone(result.details["change"])


class RefreshRows(refresh_fixture.RefreshRowsFromFiles):
    """The refresh, called as the MCP server calls it. Inherits the script's tests too."""

    def refresh(self, **kwargs):
        from tagpup.files.metadata import MetadataExtractor
        self.read = []
        with mock.patch.object(MetadataExtractor, "batch_read", autospec=True,
                               side_effect=self.fake_batch_read):
            return refresh_rows.refresh_rows(Library(self.db), "exiftool.exe", **kwargs)

    def backups(self):
        folder = Library(self.db).backups
        return sorted(os.listdir(folder)) if os.path.isdir(folder) else []

    def ids(self, *names):
        conn = db.connect(db.readonly_uri(self.db), uri=True)
        try:
            return sorted(conn.execute("SELECT id FROM photos WHERE path = ?", (self.files[n],)).fetchone()[0]
                          for n in names)
        finally:
            conn.close()

    def test_a_dry_run_plans_by_id_and_changes_nothing(self):
        before = self.rows()
        result = self.refresh()
        self.assertEqual(before, self.rows())
        self.assertEqual([], self.backups())
        self.assertEqual((5, True, True), (result.details["rehearsal"]["rows"], result.details["rehearsal"]["exact"],
                                           result.details["rehearsal"]["derived_exact"]))
        self.assertEqual(self.ids("garbled", "stale_keywords", "stale_stat", "never_read"),
                         sorted(result.details["ids"]["to_write"]))
        self.assertEqual(self.ids("twice"), result.details["ids"]["captions_only"])
        self.assertEqual((5, 0), (result.attempted, result.changed))

    def test_an_apply_records_a_change_and_counts_rows_changed(self):
        result = self.refresh(apply=True)
        self.assertEqual([], self.backups())
        self.assertIsInstance(result.details["change"], int)
        self.assertEqual((5, 5), (result.attempted, result.changed))
        self.assertEqual({"from_files": 4, "captions": 1}, result.details["changed"])
        self.assertEqual([], result.skipped)

    def test_the_librarys_people_are_read_once_a_run_not_once_a_photo(self):
        # The script read the tree's people again for every batch and every photo read,
        # each on a connection of its own. Through the script, so the old one is measured
        # the same way.
        from tagpup.store import taxonomy
        with mock.patch.object(taxonomy, "people_vocabulary", wraps=taxonomy.people_vocabulary) as read:
            self.run_script()
        self.assertEqual(4, len(self.read), "the fixture has four files to read")
        self.assertLessEqual(read.call_count, 2)

    def test_a_row_saved_after_the_read_is_skipped_by_id_and_the_rest_written(self):
        real = self.fake_batch_read

        def read_then_the_app_saves(extractor, paths, people=None):
            records = real(extractor, paths, people)
            self_conn = db.connect(self.db)
            self_conn.execute("UPDATE photos SET mtime = 2000000.0 WHERE path = ?", (self.files["stale_stat"],))
            self_conn.commit()
            self_conn.close()
            return records

        self.fake_batch_read = read_then_the_app_saves
        before = self.rows()
        result = self.refresh(apply=True)
        # The saved row is skipped, by id, and keeps its save; the other four are written
        # (the whole change was refused, and a second run read every file again). A
        # second run reads the skipped one again.
        self.assertEqual((5, 4, None), (result.attempted, result.changed, result.refused))
        self.assertEqual([("photos %d" % self.ids("stale_stat")[0], "not what the plan read: mtime changed")],
                         result.skipped)
        self.assertEqual(2000000.0, self.rows()[self.files["stale_stat"]][3], "the app's save is kept")
        for name in ("garbled", "stale_keywords", "twice"):
            self.assertNotEqual(before[self.files[name]], self.rows()[self.files[name]], name)


if __name__ == "__main__":
    unittest.main()
