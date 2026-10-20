"""A library switches to the new face model (faces.model scrfd-adaface; docs/AI_PIPELINE.md, phase 3) once its pass has
reached its faces, and every reader then compares by the new model's vectors alone.

Every face vector the three libraries held was FaceNet's; the new model's are written beside them by the face pass, each
kept under its model's key (migration 33), and every reader reads the library's model's alone. A switch to the new model
before the pass has reached the library would leave its faces with nothing to be compared by, so tagpup.services.settings
.change refuses it, saying the counts (UNPASSED_SHARE_ALLOWED). What the owner lives through:

- (s) The switch: a library more than a twentieth of whose faces are in photos the pass has not reached is refused, with
  how many faces hold a vector under the new model, how many named faces hold none, and how to finish the pass; nothing is
  written, nor in a dry run. A library the pass has nearly covered switches, told the same counts. A library with no
  faces switches. Switching back to FaceNet is always allowed. The consequences the owner ticks say what switching does.
- (c) Identify compares a library's faces by its own model's vectors. In the library switched after its pass, a face the
  pass gave no vector (an old face it matched with nothing it found) is answered one sentence (identify.NO_VECTOR) with
  400, not a 500, wherever it is asked who it resembles (TagTuner's faces strip and selection pane, New Person's seed,
  TagPup's face boxes); cluster-faces says how many faces took no part, without an error; the faces with a vector are
  compared and clustered.
- (d) Two libraries, one on each model, in one server: each Suggest is handed its own library's face model; while one
  holds the card, the other is refused, its rows unchanged.
- (e) The model switched while a Suggest runs: the run ends on the models it began with; the next is handed the new one.
- (f) A look with the new model (Runtime.look_at_faces), which writes nothing keyed by the library's faces.model (the face
  pass writes the new model's vectors and new faces with it, the library on its own model: tests/test_the_face_pass.py,
  tests/test_a_face_pass_from_the_command_line.py): refused beside a Suggest, a Suggest refused beside it; it takes the
  graphics card once, and lets go of the card and the model at end(), after a failure too.
- (g) The new model's weights never made: a look says which are missing and how to make them, and Suggest on MTCNN runs.

Fictional names; libraries in a home of the test's own; stand-in models, except the new model in (g), whose weights are
not there to load.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import FACENET, add_face, add_vector_of_face  # noqa: E402
import photo_rows  # noqa: E402

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from tagpup.core import gpu_turns, paths, processes, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.jobs import suggestions as suggestion_jobs  # noqa: E402
from tagpup.ml import gpu  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.core.result import Refused  # noqa: E402
from tagpup.services import identify  # noqa: E402
from tagpup.services import identities  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import suggester as suggester_service  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.web import app as web  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW = validation.SCRFD_ADAFACE

#: The CLI, run as the server's queue runs it, then the model libraries it imported.
PROBE = (
    "import sys\n"
    "import tagpup_cli\n"
    "try:\n"
    "    tagpup_cli.cli(sys.argv[1:], standalone_mode=False)\n"
    "finally:\n"
    "    print('LOADED=' + ','.join(m for m in ('open_clip', 'torch', 'facenet_pytorch') if m in sys.modules))\n")


class _Model:
    """A model the runtime builds from a library's settings: on the card or off it, counting its loads and unloads."""

    def __init__(self, settings, device="cpu"):
        self.settings, self.device = dict(settings), device
        self.loads = self.unloads = self.looked = 0

    def load(self):
        self.loads += 1

    def unload(self):
        self.unloads += 1

    def embed_text(self, text):
        return [0.0]

    def detect_and_embed_faces(self, path, *, max_side):
        self.looked += 1
        return []


class _Suggests:
    """What a run is handed (tagpup.services.suggester.SuggestionModel): one tag a photo."""
    model_key = "stand-in"

    def suggest(self, path, metadata):
        return {"path": path, "suggested_tags": [{"tag": "Activity/Sailing", "score": 0.9}]}

    def offered(self, suggestion):
        return [{"tag": "Activity/Sailing", "score": 0.9}], [], None

    def consensus(self, suggestions):
        return suggestions


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
    """A unit vector as float32 bytes, as both models store theirs."""
    vector = rng.normal(size=512).astype(np.float32)
    return (vector / np.linalg.norm(vector)).tobytes()


def face_ids(library):
    conn = db.connect(db.readonly_uri(library.path), uri=True)
    try:
        return [face_id for (face_id,) in conn.execute("SELECT id FROM faces ORDER BY id")]
    finally:
        conn.close()


def passed(library, face_ids, seed=3):
    """The face pass's part of `face_ids`: each given a vector under the new model beside its FaceNet one, as
    tagpup.store.faces.give_vectors writes a matched face's."""
    rng = np.random.default_rng(seed)
    conn = db.connect(library.path)
    try:
        for face_id in face_ids:
            add_vector_of_face(conn, face_id, unit(rng), model=NEW)
        conn.commit()
    finally:
        conn.close()


def switch(library, model=NEW, **kwargs):
    return library_settings.change(library, {"faces.model": model}, acknowledged=["face-model"], **kwargs)


class _Libraries(unittest.TestCase):
    """harbour, on MTCNN and FaceNet (the default), and cove, switched to the new model after its pass; each holding one
    folder of real photos, one photo of each read by the index and holding two faces FaceNet found. cove's pass gave
    its first face the new model's vector and matched its second with nothing it found, so the second keeps FaceNet's
    alone (live, 2026-10-20: 1,195 of kr-track's 11,457 faces are such)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="new_face_model_")
        self.harbour, self.regatta = self.library("harbour.db", "Regatta")
        self.cove, self.jetty = self.library("cove.db", "Jetty")
        passed(self.cove, face_ids(self.cove)[:1])
        changed = switch(self.cove)
        self.assertEqual(1, changed.changed, changed.refused)
        self.built = []
        self.card = gpu.Card(os.path.join(self.home.root, "card"), poll=0.05)
        self.addCleanup(self.card.close)
        self.runtime = Runtime(build_clip=self.build, build_faces=self.build, card=self.card)
        for library in (self.harbour, self.cove):
            self.addCleanup(self.runtime.forget, library)
            self.addCleanup(suggestion_jobs.forget, library)
            self.addCleanup(indexing_jobs.forget, library)
        #: The face models each run was handed, as model_for_run is called.
        self.handed = []

    def library(self, file_name, folder_name):
        library = Library(self.home.library(file_name))
        library_actions.create(library.path)
        folder = paths.stored(os.path.join(self.home.root, folder_name))
        os.makedirs(folder)
        library_actions.record_added(library, [folder])
        rng = np.random.default_rng(len(file_name))
        conn = db.connect(library.path)
        try:
            for n in range(3):
                path = os.path.join(folder, "mooring %d.jpg" % n)
                Image.new("RGB", (64, 48), (40 * n, 90, 130)).save(path, "JPEG")
            read = os.path.join(folder, "mooring 0.jpg")
            photo_rows.add_read(conn, read, {"XMP:Subject": ["Boats/Dinghy"]})
            for box in ([2, 2, 30, 30], [32, 4, 60, 34]):
                store_faces.insert(conn, read, box, unit(rng), prob=0.99, model=FACENET)
            conn.commit()
        finally:
            conn.close()
        return library, folder

    def build(self, settings):
        self.built.append(_Model(settings))
        return self.built[-1]

    def photos(self, folder):
        names = sorted(os.listdir(folder))
        return {paths.key(os.path.join(folder, name)): {"path": paths.stored(os.path.join(folder, name))} for name in names}

    def model_for_run(self, photo_index, clip, faces, words, **kwargs):
        self.handed.append(faces)
        return _Suggests()

    def suggest(self, library, folder, looking=False):
        """Suggest (or, `looking`, Just look) in `folder` as the page starts it, to its end: its status."""
        runs = suggestion_jobs.runs_for(library)
        work = suggestion_jobs.work_for(library, lambda: self.photos(folder), self.runtime, looking=looking)
        with mock.patch.object(suggester_service, "model_for_run", self.model_for_run):
            (runs.run_looking if looking else runs.run)(folder, work)
        return runs.status(folder)

    def begin(self, library, **kwargs):
        with mock.patch.object(suggester_service, "model_for_run", self.model_for_run):
            return self.runtime.begin(library, **kwargs)


class TheSwitchWaitsForThePass(_Libraries):
    """(s) The owner switches a library to the new model in TagTuner's settings (settings.change, as the dialog's route
    calls it)."""

    def faces_in(self, library, name, count, new, named=None, seed=5):
        """`count` faces FaceNet found in the photo `name` of the library's folder, read by the index; with `new`, each
        given the new model's vector by the pass; the first named `named`, a person of the tree."""
        rng = np.random.default_rng(seed)
        folder = self.regatta if library is self.harbour else self.jetty
        path = os.path.join(folder, name)
        conn = db.connect(library.path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Boats/Dinghy"]})
            for n in range(count):
                columns = {"name": named, "name_source": "manual"} if named and n == 0 else {}
                face_id = add_face(conn, path, box=(2 * n, 2, 2 * n + 20, 22), embedding=unit(rng), **columns)
                if new:
                    add_vector_of_face(conn, face_id, unit(rng), model=NEW)
            conn.commit()
        finally:
            conn.close()

    def test_a_library_half_passed_is_refused_with_its_counts(self):
        passed(self.harbour, face_ids(self.harbour))
        self.faces_in(self.harbour, "mooring 1.jpg", 2, new=False, named="Marta Quillfeather")
        before = rows_of(self.harbour.path)
        for apply in (False, True):
            with self.subTest(apply=apply):
                refused = switch(self.harbour, apply=apply)
                self.assertEqual(0, refused.changed)
                said = " ".join(refused.refused.split())
                self.assertIn("Nothing was written: 2 of the library's 4 faces hold a vector under %s; 1 named face holds "
                              "none" % NEW, said)
                self.assertIn("2 faces are in photos the pass has not reached", said)
                self.assertIn("face-pass <folder> --apply", said)
                self.assertEqual({"faces": 4, "without": 2, "named_without": 1, "not_passed": 2},
                                 {k: v for k, v in refused.details["face_vectors"].items() if k != "said"})
        self.assertEqual(before, rows_of(self.harbour.path))
        self.assertEqual(FACENET, library_settings.read(self.harbour)["faces.model"])

    def test_a_library_never_passed_is_refused_before_its_consequences_are_asked(self):
        refused = switch(self.harbour)
        self.assertIn("0 of the library's 2 faces hold a vector under %s" % NEW, refused.refused)
        self.assertNotIn("unacknowledged", refused.details)

    def test_a_library_the_pass_has_nearly_covered_switches_told_what_is_left(self):
        passed(self.harbour, face_ids(self.harbour))
        self.faces_in(self.harbour, "mooring 1.jpg", 19, new=True)
        # One photo the pass has not reached, its one face named: 1 of 22 faces, under the share a switch allows.
        self.faces_in(self.harbour, "mooring 2.jpg", 1, new=False, named="Marta Quillfeather", seed=6)
        changed = switch(self.harbour)
        self.assertEqual(1, changed.changed, changed.refused)
        self.assertEqual(NEW, library_settings.read(self.harbour)["faces.model"])
        said = " ".join(changed.details["face_vectors"]["said"].split())
        self.assertIn("21 of the library's 22 faces hold a vector under %s; 1 named face holds none, and would leave the "
                      "named faces Identify compares with until the pass reaches it; 1 face is in photos the pass has not "
                      "reached." % NEW, said)

    def test_a_library_with_no_faces_switches(self):
        dune = Library(self.home.library("dune.db"))
        library_actions.create(dune.path)
        changed = switch(dune)
        self.assertEqual(1, changed.changed, changed.refused)
        self.assertEqual({"faces": 0, "without": 0, "named_without": 0, "not_passed": 0}, changed.details["face_vectors"])

    def test_switching_back_is_always_allowed(self):
        # cove, indexed after its switch: a photo whose three faces hold the new model's vector alone.
        rng = np.random.default_rng(9)
        conn = db.connect(self.cove.path)
        try:
            path = os.path.join(self.jetty, "mooring 1.jpg")
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Boats/Dinghy"]})
            for n in range(3):
                store_faces.insert(conn, path, [2 * n, 2, 2 * n + 20, 22], unit(rng), prob=0.99, model=NEW)
            conn.commit()
        finally:
            conn.close()
        changed = switch(self.cove, FACENET)
        self.assertEqual(1, changed.changed, changed.refused)
        self.assertEqual(FACENET, library_settings.read(self.cove)["faces.model"])
        self.assertEqual(3, changed.details["face_vectors"]["without"])

    def test_the_consequences_say_what_switching_does(self):
        passed(self.harbour, face_ids(self.harbour))
        before = rows_of(self.harbour.path)
        refused = library_settings.change(self.harbour, {"faces.model": NEW}, acknowledged=["faces"])
        self.assertEqual(0, refused.changed)
        said = refused.refused
        self.assertIn("compare faces by the new model's vectors", said)
        self.assertIn("FaceNet's vectors stay and nothing is deleted", said)
        self.assertNotIn("refused", said.replace("Nothing was written", ""))
        # The face detection settings' consequences are MTCNN's: an index again does not apply the new model.
        self.assertNotIn("Indexing a folder again applies it", said)
        self.assertEqual(before, rows_of(self.harbour.path))


class IdentifyOnTheSwitchedLibrary(_Libraries):
    """(c) Who a face resembles, and cluster-faces, in the library switched to the new model after its pass: one face
    with the new model's vector, one the pass gave none."""

    def asked(self, library, face_id):
        """What /api/face-matches (TagPup's and TagTuner's) and /api/face-matches-unmatched (New Person) answer."""
        answers = {}
        for kind, route in (("tagpup", "/cove/api/face-matches"), ("tuner", "/cove/api/face-matches"),
                            ("tuner", "/cove/api/face-matches-unmatched")):
            app = web.create_app(kind, startup=library)
            app.testing = True
            answers[(kind, route)] = app.test_client().get("%s?id=%d" % (route, face_id))
        return answers

    def cluster(self, library):
        """identities.resolve as cluster-faces runs it: (what it named, the stages heard, what it logged at WARNING or
        above)."""
        index = PhotoIndex(db_path=library.path)
        self.assertTrue(index.load())
        heard = []
        try:
            with self.assertLogs("tagpup_cli.faces", "INFO") as logged:
                named = identities.resolve(index, on_step=lambda stage, done=0, total=1: heard.append((stage, done, total)))
        finally:
            index.close()
        return named, heard, [line for line in logged.output if not line.startswith("INFO")]

    def test_identify_compares_the_faces_by_the_librarys_own_model(self):
        _stamp, unnamed = identify.unnamed_faces(self.harbour)
        self.assertEqual(2, len(unnamed.ids))
        self.assertEqual((2, 512), tuple(unnamed.matrix.shape))
        first, _second = face_ids(self.cove)
        _stamp, unnamed = identify.unnamed_faces(self.cove)
        self.assertEqual([first], unnamed.ids.tolist(), "the switched library compares by its model's vectors")

    def test_a_face_the_pass_gave_no_vector_takes_no_part(self):
        first, second = face_ids(self.cove)
        before = rows_of(self.cove.path)
        named = lambda: identify.named_faces(self.cove)[1]   # noqa: E731
        with self.assertRaises(Refused) as raised:
            identify.face_matches(self.cove, second, named)
        self.assertEqual(identify.NO_VECTOR, str(raised.exception))
        with self.assertRaises(Refused):
            identify.unnamed_like(self.cove, second, named=named)
        conn = db.connect(db.readonly_uri(self.cove.path), uri=True)
        try:
            self.assertEqual([first], [row[0] for row in store_faces.identify_candidates(conn)])
            self.assertEqual([first], [row[0] for row in store_faces.unnamed_for_matching(conn, store_faces.model_key(conn))])
        finally:
            conn.close()
        answers = self.asked(self.cove, first)
        for (kind, route), answer in answers.items():
            with self.subTest(kind=kind, route=route, face="with a vector"):
                self.assertEqual(200, answer.status_code, answer.get_data(as_text=True)[:300])
        self.assertEqual({"matches": []}, answers[("tuner", "/cove/api/face-matches-unmatched")].get_json())
        for (kind, route), answer in self.asked(self.cove, second).items():
            with self.subTest(kind=kind, route=route, face="without"):
                self.assertEqual(400, answer.status_code, answer.get_data(as_text=True)[:300])
                self.assertEqual(identify.NO_VECTOR, answer.get_json()["error"])

        found, heard, complaints = self.cluster(self.cove)
        self.assertIn(("without_vector", 1, 2), heard)
        self.assertIn("grouping", [stage for stage, _done, _total in heard], "the face with a vector was not clustered")
        self.assertEqual([], complaints, "a face with no vector was an error")
        self.assertEqual({}, found)
        self.assertEqual(before, rows_of(self.cove.path))

    def test_cluster_faces_says_how_many_took_no_part(self):
        before = rows_of(self.cove.path)
        done = processes.run([sys.executable, "-c", PROBE, "--db", self.cove.path, "cluster-faces"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        said = " ".join((done.stdout + done.stderr).split())
        self.assertEqual(0, done.returncode, said)
        self.assertIn("1 of 2 face(s) have no vector under this library's face model and took no part", said)
        self.assertNotIn("Try tagging photos", said)
        self.assertNotIn("Error retrieving faces", said)
        self.assertEqual(before, rows_of(self.cove.path))


class TwoLibrariesOneOnEachModel(_Libraries):
    """(d) harbour on MTCNN and cove on the new model, in one server."""

    def test_each_suggests_with_its_own_model_and_waits_for_the_other(self):
        for library, folder, model in ((self.cove, self.jetty, NEW), (self.harbour, self.regatta, FACENET)):
            with self.subTest(model=model):
                done = self.suggest(library, folder)
                self.assertEqual("completed", done["status"], done)
                self.assertEqual(model, self.handed[-1].settings["model"])

        # While harbour's run holds MTCNN, cove's is refused, and writes nothing.
        before = rows_of(self.cove.path)
        held = self.begin(self.harbour)
        try:
            with self.assertRaises(gpu_turns.OtherModelsInUse):
                self.begin(self.cove)
        finally:
            held.end()
        self.assertEqual(before, rows_of(self.cove.path))


class SwitchedWhileSuggestRuns(_Libraries):
    """(e) The owner switches harbour, its pass done, to the new model while a Suggest in it runs."""

    def test_the_run_ends_on_what_it_began_with_and_the_next_has_the_new_model(self):
        passed(self.harbour, face_ids(self.harbour))
        run = self.begin(self.harbour, what="Suggest Regatta (harbour)")
        try:
            changed = switch(self.harbour)
            self.assertEqual(1, changed.changed, changed.refused)
            self.runtime.settings_changed(self.harbour)
            [faces] = self.handed
            self.assertEqual(FACENET, faces.settings["model"])
            self.assertEqual("Activity/Sailing", run.suggest("mooring 1.jpg", {})["suggested_tags"][0]["tag"])
        finally:
            run.end()
        self.begin(self.harbour).end()
        self.assertEqual([FACENET, NEW], [faces.settings["model"] for faces in self.handed])


class ALookWithTheNewModel(_Libraries):
    """(f) Phase 3's look (Runtime.look_at_faces): the new model's faces, read beside the library's; here it writes
    nothing (the face pass writes with it, the library on its own model: tests/test_the_face_pass.py)."""

    def build(self, settings):
        # The new model on the graphics card, as it would be on the owner's machine.
        on_card = settings.get("model") == NEW
        self.built.append(_Model(settings, device="cuda" if on_card else "cpu"))
        return self.built[-1]

    def test_it_is_held_as_a_run_and_lets_go_after_a_failure(self):
        suggest = self.begin(self.harbour, what="Suggest Regatta (harbour)")
        try:
            with self.assertRaises(gpu_turns.OtherModelsInUse) as raised:
                self.runtime.look_at_faces(self.harbour)
            self.assertIn("Suggest Regatta (harbour) is using face models with model %s" % FACENET,
                          str(raised.exception))
        finally:
            suggest.end()
        self.assertEqual([], [model for model in self.built if model.settings.get("model") == NEW])

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
                        self.begin(self.harbour)
                    self.assertIn("Looking at Regatta is using face models with model %s" % NEW,
                                  str(raised.exception))
                    for name in sorted(os.listdir(self.regatta)):
                        self.assertEqual([], look.detect_and_embed_faces(os.path.join(self.regatta, name), max_side=None))
                    self.assertTrue(self.card.holds())
                    raise RuntimeError("the look's caller failed part-way")
                finally:
                    look.end()
        [new] = [model for model in self.built if model.settings.get("model") == NEW]
        self.assertEqual(["Looking at Regatta"], holds, "the card was taken more than once")
        self.assertEqual(3, new.looked)
        self.assertFalse(self.card.holds(), "the card was kept after the look ended")
        self.assertTrue(new.unloads, "the new model was kept loaded after the look ended")
        self.assertEqual(before, rows_of(self.harbour.path))
        # Let go, not kept for the next look: that one builds it again.
        self.runtime.look_at_faces(self.harbour).end()
        self.assertEqual(2, len([model for model in self.built if model.settings.get("model") == NEW]))
        # And a Suggest runs again once it has ended.
        self.begin(self.harbour).end()

    def test_it_looks_with_the_new_model_alone(self):
        # A look is the new model's, never another named.
        with self.assertRaises(TypeError):
            self.runtime.look_at_faces(self.harbour, model=FACENET)
        self.assertEqual([], self.built)


class TheNewModelsWeightsNeverMade(_Libraries):
    """(g) The new model on an install where its weights were never made: the real model, its folder empty."""

    def build(self, settings):
        if settings.get("model") == NEW:
            from tagpup.ml.scrfd_adaface import ScrfdAdaFace
            self.built.append(ScrfdAdaFace(device="cpu", folder=self.weights))
            return self.built[-1]
        return super().build(settings)

    def test_a_look_names_them_and_suggest_on_mtcnn_runs(self):
        from tagpup.ml import scrfd_adaface
        self.weights = tempfile.mkdtemp(prefix="weights_", dir=self.home.root)
        look = self.runtime.look_at_faces(self.harbour)
        try:
            with self.assertRaises(FileNotFoundError) as raised:
                look.detect_and_embed_faces(os.path.join(self.regatta, "mooring 0.jpg"), max_side=None)
        finally:
            look.end()
        self.assertEqual(scrfd_adaface.missing_weights(self.weights), str(raised.exception))
        self.assertIn(scrfd_adaface.CONVERTER, str(raised.exception))
        self.assertFalse(look.loaded())

        done = self.suggest(self.harbour, self.regatta)
        self.assertEqual("completed", done["status"], done)
        [faces] = self.handed
        self.assertEqual(FACENET, faces.settings["model"])


if __name__ == "__main__":
    unittest.main()
