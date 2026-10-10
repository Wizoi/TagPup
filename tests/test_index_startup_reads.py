"""`tagpup_cli.py index` at start-up reads what it needs and no more.

Every sync, watcher run and Add folder starts an index run, and a run that indexed 0 or 1 photos
lasted 14 to 18 s: it read and stacked all of photo_index's vectors (267 MB) to learn their length,
parsed the JSON of every photo's row to learn its mtime and size, and, if it indexed anything, read the
vectors a second time at the end for a search nothing made. Here: the vectors are not read
(PhotoIndex.open, stored_dim), the stamps are (store.photos.stamps_with_vectors), and the same photos are indexed.
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
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import search  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

MODEL = "ViT-B-32|openai|cropped|2.0|native"
DIM = 8


class Reached(Exception):
    """The files were about to be read: the run decided to index something."""


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
            if vector:
                stat = os.stat(path)
                store_embeddings.put(conn, path, MODEL, stat.st_mtime, stat.st_size,
                                     np.arange(1, DIM + 1, dtype=np.float32).tobytes())
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
                mock.patch.object(tagpup_cli, "MetadataExtractor", extractor), \
                mock.patch("tagpup.ml.clip.output_dim", return_value=DIM):
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
        found = {path: (mtime, size, bool(vectored)) for path, mtime, size, vectored in store_photos.stamps_with_vectors(conn, MODEL)}
        stat = os.stat(self.path)
        self.assertEqual((stat.st_mtime, stat.st_size, True), found[self.path])
        self.assertEqual(False, found[self.bare][2])
        self.assertEqual(3, len(found), "the held row, the two photos")

    def test_the_vectors_length_is_read_from_a_vector(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual(DIM, store_photos.vector_dim(conn, MODEL))
        self.assertIsNone(store_photos.vector_dim(conn, "another|model"))


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
        self.assertEqual(DIM, index.stored_dim())

    def test_load_still_builds_the_index(self):
        index = search.PhotoIndex(self.db_path, model=MODEL)
        self.addCleanup(index.close)
        self.assertTrue(index.load())
        self.assertEqual(DIM, index.index.d)
        self.assertEqual(DIM, index.stored_dim())

    def test_a_vector_of_another_length_is_a_mismatch(self):
        index = search.PhotoIndex(self.db_path, model=MODEL)
        self.addCleanup(index.close)
        index.open()
        with mock.patch("tagpup.ml.clip.output_dim", return_value=DIM * 2):
            self.assertEqual((DIM, DIM * 2), search.stored_mismatch(index, MODEL))
        with mock.patch("tagpup.ml.clip.output_dim", return_value=DIM):
            self.assertIsNone(search.stored_mismatch(index, MODEL))


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


if __name__ == "__main__":
    unittest.main()
