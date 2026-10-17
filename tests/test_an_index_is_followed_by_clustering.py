"""An index is followed by clustering, from every entry point (owner, DECISIONS 2026-10-16; #1544).

The decision was written down (2026-10-15: "the indexer runs it after every index") and was not true: the server ran
`cluster-faces` only when a request asked for it, no page asked, sync's queue passed nothing, and the CLI's `index`
never ran it. Now the CLI's `index` decides, the one place every entry point reaches -- TagPup's Add and its index
route, TagTuner's index route and sync's new files all run that command through tagpup.services.indexing.index_folder,
and the owner runs it by hand for long indexes:

- after an index that recorded a photo, it clusters the library's faces, once its turn on the graphics card is let go
  (clustering is the processor's work: another library's index waiting for the card is not held up by it);
- an index that recorded nothing (a sync or a folder added again that found every photo current) clusters nothing;
- a clustering that fails leaves the photos indexed, and says so (the folder's status in the queue, the CLI's exit),
  and says how to try again (`cluster-faces`, not Name faces from tags, which does not cluster);
- the library's roots changed by another process while it clusters: the one roots stop (exit 75), and the folder is
  still said to be indexed, its bar at 100;
- stopped while it waited for the card, it indexed nothing and clusters nothing;
- while it clusters, and not before, writes of faces in this server are refused from both apps, whichever entry point
  started the index (tagpup.services.indexing.while_clustering; a face named meanwhile in another process is kept by
  clustering itself, tests/test_clustering_meets_naming.py), however the server's environment asks for colour.

The server's half stands in for the CLI's process (its stdout is the interface, contract row 16); the CLI's half is the
real command in this process with its models stood in for (nothing loads) and the real ExifTool.

Lifted: test_queued_clustering_is_guarded (the guard held by TagTuner's indexer only) and test_service_indexing's
three clustering tests (asked-for clustering, its failure, its guard), from the opt-in they tested to the rule.
Each test here was run against the code before this change and failed there, except the cancelled one, which holds a
rule the old code kept by never clustering.
"""
import io
import os
import sys
import unittest
from unittest import mock
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402
from rich.console import Console  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import gpu_turns, paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.jobs import indexing as indexing_jobs  # noqa: E402
from tagpup.ml import clip, faces  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import identities  # noqa: E402
from tagpup.services import indexing  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import roots  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.web import app as web  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000


class _TheCli:
    """A stand-in for the CLI's process: prints `lines`, exits with `code`; `during(began)` is called as each line is
    read, `began` whether the clustering's line was read before it -- what a person clicking meanwhile meets."""

    def __init__(self, lines, code=0, during=None):
        self.lines, self.code, self.during = list(lines), code, during
        self.commands, self.environments = [], []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        self.environments.append(kwargs.get("env"))
        proc = MagicMock()
        proc.returncode = self.code
        remaining = iter(self.lines + [""])
        began = []

        def readline():
            line = next(remaining)
            if line and self.during:
                self.during(bool(began))
            if line.strip() == indexing.CLUSTERING_STARTS:
                began.append(True)
            return line
        proc.stdout.readline.side_effect = readline
        return proc


INDEXED = ["Generating embeddings: 100%|##########| 1/1 [00:01<00:00]\n", indexing.CLUSTERING_STARTS + "\n",
           "Resolving face identities: 50%|#####     | 1/2 [00:01<00:01]\n", "No face was newly named.\n"]
NOTHING_NEW = ["All images are up to date! Index is current.\n"]


class EveryEntryPointOfTheServer(unittest.TestCase):
    """TagPup's Add and index route, TagTuner's index route, and sync's new files: the CLI's index, then its
    clustering, with writes of faces refused from both apps meanwhile."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="index_clusters_")
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        self.addCleanup(indexing_jobs.forget, self.library)
        self.addCleanup(indexing.clustering.forget, self.library)
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
        """(began, flag up, TagTuner's answer to a write of a face, TagPup's) as each line is read."""
        def during(began):
            seen.append((began, indexing.clustering.of(self.library).is_set(),
                         self.apps["tuner"].post("/harbour/api/face/unmatch", json={"face_id": 1}).status_code,
                         self.apps["tagpup"].post("/harbour/api/face/unmatch", json={"face_id": 1}).status_code))
        return during

    def test_each_index_is_followed_by_clustering_and_writes_of_faces_wait_for_it(self):
        for name, kind, route, key in self.ENTRY_POINTS:
            with self.subTest(name):
                indexing_jobs.forget(self.library)
                seen = []
                cli = _TheCli(INDEXED, during=self.writes_of_faces_meanwhile(seen))
                with mock.patch("subprocess.Popen", side_effect=cli):
                    status = self.ask(kind, route, key)
                self.assertEqual("completed", status["status"], status)
                self.assertEqual("Folder indexed and face identities resolved.", status["message"])
                # One process: the CLI's index, which clustered after itself; no second command.
                self.assertEqual([["index"]], [[c[2]] for c in cli.commands], cli.commands)
                during = [each[1:] for each in seen if each[0]]
                before = [each[1:] for each in seen if not each[0]]
                self.assertTrue(during, "nothing was read after the clustering began")
                self.assertTrue(all(each == (True, 409, 409) for each in during),
                                "(flag up, TagTuner's answer, TagPup's answer) while clustering: %s" % during)
                # The index itself takes no write of a face away: the flag is down until the clustering's line.
                self.assertTrue(before, "nothing was read before the clustering began")
                self.assertTrue(all(not flag and 409 not in answers for flag, *answers in before),
                                "(flag up, TagTuner's answer, TagPup's answer) while indexing: %s" % before)
                self.assertFalse(indexing.clustering.of(self.library).is_set(), "the flag stayed up after the run")

    def test_the_bar_stands_at_95_while_the_faces_are_clustered(self):
        heard = []
        with mock.patch("subprocess.Popen", side_effect=_TheCli(INDEXED)):
            result = indexing.index_folder(self.library, self.folder, self.home.root,
                                           report=lambda message=None, percent=None: heard.append((message, percent)))
        self.assertEqual([("Generating embeddings: 100% (1/1)", 90), (indexing.CLUSTERING_STARTS, 95),
                          ("Resolving face identities: 50% (1/2)", None), ("No face was newly named.", None)], heard)
        self.assertEqual((True, 100, True), (result.ok, result.details["percent"], result.details["clustered"]))

    def test_an_index_that_recorded_nothing_clusters_nothing_and_says_so(self):
        seen = []
        with mock.patch("subprocess.Popen", side_effect=_TheCli(NOTHING_NEW, during=self.writes_of_faces_meanwhile(seen))):
            status = self.ask("tuner", "/harbour/api/folder/index-start", "folder_paths")
        self.assertEqual("completed", status["status"], status)
        self.assertIn("faces were not clustered again", status["message"])
        self.assertIn(indexing.CLUSTERING_BUTTON, status["message"])
        self.assertTrue(seen)
        self.assertEqual([(False, False)], sorted({(began, flag) for began, flag, *_ in seen}))

    def test_a_failed_clustering_is_the_folders_error_and_the_index_stays(self):
        for code in (indexing.EXIT_CLUSTERING_FAILED, 1):
            with self.subTest(exit=code):
                indexing_jobs.forget(self.library)
                with mock.patch("subprocess.Popen", side_effect=_TheCli(INDEXED[:2], code=code)):
                    status = self.ask("tagpup", "/harbour/api/folder/index-start", "folder_path")
                self.assertEqual("failed", status["status"], status)
                self.assertIn("Folder indexed, but resolving face identities failed (exit code %d)" % code,
                              status["message"])
                # How to try again is the command that clusters; Name faces from tags names faces from tags.
                self.assertIn("cluster-faces", status["message"])
                self.assertNotIn(indexing.CLUSTERING_BUTTON, status["message"])
                self.assertEqual(100, status["percent"], "the index was done: its bar is not put back to 0")
                self.assertFalse(indexing.clustering.of(self.library).is_set())

    def test_the_roots_changed_while_the_faces_are_clustered_and_the_folder_is_still_indexed(self):
        with mock.patch("subprocess.Popen", side_effect=_TheCli(INDEXED[:2] + [roots.STOPPED + "\n"],
                                                               code=roots.EXIT_ROOTS_CHANGED)):
            status = self.ask("tuner", "/harbour/api/folder/index-start", "folder_paths")
        self.assertEqual("failed", status["status"], status)
        self.assertIn("Folder indexed, but resolving face identities stopped", status["message"])
        self.assertIn("roots were changed by another process", status["message"])
        self.assertIn("cluster-faces", status["message"])
        self.assertEqual(100, status["percent"], "the index was done: its bar is not put back to 0")
        self.assertFalse(indexing.clustering.of(self.library).is_set())

    def test_the_clustering_is_seen_when_the_servers_environment_asks_for_colour(self):
        """rich colours the line's "..." when it takes the pipe for a terminal (FORCE_COLOR), and a coloured line is
        not the clustering's: writes of faces would be taken while the faces are clustered."""
        cli = _TheCli(NOTHING_NEW)
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}), mock.patch("subprocess.Popen", side_effect=cli):
            indexing.index_folder(self.library, self.folder, self.home.root)
        printed = io.StringIO()
        Console(file=printed, _environ=cli.environments[0]).print(indexing.CLUSTERING_STARTS, markup=False,
                                                                   soft_wrap=True)
        self.assertEqual(indexing.CLUSTERING_STARTS, indexing.summarize_indexer_line(printed.getvalue()))


class _Hold:
    """The CLI's turn on the graphics card, as the runtime hands it (tagpup.ml.gpu.Hold): released or not."""

    def __init__(self):
        self.released = False

    def release(self):
        self.released = True


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class TheCliIndexByHand(unittest.TestCase):
    """`tagpup_cli.py index`, as the owner runs a long index, and as the server's queue runs it."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="cli_index_clusters_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.folder = paths.stored(os.path.join(self.home.root, "Regatta"))
        os.makedirs(self.folder)
        for name in ("one.jpg", "two.jpg"):
            Image.new("RGB", (64, 64), "navy").save(os.path.join(self.folder, name), "JPEG")
        for patcher in (mock.patch.object(clip.ClipModel, "_init_model"),
                        mock.patch.object(clip.ClipModel, "embed_picture", return_value=[0.1] * 8),
                        mock.patch.object(faces.FaceModel, "_init_models"),
                        mock.patch.object(faces.FaceModel, "detect_and_embed_faces", return_value=[])):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.holds, self.resolved = [], []
        self.cancel = False

        def gpu_turn(runtime, what, models, cancelled=None, report=None):
            if self.cancel:
                raise gpu_turns.Cancelled("stopped")
            self.holds.append(_Hold())
            return self.holds[-1]
        real = identities.resolve

        def resolve(photo_index, on_step=None):
            # Whether the card's turn was let go when clustering began.
            self.resolved.append(all(hold.released for hold in self.holds))
            if self.failing:
                raise self.failing
            return real(photo_index, on_step=on_step)
        self.failing = None
        for patcher in (mock.patch.object(Runtime, "gpu_turn", gpu_turn),
                        mock.patch.object(identities, "resolve", side_effect=resolve)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def index(self):
        return CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", self.folder])

    def photos(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            conn.close()

    def test_an_index_clusters_after_its_turn_on_the_card_and_an_index_again_does_not(self):
        first = self.index()
        self.assertEqual(0, first.exit_code, first.output)
        self.assertEqual(2, self.photos())
        self.assertIn(indexing.CLUSTERING_STARTS, first.output)
        self.assertEqual([True], self.resolved, "clustered once, with the card's turn let go")

        again = self.index()
        self.assertEqual(0, again.exit_code, again.output)
        self.assertIn("All images are up to date! Index is current.", again.output)
        self.assertNotIn(indexing.CLUSTERING_STARTS, again.output)
        self.assertEqual([True], self.resolved, "an index that recorded nothing clustered")

    def test_a_failed_clustering_exits_so_and_the_photos_stay_indexed(self):
        self.failing = RuntimeError("out of memory")
        result = self.index()
        self.assertEqual(indexing.EXIT_CLUSTERING_FAILED, result.exit_code, result.output)
        self.assertIn("The photos are indexed, but resolving face identities failed: RuntimeError: out of memory",
                      result.output)
        self.assertEqual(2, self.photos())

    def test_the_roots_changed_while_it_clusters_is_the_one_roots_stop_and_the_photos_stay(self):
        self.failing = roots.RootsChanged("the library's roots were changed while this run held them")
        result = self.index()
        self.assertEqual(roots.EXIT_ROOTS_CHANGED, result.exit_code, result.output)
        self.assertIn(roots.STOPPED, result.output)
        self.assertEqual(2, self.photos())

    def test_stopped_while_it_waits_for_the_card_it_clusters_nothing(self):
        self.cancel = True
        result = self.index()
        self.assertEqual(indexing.EXIT_CANCELLED, result.exit_code, result.output)
        self.assertEqual(0, self.photos())
        self.assertEqual([], self.resolved)


if __name__ == "__main__":
    unittest.main()
