"""A library stays on the face model it was made on (faces.model scrfd-adaface, a new library's: owner, 2026-10-25,
every library on the new models, a model change a re-embed and a re-name, no mixed-model compat). What the owner
lives through:

- (s) The owner picks FaceNet, the old model, for a library made on the new one, its faces found by the new model: it is
  refused and nothing is written, nor in a dry run; the library stays on its model.

Fictional names; a library in a home of the test's own.
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


class TheLibraryStaysOnItsModel(unittest.TestCase):
    """(s) The owner picks FaceNet in TagTuner's settings for harbour (settings.change, as the dialog's route
    calls it). harbour, made as the picker makes a library (on the new face model), holding one folder of real photos, one of
    them read by the index and holding two faces the new model found."""

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
        before = rows_of(self.harbour.path)
        for apply in (False, True):
            with self.subTest(apply=apply):
                refused = library_settings.change(self.harbour, {"faces.model": FACENET}, acknowledged=["face-model"],
                                                  apply=apply)
                self.assertEqual(0, refused.changed)
                self.assertTrue(refused.refused)
        self.assertEqual(before, rows_of(self.harbour.path))
        self.assertEqual(NEW, library_settings.read(self.harbour)["faces.model"])


if __name__ == "__main__":
    unittest.main()
