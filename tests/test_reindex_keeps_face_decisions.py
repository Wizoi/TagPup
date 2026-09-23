"""Re-indexing a photo, and resetting clustering, keep the decisions made about its faces.

faces.photo_path references photos.path ON DELETE CASCADE, and PhotoIndex turns
foreign keys on. Re-indexing a photo whose file had changed deleted its row first
(remove_paths) and then wrote it with INSERT OR REPLACE -- a delete and an insert --
so either way every face on the photo went with it: hand-given names, deliberate
"nobody" decisions and exclusions. The detector then wrote fresh unnamed faces.

`cluster-faces --reset` cleared every face's name but left name_source = 'manual',
which the resolver reads as "a person decided this face is nobody". Every name given
by hand became a permanent, binding blank.
"""
import gc
import os
import sys
import tempfile
import time
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
from tagpup_cli import cli, get_config


def make_jpeg(path, color="green"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 16), color=color).save(path, "JPEG")


def expected_dim():
    model_name = get_config().get("model", "name", fallback="ViT-B-32")
    return 768 if "ViT-L" in model_name else (1024 if "ViT-H" in model_name else 512)


def swap_case(text):
    return "".join(c.lower() if c.isupper() else c.upper() for c in text)


def fake_meta(path):
    stat = os.stat(path)
    return {"path": path, "tags": [], "people": [], "captions": [], "raw_metadata": {},
            "mtime": stat.st_mtime, "size": stat.st_size}


class FakeFaceProcessor:
    """Detects one new, unnamed face in every photo -- what the real detector returns."""

    def detect_and_embed_faces(self, path):
        return [{"box": [1, 1, 5, 5], "embedding": [0.5] * 512, "prob": 0.99,
                 "crop_image": None}]


# (box, name, name_source, excluded, excluded_reason)
CURATED = [
    ([0, 0, 4, 4], "Rowan Thackeray", "manual", 0, None),
    ([4, 0, 8, 4], None, "manual", 0, None),                  # deliberately nobody
    ([8, 0, 12, 4], None, "manual", 1, "passer-by"),         # excluded
    ([0, 8, 4, 12], "Imogen Vale", None, 0, None),            # named by clustering
]


class FaceDecisionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "decisions.db")
        os.environ["TAGPUP_DB_PATH"] = self.db_path
        os.environ["TAGPUP_TAXONOMY_PATH"] = os.path.join(self.tmp.name, "decisions_taxonomy.json")
        self.library = os.path.join(self.tmp.name, "Harbour Walk")
        self.photo = os.path.join(self.library, "jetty.jpg")
        make_jpeg(self.photo)

    def tearDown(self):
        os.environ.pop("TAGPUP_DB_PATH", None)
        os.environ.pop("TAGPUP_TAXONOMY_PATH", None)
        gc.collect()
        self.tmp.cleanup()

    def seed(self, stored_as=None):
        """Index the photo and give it curated faces. Returns {box: face id}."""
        stored_as = stored_as or self.photo
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        meta = fake_meta(self.photo)
        meta["path"] = stored_as
        index.build_or_update([[0.1] * expected_dim()], [meta], dim=expected_dim(), reload=False)
        ids = {}
        for box, name, source, excluded, reason in CURATED:
            cur = index.conn.execute(
                "INSERT INTO faces (photo_path, box, embedding, name, name_source,"
                " excluded, excluded_reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (paths.stored(stored_as), str(box), bytes(4 * 512), name, source,
                 excluded, reason))
            ids[str(box)] = cur.lastrowid
        index.conn.commit()
        index.close()
        return ids

    def faces(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return {row[0]: row[1:] for row in conn.execute(
                "SELECT box, id, name, name_source, excluded, excluded_reason FROM faces")}
        finally:
            conn.close()

    def photo_rows(self):
        conn = tagpup_db.connect(self.db_path)
        try:
            return conn.execute("SELECT path, mtime FROM photos").fetchall()
        finally:
            conn.close()

    def change_file(self):
        make_jpeg(self.photo, color="blue")
        later = time.time() + 60
        os.utime(self.photo, (later, later))

    def run_cli(self, args):
        runner = CliRunner()
        with runner.isolated_filesystem(temp_dir=self.tmp.name):
            return runner.invoke(cli, ["--test"] + args)


@patch("tagpup_cli.FaceProcessor", FakeFaceProcessor)
@patch("embedder.ClipEmbedder._init_model")
@patch("embedder.ClipEmbedder.embed_image")
@patch("tagpup_cli.MetadataExtractor.batch_read")
class TestReindexingAChangedPhoto(FaceDecisionCase):
    def reindex(self, batch_read, embed_image):
        embed_image.return_value = [0.2] * expected_dim()
        batch_read.side_effect = lambda files, *a, **k: [fake_meta(f) for f in files]
        result = self.run_cli(["index", self.library])
        self.assertEqual(result.exit_code, 0, result.output)
        embed_image.assert_called_once()  # the change was seen and the photo re-indexed

    def assert_curation_kept(self, ids):
        faces = self.faces()
        for box, name, source, excluded, reason in CURATED:
            self.assertIn(str(box), faces, "face %s was deleted by the re-index" % box)
            self.assertEqual(faces[str(box)], (ids[str(box)], name, source, excluded, reason))
        self.assertEqual(len(faces), len(CURATED),
                         "the detector's faces were added beside the kept ones: %r" % faces)

    def test_names_nobodies_and_exclusions_survive(self, batch_read, embed_image, _init):
        ids = self.seed()
        self.change_file()
        self.reindex(batch_read, embed_image)

        self.assert_curation_kept(ids)
        rows = self.photo_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][1], os.stat(self.photo).st_mtime, "the row was not updated")

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "two case spellings are two files here")
    def test_a_row_under_another_spelling_is_updated_not_duplicated(
            self, batch_read, embed_image, _init):
        other = os.path.join(os.path.dirname(self.library), swap_case("Harbour Walk"), "jetty.jpg")
        ids = self.seed(stored_as=other)
        self.change_file()
        self.reindex(batch_read, embed_image)

        self.assert_curation_kept(ids)
        rows = self.photo_rows()
        self.assertEqual([r[0] for r in rows], [other],
                         "the row keeps the spelling its faces point at")
        self.assertEqual(rows[0][1], os.stat(self.photo).st_mtime, "the row was not updated")


class TestBuildOrUpdateWritesInPlace(FaceDecisionCase):
    def test_a_second_write_of_the_same_photo_keeps_its_faces(self):
        ids = self.seed()
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        meta = fake_meta(self.photo)
        meta["tags"] = ["Places/Harbour"]
        index.build_or_update([[0.3] * expected_dim()], [meta], dim=expected_dim())
        self.assertEqual(len(index.metadata), 1)
        self.assertEqual(index.metadata[0]["tags"], ["Places/Harbour"])
        index.close()
        self.assertEqual({box: row[0] for box, row in self.faces().items()}, ids)


class FakeClusterer:
    def cluster_and_resolve_identities(self, photo_index, taxonomy, max_iterations=5):
        return {}


class TestResetKeepsHandGivenNames(FaceDecisionCase):
    @patch("tagpup_cli.FaceProcessor", FakeClusterer)
    def test_cluster_faces_reset_clears_only_automatic_names(self):
        ids = self.seed()
        result = self.run_cli(["cluster-faces", "--reset"])
        self.assertEqual(result.exit_code, 0, result.output)

        faces = self.faces()
        self.assertEqual(faces["[0, 0, 4, 4]"][1:3], ("Rowan Thackeray", "manual"))
        self.assertEqual(faces["[4, 0, 8, 4]"][1:3], (None, "manual"))
        self.assertEqual(faces["[8, 0, 12, 4]"][1:], (None, "manual", 1, "passer-by"))
        self.assertEqual(faces["[0, 8, 4, 12]"][1:3], (None, None),
                         "an automatic name survived the reset")

        index = PhotoIndex(db_path=self.db_path)
        index.load()
        try:
            self.assertEqual(index.get_manual_face_names(), {
                ids["[0, 0, 4, 4]"]: "Rowan Thackeray",
                ids["[4, 0, 8, 4]"]: None,
                ids["[8, 0, 12, 4]"]: None,
            })
            # The photo still lists the person named by hand; not the one clustering named.
            self.assertEqual(index.metadata[0]["people"], ["Rowan Thackeray"])
        finally:
            index.close()

    def test_reset_reports_the_faces_it_cleared(self):
        self.seed()
        index = PhotoIndex(db_path=self.db_path)
        index.load()
        try:
            cleared = index.reset_face_assignments()
            self.assertIsNot(cleared, True, "reported that it ran, not what it changed")
            self.assertEqual(cleared, 1)
        finally:
            index.close()


if __name__ == "__main__":
    unittest.main()
