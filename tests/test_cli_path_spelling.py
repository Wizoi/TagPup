"""The CLI produces and compares photo paths in one spelling: paths.stored / paths.key.

A folder typed as D:/Photos walked to "D:/Photos\\a.jpg" -- both separators in one
row -- and a relative folder walked to relative rows. The index command looked up
what it had already indexed by the raw string, so the same photos scanned under
another spelling were embedded again and given a second row. And --folder filters
used startswith, so C:\\Photos matched every photo in C:\\Photos2.
"""
import gc
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner
from PIL import Image

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import db as tagpup_db
import paths
from index import PhotoIndex
from tagpup_cli import cli, get_config, scan_for_images


def make_jpeg(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 16), color="green").save(path, "JPEG")


def expected_dim():
    model_name = get_config().get("model", "name", fallback="ViT-B-32")
    return 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)


def swap_case(text):
    return "".join(c.lower() if c.isupper() else c.upper() for c in text)


class TestScanForImages(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.library = os.path.join(self.tmp.name, "Summer Trip")
        make_jpeg(os.path.join(self.library, "beach.jpg"))
        make_jpeg(os.path.join(self.library, "Day Two", "dunes.jpg"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_forward_slash_folder_walks_to_stored_paths(self):
        found = scan_for_images(self.library.replace(os.sep, "/"))
        self.assertEqual(len(found), 2)
        for path in found:
            self.assertEqual(path, paths.stored(path),
                             "scan produced a path the index would not store: %r" % path)

    def test_a_relative_folder_walks_to_absolute_paths(self):
        try:
            relative = os.path.relpath(self.library)
        except ValueError:
            self.skipTest("temp directory is on another drive than the cwd")
        found = scan_for_images(relative)
        self.assertEqual(len(found), 2)
        for path in found:
            self.assertTrue(os.path.isabs(path), path)


class CliDatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "spelling.db")
        os.environ["TAGPUP_DB_PATH"] = self.db_path
        os.environ["TAGPUP_TAXONOMY_PATH"] = os.path.join(self.tmp.name, "spelling_taxonomy.json")

    def tearDown(self):
        os.environ.pop("TAGPUP_DB_PATH", None)
        os.environ.pop("TAGPUP_TAXONOMY_PATH", None)
        # PhotoIndex.remove_paths reloads, and load() opens a connection over the one
        # it had without closing it; collecting the orphan releases the file.
        gc.collect()
        self.tmp.cleanup()

    def seed(self, rows):
        """rows: [(path as the index should hold it, file on disk)]"""
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        metas = []
        for stored_as, on_disk in rows:
            stat = os.stat(on_disk)
            metas.append({"path": stored_as, "mtime": stat.st_mtime, "size": stat.st_size,
                          "tags": [], "people": [], "captions": [], "raw_metadata": {}})
        index.build_or_update([[0.1] * expected_dim()] * len(metas), metas,
                              dim=expected_dim(), reload=False)
        index.close()

    def photo_rows(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return [r[0] for r in conn.execute("SELECT path FROM photos")]
        finally:
            conn.close()

    def run_cli(self, args, input=None):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=self.tmp.name):
            return runner.invoke(cli, ["--test"] + args, input=input)


@unittest.skipUnless(paths.CASE_INSENSITIVE, "two case spellings are two files here")
class TestIndexRecognisesAnotherSpelling(CliDatabaseCase):
    @patch("embedder.ClipEmbedder._init_model")
    @patch("embedder.ClipEmbedder.embed_image")
    @patch("tagpup_cli.MetadataExtractor.batch_read")
    def test_a_folder_indexed_under_another_case_is_not_indexed_twice(
            self, batch_read, embed_image, _init_model):
        library = os.path.join(self.tmp.name, "Garden Party")
        photo = os.path.join(library, "lanterns.jpg")
        make_jpeg(photo)
        # The row as it was written when the folder was typed another way.
        other_spelling = os.path.join(os.path.dirname(library), swap_case("Garden Party"),
                                      "lanterns.jpg")
        self.seed([(other_spelling, photo)])

        embed_image.return_value = [0.1] * expected_dim()
        batch_read.side_effect = lambda files, *a, **k: [
            {"path": f, "tags": [], "people": [], "captions": [], "raw_metadata": {},
             "mtime": os.stat(f).st_mtime, "size": os.stat(f).st_size} for f in files]

        result = self.run_cli(["index", "--skip-faces", library])
        self.assertEqual(result.exit_code, 0, result.output)

        rows = self.photo_rows()
        self.assertEqual(len(rows), 1, "one photo, %d rows: %r" % (len(rows), rows))
        embed_image.assert_not_called()


class TestFolderFilterIsAFolderNotAPrefix(CliDatabaseCase):
    def setUp(self):
        super().setUp()
        self.inside = os.path.join(self.tmp.name, "Photos", "inside.jpg")
        self.sibling = os.path.join(self.tmp.name, "Photos2", "sibling.jpg")
        make_jpeg(self.inside)
        make_jpeg(self.sibling)
        self.seed([(self.inside, self.inside), (self.sibling, self.sibling)])
        self.folder = os.path.join(self.tmp.name, "Photos")

    def test_list_index_leaves_out_the_sibling_folder(self):
        result = self.run_cli(["list-index", "--folder", self.folder])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("(1 matches)", result.output)
        self.assertNotIn("sibling", result.output)

    def test_remove_folder_leaves_the_sibling_folder_alone(self):
        result = self.run_cli(["remove", "--folder", self.folder], input="YES\n")
        self.assertEqual(result.exit_code, 0, result.output)
        rows = self.photo_rows()
        self.assertEqual([paths.key(r) for r in rows], [paths.key(self.sibling)])

    def test_remove_path_matches_another_spelling_of_it(self):
        typed = self.inside.replace(os.sep, "/")
        if paths.CASE_INSENSITIVE:
            typed = typed.upper()
        result = self.run_cli(["remove", "--path", typed], input="YES\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([paths.key(r) for r in self.photo_rows()], [paths.key(self.sibling)])


if __name__ == "__main__":
    unittest.main()
