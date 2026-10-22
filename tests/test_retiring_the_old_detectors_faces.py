"""Retiring the old detector's faces of a library on the new face model (tagpup.services.retire_faces; owner, 2026-10-22:
"we have the new detector data and none of the old/manual data"). What the owner lives through (scenario P8):

- (a) The plan says, in counts, what goes and what is cleared, and writes nothing; the apply deletes every face holding
  no vector under the new model -- named automatically, by hand, excluded or undecided -- with its crop and its vectors,
  and clears every decision of the faces kept: names (by hand or automatic), "nobody", exclusions and their reasons. A
  face no work touches is untouched; the people of each photo lose the faces' names and keep the keywords' people; no
  keyword of a photo and no tag of the tree moves; the faces' AUTOINCREMENT counter does not move.
- (b) Stopped after its first chunk (Ctrl+C between two transactions), or a chunk's write failing part-way (rolled back
  whole), a run again finishes it, and the whole retiring is ONE change of the journal.
- (c) Undone from History, every deleted face comes back with its crop and vectors, and every decision as it was: the
  library's faces, crops, vectors and people are what they were, byte for byte.
- (d) A library not on the new model, or one more of whose faces are in photos the pass has not reached than a switch
  allows (an undo of its pass in History leaves it so), is refused, with nothing written; one switched back to FaceNet
  between the plan and the write is written nothing, and the run says why.
- (e) A photo removed (the sync's forget_photo) between the plan and the write is left alone, and counted so.

Fictional names; a library in a home of the test's own; rows as the indexer and the pass store them.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import FACENET, add_face, people_of, person_of  # noqa: E402
import photo_rows  # noqa: E402

import numpy as np  # noqa: E402

from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import retire_faces  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.store import db  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import people  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

NEW = validation.SCRFD_ADAFACE
OPERATION = retire_faces.OPERATION % NEW


def vector(rng):
    found = rng.normal(size=512).astype(np.float32)
    return (found / np.linalg.norm(found)).tobytes()


class RetiringTheOldDetectorsFaces(unittest.TestCase):
    """harbour: five photos read by the index, a keyword person on the first, and faces as the pass left them -- matched
    faces holding FaceNet's vector and the new model's, faces the old detector alone found holding FaceNet's -- each with
    its crop, named automatically, by hand, nobody, excluded or undecided; switched to the new model after its pass."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="retire_faces_")
        self.library = Library(self.home.library("harbour.db"))
        library_actions.create(self.library.path)
        folder = paths.stored(os.path.join(self.home.root, "Quay"))
        os.makedirs(folder)
        library_actions.record_added(self.library, [folder])
        self.photo = {n: os.path.join(folder, "ferry %d.jpg" % n) for n in range(1, 6)}
        rng = np.random.default_rng(7)
        conn = db.connect(self.library.path)
        try:
            person_of(conn, "Tamsin Okoro")
            person_of(conn, "Odile Farrant")
            photo_ids = [photo_rows.add_read(conn, self.photo[1], {"XMP:Subject": ["Tamsin Okoro"]})]
            for n in range(2, 6):
                photo_ids.append(photo_rows.add_read(conn, self.photo[n], {"XMP:Subject": ["Harbour/Ferry"]}))
            self.face = {}

            def face(label, n, matched, **columns):
                face_id = add_face(conn, self.photo[n], box=(n, n, 20 + n, 20 + n), embedding=vector(rng), prob=0.98,
                                   **columns)
                if matched:
                    conn.execute("INSERT INTO face_embeddings (face_id, model, vector) VALUES (?, ?, ?)",
                                 (face_id, NEW, vector(rng)))
                conn.execute("INSERT INTO face_crops (face_id, jpeg) VALUES (?, ?)", (face_id, bytes([n]) * 64))
                self.face[label] = face_id

            face("old undecided", 1, False)
            face("matched, named automatically", 1, True, name="Odile Farrant")
            face("old, named by hand", 2, False, name="Tamsin Okoro", name_source="manual")
            face("old, excluded", 2, False, name_source="manual", excluded=1, excluded_reason="not a face")
            face("matched, nobody", 2, True, name_source="manual")
            face("matched, named by hand", 3, True, name="Odile Farrant", name_source="manual")
            face("matched, excluded", 3, True, name_source="manual", excluded=1, excluded_reason="a statue")
            face("matched undecided", 3, True)
            face("matched undecided alone", 4, True)
            face("old, named automatically", 5, False, name="Tamsin Okoro")
            face("matched beside it", 5, True)
            people.rebuild(conn, photo_ids)   # the photos' people, as every face writer leaves them
            conn.commit()
        finally:
            conn.close()
        switched = library_settings.change(self.library, {"faces.model": NEW}, acknowledged=["face-model"])
        self.assertEqual(1, switched.changed, switched.refused)

    # ---- reading the library --------------------------------------------------------------------------------------

    def read(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def state(self):
        """Everything retiring may touch, and what it must not: faces, crops, vectors, people, keywords, the tree, the
        faces' counter."""
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return {
                "faces": conn.execute("SELECT * FROM faces ORDER BY id").fetchall(),
                "crops": conn.execute("SELECT * FROM face_crops ORDER BY face_id").fetchall(),
                "vectors": conn.execute("SELECT * FROM face_embeddings ORDER BY face_id, model").fetchall(),
                "people": {n: people_of(conn, self.photo[n]) for n in self.photo},
                "keywords": conn.execute("SELECT id, tags FROM photos ORDER BY id").fetchall(),
                "tree": conn.execute("SELECT * FROM tag_taxonomy ORDER BY id").fetchall(),
                "counter": conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'faces'").fetchone(),
            }
        finally:
            conn.close()

    def faces(self):
        return {face_id: dict(zip(store_faces.DECISION, rest)) for face_id, *rest in self.read(
            "SELECT id, " + ", ".join(store_faces.DECISION) + " FROM faces")}

    def retire_changes(self):
        return self.read("SELECT id, summary FROM changes WHERE operation = ?", (OPERATION,))

    def label(self, *labels):
        return {self.face[each] for each in labels}

    # ---- (a) --------------------------------------------------------------------------------------------------------

    def test_the_old_detectors_faces_go_and_the_kept_faces_lose_their_decisions(self):
        before = self.state()
        self.assertEqual(["Tamsin Okoro", "Odile Farrant"], before["people"][1])
        planned = retire_faces.plan(self.library)
        self.assertIsNone(planned.refused)
        self.assertEqual({"faces": 11, "photos": 4, "deleted": 4, "deleted_named": 1, "deleted_decided": 2,
                          "cleared_by_hand": 1, "cleared_automatic": 1, "cleared_nobody": 1, "cleared_excluded": 1},
                         planned.counts)
        self.assertIn("4 of the library's 11 faces hold no vector under %s" % NEW, planned.said())
        self.assertEqual(before, self.state(), "the plan wrote")

        with mock.patch.object(retire_faces, "CHUNK", 2):
            retired = retire_faces.apply(self.library, planned)
        self.assertEqual((4, 4, 0, 0), (retired.deleted, retired.cleared, retired.left_alone, retired.failed),
                         retired.said())
        self.assertEqual("4 faces deleted and 4 faces' decisions cleared, in 4 of 4 photos; 0 photos left alone "
                         "(changed since).", retired.said())

        after = self.state()
        gone = self.label("old undecided", "old, named by hand", "old, excluded", "old, named automatically")
        self.assertEqual(set(), gone & {row[0] for row in after["faces"]})
        self.assertEqual(set(), gone & {row[0] for row in after["crops"]})
        self.assertEqual(set(), gone & {row[0] for row in after["vectors"]})
        undecided = dict(store_faces.UNDECIDED)
        for face_id, decision in self.faces().items():
            self.assertEqual(undecided, decision, face_id)
        # The kept faces keep their crops and both vectors.
        kept = {row[0] for row in before["faces"]} - gone
        self.assertEqual(kept, {row[0] for row in after["crops"]})
        self.assertEqual({(face_id, model) for face_id in kept for model in (FACENET, NEW)},
                         {(row[0], row[1]) for row in after["vectors"]})
        # The faces' names leave the photos' people; the keyword's person stays; no keyword or tag moves.
        self.assertEqual(["Tamsin Okoro"], after["people"][1])
        self.assertEqual({n: [] for n in range(2, 6)}, {n: after["people"][n] for n in range(2, 6)})
        self.assertEqual(before["keywords"], after["keywords"])
        self.assertEqual(before["tree"], after["tree"])
        self.assertEqual(before["counter"], after["counter"])
        changes = self.retire_changes()
        self.assertEqual(1, len(changes))
        self.assertIn('"cleared": 4, "deleted": 4', changes[0][1])

        # Run again: nothing left to do, nothing written.
        again = retire_faces.plan(self.library)
        self.assertEqual([], again.photo_ids)
        self.assertEqual(0, again.counts["deleted"])
        self.assertEqual([], retire_faces.apply(self.library, again).chunks)
        self.assertEqual(changes, self.retire_changes())

    # ---- (b) --------------------------------------------------------------------------------------------------------

    def test_a_run_stopped_after_its_first_chunk_is_finished_by_the_next_as_one_change(self):
        asked = iter([False, True])
        with mock.patch.object(retire_faces, "CHUNK", 2):
            first = retire_faces.apply(self.library, retire_faces.plan(self.library), stop=lambda: next(asked))
            self.assertTrue(first.stopped)
            self.assertEqual((1, 1), (len(first.chunks), len(first.changes)))
            self.assertEqual((3, 2), (first.deleted, first.cleared))
            self.assertIn("Stopped", first.said())
            second = retire_faces.apply(self.library, retire_faces.plan(self.library))
        self.assertEqual((1, 2, False), (second.deleted, second.cleared, second.stopped))
        self.assertEqual(first.changes, second.changes)
        self.assertEqual(1, len(self.retire_changes()))
        self.assertEqual(set(), set(self.faces()) & self.label("old, named automatically"))

    def test_a_chunk_whose_write_fails_is_rolled_back_whole_and_a_run_again_finishes(self):
        written = store_faces.put_decisions
        calls = []

        def fails_the_second(conn, values):
            calls.append(len(values))
            if len(calls) == 2:
                raise OSError("the disk went away")
            return written(conn, values)

        planned = retire_faces.plan(self.library)
        failing = mock.patch.object(store_faces, "put_decisions", fails_the_second)
        with mock.patch.object(retire_faces, "CHUNK", 2), failing, self.assertLogs("tagpup.services.retire_faces", "ERROR"):
            first = retire_faces.apply(self.library, planned)
        self.assertEqual((2, "OSError", 1), (first.failed, first.error, len(first.chunks)))
        self.assertIn("The write of 2 photos failed", first.said())
        # The second chunk's faces are as they were: its delete went back with its decisions.
        self.assertIn(self.face["old, named automatically"], self.faces())
        self.assertEqual("Odile Farrant", self.faces()[self.face["matched, named by hand"]]["name"])
        self.assertIn('"cleared": 2, "deleted": 3', self.retire_changes()[0][1])
        with mock.patch.object(retire_faces, "CHUNK", 2):
            second = retire_faces.apply(self.library, retire_faces.plan(self.library))
        self.assertEqual((1, 2, 0), (second.deleted, second.cleared, second.failed))
        self.assertEqual(1, len(self.retire_changes()))

    # ---- (c) --------------------------------------------------------------------------------------------------------

    def test_undone_from_history_every_face_and_decision_comes_back(self):
        before = self.state()
        with mock.patch.object(retire_faces, "CHUNK", 2):
            retired = retire_faces.apply(self.library, retire_faces.plan(self.library))
        (change_id,) = retired.changes
        listed = journal_service.history(self.library, limit=5)
        self.assertIn(OPERATION, str(listed))
        undone = journal_service.undo(self.library, change_id, apply=True)
        self.assertFalse(undone.refused, undone.refused)
        self.assertEqual(before, self.state())

    # ---- (d) --------------------------------------------------------------------------------------------------------

    def test_a_library_not_on_the_new_model_is_refused(self):
        back = library_settings.change(self.library, {"faces.model": FACENET}, acknowledged=["face-model"])
        self.assertEqual(1, back.changed, back.refused)
        before = self.state()
        planned = retire_faces.plan(self.library)
        self.assertEqual(retire_faces.NOT_ON_THE_MODEL % (FACENET, NEW), planned.refused)
        retired = retire_faces.apply(self.library, planned)
        self.assertEqual(([], planned.refused), (retired.chunks, retired.said()))
        self.assertEqual(before, self.state())
        self.assertEqual([], self.retire_changes())

    def test_a_library_switched_back_after_the_plan_is_left_as_it_is(self):
        planned = retire_faces.plan(self.library)
        back = library_settings.change(self.library, {"faces.model": FACENET}, acknowledged=["face-model"])
        self.assertEqual(1, back.changed, back.refused)
        before = self.state()
        retired = retire_faces.apply(self.library, planned)
        self.assertEqual(([], 4, retire_faces.MODEL_MOVED), (retired.chunks, retired.failed, retired.error))
        self.assertIn("The write of 4 photos failed (%s)" % retire_faces.MODEL_MOVED, retired.said())
        self.assertEqual(before, self.state())
        self.assertEqual([], self.retire_changes())

    def test_a_library_its_pass_has_not_reached_is_refused(self):
        # The pass's vectors gone, as an undo of the pass in History leaves them: every face is in a photo not passed.
        conn = db.connect(self.library.path)
        try:
            conn.execute("DELETE FROM face_embeddings WHERE model = ?", (NEW,))
            conn.commit()
        finally:
            conn.close()
        before = self.state()
        planned = retire_faces.plan(self.library)
        self.assertTrue(planned.refused.startswith("Nothing was written: 0 of the library's 11 faces hold a vector"),
                        planned.refused)
        self.assertEqual([], retire_faces.apply(self.library, planned).chunks)
        self.assertEqual(before, self.state())
        self.assertEqual([], self.retire_changes())

    # ---- (e) --------------------------------------------------------------------------------------------------------

    def test_a_photo_removed_between_the_plan_and_the_write_is_left_alone(self):
        planned = retire_faces.plan(self.library)
        store_photos.forget_photo(self.library.path, self.photo[5])
        with mock.patch.object(retire_faces, "CHUNK", 2):
            retired = retire_faces.apply(self.library, planned)
        self.assertEqual((3, 4, 1), (retired.deleted, retired.cleared, retired.left_alone))
        self.assertEqual([], self.read("SELECT id FROM photos WHERE id = ?", (planned.photo_ids[-1],)))
        self.assertEqual([], self.read("SELECT id FROM faces WHERE photo_id = ?", (planned.photo_ids[-1],)))


if __name__ == "__main__":
    unittest.main()
