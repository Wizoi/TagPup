"""Scenario P9: the face model's precision by year, read-only (scripts/measure_face_gate.py --alone,
tagpup.services.face_gate.read_alone and by_gap). Before a change to how a face is matched, the owner reads how often the
library's own face model matches a named face with the right person, by how far in years the face it matched is: with a
child who grew past her old faces, a photo whose clock said 2079, a person seen at one event only, and photos whose
folder names a year. The year is the photo's Date Taken, never a year in a name.

A library holds one face model's vectors (the new one, SCRFD and AdaFace: owner, 2026-10-25, no mixed-model compat), so
the reading is of that model alone. The gate of phase 3 that scored FaceNet against the new model on faces holding both
vectors had its scenario here until the old model was retired: no face of the three libraries holds two models' vectors
(counted read-only 2026-10-26: face_embeddings holds scrfd-adaface alone in each), and its tests went with it.

Each library is in a home of the test's own; photos as the index makes their rows (tests/photo_rows.py), faces as the
indexer stores them (tests/face_rows.py, the library's model's vector).
"""
import contextlib
import hashlib
import io
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import measure_face_gate as gate_script  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.core import validation  # noqa: E402
from tagpup.store import db, schema  # noqa: E402

PEOPLE = ("Ilse Varnham", "Tobin Marlowe", "Odile Brackwater", "Fenn Ashgrove")
NEW_KEY = validation.SCRFD_ADAFACE


def fingerprint(path):
    """The library's bytes and what its write-ahead log holds: what a read-only run must leave as they were. A read-only
    connection may leave an empty log and its index beside the file (SQLite's, as every read through db.readonly_uri does);
    a write would put pages in the log."""
    with open(path, "rb") as handle:
        found = hashlib.sha256(handle.read()).hexdigest()
    log = path + "-wal"
    return found, os.path.getsize(log) if os.path.exists(log) else 0


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        answers = gate_script.main(list(argv))
    return answers, out.getvalue()


class TheGateReadsByYearGap(unittest.TestCase):
    """A library on the new face model: four people photographed twice a year from 2010 to 2013 (a spring and an autumn folder, each
    face a little older than the last); a child photographed in 2008 and again in 2020, when she looks like her cousin
    more than like her old faces; the cousin, seen in 2008 at one event only (one folder); a photo whose clock said
    2079, of the first person as he was in 2010; a photo with no Date Taken in a folder named 'Picnic 2015', of the
    second as she was after 2013; and a photo taken in 2014 in a folder named 'Summer 1999', of the third."""

    STEADY = PEOPLE[:4]
    CHILD, COUSIN = "Tamsin Revell", "Corin Ashby"

    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("fells.db")
        schema.ensure(self.path)
        rng = np.random.default_rng(7)
        centre = {person: rng.normal(size=64) for person in self.STEADY + (self.CHILD, self.COUSIN)}
        older = {person: rng.normal(size=64) for person in self.STEADY}

        def unit(vector):
            vector = vector / np.linalg.norm(vector) + rng.normal(scale=0.01, size=64)
            return (vector / np.linalg.norm(vector)).astype(np.float32).tobytes()

        conn = db.connect(self.path)
        try:
            def face(folder, name, year, vector, person):
                photo = os.path.join(self.home.root, folder, name + ".jpg")
                photo_rows.add_read(conn, photo, {} if year is None else {"EXIF:DateTimeOriginal": "%d:06:01 12:00:00" % year})
                add_face(conn, photo, (0, 0, 60, 60), unit(vector), name=person, prob=0.99)

            def aged(person, year):
                return centre[person] / np.linalg.norm(centre[person]) + 0.25 * (year - 2010) * older[person] / 8

            for year in range(2010, 2014):
                for season in ("Spring", "Autumn"):
                    for person in self.STEADY:
                        face("%s %d" % (season, year), person.split()[0], year, aged(person, year), person)
            for i in range(3):
                face("Spring 2008", "Tamsin %d" % i, 2008, centre[self.CHILD], self.CHILD)
                face("Autumn 2008", "Corin %d" % i, 2008, centre[self.COUSIN], self.COUSIN)
                grown = 0.3 * centre[self.CHILD] / np.linalg.norm(centre[self.CHILD]) + centre[self.COUSIN] / np.linalg.norm(
                    centre[self.COUSIN])
                face("Spring 2020", "Tamsin %d" % i, 2020, grown, self.CHILD)
            face("Lost clock", "Ilse", 2079, centre[self.STEADY[0]], self.STEADY[0])
            face("Picnic 2015", "Tobin", None, aged(self.STEADY[1], 2014.5), self.STEADY[1])
            face("Summer 1999", "Odile", 2014, aged(self.STEADY[2], 2014.5), self.STEADY[2])
            conn.commit()
        finally:
            conn.close()

    def test_precision_by_the_years_to_the_face_matched(self):
        before = fingerprint(self.path)
        answers, text = run("--alone", "--source", self.path)
        self.assertEqual(before, fingerprint(self.path), "a read-only run changed the library")
        [bands] = answers["fells"].values()
        by = {(band["low"], band["high"]): band for band in bands}
        # The library's own model alone, at its naming cuts.
        self.assertEqual([NEW_KEY], list(answers["fells"]))
        # Each steady face meets its twin of the other season, the same year: right, no year apart.
        self.assertEqual(1.0, by[(0, 0)]["top1_right"])
        self.assertGreaterEqual(by[(0, 0)]["faces"], 28)
        # The child grew past her old faces: the three of 2008 still find hers of 2020, but those of 2020 her cousin's of
        # 2008, twelve years off and wrong; named at the cuts, each of those is a wrong name.
        self.assertEqual((6, 3, 0.5), (by[(11, None)]["faces"], by[(11, None)]["right"], by[(11, None)]["top1_right"]))
        self.assertEqual(3, by[(11, None)]["wrong"])
        # A clock that said 2079 is no year to match by: that face, and the faces matched with it, are undated, not 69
        # years off (core.dates.era_year).
        self.assertGreaterEqual(by[(None, None)]["faces"], 1)
        # The year is the photo's Date Taken: the picnic photo has none, so it is undated, not 2015 from its folder's
        # name (two years from the 2013 face it meets); the summer photo was taken in 2014, one year from its 2013 face,
        # not 1999 from its folder's name.
        self.assertEqual(0, by[(2, 2)]["faces"])
        self.assertEqual(1, by[(1, 1)]["faces"])
        # The cousin's faces are all in one folder: her person is not in the gallery, so she is not among the faces
        # read, and the name the cut gives her (her cousin's, 2020) is counted apart, wrong.
        self.assertEqual(4 * 4 * 2 + 1 + 6 + 2, sum(band["faces"] for band in bands))
        self.assertEqual(3, sum(band["alone_named"] for band in bands))
        self.assertIn("undated", text)
        self.assertIn("11+", text)
        self.assertIn("top-1 right", text)
        self.assertIn("precision at the cut", text)
        for name in PEOPLE + (self.CHILD, self.COUSIN, "Spring", "Autumn", "Lost clock", "Picnic", "Summer", ".jpg"):
            self.assertNotIn(name, text)

    def test_photos_the_index_never_read_are_undated(self):
        """A library whose photos Suggest made rows for and the index never read: no Date Taken, so every face it reads
        is undated, by the library's own model."""
        path = self.home.library("meadow.db")
        schema.ensure(path)
        rng = np.random.default_rng(11)
        conn = db.connect(path)
        try:
            for person in PEOPLE:
                centre = rng.normal(size=64)
                for folder in ("Meadow", "Orchard"):
                    photo = os.path.join(self.home.root, folder, person.split()[0] + ".jpg")
                    photo_rows.add_unread(conn, photo)
                    vector = centre / np.linalg.norm(centre) + rng.normal(scale=0.01, size=64)
                    add_face(conn, photo, (0, 0, 60, 60), (vector / np.linalg.norm(vector)).astype(np.float32).tobytes(),
                             name=person, prob=0.99)
            conn.commit()
        finally:
            conn.close()
        answers, _text = run("--alone", "--source", path)
        self.assertEqual([NEW_KEY], list(answers["meadow"]))
        [bands] = answers["meadow"].values()
        self.assertEqual(8, sum(band["faces"] for band in bands))
        self.assertEqual(8, bands[-1]["faces"])
        self.assertEqual((None, None), (bands[-1]["low"], bands[-1]["high"]))


if __name__ == "__main__":
    unittest.main()
