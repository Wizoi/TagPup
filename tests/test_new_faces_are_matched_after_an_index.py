"""An index is followed by matching the faces it made, from every entry point, and nothing is written (owner, DECISIONS
2026-10-23; it was a full clustering, DECISIONS 2026-10-16, #1544).

The CLI's `index` decides, the one place every entry point reaches -- TagPup's Add and its index route, TagTuner's
index route and sync's new files all run that command through tagpup.services.indexing.index_folder, and the owner runs
it by hand for long indexes:

- after an index that recorded a photo, the faces it made (the ids between the two reads of the newest face, taken
  while it held the card's turn) are matched with the faces a person decided by Re-examine's matcher
  (tagpup.services.faces.automatch_new), and it says, counted, how many have a likely name, or that none does, or that
  no new face waits, and how many photos carry a person's keyword no face has; the server ends the folder's message
  with that sentence, read whole however long it is (indexing.NEW_FACES);
- it writes no face, no row and no file: a face named in Identify meanwhile keeps its name and nothing is refused;
- a matching that fails says so on the same line, names Re-examine and cluster-faces, and the run exits 0: the photos
  are indexed;
- two libraries holding one folder each count only the faces their own index made;
- an index that recorded nothing says nothing of faces; stopped while it waited for the card, it indexed nothing;
- the roots changed while it indexes is the one roots stop; the line is read however the server's environment asks
  for colour.

The server's half stands in for the CLI's process (its stdout is the interface, contract row 16); the CLI's half is the
real command in this process with its models stood in for (nothing loads; a face detector whose vectors are the test's)
and the real ExifTool. Each CLI test here was run against the code before this change and failed there, except the
cancelled one.

Gone with the clustering: its exit code (77), the bar at 95 while it ran, the flag that refused writes of faces from
its line on, and the clustering run once with the card's turn let go.
"""
import io
import os
import sys
import unittest
from unittest import mock
from unittest.mock import MagicMock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import face_rows  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from stand_in_models import NewFaces  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402
from rich.console import Console  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import fields, gpu_turns, paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.ml import clip  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import indexing  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import app as web  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000


class _TheCli:
    """A stand-in for the CLI's process: prints `lines`, exits with `code`; `during()` is called as each line is read --
    what a person clicking meanwhile meets."""

    def __init__(self, lines, code=0, during=None):
        self.lines, self.code, self.during = list(lines), code, during
        self.commands, self.environments = [], []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.environments.append(kwargs.get("env"))
        proc = MagicMock()
        proc.returncode = self.code
        remaining = iter(self.lines + [""])

        def readline():
            line = next(remaining)
            if line and self.during:
                self.during()
            return line
        proc.stdout.readline.side_effect = readline
        return proc


#: What the CLI says of the faces it made: longer than the bar shows (90 characters).
MATCHED = ("2 of the 3 new faces have a likely name: open Re-examine on their folder to confirm them. 1 photo(s) carry a "
           "person's keyword that no face has: %s, or %s, names them." % (indexing.CLUSTER_ELSEWHERE, indexing.CLUSTER_AGAIN))
INDEXED = ["Generating embeddings: 100%|##########| 1/1 [00:01<00:00]\n", indexing.NEW_FACES + MATCHED + "\n"]
NOTHING_NEW = ["All images are up to date! Index is current.\n"]


class EveryEntryPointOfTheServer(unittest.TestCase):
    """TagPup's Add and index route, TagTuner's index route, and sync's new files: the CLI's index, which ends with
    what its new faces matched, and writes of faces taken from both apps meanwhile."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="index_matches_")
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.apps = {}
        for kind in ("tagpup", "tuner"):
            app = web.create_app(kind, startup=self.library)
            app.testing = True
            self.apps[kind] = app.test_client()
        self.folder = paths.stored(os.path.join(self.home.root, "Harbourview Regatta"))
        os.makedirs(self.folder)
        # A photo the library has, and a new one beside it: what sync finds.
        known = os.path.join(self.folder, "regatta_01.jpg")
        for name in ("regatta_01.jpg", "regatta_02.jpg"):
            with open(os.path.join(self.folder, name), "wb") as handle:
                handle.write(b"jpeg")
            os.utime(os.path.join(self.folder, name), (THEN, THEN))
        db.write_with_connection(self.library.path, lambda conn: photo_rows.add_read(conn, known, {}))
        runner = mock.patch.object(indexing_jobs.IndexQueue, "_ensure_runner")
        runner.start()
        self.addCleanup(runner.stop)

    ENTRY_POINTS = (
        ("TagPup's Add", "tagpup", "/harbour/api/folder/add", "folder_path"),
        ("TagPup's index route", "tagpup", "/harbour/api/folder/index-start", "folder_path"),
        ("TagTuner's index route", "tuner", "/harbour/api/folder/index-start", "folder_paths"),
        ("sync's new files", "tuner", "/harbour/api/sync", None),
    )

    def ask(self, kind, route, key):
        body = {"apply": True} if key is None else {key: [self.folder] if key == "folder_paths" else self.folder}
        answer = self.apps[kind].post(route, json=body)
        self.assertEqual(200, answer.status_code, answer.get_json())
        queue = indexing_jobs.queue_for(self.library)
        self.assertTrue(queue.pending(), "nothing was queued")
        queue.run_pending()
        return queue.status(self.folder)

    def writes_of_faces_meanwhile(self, seen):
        """TagTuner's and TagPup's answers to a write of a face, as each line is read."""
        def during():
            seen.append((self.apps["tuner"].post("/harbour/api/face/unmatch", json={"face_id": 1}).status_code,
                         self.apps["tagpup"].post("/harbour/api/face/unmatch", json={"face_id": 1}).status_code))
        return during

    def test_each_index_ends_with_what_its_new_faces_matched_and_refuses_no_write_of_a_face(self):
        for name, kind, route, key in self.ENTRY_POINTS:
            with self.subTest(name):
                indexing_jobs.forget(self.library)
                seen = []
                cli = _TheCli(INDEXED, during=self.writes_of_faces_meanwhile(seen))
                with mock.patch("subprocess.Popen", side_effect=cli):
                    status = self.ask(kind, route, key)
                self.assertEqual("completed", status["status"], status)
                # Whole, though the bar shows 90 characters of a line.
                self.assertEqual("Folder indexed. " + MATCHED, status["message"])
                self.assertEqual(100, status["percent"])
                # One process: the CLI's index, which matched after itself; no second command.
                self.assertEqual([["index"]], [[c[2]] for c in cli.commands], cli.commands)
                self.assertTrue(seen, "nothing was read")
                self.assertNotIn(409, [answer for answers in seen for answer in answers],
                                 "a write of a face was refused while the folder was indexed: %s" % seen)

    def test_an_index_that_recorded_nothing_says_nothing_of_faces(self):
        with mock.patch("subprocess.Popen", side_effect=_TheCli(NOTHING_NEW)):
            status = self.ask("tuner", "/harbour/api/folder/index-start", "folder_paths")
        self.assertEqual("completed", status["status"], status)
        self.assertEqual("Folder indexed: no photo was added or read again.", status["message"])

    def test_the_roots_changed_while_it_indexes_is_the_one_roots_stop(self):
        with mock.patch("subprocess.Popen", side_effect=_TheCli(INDEXED[:1] + [roots.STOPPED + "\n"],
                                                               code=roots.EXIT_ROOTS_CHANGED)):
            status = self.ask("tuner", "/harbour/api/folder/index-start", "folder_paths")
        self.assertEqual("failed", status["status"], status)
        self.assertEqual(roots.STOPPED, status["message"])

    def test_the_line_is_read_when_the_servers_environment_asks_for_colour(self):
        """rich colours a line when it takes the pipe for a terminal (FORCE_COLOR): the line the CLI prints, as it prints
        it, under the environment the server hands it, is read whole."""
        probe = _TheCli(NOTHING_NEW)
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}), mock.patch("subprocess.Popen", side_effect=probe):
            indexing.index_folder(self.library, self.folder, self.home.root)
        printed = io.StringIO()
        Console(file=printed, _environ=probe.environments[0]).print(indexing.NEW_FACES + MATCHED, markup=False,
                                                                    soft_wrap=True, highlight=False)
        with mock.patch("subprocess.Popen", side_effect=_TheCli([printed.getvalue()])):
            result = indexing.index_folder(self.library, self.folder, self.home.root)
        self.assertEqual("Folder indexed. " + MATCHED, result.details["message"])


class _Hold:
    """The CLI's turn on the graphics card, as the runtime hands it (tagpup.ml.gpu.Hold)."""

    def release(self):
        pass


class _Detector(NewFaces):
    """A face detector filing its vectors under the key a new library's faces are read under (FaceNet's, the validation
    default), each vector the unit vector of its seed's axis: two seeds alike or nothing alike."""
    key = validation.MTCNN_FACENET

    @staticmethod
    def vector(seed, size=8):
        return [1.0 if i == seed else 0.0 for i in range(size)]


def _bytes(seed):
    return np.asarray(_Detector.vector(seed), dtype=np.float32).tobytes()


#: A face's box in a 64 by 64 photo: above the detector's floor (tagpup.ml.scrfd_adaface.NEW_FACE_FLOOR).
BOX, OTHER_BOX = (2, 2, 58, 58), (6, 6, 62, 62)


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheCliIndexByHand(unittest.TestCase):
    """`tagpup_cli.py index`, as the owner runs a long index, and as the server's queue runs it: two people named by hand
    on an earlier photo, Rowan Thackeray (seed 0) and Imogen Vale (seed 1)."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="cli_index_matches_")
        self.db_path = self.make_library("harbour.db")
        self.folder = self.photos_in("Regatta", "one.jpg", "two.jpg")
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "picture_input"),
                        mock.patch.object(clip.ClipModel, "embed_input", return_value=[0.1] * 8)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.detector = _Detector({"one.jpg": [(BOX, 0.99, 0)], "two.jpg": [(BOX, 0.99, 5)]})
        self.cancel = False

        def gpu_turn(runtime, what, models, cancelled=None, report=None):
            if self.cancel:
                raise gpu_turns.Cancelled("stopped")
            return _Hold()
        for patcher in (mock.patch.object(Runtime, "gpu_turn", gpu_turn),
                        mock.patch.object(Runtime, "faces", lambda runtime, library, settings=None: self.detector)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_library(self, name, named=True):
        """A library holding two earlier photos: the first's two faces named by hand when `named`, and an unnamed face
        on the second either way."""
        path = self.home.library(name)
        library_actions.create(path)
        earlier, later = (os.path.join(self.home.root, "Earlier", "%s.%s.jpg" % (name, which)) for which in "ab")

        def seed(conn):
            photo_rows.add_read(conn, earlier, {})
            photo_rows.add_read(conn, later, {})
            if named:
                face_rows.add_face(conn, earlier, box=BOX, embedding=_bytes(0), name="Rowan Thackeray",
                                   name_source="manual")
                face_rows.add_face(conn, earlier, box=OTHER_BOX, embedding=_bytes(1), name="Imogen Vale",
                                   name_source="manual")
            face_rows.add_face(conn, later, box=(0, 0, 50, 50), embedding=_bytes(1))
        db.write_with_connection(path, seed)
        return path

    def photos_in(self, folder_name, *names):
        folder = paths.stored(os.path.join(self.home.root, folder_name))
        os.makedirs(folder, exist_ok=True)
        for name in names:
            Image.new("RGB", (64, 64), "navy").save(os.path.join(folder, name), "JPEG")
        return folder

    def index(self, folder=None, db_path=None):
        return CliRunner().invoke(tagpup_cli.cli, ["--db", db_path or self.db_path, "index", folder or self.folder])

    def query(self, sql, params=(), db_path=None):
        conn = db.connect(db.readonly_uri(db_path or self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def faces(self, db_path=None):
        """{(photo file name, box): (name, name_source)} of every face."""
        return {(os.path.basename(path), box): (name, source) for path, box, name, source in self.query(
            "SELECT p.path, f.box, f.name, f.name_source FROM " + face_rows.FACES_WITH_PATHS, db_path=db_path)}

    def said(self, result):
        lines = [line for line in result.output.splitlines() if line.startswith(indexing.NEW_FACES)]
        return [line[len(indexing.NEW_FACES):] for line in lines]

    def keywords(self, path):
        with ExifToolSession(executable=EXIFTOOL) as et:
            return fields.field_values(et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject"))

    def test_a_new_face_is_proposed_and_a_face_named_in_identify_meanwhile_keeps_its_name(self):
        library = Library(self.db_path)
        (waiting,) = [face_id for (face_id,) in self.query("SELECT id FROM faces WHERE name IS NULL")]
        named_meanwhile = []
        detect = self.detector.detect_and_embed

        def detect_and_name(picture):
            # As the index reaches the second photo, the owner names the earlier unnamed face in Identify.
            if os.path.basename(picture.path) == "two.jpg":
                named_meanwhile.append(face_service.name_face(library, waiting, "Imogen Vale"))
            return detect(picture)
        self.detector.detect_and_embed = detect_and_name
        result = self.index()
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(["1 of the 2 new faces have a likely name: open Re-examine on their folder to confirm them."],
                         self.said(result))
        self.assertTrue(named_meanwhile and named_meanwhile[0].ok and named_meanwhile[0].changed == 1,
                        "the name given in Identify was refused")
        found = self.faces()
        self.assertEqual(("Imogen Vale", "manual"), found[("harbour.db.b.jpg", "[0, 0, 50, 50]")])
        # Proposed, not written: the new faces wait unnamed, and no file was given a keyword.
        self.assertEqual({(None, None)}, {found[(name, "[2, 2, 58, 58]")] for name in ("one.jpg", "two.jpg")})
        for name in ("one.jpg", "two.jpg"):
            self.assertEqual([], self.keywords(os.path.join(self.folder, name)))

        again = self.index()
        self.assertEqual(0, again.exit_code, again.output)
        self.assertIn("All images are up to date! Index is current.", again.output)
        self.assertEqual([], self.said(again), "an index that recorded nothing spoke of faces")

    def test_a_folder_of_strangers_and_a_folder_without_faces_are_said_as_they_are(self):
        self.detector.found = {"one.jpg": [(BOX, 0.99, 5)], "two.jpg": [(BOX, 0.99, 6)]}
        strangers = self.index()
        self.assertEqual(0, strangers.exit_code, strangers.output)
        self.assertEqual(["None of the 2 new faces looks enough like anyone named to be proposed: they wait in "
                          "Identify Faces."], self.said(strangers))
        faceless = self.index(self.photos_in("Harbour Wall", "wall.jpg"))
        self.assertEqual(0, faceless.exit_code, faceless.output)
        self.assertEqual(["No new face is waiting for a name."], self.said(faceless))

    def test_a_photo_known_by_its_keyword_alone_is_counted_and_not_named(self):
        quay = self.photos_in("Quay", "quay.jpg")
        with ExifToolSession(executable=EXIFTOOL) as et:
            et.set_tags([os.path.join(quay, "quay.jpg")], tags={"XMP:Subject": ["Rowan Thackeray"]},
                        params=["-overwrite_original"])
        # Two strangers' faces and one keyword person: nobody to give the keyword to unasked.
        self.detector.found = {"quay.jpg": [(BOX, 0.99, 5), (OTHER_BOX, 0.98, 6)]}
        result = self.index(quay)
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(["None of the 2 new faces looks enough like anyone named to be proposed: they wait in Identify "
                          "Faces. 1 photo(s) carry a person's keyword that no face has: %s, or %s, may name them."
                          % (indexing.CLUSTER_ELSEWHERE, indexing.CLUSTER_AGAIN)], self.said(result))
        self.assertEqual({(None, None)}, {named for (name, _box), named in self.faces().items() if name == "quay.jpg"})

    def test_a_matching_that_fails_says_so_names_the_ways_on_and_the_photos_stay(self):
        with mock.patch.object(face_service, "_closest_named", side_effect=RuntimeError("out of memory")):
            result = self.index()
        self.assertEqual(0, result.exit_code, result.output)
        self.assertEqual(["The photos are indexed, but matching their faces failed: RuntimeError: out of memory. Open "
                          "Re-examine on their folder, or run %s." % indexing.CLUSTER_AGAIN], self.said(result))
        self.assertEqual(4, self.query("SELECT COUNT(*) FROM photos")[0][0])
        # The earlier unnamed face and the two new ones: nothing was named.
        self.assertEqual(3, self.query("SELECT COUNT(*) FROM faces WHERE name IS NULL")[0][0])

    def test_two_libraries_holding_one_folder_each_count_their_own_new_faces(self):
        other = self.make_library("north.db", named=False)
        here = self.index()
        there = self.index(db_path=other)
        self.assertEqual((0, 0), (here.exit_code, there.exit_code), (here.output, there.output))
        self.assertEqual(["1 of the 2 new faces have a likely name: open Re-examine on their folder to confirm them."],
                         self.said(here))
        # Its own two new faces, not its earlier one, and nobody named there to liken them to.
        self.assertEqual(["None of the 2 new faces looks enough like anyone named to be proposed: they wait in Identify "
                          "Faces."], self.said(there))

    def test_stopped_while_it_waits_for_the_card_it_indexes_and_matches_nothing(self):
        self.cancel = True
        result = self.index()
        self.assertEqual(indexing.EXIT_CANCELLED, result.exit_code, result.output)
        self.assertEqual(2, self.query("SELECT COUNT(*) FROM photos")[0][0])
        self.assertEqual([], self.said(result))


if __name__ == "__main__":
    unittest.main()
