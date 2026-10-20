"""Scenario P9: the gate of phase 3, read-only (scripts/measure_face_gate.py, tagpup.services.face_gate). After the face pass,
the owner scores the new face model against FaceNet on the same held-out named faces of each library and of both together,
and reads the cut each would name at for the precision he chooses, before he decides to switch.

Each library is in a home of the test's own; photos as the index makes their rows (tests/photo_rows.py), old faces as the
indexer stores them (tests/face_rows.py, FaceNet's vector), and the new model's vectors and new faces written by the pass's
own writer (store.faces.give_vectors, in a journaled change, as face_pass does). The vectors are stand-ins with a known
separability: each person a centre, FaceNet's faces far from it, the new model's near it.
"""
import contextlib
import hashlib
import io
import os
import sys
import unittest
import unittest.mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import measure_face_gate as gate_script  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import FACENET, add_face  # noqa: E402

from tagpup.services import face_gate, face_pass  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

PEOPLE = ("Ilse Varnham", "Tobin Marlowe", "Odile Brackwater", "Fenn Ashgrove", "Calla Dunmore", "Rufus Pellingham",
          "Neve Holloway", "Bram Quenby", "Selma Ardwick", "Jory Castleton", "Wynn Halberd", "Petra Lunsford")
NEW_KEY = face_gate.NEW


def library_of(home, name, people, faces_each, folders, old_noise, new_noise, seed):
    """A library in `home` whose `people` each have `faces_each` named faces, one a photo, spread over `folders` folders, each
    holding a FaceNet vector (`old_noise` from its person's centre) and, by the pass, the new model's (`new_noise`); plus, in the
    first photo, an unnamed face the pass made and a named face it did not match. Its path."""
    rng = np.random.default_rng(seed)
    path = home.library(name)
    schema.ensure(path)
    centres = {person: rng.normal(size=64) for person in people}

    def near(centre, noise):
        vector = centre / np.linalg.norm(centre) + rng.normal(scale=noise, size=64)
        return (vector / np.linalg.norm(vector)).astype(np.float32).tobytes()

    conn = db.connect(path)
    try:
        change = journal.record(conn, face_pass.OPERATION % NEW_KEY, [], {"folder": "x", "matched": 0, "new": 0})
        n = 0
        for person in people:
            for i in range(faces_each):
                photo = os.path.join(home.root, "Event %d" % ((n + i) % folders), "%s %d.jpg" % (person.split()[0], i))
                photo_rows.add_unread(conn, photo)
                side = 25 + 10 * (i % 4)   # 25, 35, 45 and 55 px: each band of the floor
                face_id = add_face(conn, photo, (0, 0, side, side), near(centres[person], old_noise), name=person, prob=0.99)
                store_faces.give_vectors(conn, change, photo, NEW_KEY, [(face_id, near(centres[person], new_noise))], [],
                                         {"matched": 1, "new": 0})
                if n == 0 and i == 0:
                    add_face(conn, photo, (60, 0, 120, 60), near(centres[person], old_noise), name=people[-1])
                    store_faces.give_vectors(conn, change, photo, NEW_KEY, [],
                                             [([200, 0, 260, 60], 0.98, near(centres[person], new_noise), b"jpeg")],
                                             {"matched": 0, "new": 1})
            n += faces_each
        conn.commit()
    finally:
        conn.close()
    return path


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


class TheGateReadsBothModels(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.big = library_of(self.home, "harbour.db", PEOPLE, 25, 5, old_noise=0.16, new_noise=0.03, seed=3)
        self.tiny = library_of(self.home, "orchard.db", PEOPLE[:2], 3, 2, old_noise=0.16, new_noise=0.03, seed=4)

    def test_the_better_model_ranks_better_and_nothing_is_written(self):
        before = fingerprint(self.big), fingerprint(self.tiny)
        with unittest.mock.patch.object(db, "connect", wraps=db.connect) as connect:
            answers, text = run("--source", self.big, "--source", self.tiny, "--precision", "0.95", "--precision", "0.98")
        self.assertEqual(before, (fingerprint(self.big), fingerprint(self.tiny)), "a read-only run changed a library")
        self.assertTrue(connect.call_args_list)
        self.assertTrue(all(call.args[0].endswith("?mode=ro") for call in connect.call_args_list), "opened for writing")

        harbour = answers["harbour"]
        self.assertFalse(harbour["thin"])
        for precision in (0.95, 0.98):
            both = harbour["precisions"][precision]
            old, new = both["old"]["margin"], both["new"]["margin"]
            self.assertTrue(both["better"], precision)
            self.assertGreater(new["at"]["recall"], old["at"]["recall"])
            self.assertGreaterEqual(new["at"]["precision"], precision)
            self.assertEqual(new["at"]["faces"], sum(band["faces"] for band in new["bands"]))
            self.assertEqual([1, 1, 1, 1], [int(band["faces"] > 0) for band in new["bands"]])   # every band has faces
            under = [floor["right_under"] for floor in new["floors"]]
            self.assertEqual(sorted(under), under)   # a higher floor loses as many or more

        # The pass's counts, from the rows and the journal: the unmatched named face is on the review list.
        counts = harbour["pass"]
        self.assertEqual((300, 1, 1), (counts["matched"], counts["unmatched_named"], counts["new"]))
        self.assertEqual((1, 300, 1), (counts["changes"], counts["journal_matched"], counts["journal_new"]))

        # The tiny library says so, and decides nothing; pooled, its faces count beside the big one's.
        self.assertTrue(answers["orchard"]["thin"])
        self.assertIsNone(answers["orchard"]["precisions"][0.95]["better"])
        self.assertIn("TOO THIN", text)
        self.assertEqual(answers["harbour"]["evaluated"] + answers["orchard"]["evaluated"], answers["pooled"]["evaluated"])

        # A library where FaceNet is the better is read so too.
        worse = library_of(self.home, "quay.db", PEOPLE, 25, 5, old_noise=0.03, new_noise=0.16, seed=5)
        self.assertFalse(run("--source", worse, "--precision", "0.95")[0]["quay"]["precisions"][0.95]["better"])

        # Counts only: no person, photo or folder is named.
        for name in PEOPLE + ("Event", ".jpg"):
            self.assertNotIn(name, text)

    def test_a_model_whose_margins_run_past_half_is_read_there(self):
        """A model that separates people widely puts its wrong faces at margins over 0.5 too (AdaFace's live margins reach
        0.744): its cut is read where the precision is reached, not "no cut reaches it" because the grid stopped at 0.5."""
        count = 400
        margin = np.concatenate([np.linspace(0.60, 0.70, 300), np.linspace(0.40, 0.52, 100)])
        found = {"right": np.arange(count) < 300, "reachable": np.ones(count, dtype=bool), "margin": margin,
                 "likeness": margin + 0.2, "short": np.full(count, 60.0), "unnamed_likeness": np.empty(0),
                 "unnamed_margin": np.empty(0)}
        reading = {"pass": {}, "models": {face_gate.OLD: found, face_gate.NEW: found}}
        cut = face_gate.score(reading, [0.95])["precisions"][0.95]["new"]["margin"]
        self.assertIsNotNone(cut, "no cut reached 0.95 though every face over 0.52 is right")
        self.assertGreater(cut["cut"], 0.5)
        self.assertEqual(300, cut["at"]["right"])
        self.assertGreaterEqual(cut["at"]["precision"], 0.95)

    def test_a_library_the_pass_has_not_reached_has_nothing_to_score(self):
        path = self.home.library("meadow.db")
        schema.ensure(path)
        conn = db.connect(path)
        try:
            photo = os.path.join(self.home.root, "Meadow", "a.jpg")
            photo_rows.add_unread(conn, photo)
            add_face(conn, photo, (0, 0, 60, 60), np.ones(64, dtype=np.float32).tobytes(), name=PEOPLE[0], model=FACENET)
            conn.commit()
        finally:
            conn.close()
        answers, text = run("--source", path)
        self.assertIn("nothing to score", text)
        self.assertIsNone(answers["meadow"]["precisions"][0.99]["better"])
        self.assertEqual((0, 1), (answers["meadow"]["pass"]["photos_passed"], answers["meadow"]["pass"]["not_passed"]))


if __name__ == "__main__":
    unittest.main()
