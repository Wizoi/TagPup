"""A library set to the new face model (faces.model scrfd-adaface; docs/AI_PIPELINE.md, phase 3) writes nothing.

Every face vector the three libraries hold is FaceNet's, kept by no model's name; another model's vectors written beside
them would be compared with them as if alike. Until migration 33 keeps each model's apart, a library switched to the new
model is refused every write of faces, with one sentence (tagpup.services.settings.FACE_MODEL_NOT_KEPT), and nothing of
the model is built or loaded. What the owner lives through:

- (a) Add a folder, or sync: the CLI's index exits 78 with the sentence, having imported no model's library and written
  no row; the server's queue (index_folder) says "Nothing was indexed: ..." instead of an exit code. The model switched
  between the index's first read of the settings and the one it runs by: the same, before the folder is recorded. Add
  in TagPup and
  TagTuner (tagpup.services.libraries.add) answers with the sentence and records no folder as added: one recorded with
  no row would be held, and every sync after would queue its refused index again.
- (b) Suggest, and Just look, in that library: the run ends in an error naming the sentence, and no model is built; the
  start-up warm-up builds nothing for it.
- (c) Identify compares a library's faces by its own model's vectors: the switched library's are FaceNet's, so it
  compares none; the other library's, all. Asked who one of its faces resembles (TagTuner's faces strip and selection
  pane, New Person's seed, TagPup's face boxes), it answers one sentence (identify.NO_VECTOR) with 400, not a 500;
  cluster-faces says how many faces took no part, without an error, and names nothing. Part-way through the new
  model's pass (some faces with its vector, some without), the ones with it are compared and clustered.
- (d) Two libraries, one on each model, in one server: the one on MTCNN suggests; the other is refused, its rows unchanged.
- (e) The model switched while a Suggest runs: the run ends on the models it began with; the next is refused.
- (f) A look with the new model, which writes nothing (Runtime.look_at_faces): refused beside a Suggest, a Suggest refused
  beside it; it takes the graphics card once, and lets go of the card and the model at end(), after a failure too.
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
from face_rows import FACENET, add_vector_of_face  # noqa: E402
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
from tagpup.services import indexing as indexing_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import suggester as suggester_service  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.web import app as web  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SENTENCE = library_settings.FACE_MODEL_NOT_KEPT

#: The CLI's index, run as the server's queue runs it, then the model libraries it imported.
PROBE = (
    "import sys\n"
    "import tagpup_cli\n"
    "try:\n"
    "    tagpup_cli.cli(sys.argv[1:], standalone_mode=False)\n"
    "finally:\n"
    "    print('LOADED=' + ','.join(m for m in ('open_clip', 'torch', 'facenet_pytorch') if m in sys.modules))\n")

#: The CLI's index with the face model switched between its first read of the settings (peek_settings, before anything
#: is written) and the read it runs by (Runtime.settings): a save in the settings dialog at that moment.
SWITCHED_BETWEEN = (
    "import sys\n"
    "from unittest import mock\n"
    "import tagpup_cli\n"
    "from tagpup.core import validation\n"
    "from tagpup.runtime import Runtime\n"
    "from tagpup.services import settings\n"
    "def switched(self, library):\n"
    "    values = dict(settings.read(library).values, **{'faces.model': validation.SCRFD_ADAFACE})\n"
    "    return settings.LibrarySettings(values)\n"
    "with mock.patch.object(Runtime, 'settings', switched):\n"
    "    tagpup_cli.cli(sys.argv[1:], standalone_mode=False)\n")


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

    def faces_in(self, path):
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


class _Libraries(unittest.TestCase):
    """harbour, on MTCNN and FaceNet (the default), and cove, switched to the new model; each holding one folder of real
    photos, one photo of each read by the index and holding two faces FaceNet found."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="new_face_model_")
        self.harbour, self.regatta = self.library("harbour.db", "Regatta")
        self.cove, self.jetty = self.library("cove.db", "Jetty")
        changed = library_settings.change(self.cove, {"faces.model": validation.SCRFD_ADAFACE},
                                          acknowledged=["face-model"])
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
                vector = rng.normal(size=512).astype(np.float32)
                store_faces.insert(conn, read, box, (vector / np.linalg.norm(vector)).tobytes(), prob=0.99, model=FACENET)
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


class TheOwnerIsToldFirst(_Libraries):
    """Before the switch: what the owner is asked to acknowledge says what switching does, and nothing that is not so."""

    def test_the_consequences_say_it_is_refused(self):
        before = rows_of(self.harbour.path)
        refused = library_settings.change(self.harbour, {"faces.model": validation.SCRFD_ADAFACE},
                                          acknowledged=["faces"])
        self.assertEqual(0, refused.changed)
        said = refused.refused
        self.assertIn("every index and every Suggest in this library is refused and writes nothing", said)
        # The face detection settings' consequences are MTCNN's: an index again does not apply the new model.
        self.assertNotIn("Indexing a folder again applies it", said)
        self.assertEqual(before, rows_of(self.harbour.path))


class TheIndexIsRefused(_Libraries):
    """(a) Add a folder, or sync, in the library switched to the new model."""

    def test_the_cli_writes_nothing_and_loads_no_model(self):
        before = rows_of(self.cove.path)
        done = processes.run([sys.executable, "-c", PROBE, "--db", self.cove.path, "index", self.jetty], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(indexing_service.EXIT_FACE_MODEL_NOT_READY, done.returncode, done.stdout + done.stderr)
        self.assertIn("Not run: %s" % SENTENCE, " ".join(done.stdout.split()))
        self.assertEqual("LOADED=", [line for line in done.stdout.splitlines() if line.startswith("LOADED=")][-1])
        self.assertEqual(before, rows_of(self.cove.path))

    def test_switched_between_its_two_reads_it_writes_nothing(self):
        slipway = paths.stored(os.path.join(self.home.root, "Slipway"))
        os.makedirs(slipway)
        Image.new("RGB", (64, 48), (10, 90, 130)).save(os.path.join(slipway, "launch.jpg"), "JPEG")
        before = rows_of(self.harbour.path)
        done = processes.run([sys.executable, "-c", SWITCHED_BETWEEN, "--db", self.harbour.path, "index", slipway],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        self.assertEqual(indexing_service.EXIT_FACE_MODEL_NOT_READY, done.returncode, done.stdout + done.stderr)
        self.assertIn("Not run: %s" % SENTENCE, " ".join(done.stdout.split()))
        self.assertEqual(before, rows_of(self.harbour.path))

    def test_the_servers_queue_says_why_in_words(self):
        before = rows_of(self.cove.path)
        result = indexing_service.index_folder(self.cove, self.jetty, ROOT)
        self.assertFalse(result.ok)
        self.assertEqual("Nothing was indexed: %s" % SENTENCE, result.message())
        self.assertEqual(0, result.details["percent"])
        self.assertEqual(before, rows_of(self.cove.path))


class AddingAFolderIsRefused(_Libraries):
    """(a) Add a folder in the library switched to the new model: TagPup's Add, its index route, TagTuner's Add Folder."""

    ROUTES = (("TagPup's Add", "tagpup", "/cove/api/folder/add", "folder_path"),
              ("TagPup's index route", "tagpup", "/cove/api/folder/index-start", "folder_path"),
              ("TagTuner's Add Folder", "tuner", "/cove/api/folder/index-start", "folder_paths"))

    def setUp(self):
        super().setUp()
        self.slipway = paths.stored(os.path.join(self.home.root, "Slipway"))
        os.makedirs(self.slipway)
        Image.new("RGB", (64, 48), (10, 90, 130)).save(os.path.join(self.slipway, "launch.jpg"), "JPEG")
        # On the code before the refusal, nothing a route queued may start an indexer.
        runner = mock.patch.object(indexing_jobs.IndexQueue, "_ensure_runner")
        runner.start()
        self.addCleanup(runner.stop)

    def test_add_records_nothing_and_queues_nothing(self):
        before = rows_of(self.cove.path)
        queued = []
        result = library_actions.add(self.cove, [self.slipway], lambda folders: queued.append(folders))
        self.assertEqual(SENTENCE, result.refused)
        self.assertEqual(0, result.changed)
        self.assertEqual([], queued)
        self.assertEqual(before, rows_of(self.cove.path))
        self.assertFalse(library_actions.holds(self.cove, self.slipway))

    def test_each_route_shows_the_sentence(self):
        before = rows_of(self.cove.path)
        for name, kind, route, key in self.ROUTES:
            with self.subTest(name):
                app = web.create_app(kind, startup=self.cove)
                app.testing = True
                answer = app.test_client().post(route, json={key: [self.slipway] if key == "folder_paths" else self.slipway})
                self.assertEqual(400, answer.status_code, answer.get_json())
                self.assertEqual(SENTENCE, answer.get_json()["error"])
                self.assertFalse(indexing_jobs.queue_for(self.cove).pending(), "a refused folder was queued")
        self.assertEqual(before, rows_of(self.cove.path))


class SuggestIsRefused(_Libraries):
    """(b) Suggest, and Just look, in the library switched to the new model; (c) Identify beside them."""

    def test_suggest_and_just_look_say_why_and_build_nothing(self):
        before = rows_of(self.cove.path)
        with self.assertLogs("tagpup.jobs.suggestions", "INFO") as logged:
            refused = self.suggest(self.cove, self.jetty)
            looked = self.suggest(self.cove, self.jetty, looking=True)
        # A refusal, not a failure: no traceback in the log.
        self.assertFalse([line for line in logged.output if not line.startswith("INFO")], logged.output)
        self.assertFalse([record for record in logged.records if record.exc_info])
        self.assertEqual("error", refused["status"], refused)
        self.assertIn(SENTENCE, refused["message"])

        self.assertEqual("error", looked["status"], looked)
        self.assertIn("The models could not be made ready, so no photo was analysed and nothing was changed: %s" % SENTENCE,
                      looked["message"])

        for remember in (True, False):
            with self.subTest(remember=remember), self.assertRaises(library_settings.FaceModelNotReady) as raised:
                self.begin(self.cove, remember=remember)
            self.assertEqual(SENTENCE, str(raised.exception))
        with self.assertLogs("tagpup.runtime", "INFO") as logged:
            self.runtime.warm_up([self.cove])
        self.assertIn(SENTENCE, "\n".join(logged.output))
        # The warm-up's refusal is a refusal too, said at INFO.
        self.assertFalse([line for line in logged.output if not line.startswith("INFO")], logged.output)

        self.assertEqual([], self.built, "a model was built for the switched library")
        self.assertEqual([], self.handed)
        self.assertFalse(self.card.holds())
        self.assertEqual(before, rows_of(self.cove.path))

    def test_identify_compares_the_faces_by_the_librarys_own_model(self):
        _stamp, unnamed = identify.unnamed_faces(self.harbour)
        self.assertEqual(2, len(unnamed.ids))
        self.assertEqual((2, 512), tuple(unnamed.matrix.shape))
        _stamp, unnamed = identify.unnamed_faces(self.cove)
        self.assertEqual(0, len(unnamed.ids), "the switched library's vectors are FaceNet's, not its model's")


class IdentifyOnTheSwitchedLibrary(_Libraries):
    """(c) Who a face resembles, and cluster-faces, in the library switched to the new model: before its pass (no face
    with the new model's vector) and part-way through it (one face with it)."""

    def face_ids(self, library):
        conn = db.connect(db.readonly_uri(library.path), uri=True)
        try:
            return [face_id for (face_id,) in conn.execute("SELECT id FROM faces ORDER BY id")]
        finally:
            conn.close()

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

    def test_before_the_pass_it_says_why_and_writes_nothing(self):
        before = rows_of(self.cove.path)
        first, second = self.face_ids(self.cove)
        named = lambda: identify.named_faces(self.cove)[1]   # noqa: E731
        with self.assertRaises(Refused) as raised:
            identify.face_matches(self.cove, first, named)
        self.assertEqual(identify.NO_VECTOR, str(raised.exception))
        with self.assertRaises(Refused):
            identify.unnamed_like(self.cove, second, named=named)
        for (kind, route), answer in self.asked(self.cove, first).items():
            with self.subTest(kind=kind, route=route):
                self.assertEqual(400, answer.status_code, answer.get_data(as_text=True)[:300])
                self.assertEqual(identify.NO_VECTOR, answer.get_json()["error"])

        found, heard, complaints = self.cluster(self.cove)
        self.assertEqual({}, found)
        self.assertIn(("without_vector", 2, 2), heard)
        self.assertEqual([], complaints, "a face with no vector was an error")
        self.assertEqual(before, rows_of(self.cove.path))

    def test_cluster_faces_says_how_many_took_no_part(self):
        before = rows_of(self.cove.path)
        done = processes.run([sys.executable, "-c", PROBE, "--db", self.cove.path, "cluster-faces"], cwd=ROOT,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
        said = " ".join((done.stdout + done.stderr).split())
        self.assertEqual(0, done.returncode, said)
        self.assertIn("2 of 2 face(s) have no vector under this library's face model and took no part", said)
        self.assertIn("No face was newly named.", said)
        self.assertNotIn("Try tagging photos", said)
        self.assertNotIn("Error retrieving faces", said)
        self.assertEqual(before, rows_of(self.cove.path))

    def test_part_way_through_the_pass_the_faces_with_a_vector_take_part(self):
        first, second = self.face_ids(self.cove)
        rng = np.random.default_rng(7)
        vector = rng.normal(size=512).astype(np.float32)
        conn = db.connect(self.cove.path)
        try:
            add_vector_of_face(conn, first, (vector / np.linalg.norm(vector)).tobytes(), model=validation.SCRFD_ADAFACE)
            conn.commit()
        finally:
            conn.close()
        before = rows_of(self.cove.path)

        _stamp, unnamed = identify.unnamed_faces(self.cove)
        self.assertEqual([first], unnamed.ids.tolist())
        conn = db.connect(db.readonly_uri(self.cove.path), uri=True)
        try:
            self.assertEqual([first], [row[0] for row in store_faces.identify_candidates(conn)])
            self.assertEqual([first], [row[0] for row in store_faces.unnamed_for_matching(conn)])
        finally:
            conn.close()
        answers = self.asked(self.cove, first)
        for (kind, route), answer in answers.items():
            with self.subTest(kind=kind, route=route, face="with a vector"):
                self.assertEqual(200, answer.status_code, answer.get_data(as_text=True)[:300])
        self.assertEqual({"matches": []}, answers[("tuner", "/cove/api/face-matches-unmatched")].get_json())
        for (kind, route), answer in self.asked(self.cove, second).items():
            with self.subTest(kind=kind, route=route, face="without"):
                self.assertEqual(400, answer.status_code)
                self.assertEqual(identify.NO_VECTOR, answer.get_json()["error"])

        found, heard, complaints = self.cluster(self.cove)
        self.assertIn(("without_vector", 1, 2), heard)
        self.assertIn("grouping", [stage for stage, _done, _total in heard], "the face with a vector was not clustered")
        self.assertEqual([], complaints)
        self.assertEqual({}, found)
        self.assertEqual(before, rows_of(self.cove.path))


class TwoLibrariesOneOnEachModel(_Libraries):
    """(d) harbour on MTCNN and cove on the new model, in one server."""

    def test_one_suggests_the_other_is_refused_and_unchanged(self):
        before = rows_of(self.cove.path)
        refused = self.suggest(self.cove, self.jetty)
        self.assertEqual("error", refused["status"], refused)
        self.assertIn(SENTENCE, refused["message"])

        done = self.suggest(self.harbour, self.regatta)
        self.assertEqual("completed", done["status"], done)
        [faces] = self.handed
        self.assertEqual(validation.MTCNN_FACENET, faces.settings["model"])

        # While harbour's run holds MTCNN, cove is refused still, and writes nothing.
        held = self.begin(self.harbour)
        try:
            with self.assertRaises((library_settings.FaceModelNotReady, gpu_turns.OtherModelsInUse)):
                self.begin(self.cove)
        finally:
            held.end()
        self.assertEqual([validation.MTCNN_FACENET], sorted({model.settings.get("model") for model in self.built
                                                             if "model" in model.settings}))
        self.assertEqual(before, rows_of(self.cove.path))


class SwitchedWhileSuggestRuns(_Libraries):
    """(e) The owner switches harbour to the new model while a Suggest in it runs."""

    def test_the_run_ends_on_what_it_began_with_and_the_next_is_refused(self):
        run = self.begin(self.harbour, what="Suggest Regatta (harbour)")
        try:
            changed = library_settings.change(self.harbour, {"faces.model": validation.SCRFD_ADAFACE},
                                              acknowledged=["face-model"])
            self.assertEqual(1, changed.changed, changed.refused)
            self.runtime.settings_changed(self.harbour)
            [faces] = self.handed
            self.assertEqual(validation.MTCNN_FACENET, faces.settings["model"])
            self.assertEqual("Activity/Sailing", run.suggest("mooring 1.jpg", {})["suggested_tags"][0]["tag"])
        finally:
            run.end()
        before = rows_of(self.harbour.path)
        with self.assertRaises(library_settings.FaceModelNotReady):
            self.begin(self.harbour)
        self.assertEqual(1, len(self.handed))
        self.assertEqual(before, rows_of(self.harbour.path))


class ALookWithTheNewModel(_Libraries):
    """(f) Phase 3's look (Runtime.look_at_faces): the new model's faces, read beside the library's, writing nothing."""

    def build(self, settings):
        # The new model on the graphics card, as it would be on the owner's machine.
        on_card = settings.get("model") == validation.SCRFD_ADAFACE
        self.built.append(_Model(settings, device="cuda" if on_card else "cpu"))
        return self.built[-1]

    def test_it_is_held_as_a_run_and_lets_go_after_a_failure(self):
        suggest = self.begin(self.harbour, what="Suggest Regatta (harbour)")
        try:
            with self.assertRaises(gpu_turns.OtherModelsInUse) as raised:
                self.runtime.look_at_faces(self.harbour)
            self.assertIn("Suggest Regatta (harbour) is using face models with model %s" % validation.MTCNN_FACENET,
                          str(raised.exception))
        finally:
            suggest.end()
        self.assertEqual([], [model for model in self.built if model.settings.get("model") == validation.SCRFD_ADAFACE])

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
                    self.assertIn("Looking at Regatta is using face models with model %s" % validation.SCRFD_ADAFACE,
                                  str(raised.exception))
                    for name in sorted(os.listdir(self.regatta)):
                        self.assertEqual([], look.faces_in(os.path.join(self.regatta, name)))
                    self.assertTrue(self.card.holds())
                    raise RuntimeError("the look's caller failed part-way")
                finally:
                    look.end()
        [new] = [model for model in self.built if model.settings.get("model") == validation.SCRFD_ADAFACE]
        self.assertEqual(["Looking at Regatta"], holds, "the card was taken more than once")
        self.assertEqual(3, new.looked)
        self.assertFalse(self.card.holds(), "the card was kept after the look ended")
        self.assertTrue(new.unloads, "the new model was kept loaded after the look ended")
        self.assertEqual(before, rows_of(self.harbour.path))
        # Let go, not kept for the next look: that one builds it again.
        self.runtime.look_at_faces(self.harbour).end()
        self.assertEqual(2, len([model for model in self.built if model.settings.get("model") == validation.SCRFD_ADAFACE]))
        # And a Suggest runs again once it has ended.
        self.begin(self.harbour).end()

    def test_it_looks_with_the_new_model_alone(self):
        # MTCNN's model has no faces_in: a look is the new model's, never another named.
        with self.assertRaises(TypeError):
            self.runtime.look_at_faces(self.harbour, model=validation.MTCNN_FACENET)
        self.assertEqual([], self.built)


class TheNewModelsWeightsNeverMade(_Libraries):
    """(g) The new model on an install where its weights were never made: the real model, its folder empty."""

    def build(self, settings):
        if settings.get("model") == validation.SCRFD_ADAFACE:
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
                look.faces_in(os.path.join(self.regatta, "mooring 0.jpg"))
        finally:
            look.end()
        self.assertEqual(scrfd_adaface.missing_weights(self.weights), str(raised.exception))
        self.assertIn(scrfd_adaface.CONVERTER, str(raised.exception))
        self.assertFalse(look.loaded())

        done = self.suggest(self.harbour, self.regatta)
        self.assertEqual("completed", done["status"], done)
        [faces] = self.handed
        self.assertEqual(validation.MTCNN_FACENET, faces.settings["model"])


if __name__ == "__main__":
    unittest.main()
