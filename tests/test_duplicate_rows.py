"""One file under two rows is merged onto one; a copy of a file is not (docs/findings.md, #479; tagpup.services.duplicate_rows).

A file reached by two names -- here a hard link, which has one file index -- was indexed under each, so the library holds
it twice. The rows are made as the indexer makes them (tests/photo_rows), the faces and vectors in plain SQL
(tests/face_rows). A COPY of a file (shutil.copy2: the same name, size and time, a file of its own) is the case that looked like the
same and is not: it keeps its row.
"""
import json
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face, add_vector  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import duplicate_rows  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.store import db  # noqa: E402

VECTOR = b"\x00\x00\x80?" * 2


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="dup_rows_")
        self.db_path = self.home.library("library.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.first = os.path.join(self.home.root, "Pictures", "Regatta")
        self.second = os.path.join(self.home.root, "Share", "Regatta")
        for folder in (self.first, self.second):
            os.makedirs(folder)
        # As the index stores them, so a face seeded by path finds the row the indexer made.
        self.original = paths.stored(os.path.join(self.first, "IMG_0001.jpg"))
        with open(self.original, "wb") as handle:
            handle.write(b"the picture")
        os.utime(self.original, (1_700_000_000, 1_700_000_000))
        self.linked = paths.stored(os.path.join(self.second, "IMG_0001.jpg"))
        os.link(self.original, self.linked)

    def rows_for(self, *photo_paths):
        conn = db.connect(self.db_path)
        try:
            ids = [photo_rows.add_read(conn, path, {"XMP:Subject": ["Events/Regatta"]}) for path in photo_paths]
            conn.commit()
        finally:
            conn.close()
        return ids

    def sql(self, sql, params=(), write=False):
        conn = db.connect(self.db_path)
        try:
            found = conn.execute(sql, params).fetchall()
            if write:
                conn.commit()
            return found
        finally:
            conn.close()

    def paths(self):
        return sorted(row[0] for row in self.sql("SELECT path FROM photos"))


class TheRowsOfOneFile(Case):
    def test_a_dry_run_counts_and_writes_nothing(self):
        self.rows_for(self.original, self.linked)
        result = duplicate_rows.dedupe_spelled_rows(self.library)
        counts = result.details["counts"]
        self.assertEqual((1, 1), (counts["files_held_twice"], counts["rows_to_remove"]))
        self.assertEqual(2, len(self.paths()), "a dry run wrote")
        self.assertEqual(2, len(self.sql("SELECT id FROM photos")))
        self.assertIsNone(result.details["change"])

    def test_applied_the_extra_row_goes_and_what_it_held_moves_onto_the_kept_one(self):
        keep, extra = self.rows_for(self.original, self.linked)
        conn = db.connect(self.db_path)
        try:
            # Each row has one face a person decided: the same two boxes on both, the decision on a different one.
            add_face(conn, self.original, [0, 0, 10, 10], embedding=b"a", name="Ada Marchetti", name_source="manual")
            add_face(conn, self.original, [40, 40, 50, 50], embedding=b"c")
            add_face(conn, self.linked, [0, 0, 10, 10], embedding=b"a")
            add_face(conn, self.linked, [40, 40, 50, 50], embedding=b"c", name="Rowan Thackeray", name_source="manual")
            # A face only the extra row has, and a vector only it has.
            lone = add_face(conn, self.linked, [20, 20, 30, 30], embedding=b"b")
            add_vector(conn, self.linked, VECTOR, model="model-b")
            add_vector(conn, self.original, VECTOR, model="model-a")
            conn.commit()
        finally:
            conn.close()
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual([(keep,)], self.sql("SELECT id FROM photos"))
        self.assertEqual([self.original], self.paths(), "the row kept is the oldest of those alike")
        faces = self.sql("SELECT id, box, name FROM faces WHERE photo_id = ? ORDER BY id", (keep,))
        self.assertEqual(3, len(faces), "a face was doubled or lost: %r" % (faces,))
        self.assertEqual(["Ada Marchetti", "Rowan Thackeray"], sorted(name for _i, _b, name in faces if name),
                         "a decision was lost, or not carried onto the face the kept row had")
        self.assertEqual(lone, [face[0] for face in faces if json.loads(face[1]) == [20, 20, 30, 30]][0])
        self.assertEqual(["model-a", "model-b"], sorted(m for (m,) in self.sql(
            "SELECT model FROM embeddings WHERE photo_id = ?", (keep,))))
        self.assertEqual([], self.sql("SELECT id FROM faces WHERE photo_id = ?", (extra,)))
        self.assertEqual(0, result.details["remaining"]["files_held_twice"])
        counts = result.details["counts"]
        self.assertEqual((1, 1, 1), (counts["faces_moved"], counts["decisions_carried"], counts["vectors_carried"]))

    def test_it_is_one_change_of_the_journal_and_undo_puts_everything_back(self):
        keep, extra = self.rows_for(self.original, self.linked)
        conn = db.connect(self.db_path)
        try:
            add_face(conn, self.linked, [0, 0, 10, 10], embedding=b"a", name="Rowan Thackeray", name_source="manual")
            conn.commit()
        finally:
            conn.close()
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        change = result.details["change"]
        self.assertIsNotNone(change)
        undone = journal_service.undo(self.library, change, apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertEqual(2, len(self.sql("SELECT id FROM photos")))
        self.assertEqual([("Rowan Thackeray", extra)], self.sql("SELECT name, photo_id FROM faces"))

    def test_the_row_under_a_root_is_the_one_kept(self):
        first, second = self.rows_for(self.original, self.linked)
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            group = [(first, self.original, self.original, "[]", "[]"), (second, "@pictures/IMG_0001.jpg", self.linked, "[]", "[]")]
            planned = duplicate_rows._plan_set(conn, group)
        finally:
            conn.close()
        self.assertEqual(second, planned["keep"][0])

    def test_a_row_that_knows_more_is_kept_among_rows_alike(self):
        first, second = self.rows_for(self.original, self.linked)
        conn = db.connect(self.db_path)
        try:
            add_face(conn, self.linked, [0, 0, 10, 10], embedding=b"a", name="Rowan Thackeray", name_source="manual")
            conn.commit()
        finally:
            conn.close()
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual([self.linked], self.paths(), "the row a person had curated was not kept")
        self.assertEqual([("Rowan Thackeray",)], self.sql("SELECT name FROM faces"))


class WhatIsNotOneFile(Case):
    def test_a_copy_keeps_its_row(self):
        copy = os.path.join(self.second, "IMG_0002.jpg")
        other = os.path.join(self.first, "IMG_0002.jpg")
        with open(other, "wb") as handle:
            handle.write(b"another picture")
        shutil.copy2(other, copy)
        self.rows_for(other, copy)
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        counts = result.details["counts"]
        self.assertEqual((0, 2, 1), (counts["files_held_twice"], counts["copies_left_alone"], counts["copy_groups_left_alone"]))
        self.assertEqual(2, len(self.paths()), "a copy's row was removed")

    def test_a_row_whose_file_is_missing_is_left_alone(self):
        self.rows_for(self.original, self.linked)
        os.remove(self.linked)
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        self.assertEqual(1, result.details["counts"]["missing_files_left_alone"])
        self.assertEqual(2, len(self.paths()))

    def test_two_names_for_one_face_are_left_for_a_person(self):
        self.rows_for(self.original, self.linked)
        conn = db.connect(self.db_path)
        try:
            add_face(conn, self.original, [0, 0, 10, 10], embedding=b"a", name="Rowan Thackeray", name_source="manual")
            add_face(conn, self.linked, [0, 0, 10, 10], embedding=b"a", name="Ada Marchetti", name_source="manual")
            conn.commit()
        finally:
            conn.close()
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        self.assertEqual(1, result.details["counts"]["sets_disputed"])
        self.assertEqual(2, len(self.paths()))
        self.assertEqual(2, len(self.sql("SELECT id FROM faces")))

    def test_rows_whose_tags_differ_are_left_for_a_person(self):
        first, second = self.rows_for(self.original, self.linked)
        self.sql("UPDATE photos SET tags = '[\"Events/Other\"]' WHERE id = ?", (second,), write=True)
        result = duplicate_rows.dedupe_spelled_rows(self.library, apply=True)
        self.assertEqual(1, result.details["counts"]["sets_whose_rows_differ"])
        self.assertEqual(2, len(self.paths()))


if __name__ == "__main__":
    unittest.main()
