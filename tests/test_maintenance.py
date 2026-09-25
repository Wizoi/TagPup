"""The maintenance scaffold: a dry run plans and changes nothing; an apply backs up once,
writes what the plan said, and counts what the writes changed.

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
from tagpup.store import db, schema  # noqa: E402
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
    """run() with an operation of the test's own."""

    def operation(self, size, changes):
        self.writes = []

        def plan(library):
            return maintenance.Plan(size=size, counts={"things": size}, ids={"things": list(range(size))},
                                    reveal={"names": ["Rowan Thackeray"]}, work="the work")

        def write(library, planned, result):
            self.writes.append(planned.work)
            result.changed = changes

        return plan, write

    def test_a_dry_run_plans_and_neither_writes_nor_backs_up(self):
        plan, write = self.operation(3, 3)
        result = maintenance.run(self.library, "test-op", plan, write)
        self.assertEqual([], self.writes)
        self.assertEqual([], self.backups())
        self.assertEqual((3, 0), (result.attempted, result.changed))
        self.assertTrue(result.details["dry_run"])
        self.assertIsNone(result.details["backup"])
        self.assertEqual({"things": 3}, result.details["counts"])
        self.assertEqual({"things": [0, 1, 2]}, result.details["ids"])
        self.assertEqual({"names": ["Rowan Thackeray"]}, result.details["reveal"])

    def test_an_apply_backs_up_once_then_writes(self):
        plan, write = self.operation(3, 3)
        with mock.patch.object(maintenance.db, "backup", wraps=db.backup) as backup:
            result = maintenance.run(self.library, "test-op", plan, write, apply=True)
        self.assertEqual(1, backup.call_count)
        self.assertEqual(["the work"], self.writes)
        self.assertEqual(1, len(self.backups()))
        self.assertIn(".before-test-op-", self.backups()[0])
        self.assertEqual(os.path.join(self.library.backups, self.backups()[0]), result.details["backup"])
        self.assertFalse(result.details["dry_run"])

    def test_changed_is_what_the_write_counted_not_the_plan(self):
        plan, write = self.operation(3, 1)
        result = maintenance.run(self.library, "test-op", plan, write, apply=True)
        self.assertEqual((3, 1), (result.attempted, result.changed))

    def test_an_empty_plan_is_neither_backed_up_nor_written(self):
        plan, write = self.operation(0, 0)
        result = maintenance.run(self.library, "test-op", plan, write, apply=True)
        self.assertEqual([], self.writes)
        self.assertEqual([], self.backups())
        self.assertIsNone(result.details["backup"])

    def test_a_refused_plan_is_neither_backed_up_nor_written(self):
        def plan(library):
            return maintenance.Plan(size=2, refused="no tree")
        writes = []
        result = maintenance.run(self.library, "test-op", plan, lambda *a: writes.append(a), apply=True)
        self.assertEqual("no tree", result.refused)
        self.assertEqual([], writes)
        self.assertEqual([], self.backups())

    def test_remaining_is_read_after_the_write(self):
        plan, write = self.operation(2, 2)
        result = maintenance.run(self.library, "test-op", plan, write, apply=True,
                                 remaining=lambda library: {"things": len(self.writes)})
        self.assertEqual({"things": 1}, result.details["remaining"])


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
        self.assertEqual([], self.backups())
        self.assertEqual([self.copy, self.other_copy], sorted(result.details["ids"]["redundant"]))
        self.assertEqual([sorted(self.disputed)], result.details["ids"]["disputed"])
        self.assertEqual({"redundant": 2, "redundant_named": 0, "redundant_excluded": 0, "disputed": 1},
                         result.details["counts"])
        self.assertEqual((2, 0), (result.attempted, result.changed))

    def test_an_apply_removes_what_the_plan_named_after_one_backup(self):
        self.seed()
        planned = set(duplicate_faces.dedupe_faces(self.library).details["ids"]["redundant"])
        before = self.face_ids()
        result = duplicate_faces.dedupe_faces(self.library, apply=True)
        self.assertEqual(before - planned, self.face_ids())
        self.assertEqual((2, 2), (result.attempted, result.changed))
        self.assertEqual(1, len(self.backups()))
        self.assertEqual({"redundant": 0, "disputed": 1}, result.details["remaining"])

    def test_a_face_gone_before_the_write_is_not_counted(self):
        self.seed()
        real = duplicate_faces._plan

        def plan_then_someone_deletes(library):
            planned = real(library)
            self.execute("DELETE FROM faces WHERE id = ?", (self.copy,))
            return planned

        with mock.patch.object(duplicate_faces, "_plan", plan_then_someone_deletes):
            result = duplicate_faces.dedupe_faces(self.library, apply=True)
        self.assertEqual((2, 1), (result.attempted, result.changed))


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
        self.assertEqual([], self.backups())
        self.assertEqual({"duplicates": 1, "affected_photos": 1}, result.details["counts"])
        self.assertEqual({"duplicates": [self.bare], "affected_photos": [self.photo]}, result.details["ids"])
        self.assertEqual([("Rowan Thackeray", "People/Rowan Thackeray")], result.details["reveal"]["duplicates"])

    def test_an_apply_removes_the_node_and_leaves_the_photos(self):
        self.seed()
        result = person_tags.merge_duplicate_person_tags(self.library, apply=True)
        self.assertEqual(self.nodes(), {"People", "People/Rowan Thackeray", "Sailing"})
        self.assertEqual([(self.tags,)], self.query("SELECT tags FROM photos WHERE path = ?", (PHOTO,)))
        self.assertEqual((1, 1), (result.attempted, result.changed))
        self.assertEqual(1, len(self.backups()))
        self.assertEqual({"duplicates": 0}, result.details["remaining"])

    def test_a_library_without_a_tree_is_refused(self):
        bare = os.path.join(self.dir, "no_tree.db")
        conn = db.connect(bare)
        conn.execute("CREATE TABLE photos (id INTEGER PRIMARY KEY, path TEXT, tags TEXT)")
        conn.commit()
        conn.close()
        result = person_tags.merge_duplicate_person_tags(Library(bare), apply=True)
        self.assertIn("no tag_taxonomy table", result.refused)
        self.assertIsNone(result.details["backup"])


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
        self.assertEqual(self.ids("garbled", "stale_keywords", "stale_stat"), sorted(result.details["ids"]["to_write"]))
        self.assertEqual(self.ids("twice"), result.details["ids"]["captions_only"])
        self.assertEqual((4, 0), (result.attempted, result.changed))

    def test_an_apply_backs_up_once_and_counts_rows_changed(self):
        result = self.refresh(apply=True)
        self.assertEqual(1, len(self.backups()))
        self.assertEqual((4, 4), (result.attempted, result.changed))
        self.assertEqual({"from_files": 3, "captions": 1}, result.details["changed"])
        self.assertEqual([], result.skipped)

    def test_the_librarys_people_are_read_once_a_run_not_once_a_photo(self):
        # The script read the tree's people again for every batch and every photo read,
        # each on a connection of its own. Through the script, so the old one is measured
        # the same way.
        from tagpup.store import taxonomy
        with mock.patch.object(taxonomy, "people_vocabulary", wraps=taxonomy.people_vocabulary) as read:
            self.run_script()
        self.assertEqual(3, len(self.read), "the fixture has three files to read")
        self.assertLessEqual(read.call_count, 2)

    def test_a_row_saved_after_the_read_is_skipped_by_id(self):
        real = self.fake_batch_read

        def read_then_the_app_saves(extractor, paths, people=None):
            records = real(extractor, paths, people)
            self_conn = db.connect(self.db)
            self_conn.execute("UPDATE photos SET mtime = 2000000.0 WHERE path = ?", (self.files["stale_stat"],))
            self_conn.commit()
            self_conn.close()
            return records

        self.fake_batch_read = read_then_the_app_saves
        result = self.refresh(apply=True)
        self.assertEqual((4, 3), (result.attempted, result.changed))
        self.assertEqual([("photo %d" % self.ids("stale_stat")[0], "changed after this run read its file")],
                         result.skipped)


if __name__ == "__main__":
    unittest.main()
