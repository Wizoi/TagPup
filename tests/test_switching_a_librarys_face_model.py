"""Scenario V11: a library stays on the face model it is on, the new one (faces.model scrfd-adaface). MTCNN and FaceNet
are retired, and no library is switched to them (owner, 2026-10-25: the new models only, a model change a re-embed and a
re-name, no mixed-model compat; 2026-10-28: a switch to FaceNet is refused for every library, #1987). What the owner
lives through:

- (s) The owner picks FaceNet in TagTuner's gear for a library made on the new model, its faces found by the new model:
  refused with one sentence (settings.NOT_FACENET), nothing written, nor in a dry run; the library stays on its model.
- (t) The same for a library made a moment ago, holding no face yet: it was allowed, nothing counting against it (#1987).
- (u) A library stamped before the face model was a setting holds no faces.model row and reads FaceNet
  (validation.BEFORE_FACE_MODELS); its first switch inserts the row, as the three live libraries' did (their changes
  3244, 342, 1221). That switch is not undone in History: the undo deletes the row and puts FaceNet back. History lists
  it with the sentence and no Undo, and the rehearsal and the undo refuse it so, writing nothing (#1781: it was undone
  while FaceNet's vectors stood).

Fictional names; libraries in a home of the test's own.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import FACENET  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

NEW = validation.SCRFD_ADAFACE


def rows_of(db_path):
    """Every row of every table of the library, read-only: what a refused write must leave as it was."""
    conn = db.connect(db.readonly_uri(db_path), uri=True)
    try:
        tables = [name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
                  if not name.startswith("sqlite_")]
        return {table: sorted(map(repr, conn.execute('SELECT * FROM "%s"' % table))) for table in tables}
    finally:
        conn.close()


def unit(rng):
    """A unit vector as float32 bytes, as the model stores its."""
    vector = rng.normal(size=512).astype(np.float32)
    return (vector / np.linalg.norm(vector)).tobytes()


def refused_with_nothing_written(case, library):
    """FaceNet picked for `library`, its consequences ticked, rehearsed and applied (settings.change, as the gear's route
    calls it): refused with the sentence, every row as it was, the library on the new model."""
    before = rows_of(library.path)
    for apply in (False, True):
        with case.subTest(apply=apply):
            refused = library_settings.change(library, {"faces.model": FACENET}, acknowledged=["face-model"],
                                              apply=apply)
            case.assertEqual((0, library_settings.NOT_FACENET), (refused.changed, refused.refused))
    case.assertEqual(before, rows_of(library.path))
    case.assertEqual(NEW, library_settings.read(library)["faces.model"])


class TheLibraryStaysOnItsModel(unittest.TestCase):
    """(s) and (u). harbour, made as the picker makes a library (on the new face model), holding one folder of real
    photos, one of them read by the index and holding two faces the new model found (their vectors its alone, as in the
    live libraries)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="new_face_model_")
        self.harbour = Library(self.home.library("harbour.db"))
        library_actions.create(self.harbour.path)
        self.regatta = paths.stored(os.path.join(self.home.root, "Regatta"))
        os.makedirs(self.regatta)
        library_actions.record_added(self.harbour, [self.regatta])
        rng = np.random.default_rng(7)
        conn = db.connect(self.harbour.path)
        try:
            for n in range(3):
                Image.new("RGB", (64, 48), (40 * n, 90, 130)).save(os.path.join(self.regatta, "mooring %d.jpg" % n), "JPEG")
            read = os.path.join(self.regatta, "mooring 0.jpg")
            photo_rows.add_read(conn, read, {"XMP:Subject": ["Boats/Dinghy"]})
            for box in ([2, 2, 30, 30], [32, 4, 60, 34]):
                store_faces.insert(conn, read, box, unit(rng), prob=0.99, model=NEW)
            conn.commit()
        finally:
            conn.close()

    def test_facenet_is_refused_and_nothing_is_written(self):
        refused_with_nothing_written(self, self.harbour)

    def test_the_first_switch_of_a_library_stamped_before_the_setting_is_not_undone(self):
        held = "SELECT value FROM settings WHERE key = 'faces.model'"
        conn = db.connect(self.harbour.path)
        try:
            # Stamped before faces.model was a setting: the row is not there (made so here, today's stamp writing it).
            conn.execute("DELETE FROM settings WHERE key = 'faces.model'")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(FACENET, library_settings.read(self.harbour)["faces.model"])
        switched = library_settings.change(self.harbour, {"faces.model": NEW}, acknowledged=["face-model"])
        self.assertEqual(1, switched.changed, switched.refused)
        change = switched.details["change"]
        before = rows_of(self.harbour.path)
        self.assertEqual({change: library_settings.NOT_FACENET},
                         journal_service.refusals(self.harbour, [{"id": change}]), "History offers the Undo")
        for apply in (False, True):
            with self.subTest(apply=apply):
                self.assertEqual(library_settings.NOT_FACENET,
                                 journal_service.undo(self.harbour, change, apply=apply).refused)
        self.assertEqual(before, rows_of(self.harbour.path))
        self.assertEqual(NEW, library_settings.read(self.harbour)["faces.model"])
        conn = db.connect(db.readonly_uri(self.harbour.path), uri=True)
        try:
            self.assertEqual([(NEW,)], conn.execute(held).fetchall())
        finally:
            conn.close()


class ALibraryWithNoFaceYet(unittest.TestCase):
    """(t) meadow, made a moment ago as the picker makes a library, holding no face."""

    def test_facenet_is_refused_and_nothing_is_written(self):
        home = own_home.for_test(self, prefix="new_face_model_empty_")
        meadow = Library(home.library("meadow.db"))
        library_actions.create(meadow.path)
        refused_with_nothing_written(self, meadow)


if __name__ == "__main__":
    unittest.main()
