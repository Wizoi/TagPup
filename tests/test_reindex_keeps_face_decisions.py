"""Re-indexing a photo keeps the decisions made about its faces.

faces.photo_path referenced photos.path ON DELETE CASCADE (faces.photo_id references
photos.id now), and PhotoIndex turns foreign keys on. Re-indexing a photo whose file had changed deleted its row first
(remove_paths) and then wrote it with INSERT OR REPLACE -- a delete and an insert --
so either way every face on the photo went with it: hand-given names, deliberate
"nobody" decisions and exclusions. The detector then wrote fresh unnamed faces.
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

from tagpup.store import db as tagpup_db
from tagpup.core import paths
from tagpup.services.search import PhotoIndex
from tagpup_cli import cli

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stand_in_models  # noqa: E402
from face_rows import add_face, configured_model  # noqa: E402


def make_jpeg(path, color="green"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (16, 16), color=color).save(path, "JPEG")


def expected_dim():
    """Any length: a model's vectors are kept under its own key (tagpup.store.embeddings.model_key)."""
    return 8


def swap_case(text):
    return "".join(c.lower() if c.isupper() else c.upper() for c in text)


def fake_meta(path):
    stat = os.stat(path)
    return {"path": path, "tags": [], "people": [], "captions": [], "raw_metadata": {},
            "mtime": stat.st_mtime, "size": stat.st_size}


class FakeFaceProcessor(stand_in_models.Faces):
    """Detects one new, unnamed face in every photo -- what the real detector returns."""

    def detect_and_embed_faces(self, path, *, max_side):
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
        self.library = os.path.join(self.tmp.name, "Harbour Walk")
        self.photo = os.path.join(self.library, "jetty.jpg")
        make_jpeg(self.photo)

    def tearDown(self):
        os.environ.pop("TAGPUP_DB_PATH", None)
        gc.collect()
        self.tmp.cleanup()

    def seed(self, stored_as=None):
        """Index the photo and give it curated faces. Returns {box: face id}."""
        stored_as = stored_as or self.photo
        index = PhotoIndex(self.db_path, configured_model())
        index.load()
        meta = fake_meta(self.photo)
        meta["path"] = stored_as
        index.build_or_update([[0.1] * expected_dim()], [meta], dim=expected_dim())
        ids = {}
        for box, name, source, excluded, reason in CURATED:
            ids[str(box)] = add_face(index.conn, paths.stored(stored_as), box=str(box),
                                     embedding=bytes(4 * 512), name=name, name_source=source,
                                     excluded=excluded, excluded_reason=reason)
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


@patch("tagpup.ml.faces.FaceModel", lambda **settings: FakeFaceProcessor())
@patch("tagpup.ml.clip.ClipModel._init_model")
@patch("tagpup.ml.clip.ClipModel.picture_input")
@patch("tagpup.ml.clip.ClipModel.embed_input")
@patch("tagpup_cli.MetadataExtractor.batch_read")
class TestReindexingAChangedPhoto(FaceDecisionCase):
    def reindex(self, batch_read, embed_input):
        embed_input.return_value = [0.2] * expected_dim()
        batch_read.side_effect = lambda files, *a, **k: [fake_meta(f) for f in files]
        # What the index's write keeps: the clustering that follows an index re-derives the automatic names by its own
        # rules (the photo here names nobody, so it takes the automatic one back; tests/test_clustering_meets_naming.py).
        with patch("tagpup_cli._cluster_after_index") as clustered:
            result = self.run_cli(["index", self.library])
        self.assertEqual(result.exit_code, 0, result.output)
        embed_input.assert_called_once()  # the change was seen and the photo re-indexed
        clustered.assert_called_once()    # and, a photo recorded, the faces are clustered after the index

    def assert_curation_kept(self, ids):
        faces = self.faces()
        for box, name, source, excluded, reason in CURATED:
            self.assertIn(str(box), faces, "face %s was deleted by the re-index" % box)
            self.assertEqual(faces[str(box)], (ids[str(box)], name, source, excluded, reason))
        self.assertEqual(len(faces), len(CURATED),
                         "the detector's faces were added beside the kept ones: %r" % faces)

    def test_names_nobodies_and_exclusions_survive(self, batch_read, embed_input, _picture, _init):
        ids = self.seed()
        self.change_file()
        self.reindex(batch_read, embed_input)

        self.assert_curation_kept(ids)
        rows = self.photo_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0][1], os.stat(self.photo).st_mtime, "the row was not updated")

    @unittest.skipUnless(paths.CASE_INSENSITIVE, "two case spellings are two files here")
    def test_a_row_under_another_spelling_is_updated_not_duplicated(
            self, batch_read, embed_input, _picture, _init):
        other = os.path.join(os.path.dirname(self.library), swap_case("Harbour Walk"), "jetty.jpg")
        ids = self.seed(stored_as=other)
        self.change_file()
        self.reindex(batch_read, embed_input)

        self.assert_curation_kept(ids)
        rows = self.photo_rows()
        self.assertEqual([r[0] for r in rows], [other],
                         "the row keeps the spelling its faces point at")
        self.assertEqual(rows[0][1], os.stat(self.photo).st_mtime, "the row was not updated")


class TestBuildOrUpdateWritesInPlace(FaceDecisionCase):
    def test_a_second_write_of_the_same_photo_keeps_its_faces(self):
        ids = self.seed()
        index = PhotoIndex(self.db_path, configured_model())
        index.load()
        meta = fake_meta(self.photo)
        meta["tags"] = ["Places/Harbour"]
        index.build_or_update([[0.3] * expected_dim()], [meta], dim=expected_dim())
        self.assertEqual(len(index.records()), 1)
        self.assertEqual(index.records()[0]["tags"], ["Places/Harbour"])
        index.close()
        self.assertEqual({box: row[0] for box, row in self.faces().items()}, ids)


if __name__ == "__main__":
    unittest.main()
