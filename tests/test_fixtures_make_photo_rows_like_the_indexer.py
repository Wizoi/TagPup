"""A test's photo row is made by the code that makes the real one (tests/photo_rows.py: add_read, add_unread), not by a
bare `INSERT INTO photos`, which makes no folder row: the folders reader (store.folders.with_rows) reads the derived
folders, so such a row is in no folder, a state no indexer leaves (docs/findings.md, #1505).

The files below predate that rule and seed a row by hand. By design, for each: none reads a folder list, a sync or the
folder watcher through such a row (swept 2026-10-16; the three that did, a scan, a folder removal and a sync, were moved to
add_read, and test_folder_watcher and the subfolder counts before them), or it rebuilds the derived tables after seeding
(derived.rebuild_all), so a bare INSERT there is harmless. It is a ratchet: a file may not be added, and one that no longer has a bare INSERT
must be taken off the list, so it only shrinks.

A test's face is made as the writers make one (tests/face_rows.add_face), filed with its vector under the library's face
model (tagpup.store.faces.insert files every face so; 0 of 197,285 live faces lack one, counted 2026-10-28, #2122). Every
reader joins a face's vector, so a face without one is seen by none of them, and a test seeding one tests nothing real.
add_face gives a face its own vector when the test names none; a bare `INSERT INTO faces` gives it none, and is held to
the same ratchet as the photo rows: the files below that still write one, each for a reason of its own (an old schema a
migration reads, a row the journal is shown to refuse, a script's backup, a scale fixture), may not grow.
"""
import glob
import os
import re
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import face_rows  # noqa: E402
import own_home  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
#: Every spelling of a statement that makes a photo row: INSERT INTO, INSERT OR REPLACE / IGNORE INTO, REPLACE INTO, in
#: any case and across a line break (#1546: the clustering fixtures' INSERT OR REPLACE was outside the count).
BARE = re.compile(r"(?:INSERT\s+(?:OR\s+\w+\s+)?|REPLACE\s+)INTO\s+photos\b", re.IGNORECASE)

#: The test files and helpers that still seed a photo row by hand: a count of such statements in each.
STILL_BARE = {
    "face_rows.py": 1, "journal_library.py": 1, "service_fixture.py": 1, "test_compact_gives_back_free_space.py": 1,
    "test_database_axis_guard.py": 1, "test_database_context_isolation.py": 1,
    "test_derived_tables.py": 1, "test_derived_writers.py": 1, "test_doctor.py": 3,
    "test_face_click_reads_named_faces_once.py": 1, "test_identify_cache_keeps_others_writes.py": 1,
    "test_identify_cache_sees_every_change.py": 1, "test_inspect_uses_the_stores_mtime_rule.py": 1,
    "test_journal_undo_refusals.py": 1, "test_maintenance.py": 1, "test_mcp_photo_against_file.py": 3,
    "test_mcp_server.py": 2, "test_partial_writes_leave_rows_unread.py": 1,
    "test_paths.py": 1, "test_paths_roots.py": 1, "test_person_rename_reaches_the_files.py": 1,
    "test_relink_checks_the_destination.py": 1, "test_roots_at_scale.py": 1, "test_roots_sample.py": 1, "test_schema.py": 2,
    "test_snapshots.py": 1, "test_suggest_index_stays_current.py": 1, "test_suggest_reads_people_once.py": 1,
    "test_tag_writes_keep_the_index_true.py": 1, "test_taxonomy_edits_report_what_they_wrote.py": 1,
    "test_tuner_reads.py": 1, "test_web_app.py": 1,
}

#: Every spelling of a statement that makes a face row, as BARE is of a photo row's.
BARE_FACE = re.compile(r"(?:INSERT\s+(?:OR\s+\w+\s+)?|REPLACE\s+)INTO\s+faces\b", re.IGNORECASE)

#: The test files and helpers that still make a face row by hand: a count of such statements in each. face_rows.py is the
#: helper itself; test_person_ids_single_owner.py names the statement in its docstring.
STILL_BARE_FACES = {
    "face_rows.py": 1, "test_bulk_scripts_back_up.py": 1, "test_journal.py": 1,
    "test_migrations.py": 2, "test_person_ids_single_owner.py": 1,
    "test_person_rename_reaches_the_files.py": 1, "test_roots_at_scale.py": 1, "test_taxonomy_lifecycle.py": 2,
    "test_tuner_server_api.py": 2,
}


def statements(pattern):
    """{file name: how many statements `pattern` finds in it}, for the files in tests/ but this one."""
    found = {}
    for path in glob.glob(os.path.join(HERE, "*.py")):
        name = os.path.basename(path)
        if name == os.path.basename(__file__):
            continue
        with open(path, encoding="utf-8") as handle:
            count = len(pattern.findall(handle.read()))
        if count:
            found[name] = count
    return found


def grown(found, listed):
    return {name: count for name, count in found.items() if count > listed.get(name, 0)}


def shrunk(found, listed):
    return sorted(name for name in listed if name not in found or found[name] < listed[name])


class PhotoRowsAreMadeLikeTheIndexersTest(unittest.TestCase):
    def test_no_new_file_seeds_a_photo_row_by_hand_and_the_list_only_shrinks(self):
        found = statements(BARE)
        self.assertEqual({}, grown(found, STILL_BARE),
                         "seed a photo row with photo_rows.add_read (or add_unread), as the indexer makes it")
        self.assertEqual([], shrunk(found, STILL_BARE),
                         "these have fewer bare INSERTs than listed: lower or remove them in STILL_BARE")


class FaceRowsAreMadeLikeTheWritersTest(unittest.TestCase):
    def test_no_new_file_makes_a_face_row_by_hand_and_the_list_only_shrinks(self):
        found = statements(BARE_FACE)
        self.assertEqual({}, grown(found, STILL_BARE_FACES),
                         "make a face with face_rows.add_face, which files it with its vector as the writers do")
        self.assertEqual([], shrunk(found, STILL_BARE_FACES),
                         "these have fewer bare INSERTs than listed: lower or remove them in STILL_BARE_FACES")

    def test_a_face_a_test_makes_has_a_vector_under_the_librarys_face_model_as_the_writers_file_it(self):
        home = own_home.for_test(self)
        path = home.library("photo_index.db")
        schema.ensure(path)
        conn = db.connect(path)
        self.addCleanup(conn.close)
        plain = face_rows.add_face(conn, "C:/Photos/IMG_0001.jpg")
        named = face_rows.add_face(conn, "C:/Photos/IMG_0002.jpg", name="Rowan Thackeray")
        given = face_rows.add_face(conn, "C:/Photos/IMG_0002.jpg", box=(20, 0, 30, 10),
                                   embedding=np.ones(4, dtype=np.float32).tobytes())
        conn.commit()
        held = dict(conn.execute("SELECT f.id, v.vector FROM faces f" + face_rows.VECTOR_OF_FACE))
        self.assertEqual({plain, named, given}, {face for face, vector in held.items() if vector is not None},
                         "a face without a vector under the library's face model is a shape no writer makes")
        self.assertEqual(np.ones(4, dtype=np.float32).tobytes(), held[given], "a vector the test gives is kept")
        made = np.frombuffer(held[plain], dtype=np.float32)
        self.assertEqual(face_rows.FACE_DIMENSION, len(made))
        self.assertAlmostEqual(1.0, float(np.linalg.norm(made)), places=5)   # the model gives its vectors normalized
        self.assertEqual(face_rows.vector_of(plain), held[plain], "the same face is given the same vector in every run")
        other = np.frombuffer(held[named], dtype=np.float32)
        self.assertLess(abs(float(made @ other)), 0.3, "two faces given no vector are as unalike as two strangers")
        with self.assertRaises(ValueError):
            face_rows.add_face(conn, "C:/Photos/IMG_0003.jpg", embedding=b"")


if __name__ == "__main__":
    unittest.main()
