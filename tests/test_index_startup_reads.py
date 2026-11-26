"""`tagpup_cli.py index` at start-up reads what it needs and no more.

Every sync, watcher run and Add folder starts an index run, and a run that indexed 0 or 1 photos
lasted 14 to 18 s: it read and stacked all of photo_index's vectors (267 MB) to learn their length,
parsed the JSON of every photo's row to learn its mtime and size, and, if it indexed anything, read the
vectors a second time at the end for a search nothing made. Here: the vectors are not read
(PhotoIndex.open), the stamps are (store.photos.stamps), and the same photos are indexed.
A run that finds nothing to index imports no model's library either (#1089): open_clip and torch, 2 of a 3.3 s run in a
sandbox (6 to 8 s under load), were imported to compare a vector length and make a CLIP model nothing used. (The length
check went with it: a model's vectors are kept under its own key, and no key held two lengths in the three libraries.)
"""
import os
import sys
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from PIL import Image  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths, processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import search  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db, faces_detected  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import photo_fingerprints  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

MODEL = "ViT-B-32|openai|cropped|2.0|native"
DIM = 8


class Reached(Exception):
    """The files were about to be read: the run decided to index something."""


def examined(conn, db_path, path):
    """Record the photo looked at for faces by the library's face model, none found, as the indexer records it
    (faces.record_batch): a photo with neither this nor a face is the index's work whatever its vector."""
    settings = library_settings.of(Library(db_path))
    faces_detected.record(conn, path, face_service.detector_named(settings.faces),
                          photo_rows.stamp_of(path), 0)


class Harbour(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="startup_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.folder = os.path.join(self.home.root, "Regatta")
        os.makedirs(self.folder)
        conn = db.connect(self.db_path)
        try:
            photo_rows.hold(conn, self.folder)
            conn.commit()
        finally:
            conn.close()

    def photo(self, name, vector=True):
        """A photo file, its row as the indexer records it, and (unless `vector` is False) its vector."""
        path = os.path.join(self.folder, name)
        Image.new("RGB", (8, 8)).save(path, "JPEG")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Boats/Dinghy"]})
            examined(conn, self.db_path, path)
            if vector:
                stat = os.stat(path)
                store_embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size,
                                     np.arange(1, DIM + 1, dtype=np.float32).tobytes(), photo_rows.fingerprint_of(path))
            conn.commit()
        finally:
            conn.close()
        return path

    def index(self, folder=None):
        """Run `index` on the folder; (result, whether it went on to read the files)."""
        runtime = mock.Mock()
        runtime.settings.side_effect = lambda library, *a: library_settings.of(Library(self.db_path))
        runtime.model_key.return_value = MODEL
        extractor = mock.Mock(side_effect=Reached)
        with mock.patch.object(tagpup_cli, "get_runtime", return_value=runtime), \
                mock.patch("tagpup.files.field_values.Fields.whole", extractor):
            result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder or self.folder])
        return result, extractor.called


class TheStore(Harbour):
    def setUp(self):
        super().setUp()
        self.path = self.photo("start.jpg")
        self.bare = self.photo("bare.jpg", vector=False)

    def test_stamps_are_the_path_mtime_size_and_whether_there_is_a_vector(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        found = {path: (mtime, size, bool(vectored))
                 for _id, path, mtime, size, vectored in store_photos.stamps(conn, with_vectors=MODEL)}
        stat = os.stat(self.path)
        self.assertEqual((stat.st_mtime, stat.st_size, True), found[self.path])
        self.assertEqual(False, found[self.bare][2])
        self.assertEqual(3, len(found), "the held row, the two photos")
        with_stamps = {path: vector for _id, path, _m, _s, vector in store_photos.stamps(conn, vector_stamps=MODEL)}
        self.assertEqual(((stat.st_mtime, stat.st_size), None), (with_stamps[self.path], with_stamps[self.bare]),
                         "the vector's stamp, or None for a photo with no vector")

    def test_a_photo_with_no_face_row_and_no_record_of_the_detector_is_unexamined(self):
        detector = faces_detected.detector({"min_face_size": 20})
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        ids = dict((path, photo_id) for photo_id, path, _m, _s in store_photos.stamps(conn))
        conn.execute("DELETE FROM faces_detected")
        self.assertTrue({ids[self.path], ids[self.bare]} <= faces_detected.unexamined(conn, detector))
        faces_detected.record(conn, self.path, faces_detected.detector({"min_face_size": 40}), None, 0)
        self.assertIn(ids[self.path], faces_detected.unexamined(conn, detector), "another detector's record is not one")
        faces_detected.record(conn, self.path, detector, None, 0)
        self.assertNotIn(ids[self.path], faces_detected.unexamined(conn, detector), "looked at, none found")


class TheWorkTheIndexOwes(Harbour):
    """photo_fingerprints.owed, asked through PhotoIndex.owed: the index's start and sync's look give one answer to which
    photos the index owes work, and why. Before it, the index asked current() and unexamined() and sync asked the
    vectors' stamps alone: sync said "In step" over photos the index would have worked on."""

    def setUp(self):
        super().setUp()
        self.done = self.photo("done.jpg")
        self.bare = self.photo("bare.jpg", vector=False)
        self.edited = self.photo("edited.jpg")
        self.unlooked = self.photo("unlooked.jpg")
        with open(self.edited, "ab") as handle:   # written elsewhere after its vector was made
            handle.write(b"\0")
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM faces_detected WHERE photo_id = (SELECT id FROM photos WHERE path = ?)",
                         (self.unlooked,))
            conn.commit()
        finally:
            conn.close()
        self.index = search.PhotoIndex(self.db_path, model=MODEL)
        self.addCleanup(self.index.close)
        self.assertTrue(self.index.open())
        settings = library_settings.of(Library(self.db_path))
        self.detector = face_service.detector_named(settings.faces)

    def on_disk(self, *photos):
        return {paths.key(path): (path, os.stat(path).st_mtime, os.stat(path).st_size) for path in photos}

    def test_each_photo_is_owed_for_its_reasons_and_a_photo_done_is_current(self):
        on_disk = self.on_disk(self.done, self.bare, self.edited, self.unlooked)
        owed = self.index.owed(on_disk, self.detector)
        self.assertEqual({paths.key(self.bare): (photo_fingerprints.NO_VECTOR,),
                          paths.key(self.edited): (photo_fingerprints.STALE,),
                          paths.key(self.unlooked): (photo_fingerprints.UNEXAMINED,)}, owed)
        self.assertEqual({paths.key(self.done)}, self.index.current(on_disk, owed))

    def test_faces_not_asked_owe_no_look_and_a_file_not_on_disk_is_not_asked(self):
        owed = self.index.owed(self.on_disk(self.done, self.unlooked, self.edited), None)
        self.assertEqual({paths.key(self.edited): (photo_fingerprints.STALE,)}, owed)

    def test_a_photo_never_looked_at_with_no_vector_owes_both(self):
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM faces_detected WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (self.bare,))
            conn.commit()
        finally:
            conn.close()
        owed = self.index.owed(self.on_disk(self.bare), self.detector)
        self.assertEqual({paths.key(self.bare): (photo_fingerprints.NO_VECTOR, photo_fingerprints.UNEXAMINED)}, owed)


class OpeningTheIndex(Harbour):
    def setUp(self):
        super().setUp()
        self.photo("start.jpg")

    def test_open_connects_and_reads_no_vector(self):
        index = search.PhotoIndex(self.db_path, model=MODEL)
        self.addCleanup(index.close)
        with mock.patch.object(store_photos, "vectors", side_effect=AssertionError("vectors read")), \
                mock.patch.object(search, "VectorIndex", side_effect=AssertionError("index built")):
            self.assertTrue(index.open())
        self.assertIsNotNone(index.conn)
        self.assertIsNone(index.index)

    def test_load_still_builds_the_index(self):
        index = search.PhotoIndex(self.db_path, model=MODEL)
        self.addCleanup(index.close)
        self.assertTrue(index.load())
        self.assertEqual(DIM, index.index.d)


class TheIndexCommand(Harbour):
    def setUp(self):
        super().setUp()
        self.path = self.photo("start.jpg")

    def quiet(self):
        """Fail on what the run must not do: build the vector index, or parse a row's JSON."""
        for patcher in (mock.patch.object(search, "VectorIndex", side_effect=AssertionError("vectors stacked")),
                        mock.patch.object(search, "_record", side_effect=AssertionError("a row's JSON parsed"))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_nothing_changed_reads_no_vectors_and_no_json_and_indexes_nothing(self):
        self.quiet()
        result, read = self.index()
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("All images are up to date", result.output)
        self.assertFalse(read)

    def test_a_changed_mtime_is_read_again(self):
        stat = os.stat(self.path)
        os.utime(self.path, (stat.st_atime, stat.st_mtime + 60))
        result, read = self.index()
        self.assertTrue(read, result.output)

    def test_a_changed_size_is_read_again(self):
        with open(self.path, "ab") as handle:
            handle.write(b"\0")
        stat = os.stat(self.path)
        conn = db.connect(self.db_path)
        try:   # the mtime as it was: only the size differs
            conn.execute("UPDATE photos SET mtime = ? WHERE path = ?", (stat.st_mtime, self.path))
            conn.commit()
        finally:
            conn.close()
        result, read = self.index()
        self.assertTrue(read, result.output)

    def test_a_row_a_sync_read_again_whose_vector_is_stamped_before_is_read_again(self):
        # The shape of photo_index's 74 (2026-10-27): a file written outside, its row read again by a sync (stamped as
        # the file is), its vector stamped as the file was. Its row describes its file; its vector's stamp does not.
        with open(self.path, "ab") as handle:
            handle.write(b"\0")
        stat = os.stat(self.path)
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE photos SET mtime = ?, size = ? WHERE path = ?", (stat.st_mtime, stat.st_size, self.path))
            conn.commit()
        finally:
            conn.close()
        result, read = self.index()
        self.assertTrue(read, result.output)

    def test_a_photo_with_no_vector_is_read(self):
        self.photo("bare.jpg", vector=False)
        result, read = self.index()
        self.assertTrue(read, result.output)

    @unittest.skipUnless(os.name == "nt", "a Windows path is spelled in any case")
    def test_a_folder_typed_in_another_case_is_the_same_photos(self):
        self.quiet()
        result, read = self.index(self.folder.upper())
        self.assertEqual(0, result.exit_code, result.output)
        self.assertIn("All images are up to date", result.output)
        self.assertFalse(read)


#: The CLI's index, run as the server's queue runs it, then the model libraries it imported.
PROBE = (
    "import sys\n"
    "import tagpup_cli\n"
    "try:\n"
    "    tagpup_cli.cli(sys.argv[1:], standalone_mode=False)\n"
    "finally:\n"
    "    print('LOADED=' + ','.join(m for m in ('open_clip', 'torch') if m in sys.modules))\n")


class AnIndexWithNothingToDo(Harbour):
    """Every sync, watcher run and Add folder starts an index run; most find every photo current."""

    def test_imports_no_models_library(self):
        path = os.path.join(self.folder, "start.jpg")
        Image.new("RGB", (8, 8)).save(path, "JPEG")
        embedder = library_settings.of(Library(self.db_path)).embedder
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_read(conn, path, {"XMP:Subject": ["Boats/Dinghy"]})
            examined(conn, self.db_path, path)
            stat = os.stat(path)
            # Its vector under the library's model's key.
            store_embeddings.put(conn, path, store_embeddings.model_key(**embedder), stat.st_mtime, stat.st_size,
                                 np.ones(DIM, dtype=np.float32).tobytes(), photo_rows.fingerprint_of(path))
            conn.commit()
        finally:
            conn.close()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        done = processes.run([sys.executable, "-c", PROBE, "--db", self.db_path, "index", self.folder], cwd=root,
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        self.assertEqual(0, done.returncode, done.stdout + done.stderr)
        self.assertIn("All images are up to date", done.stdout)
        self.assertEqual("LOADED=", [line for line in done.stdout.splitlines() if line.startswith("LOADED=")][-1])


if __name__ == "__main__":
    unittest.main()
