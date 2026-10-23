"""Retiring the old face model's VECTORS of a library on the new one (tagpup.services.retire_faces, plan_vectors and
apply_vectors; `retire-vectors`; owner, 2026-10-23, DECISIONS: "THE FACENET VECTORS OF photo_index ARE RETIRED NOW").
What the owner lives through (scenarios P8 and D4):

- (a) The plan is refused, nothing written, while a face holds FaceNet's vector alone (live: kr-track 1,211 such faces,
  renton_parkrun 329, counted 2026-10-23: retire-faces first), and for a library not on the new model; a library switched
  back between the plan and the write is written nothing.
- (b) After retire-faces, the dry run (the CLI's default) counts the vectors and writes nothing, no snapshot either.
- (c) Applied in chunks, every reader keyed by the library's model (the Identify queue, clustering, a photo's faces, a
  person's page) answers the same rows before and after; the faces generation moves (a cache of the old vectors is not
  the library's); ONE change of the journal with no rows, its summary the counts committed.
- (d) From the command line, a forced snapshot first; Ctrl+C between two chunks stops it, and a run again continues the
  SAME change and takes no second snapshot. A snapshot restore gives the vectors back.
- (e) History lists the change as not undone, naming the snapshot; the undo of the earlier retiring of the old
  detector's faces (its recorded vectors under FaceNet's key, change 3247's shape; #1783) and of the switch to the new
  model (#1781's check, an update here; the insert shape is P10's) are refused naming the snapshot, nothing written; a
  switch back to FaceNet is refused once no face holds its vector (#1782). A library a stopped run left holding some of
  FaceNet's vectors may still switch back, told how many faces hold none.

Fictional names; a library in a home of the test's own; rows as the indexer and the pass store them (tests/face_rows.py,
tests/photo_rows.py).
"""
import json
import os
import signal
import sys
import unittest
from unittest import mock

from click.testing import CliRunner

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
from face_rows import FACENET, add_face, add_vector_of_face, person_of  # noqa: E402
import photo_rows  # noqa: E402

import numpy as np  # noqa: E402

import tagpup_cli  # noqa: E402
from tagpup.core import paths, validation  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import retire_faces  # noqa: E402
from tagpup.services import settings as library_settings  # noqa: E402
from tagpup.services import snapshots as library_snapshots  # noqa: E402
from tagpup.store import db, generations  # noqa: E402
from tagpup.store import faces as store_faces  # noqa: E402
from tagpup.store import snapshots as store_snapshots  # noqa: E402

NEW = validation.SCRFD_ADAFACE
OPERATION = retire_faces.VECTORS_OPERATION % FACENET
PERSON = "Imogen Varga"


def vector(rng):
    found = rng.normal(size=512).astype(np.float32)
    return (found / np.linalg.norm(found)).tobytes()


class RetiringTheOldModelsVectors(unittest.TestCase):
    """estuary: three photos read by the index; faces as the pass left them -- four matched (FaceNet's vector and the
    new model's), one the old detector alone found (FaceNet's alone), one the new model alone found -- and switched to
    the new model after its pass."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="retire_vectors_")
        self.library = Library(self.home.library("estuary.db"))
        library_actions.create(self.library.path)
        folder = paths.stored(os.path.join(self.home.root, "Estuary"))
        os.makedirs(folder)
        library_actions.record_added(self.library, [folder])
        self.photo = {n: os.path.join(folder, "heron %d.jpg" % n) for n in range(1, 4)}
        rng = np.random.default_rng(11)
        conn = db.connect(self.library.path)
        try:
            self.person = person_of(conn, PERSON)
            photo_rows.add_read(conn, self.photo[1], {"XMP:Subject": [PERSON]})
            for n in (2, 3):
                photo_rows.add_read(conn, self.photo[n], {"XMP:Subject": ["Estuary/Heron"]})
            self.face = {}

            def matched(label, n, box, **columns):
                self.face[label] = add_face(conn, self.photo[n], box, vector(rng), prob=0.99, **columns)
                add_vector_of_face(conn, self.face[label], vector(rng), NEW)

            matched("named", 1, (2, 2, 30, 30), name=PERSON)
            self.face["old alone"] = add_face(conn, self.photo[1], (32, 4, 60, 34), vector(rng), prob=0.97)
            matched("matched 2", 2, (4, 4, 30, 30))
            self.face["new alone"] = add_face(conn, self.photo[2], (34, 4, 60, 30), vector(rng), model=NEW, prob=0.98)
            matched("matched 3", 3, (6, 6, 30, 30))
            matched("matched 3b", 3, (36, 6, 60, 30))
            conn.commit()
        finally:
            conn.close()
        switched = library_settings.change(self.library, {"faces.model": NEW}, acknowledged=["face-model"])
        self.assertEqual(1, switched.changed, switched.refused)
        self.switch_change = switched.details["change"]

    # ---- reading the library --------------------------------------------------------------------------------------

    def read(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def state(self):
        """What the run may touch, and what it must not."""
        return {"faces": self.read("SELECT * FROM faces ORDER BY id"),
                "crops": self.read("SELECT * FROM face_crops ORDER BY face_id"),
                "vectors": self.read("SELECT face_id, model, vector FROM face_embeddings ORDER BY face_id, model"),
                "settings": self.read("SELECT key, value FROM settings ORDER BY key"),
                "changes": self.read("SELECT id, operation, status, summary FROM changes ORDER BY id")}

    def facenet_vectors(self):
        return self.read("SELECT COUNT(*) FROM face_embeddings WHERE model = ?", (FACENET,))[0][0]

    def vector_changes(self):
        return [(change_id, json.loads(summary)) for change_id, summary in
                self.read("SELECT id, summary FROM changes WHERE operation = ?", (OPERATION,))]

    def readers(self):
        """What every reader keyed by the library's model answers."""
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            key = store_faces.model_key(conn)
            return {"queue": store_faces.identify_candidates(conn), "clustering": store_faces.for_clustering(conn, key),
                    "photos": {n: store_faces.in_photo(conn, path, key) for n, path in self.photo.items()},
                    "person": store_faces.person_page(conn, PERSON, -1, 0, key)}
        finally:
            conn.close()

    def faces_generation(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return generations.value(conn, "faces")
        finally:
            conn.close()

    def retire_the_old_faces(self):
        """retire-faces, as the owner runs it first; a person named again by hand after it (Name faces, the strip).
        The change's id."""
        retired = retire_faces.apply(self.library, retire_faces.plan(self.library))
        (change_id,) = retired.changes
        conn = db.connect(self.library.path)
        try:
            self.assertEqual(1, store_faces.name(conn, [self.face["named"]], self.person))
            conn.commit()
        finally:
            conn.close()
        return change_id

    def refusals(self, *change_ids):
        operations = dict(self.read("SELECT id, operation FROM changes"))
        return journal_service.refusals(self.library, [{"id": cid, "operation": operations[cid]} for cid in change_ids])

    # ---- (a) --------------------------------------------------------------------------------------------------------

    def test_a_face_holding_facenets_vector_alone_refuses_the_plan(self):
        before = self.state()
        planned = retire_faces.plan_vectors(self.library)
        self.assertEqual(retire_faces.ALONE % (1, FACENET, NEW), planned.refused)
        retired = retire_faces.apply_vectors(self.library, planned)
        self.assertEqual(([], planned.refused), (retired.chunks, retired.said()))
        self.assertEqual(before, self.state())

    def test_a_library_not_on_the_new_model_is_refused_and_one_switched_back_after_the_plan_is_written_nothing(self):
        self.retire_the_old_faces()
        planned = retire_faces.plan_vectors(self.library)
        self.assertIsNone(planned.refused)
        back = library_settings.change(self.library, {"faces.model": FACENET}, acknowledged=["face-model"])
        self.assertEqual(1, back.changed, back.refused)
        before = self.state()
        retired = retire_faces.apply_vectors(self.library, planned)
        self.assertEqual(([], retire_faces.MODEL_MOVED), (retired.chunks, retired.error))
        self.assertEqual(before, self.state())
        self.assertEqual(retire_faces.NOT_ON_THE_MODEL % (FACENET, NEW), retire_faces.plan_vectors(self.library).refused)
        self.assertEqual([], self.vector_changes())

    # ---- (b), (c) ---------------------------------------------------------------------------------------------------

    def test_applied_in_chunks_every_reader_answers_the_same_and_the_faces_generation_moves(self):
        self.retire_the_old_faces()
        planned = retire_faces.plan_vectors(self.library)
        self.assertEqual((4, 4 * 2048, None, None), (planned.vectors, planned.bytes, planned.refused, planned.continues))
        before, readers, generation = self.state(), self.readers(), self.faces_generation()
        self.assertEqual(1, len(readers["person"]))
        self.assertEqual(before, self.state(), "the plan wrote")
        heard = []
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 3):
            retired = retire_faces.apply_vectors(self.library, planned, heard=lambda chunk, chunks: heard.append(chunks))
        self.assertEqual(([3, 1], [2, 2], None), ([c.deleted for c in retired.chunks], heard, retired.error))
        self.assertEqual("4 vectors under %s deleted (0.0 MB), in 2 chunks." % FACENET, retired.said())

        self.assertEqual(readers, self.readers(), "a reader keyed by the library's model answers otherwise")
        self.assertNotEqual(generation, self.faces_generation())
        after = self.state()
        self.assertEqual((before["faces"], before["crops"], before["settings"]),
                         (after["faces"], after["crops"], after["settings"]))
        self.assertEqual([row for row in before["vectors"] if row[1] == NEW], after["vectors"])
        self.assertEqual([(retired.change_id, {"bytes": 4 * 2048, "deleted": 4})], self.vector_changes())
        self.assertEqual([], self.read("SELECT * FROM change_rows WHERE change_id = ?", (retired.change_id,)),
                         "the change holds no rows: the snapshot is the way back")
        again = retire_faces.plan_vectors(self.library)
        self.assertEqual((0, retired.change_id), (again.vectors, again.continues))

    # ---- (d) --------------------------------------------------------------------------------------------------------

    def cli(self, *args):
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 3):
            return CliRunner().invoke(tagpup_cli.cli, ["--db", self.library.path, "retire-vectors"] + list(args))

    def dailies(self):
        return store_snapshots.listed(self.library.path, store_snapshots.DAILY)

    def test_from_the_command_line_a_dry_run_writes_nothing_and_takes_no_snapshot(self):
        refused = self.cli()
        self.assertEqual(1, refused.exit_code, refused.output)
        self.assertIn(" ".join((retire_faces.ALONE % (1, FACENET, NEW)).split()), " ".join(refused.output.split()))
        self.retire_the_old_faces()
        before = self.state()
        ran = self.cli()
        self.assertEqual(0, ran.exit_code, ran.output)
        self.assertTrue(ran.output.startswith("Retiring the old face model's vectors of estuary (a dry run)."), ran.output)
        self.assertIn("4 vectors under %s (0.0 MB) are deleted" % FACENET, ran.output)
        self.assertIn("Nothing changed. --apply writes it", ran.output)
        self.assertEqual(before, self.state())
        self.assertEqual([], self.dailies(), "a dry run takes no snapshot")

    def test_from_the_command_line_stopped_between_chunks_a_run_again_continues_the_change_and_a_restore_gives_them_back(self):
        self.retire_the_old_faces()
        deleting = store_faces.delete_vectors
        pressed = []

        def ctrl_c_in_the_first_chunk(conn, rowids):
            if not pressed:
                pressed.append(True)
                signal.raise_signal(signal.SIGINT)
            return deleting(conn, rowids)

        with mock.patch.object(store_faces, "delete_vectors", ctrl_c_in_the_first_chunk):
            first = self.cli("--apply")
        self.assertEqual(1, first.exit_code, first.output)
        self.assertIn("Took a snapshot of the library first (daily", first.output)
        self.assertIn("Chunk 1 of 2: 3 vectors deleted.", first.output)
        self.assertNotIn("Chunk 2 of 2", first.output)
        self.assertIn("Stopped: run it again to continue.", first.output)
        self.assertEqual(1, self.facenet_vectors())
        ((change_id, summary),) = self.vector_changes()
        self.assertEqual({"bytes": 3 * 2048, "deleted": 3}, summary)
        snapshot = [each.name for each in self.dailies()]
        self.assertEqual(1, len(snapshot))

        second = self.cli("--apply")
        self.assertEqual(0, second.exit_code, second.output)
        self.assertIn("Continuing change %d" % change_id, second.output)
        self.assertNotIn("Took a snapshot", second.output)
        self.assertEqual(snapshot, [each.name for each in self.dailies()], "a second snapshot, holding fewer vectors")
        self.assertEqual([(change_id, {"bytes": 4 * 2048, "deleted": 4})], self.vector_changes(), "one change")
        self.assertEqual(0, self.facenet_vectors())
        self.assertIn("Written: change %d, which is not undone" % change_id, second.output)
        self.assertIn("Nothing to retire", self.cli().output)

        (listed,) = [each for each in library_snapshots.listing(self.library)["snapshots"] if each["name"] == snapshot[0]]
        self.assertEqual(1, listed["changes_since"], "the restore's loss is counted")
        asked = library_snapshots.restore(self.library, snapshot[0])
        self.assertEqual([(change_id, OPERATION)], [(lost["id"], lost["operation"]) for lost in asked.details["lost"]])
        restored = library_snapshots.restore(self.library, snapshot[0], apply=True)
        self.assertEqual(1, restored.changed, restored.refused)
        self.assertEqual(4, self.facenet_vectors())
        self.assertEqual([], self.vector_changes())

    # ---- (e) --------------------------------------------------------------------------------------------------------

    def test_once_retired_their_undo_the_retirings_and_the_switchs_and_a_switch_back_are_refused(self):
        retiring = self.retire_the_old_faces()
        self.assertEqual([("update",)], self.read("SELECT DISTINCT action FROM change_rows WHERE change_id = ?",
                                                  (self.switch_change,)))
        self.assertEqual({retiring: None, self.switch_change: None}, self.refusals(retiring, self.switch_change),
                         "undoable while FaceNet's vectors stand")
        retired = retire_faces.apply_vectors(self.library, retire_faces.plan_vectors(self.library))
        self.assertEqual(4, retired.deleted)
        vectors = retired.change_id
        listed = journal_service.history(self.library, limit=5)
        self.assertIn(OPERATION, str(listed))

        gone = retire_faces.VECTORS_RETIRED % FACENET
        none_held = library_settings.NONE_HELD % (5, FACENET)
        self.assertEqual({vectors: gone, retiring: gone, self.switch_change: none_held},
                         self.refusals(vectors, retiring, self.switch_change))
        before = self.state()
        for change_id, said in ((vectors, gone), (retiring, gone), (self.switch_change, none_held)):
            for apply in (False, True):
                refused = journal_service.undo(self.library, change_id, apply=apply)
                self.assertEqual(said, refused.refused if isinstance(refused.refused, str) else refused.refused[0])
        self.assertIn("snapshots restore", gone)
        self.assertIn("snapshots restore", none_held)
        back = library_settings.change(self.library, {"faces.model": FACENET}, acknowledged=["face-model"])
        self.assertEqual((0, none_held), (back.changed, back.refused))
        self.assertEqual(before, self.state(), "a refusal wrote")

    def test_a_library_keeping_some_of_facenets_vectors_may_still_switch_back(self):
        self.retire_the_old_faces()
        with mock.patch.object(retire_faces, "VECTOR_CHUNK", 2):
            retired = retire_faces.apply_vectors(self.library, retire_faces.plan_vectors(self.library),
                                                 stop=lambda: bool(self.vector_changes()))
        self.assertEqual((2, True), (retired.deleted, retired.stopped))
        self.assertEqual(2, self.facenet_vectors())
        back = library_settings.change(self.library, {"faces.model": FACENET}, acknowledged=["face-model"])
        self.assertEqual(1, back.changed, back.refused)
        self.assertEqual(3, back.details["face_vectors"]["without"])


if __name__ == "__main__":
    unittest.main()
