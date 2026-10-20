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

from tagpup.ml import scrfd_adaface  # noqa: E402
from tagpup.services import face_gate, face_pass  # noqa: E402
from tagpup.store import db, journal, schema  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402

PEOPLE = ("Ilse Varnham", "Tobin Marlowe", "Odile Brackwater", "Fenn Ashgrove", "Calla Dunmore", "Rufus Pellingham",
          "Neve Holloway", "Bram Quenby", "Selma Ardwick", "Jory Castleton", "Wynn Halberd", "Petra Lunsford")
#: People seen at one event only: each has a folder of their own, so held out by folder their own person is not in the gallery.
STRANGERS = ("Ottilie Grisewood", "Lucan Merriweather")
NEW_KEY = face_gate.NEW


def library_of(home, name, people, faces_each, folders, old_noise, new_noise, seed, strangers=()):
    """A library in `home` whose `people` each have `faces_each` named faces, one a photo, spread over `folders` folders, each
    holding a FaceNet vector (`old_noise` from its person's centre) and, by the pass, the new model's (`new_noise`); plus, in the
    first photo, an unnamed face the pass made and a named face it did not match; and as many of each of the `strangers`, all
    in a folder of their own. Its path."""
    rng = np.random.default_rng(seed)
    path = home.library(name)
    schema.ensure(path)
    centres = {person: rng.normal(size=64) for person in tuple(people) + tuple(strangers)}

    def near(centre, noise):
        vector = centre / np.linalg.norm(centre) + rng.normal(scale=noise, size=64)
        return (vector / np.linalg.norm(vector)).astype(np.float32).tobytes()

    conn = db.connect(path)
    try:
        change = journal.record(conn, face_pass.OPERATION % NEW_KEY, [], {"folder": "x", "matched": 0, "new": 0})
        n = 0
        for person in tuple(people) + tuple(strangers):
            for i in range(faces_each):
                folder = "Away %d" % strangers.index(person) if person in strangers else "Event %d" % ((n + i) % folders)
                photo = os.path.join(home.root, folder, "%s %d.jpg" % (person.split()[0], i))
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
            old, new = both["old"]["closed"]["margin"], both["new"]["closed"]["margin"]
            self.assertTrue(both["better"], precision)
            self.assertGreater(new["at"]["recall"], old["at"]["recall"])
            self.assertGreaterEqual(new["at"]["precision"], precision)
            self.assertEqual(new["at"]["faces"], sum(band["faces"] for band in new["bands"]))
            self.assertEqual([1, 1, 1, 1], [int(band["faces"] > 0) for band in new["bands"]])   # every band has faces
            under = [floor["right_under"] for floor in new["floors"]]
            self.assertEqual(sorted(under), under)   # a higher floor loses as many or more
            # The floor a new face is made at is among those read: a new floor is not shipped unread.
            self.assertIn(scrfd_adaface.NEW_FACE_FLOOR, [floor["floor"] for floor in new["floors"]])
            # A band of about 75 faces cannot tell 0.98 from what is under it (196 needed): said thin; the bands together can.
            if precision == 0.98:
                self.assertEqual([True] * 4, [band["thin"] for band in new["bands"]])
                self.assertFalse(new["thin"])
            # The floor read twice: an old face under it counted as missed, or left out (the floor makes no new face
            # under it, so an old face there is not lost); the second is the bands at or above the floor, pooled.
            for floor, start in zip(new["floors"], (1, 2, 3)):
                above = new["bands"][start:]
                self.assertAlmostEqual(sum(band["right"] for band in above) / sum(band["faces"] for band in above),
                                       floor["recall_at_or_above"])
                self.assertAlmostEqual((new["at"]["right"] - floor["right_under"]) / new["at"]["faces"],
                                       floor["recall_counting_under"])
        self.assertRegex(text, r"px \d+ faces, right \d+, wrong \d+, recall [0-9.]+ [(]thin[)]")
        self.assertIn("among the faces at or above it", text)

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

    def test_a_face_whose_person_is_not_in_the_gallery_is_named_wrong(self):
        """Two people seen at one event only: held out by folder, their own person is not there to be found, so every name
        the gate's cut gives their faces is wrong. The closed set (the models compared) leaves them out; the open set
        counts them, its precision is lower at the same cut, and its own cut is read on that curve."""
        path = library_of(self.home, "harbour away.db", PEOPLE, 25, 5, old_noise=0.16, new_noise=0.03, seed=3,
                          strangers=STRANGERS)
        answers, text = run("--source", path, "--precision", "0.95")
        both = answers["harbour away"]["precisions"][0.95]
        self.assertTrue(both["better"])
        for side in ("old", "new"):
            closed, opened = both[side]["closed"]["margin"], both[side]["open"]["margin"]
            self.assertEqual(50, both[side]["alone"])
            self.assertLessEqual(closed["at"]["open_precision"], closed["at"]["precision"])
            self.assertGreaterEqual(opened["cut"], closed["cut"])
            self.assertGreaterEqual(opened["at"]["open_precision"], 0.95)
        # AdaFace names every face right on the closed set: its cut is the lowest, it names the strangers too, and says so.
        closed = both["new"]["closed"]["margin"]
        self.assertTrue(closed["every"])
        self.assertEqual(50, closed["at"]["alone_named"])
        self.assertLess(closed["at"]["open_precision"], 0.95)
        self.assertGreater(both["new"]["open"]["margin"]["cut"], closed["cut"])
        self.assertIn("no cut: it names every face", text)
        self.assertIn("whose person is not in the gallery (alone in their folder) are all wrong", text)
        for name in PEOPLE + STRANGERS + ("Away", "Event"):
            self.assertNotIn(name, text)

    def test_a_face_called_nobody_is_not_one_it_would_name(self):
        """Two unnamed faces the pass matched; the owner called one of them nobody by hand (store.faces.unname, as the page
        does). The gate's "unnamed it would name" counts the other alone: a decided nobody is not waiting for a name."""
        conn = db.connect(self.big)
        try:
            photo = os.path.join(self.home.root, "Event 0", "Ilse 0.jpg")
            change = journal.record(conn, face_pass.OPERATION % NEW_KEY, [], {"folder": "x", "matched": 0, "new": 0})
            vector = np.ones(64, dtype=np.float32) / 8
            ids = [add_face(conn, photo, (0, 70 * k, 60, 70 * k + 60), vector.tobytes(), prob=0.99) for k in (2, 3)]
            store_faces.give_vectors(conn, change, photo, NEW_KEY, [(face_id, vector.tobytes()) for face_id in ids], [],
                                     {"matched": 2, "new": 0})
            store_faces.unname(conn, ids[:1])
            conn.commit()
        finally:
            conn.close()
        answers, text = run("--source", self.big, "--precision", "0.95")
        harbour = answers["harbour"]
        self.assertEqual(1, harbour["pass"]["unnamed_nobody"])
        for side in ("old", "new"):
            for kind in ("closed", "open"):
                self.assertEqual(1, harbour["precisions"][0.95][side][kind]["margin"]["unnamed"])
        self.assertIn("unnamed faces holding both vectors: 1 (1 called nobody by hand left out)", text)
        self.assertIn("held out by photo", text)

    def test_a_model_whose_margins_run_past_half_is_read_there(self):
        """A model that separates people widely puts its wrong faces at margins over 0.5 too (AdaFace's live margins reach
        0.744): its cut is read where the precision is reached, not "no cut reaches it" because the grid stopped at 0.5."""
        count = 400
        margin = np.concatenate([np.linspace(0.60, 0.70, 300), np.linspace(0.40, 0.52, 100)])
        found = {"right": np.arange(count) < 300, "reachable": np.ones(count, dtype=bool), "margin": margin,
                 "likeness": margin + 0.2, "short": np.full(count, 60.0), "unnamed_likeness": np.empty(0),
                 "unnamed_margin": np.empty(0)}
        reading = {"pass": {}, "models": {face_gate.OLD: found, face_gate.NEW: found}}
        cut = face_gate.score(reading, [0.95])["precisions"][0.95]["new"]["closed"]["margin"]
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
