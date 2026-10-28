"""A library stays on the face model it was made on (faces.model scrfd-adaface, a new library's: owner, 2026-10-25,
every library on the new models, a model change a re-embed and a re-name, no mixed-model compat), and the face pass's look
with that model (Runtime.look_at_faces) is held as a run is. What the owner lives through:

- (s) The owner picks FaceNet, the old model, for a library made on the new one, its faces found by the new model: it is
  refused and nothing is written, nor in a dry run; the library stays on its model.
- (f) The face pass's look with the new model (Runtime.look_at_faces), which writes nothing to the library (the pass writes
  with it: a command since deleted): refused beside a Suggest
  holding another set of models (a library on another photo model), a Suggest on that set refused beside it; it takes the
  graphics card once, and lets go of the card and the model at end(), after a failure too.

Fictional names; libraries in a home of the test's own; stand-in models.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402
from face_rows import FACENET  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core import gpu_turns, paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import suggester as suggester_service  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

NEW = validation.SCRFD_ADAFACE


class _Model:
    """A model the runtime builds from a library's settings: on the card or off it, counting its loads, unloads and the
    photos it looked at."""

    def __init__(self, settings, device="cpu"):
        self.settings, self.device = dict(settings), device
        self.loads = self.unloads = self.looked = 0

    def load(self):
        self.loads += 1

    def unload(self):
        self.unloads += 1

    def embed_text(self, text):
        return [0.0]

    def picture(self, path, *, max_side):
        """The processor's half, which uses no model: the path, here."""
        return path

    def detect_and_embed(self, picture):
        self.looked += 1
        return []


class _Suggests:
    """What a run is handed (tagpup.services.suggester.SuggestionModel): one tag a photo."""
    model_key = "stand-in"

    def suggest(self, path, metadata):
        return {"path": path, "suggested_tags": [{"tag": "Activity/Sailing", "score": 0.9}]}


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


class _Libraries(unittest.TestCase):
    """harbour, made as the picker makes a library (on the new face model), holding one folder of real photos, one of
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
        self.built = []
        self.card = gpu.Card(os.path.join(self.home.root, "card"), poll=0.05)
        self.addCleanup(self.card.close)
        self.runtime = Runtime(build_clip=self.build, build_faces=self.build, card=self.card)
        self.forget(self.harbour)

    def forget(self, library):
        self.addCleanup(self.runtime.forget, library)
        self.addCleanup(suggestion_jobs.forget, library)
        self.addCleanup(indexing_jobs.forget, library)

    def build(self, settings):
        # The new face model on the graphics card, as it would be on the owner's machine.
        on_card = settings.get("model") == NEW
        self.built.append(_Model(settings, device="cuda" if on_card else "cpu"))
        return self.built[-1]

    def begin(self, library, **kwargs):
        with mock.patch.object(suggester_service, "model_for_run", lambda *args, **kw: _Suggests()):
            return self.runtime.begin(library, **kwargs)


class TheLibraryStaysOnItsModel(_Libraries):
    """(s) The owner picks FaceNet in TagTuner's settings for harbour (settings.change, as the dialog's route calls it)."""

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


class ALookWithTheNewModel(_Libraries):
    """(f) The face pass's look (Runtime.look_at_faces): the new model's faces, read beside the library's; here it writes
    nothing. dune, on another photo model, holds another set of models while a Suggest runs in it."""

    def setUp(self):
        super().setUp()
        stand_in_models.declare_other_photo_model(self)
        self.dune = Library(self.home.library("dune.db"))
        library_actions.create(self.dune.path)
        self.forget(self.dune)
        changed = library_settings.change(self.dune, stand_in_models.ON_THE_OTHER_PHOTO_MODEL, acknowledged=["clip"])
        self.assertEqual(2, changed.changed, changed.refused)

    def test_it_is_held_as_a_run_and_lets_go_after_a_failure(self):
        suggest = self.begin(self.dune, what="Suggest Shore (dune)")
        try:
            with self.assertRaises(gpu_turns.OtherModelsInUse) as raised:
                self.runtime.look_at_faces(self.harbour)
            self.assertIn("Suggest Shore (dune) is using the CLIP model", str(raised.exception))
        finally:
            suggest.end()

        before = rows_of(self.harbour.path)
        holds = []
        real_hold = self.card.hold

        def hold(*args, **kwargs):
            holds.append(args[0])
            return real_hold(*args, **kwargs)
        look = self.runtime.look_at_faces(self.harbour, what="Looking at Regatta")
        with mock.patch.object(self.card, "hold", side_effect=hold):
            with self.assertRaises(RuntimeError):
                try:
                    with self.assertRaises(gpu_turns.OtherModelsInUse) as raised:
                        self.begin(self.dune)
                    self.assertIn("Looking at Regatta is using the CLIP model", str(raised.exception))
                    for name in sorted(os.listdir(self.regatta)):
                        self.assertEqual([], look.detect_and_embed(look.picture(os.path.join(self.regatta, name),
                                                                                max_side=None)))
                    self.assertTrue(self.card.holds())
                    raise RuntimeError("the look's caller failed part-way")
                finally:
                    look.end()
        new = [model for model in self.built if model.settings.get("model") == NEW]
        self.assertEqual(["Looking at Regatta"], holds, "the card was taken more than once")
        self.assertEqual(3, sum(model.looked for model in new))
        self.assertFalse(self.card.holds(), "the card was kept after the look ended")
        self.assertTrue(all(model.unloads for model in new if model.looked), "the model was kept loaded after the look")
        self.assertEqual(before, rows_of(self.harbour.path))
        # And a Suggest on the other set runs again once it has ended.
        self.begin(self.dune).end()


if __name__ == "__main__":
    unittest.main()
