"""The owner deletes the rows the old face detector (MTCNN) left in a library on the new face model (TagPup CLI:
delete-old-detector-rows, tagpup.services.old_detector_rows; owner, 2026-10-26). What the owner lives through:

- The dry run counts the "Face detection" settings and the photos' records of detection under MTCNN's name, says the
  rehearsal's undo was exact, and writes nothing.
- Applied, after a snapshot, one change of the journal deletes them; faces.model, the records under the new model's name
  and every face stay; what the library's settings read is what it read before (the defaults are MTCNN's values).
- History offers its undo, which puts every row back exactly; applied again, a detection recorded since on one of its
  photos makes its undo refused, naming the row, nothing written.
- A detection recorded between the plan and the write refuses the whole change, nothing written; a run again deletes.
- A library holding none says so and writes nothing, no snapshot; a library still on the old model is refused.
- A dry run whose rehearsal raised says what failed and exits non-zero, nothing written, no snapshot.

A library made by Create, in a home of the test's own; photo rows as the indexer stores them (photo_rows), detections as
it records them (store.faces_detected.record, under the names services.faces.detector_of gives). Fictional names.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face, on_facenet  # noqa: E402

from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.ml.scrfd_adaface import ScrfdAdaFace  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import maintenance  # noqa: E402
from tagpup.services import old_detector_rows  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, faces_detected, schema  # noqa: E402

#: MTCNN's detector name as the index recorded it (its settings), and the new model's (its key alone).
MTCNN = faces_detected.detector({"min_face_size": 20, "confidence_threshold": 0.85, "mtcnn_thresholds": [0.6, 0.7, 0.7]})
NEW = faces_detected.detector({"model": validation.SCRFD_ADAFACE})


def rows_of(db_path, but=("changes", "change_rows", "generations")):
    """Every row of every table but the journal's, read-only."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                  if not name.startswith("sqlite_") and name not in but]
        return {table: sorted(map(repr, conn.execute('SELECT * FROM "%s"' % table))) for table in tables}
    finally:
        conn.close()


def read(db_path, sql, params=()):
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


class DeletingTheOldDetectorsRows(unittest.TestCase):
    """Three photos read by the index: a heron MTCNN found two faces in (the new model's face on it now), a gull it found
    none in, and a tern the new model's pass recorded."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="old_detector_rows_")
        self.library = Library(self.home.library("harbour.db"))
        self.assertEqual(1, library_actions.create(self.library.path).changed)
        self.paths = [os.path.join(self.home.root, "Harbour", name) for name in ("heron.jpg", "gull.jpg", "tern.jpg")]
        conn = db.connect(self.library.path)
        try:
            for path in self.paths:
                photo_rows.add_read(conn, path, {"XMP:Subject": ["Birds"]})
            for path in (self.paths[0], self.paths[2]):
                add_face(conn, path, model=validation.SCRFD_ADAFACE)
            conn.commit()
        finally:
            conn.close()
        self.record({self.paths[0]: (MTCNN, 2), self.paths[1]: (MTCNN, 0), self.paths[2]: (NEW, 1)})

    def record(self, detections):
        """Each photo's detection {path: (detector name, faces found)}, as the index and Suggest record it."""
        conn = db.connect(self.library.path)
        try:
            for path, (name, found) in detections.items():
                faces_detected.record(conn, path, name, None, found)
            conn.commit()
        finally:
            conn.close()

    def cli(self, *args):
        return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "delete-old-detector-rows"] + list(args))

    def held(self):
        return {key for (key,) in read(self.library.path, "SELECT key FROM settings")}

    def records(self):
        return read(self.library.path, "SELECT detector, COUNT(*) FROM faces_detected GROUP BY detector ORDER BY detector")

    def changes(self):
        return read(self.library.path, "SELECT id, operation, status FROM changes ORDER BY id")

    def test_the_owner_deletes_them_undoes_it_and_deletes_them_again(self):
        # The rule tells MTCNN's names by the absence of a `model` key: the new model's name, as the index records it.
        self.assertEqual(NEW, face_service.detector_of(object.__new__(ScrfdAdaFace), None))
        before, changes, settings = rows_of(self.library.path), self.changes(), library_settings.read(self.library)
        # A new library is stamped with the group's four (the live ones hold three: no faces.detect_max_side).
        self.assertLessEqual(set(old_detector_rows.OLD_SETTINGS), self.held())

        dry = self.cli()
        self.assertEqual(0, dry.exit_code, dry.output)
        said = " ".join(dry.output.split())
        self.assertIn("4 setting(s), 2 detection record(s) under 1 name(s); 1 record(s) under the library's own", said)
        self.assertIn("the undo restored every one exactly", said)
        self.assertEqual((before, changes), (rows_of(self.library.path), self.changes()), "a dry run wrote")
        self.assertEqual([], os.listdir(self.library.backups) if os.path.isdir(self.library.backups) else [])

        applied = self.cli("--apply")
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertIn("Deleted 6 row(s): 4 setting(s), 2 detection record(s).", " ".join(applied.output.split()))
        self.assertTrue(os.listdir(self.library.backups), "no snapshot was taken first")
        self.assertEqual([(NEW, 1)], self.records())
        self.assertEqual(set(), set(old_detector_rows.OLD_SETTINGS) & self.held())
        self.assertIn(library_settings.FACE_MODEL, self.held())
        self.assertEqual(settings, library_settings.read(self.library), "what the settings read changed")
        self.assertEqual(before["faces"], rows_of(self.library.path)["faces"])
        (change_id, operation, status) = self.changes()[-1]
        self.assertEqual((old_detector_rows.OPERATION, "applied"), (operation, status))

        listed = {entry["id"]: entry["why_not"] for entry in journal_service.history(self.library)["changes"]}
        self.assertIsNone(listed[change_id], "History does not offer its undo")
        undone = journal_service.undo(self.library, change_id, apply=True)
        self.assertFalse(undone.refused, undone.refused)
        self.assertEqual(6, undone.changed)
        self.assertEqual(before, rows_of(self.library.path), "the undo did not put every row back")

        again = old_detector_rows.delete(self.library, apply=True)
        self.assertEqual({"settings": 4, "faces_detected": 2}, again.details["changed"])
        self.assertEqual({"settings": 0, "faces_detected": 0}, again.details["remaining"])
        # Suggest detects the gull again, under the new model, after the deletion: the undo would put MTCNN's back over it.
        self.record({self.paths[1]: (NEW, 0)})
        after = rows_of(self.library.path)
        refused = journal_service.undo(self.library, again.details["change"], apply=True)
        self.assertIn("faces_detected", refused.refused or "")
        self.assertEqual(after, rows_of(self.library.path), "a refused undo wrote")

        none = self.cli("--apply")
        self.assertEqual(0, none.exit_code, none.output)
        self.assertIn("Nothing to delete", none.output)
        self.assertEqual(after, rows_of(self.library.path))

    def test_a_detection_recorded_between_the_plan_and_the_write_refuses_the_change(self):
        planned = old_detector_rows._plan

        def a_detection_meanwhile(library):
            found = planned(library)
            self.record({self.paths[0]: (NEW, 1)})
            return found
        before_changes = self.changes()
        with mock.patch.object(old_detector_rows, "_plan", a_detection_meanwhile):
            refused = old_detector_rows.delete(self.library, apply=True)
        self.assertTrue(refused.refused)
        self.assertEqual(before_changes, self.changes())
        self.assertEqual(sorted([(MTCNN, 1), (NEW, 2)]), self.records(), "part of the change was written")
        self.assertEqual(5, old_detector_rows.delete(self.library, apply=True).changed, "a run again did not delete")

    def test_a_dry_run_whose_rehearsal_raised_says_what_failed(self):
        before = rows_of(self.library.path)
        with mock.patch.object(maintenance.journal, "rehearse", side_effect=OSError("the disk went away")):
            dry = self.cli()
        self.assertEqual(1, dry.exit_code, dry.output)
        self.assertIn("FAILED: the rehearsal: OSError: the disk went away", " ".join(dry.output.split()))
        self.assertNotIn("--apply deletes them", dry.output)
        self.assertEqual(before, rows_of(self.library.path), "a dry run wrote")
        self.assertEqual([], os.listdir(self.library.backups) if os.path.isdir(self.library.backups) else [])

    def test_a_library_still_on_the_old_model_is_refused(self):
        old = Library(self.home.library("estuary.db"))
        schema.ensure(old.path)
        on_facenet(old.path)
        before = rows_of(old.path)
        result = CliRunner().invoke(tagpup_cli.cli, ["--db", old.path, "delete-old-detector-rows", "--apply"])
        self.assertEqual(1, result.exit_code, result.output)
        self.assertIn("is on %s" % validation.MTCNN_FACENET, " ".join(result.output.split()))
        self.assertEqual(before, rows_of(old.path))


if __name__ == "__main__":
    unittest.main()
