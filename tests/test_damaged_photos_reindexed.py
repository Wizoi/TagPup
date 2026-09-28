"""A photo indexed from a damaged file is indexed again for real once its file is whole
(docs/findings.md, #407).

A photo that may be an incomplete copy -- it decodes, grey below a line -- is indexed: a
vector and faces made from half a picture. Replaced by a good copy, its record was
forgotten but the vector and the faces stayed, since the indexer passes over a photo
that has a vector. Now, when a record is forgotten because its file changed or reads
whole, the photo's vectors are taken away and its faces -- unless one carries a decision,
when they are kept and not detected again -- and its folder is indexed again.

Real files and real ExifTool; the CLIP model and the face detector are stood in for, the
model's vector different at each call, the detector's face where the test says.
"""
import itertools
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import damaged_photos  # noqa: E402
import own_home  # noqa: E402
from click.testing import CliRunner  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.ml.clip import output_dim  # noqa: E402
from tagpup.services import damaged_photos as damaged  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
THEN = 1_700_000_000
DIM = output_dim(library_settings.DEFAULTS["model.name"]) or 512


class Detector:
    """What the face detector finds: one unnamed face at `box`."""
    box = [1, 1, 20, 20]

    def detect_and_embed_faces(self, path):
        return [{"box": list(Detector.box), "embedding": [0.5] * 512, "prob": 0.99, "crop_image": None}]


@unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")
class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="damaged_again_")
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.folder = os.path.join(self.home.root, "Harbour")
        self.half = damaged_photos.second_half_zeros(os.path.join(self.folder, "copy stopped.jpg"))
        os.utime(self.half, (THEN, THEN))
        self.calls = itertools.count(1)
        Detector.box = [1, 1, 20, 20]
        self.index()
        self.assertEqual(["copy stopped.jpg"], [each["name"] for each in damaged.listed(self.library)])
        self.first = self.vector()

    def index(self, folders=None):
        with mock.patch("tagpup.ml.clip.ClipModel._init_model"), \
                mock.patch("tagpup.ml.clip.ClipModel.embed_picture", side_effect=lambda img: [0.01 * next(self.calls)] * DIM), \
                mock.patch("tagpup.ml.faces.FaceModel", lambda **settings: Detector()):
            for folder in folders or [self.folder]:
                result = CliRunner().invoke(tagpup_cli.cli, ["--db", self.db_path, "index", folder, "--no-subfolders"])
                self.assertEqual(0, result.exit_code, result.output)
        return Result(attempted=1, changed=len(folders or [self.folder]))

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def vector(self):
        where, params = paths.sql_equals("p.path", self.half)
        # Each vector's first value: the model stood in for makes a new one at each call.
        return [bytes(v)[:4].hex() for (v,) in self.query(
            "SELECT e.vector FROM embeddings e JOIN photos p ON p.id = e.photo_id WHERE " + where, params)]

    def faces(self):
        return sorted((box, name) for box, name in self.query("SELECT box, name FROM faces"))

    def replace_with_the_whole_photo(self):
        damaged_photos.whole_jpeg(self.half, seed=13, size=(480, 360))
        Detector.box = [30, 30, 60, 60]


class ReplacedAndSynced(Case):
    def test_its_vector_and_faces_are_made_again_from_the_whole_photo(self):
        self.assertEqual([("[1, 1, 20, 20]", None)], self.faces())
        self.replace_with_the_whole_photo()
        sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL, queue=self.index)
        self.assertEqual([], damaged.listed(self.library))
        self.assertEqual(1, len(self.vector()))
        self.assertNotEqual(self.first, self.vector(), "the vector made from half a picture was kept")
        self.assertEqual([("[30, 30, 60, 60]", None)], self.faces(), "the faces found in half a picture were kept")

    def test_a_face_with_a_name_is_kept_and_said_so(self):
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE faces SET name = ?, name_source = 'manual'", ("Rowan Thackeray",))
            conn.commit()
        finally:
            conn.close()
        self.replace_with_the_whole_photo()
        with self.assertLogs("tagpup.services.damaged_photos", logging.INFO) as said:
            sync.sync(self.library, apply=True, exiftool_path=EXIFTOOL, queue=self.index)
        self.assertEqual([("[1, 1, 20, 20]", "Rowan Thackeray")], self.faces())
        self.assertNotEqual(self.first, self.vector())
        self.assertTrue(any("faces are kept" in line for line in said.output), said.output)


class ReplacedAndIndexed(Case):
    def test_the_indexer_makes_them_again_too(self):
        self.replace_with_the_whole_photo()
        self.index()
        self.assertEqual([], damaged.records(self.library))
        self.assertNotEqual(self.first, self.vector())
        self.assertEqual([("[30, 30, 60, 60]", None)], self.faces())


if __name__ == "__main__":
    unittest.main()
