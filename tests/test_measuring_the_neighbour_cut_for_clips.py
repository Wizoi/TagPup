"""The owner measures Suggest's neighbour cut for clips (scripts/measure_pipeline_holdouts.py clips; findings #2626,
DECISIONS 2026-11-07): on a copy of a library holding a clip, every photo with a tag that is not a person and every clip
asked for its neighbours as a clip is matched on its best of five frames and on its frame 0 alone, held out by photo and
by folder; at each cut, the share of the neighbour slots the clips take, and the precision and recall of the tags
Suggest offers from the neighbours.

The scenarios: a clip untagged whose late frame is like a photo takes that photo's one neighbour slot at its best frame,
and the tag the frame-0 match offered is lost; a clip asked finds no clip; the run on the owner's library writes nothing
to it (the copy, in a work folder of its own, is what is loaded) and prints no path or tag. The rows are made as the
indexer makes them (photo_rows.add_read, store.embeddings.put), in a home of their own. Fictional names only; no model.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import own_home  # noqa: E402
import photo_rows  # noqa: E402
import stand_in_models  # noqa: E402

import measure_pipeline_holdouts as holdouts  # noqa: E402
from tagpup.runtime import Runtime  # noqa: E402
from tagpup.services import settings, suggester  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db, schema  # noqa: E402
from tagpup.store import embeddings as store_embeddings  # noqa: E402


class NoFaces(stand_in_models.Faces):
    """The library's face model, which finds no face: the tags are the measurement's, not the people."""

    def detect_and_embed_faces(self, *_args, **_kwargs):
        return []


def blob(*values):
    return np.asarray(values, dtype=np.float32).tobytes()


class ALibraryWithAClip(unittest.TestCase):
    """Four folders: the asked photo (Quay), a photo of the same tag (Mole), an untagged clip whose frames 1 to 4 are
    like the asked photo and whose frame 0 is like nothing of it (Ferry), and an untagged photo like the clip's frame 0
    (Dune). Likeness to the asked photo: the clip 0.96 at its best, 0 at frame 0; the other photo 0.8."""

    def setUp(self):
        self.home = own_home.for_test(self, prefix="clip_cut_")
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        conn = db.connect(self.path)
        try:
            key = settings.photo_key_on(conn)
            self.ids = {}
            for name, tags, vector, frames in (
                    ("Quay/Quay - 1.jpg", ["Place/Quay"], (1, 0, 0), ()),
                    ("Mole/Mole - 1.jpg", ["Place/Quay"], (0.8, 0.6, 0), ()),
                    ("Ferry/Ferry - 1.mp4", [], (0, 0, 1), [(0.96, -0.28, 0)] * 4),
                    ("Dune/Dune - 1.jpg", [], (0, 0.6, 0.8), ())):
                path = os.path.join(self.home.root, "photos", name)
                self.ids[name.split("/")[0]] = photo_rows.add_read(conn, path, {"XMP:Subject": tags})
                store_embeddings.put(conn, path, key, 1.0, 1, blob(*vector), None, frames=[blob(*f) for f in frames])
            conn.commit()
        finally:
            conn.close()
        self.key = key

    def measured(self):
        index = PhotoIndex(self.path, self.key)
        self.assertTrue(index.load())
        self.addCleanup(index.close)
        with mock.patch.object(holdouts, "NEIGHBOURS", 1):   # one slot: a clip in it is a photo's tag lost
            return holdouts.clip_neighbours(index, suggester.neighbours_model(index, NoFaces()), 10, 1, lambda _: None)

    def test_a_clip_at_its_best_frame_takes_the_slot_and_its_tag_is_lost(self):
        found = self.measured()
        self.assertEqual((4, 1, 2, 3), (found["photos"], found["clips"], found["eligible"], found["queries"]))
        for held_out in ("by_photo", "by_folder"):   # one photo a folder: the two agree
            best, first = found[held_out]["best_of_five"], found[held_out]["frame_0"]
            # The asked photo's one neighbour is the clip at 0.96 (its frame 0 is 0 to it); the other photo's is the
            # asked one at 0.8. On frame 0 alone both photos' neighbours are photos.
            self.assertEqual([0.96], [round(best["likeness"]["photo"]["clip"]["1"]["median"], 2)])
            self.assertAlmostEqual(0.0, best["likeness"]["photo"]["clip_frame_0"]["1"]["median"], places=5)
            self.assertEqual(1, best["likeness"]["photo"]["photo"]["1"]["n"])
            self.assertEqual({}, first["likeness"]["photo"]["clip"])
            self.assertEqual(2, first["likeness"]["photo"]["photo"]["1"]["n"])
            at_cut = best["cuts"]["%.2f" % holdouts.NEIGHBOUR_CUT]["photo"]
            before = first["cuts"]["%.2f" % holdouts.NEIGHBOUR_CUT]["photo"]
            self.assertEqual((2, 1), (at_cut["slots"], at_cut["clip_slots"]))
            self.assertEqual((2, 0), (before["slots"], before["clip_slots"]))
            # The clip carries no tag: the asked photo is offered nothing; the other photo, its own tag.
            self.assertEqual((1, 1, 4), (at_cut["offered"]["offered"], at_cut["offered"]["right"],
                                         at_cut["offered"]["truth"]))
            self.assertEqual((2, 2), (before["offered"]["offered"], before["offered"]["right"]))
            # At 0.85 the clip's best frame alone is kept: every slot a clip's, nothing offered; frame 0 keeps none.
            high = best["cuts"]["0.85"]["photo"]
            self.assertEqual((1, 1, 0), (high["slots"], high["clip_slots"], high["offered"]["offered"]))
            self.assertEqual(0, first["cuts"]["0.85"]["photo"]["slots"])

    def test_a_clip_asked_finds_the_photo_like_its_frame_0_and_is_not_scored(self):
        found = self.measured()
        for match in holdouts.MATCHES:
            asked = found["by_folder"][match]
            self.assertEqual(0.8, round(asked["likeness"]["clip"]["photo"]["1"]["median"], 2))
            self.assertEqual({}, asked["likeness"]["clip"]["clip"])
            self.assertEqual(0, asked["cuts"]["0.35"]["clip"]["offered"]["photos"])   # it has no tag of its own

    def test_the_run_reads_a_copy_and_writes_nothing_to_the_library(self):
        work, out = tempfile.mkdtemp(prefix="clip_cut_work_"), tempfile.mkdtemp(prefix="clip_cut_out_")
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, out, True)
        shutil.rmtree(work)   # claim_work_folder takes a missing folder, never one holding files it did not make

        def digest():
            with open(self.path, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
        before, printed = digest(), io.StringIO()
        args = Namespace(work=work, sample=10, seed=1, keep_work=False)
        with mock.patch.dict(os.environ), mock.patch.object(Runtime, "faces", return_value=NoFaces()), \
                contextlib.redirect_stdout(printed):
            holdouts.clips_main(args, os.path.abspath(self.path), out)
        self.assertEqual(before, digest())
        self.assertFalse(os.path.exists(work), "the copy is removed when the run is done")
        written = [name for name in os.listdir(out) if name.startswith("holdout-clips-")]
        self.assertEqual(1, len(written))
        self.assertTrue(written[0].startswith("holdout-clips-harbour-"), "the file names the library measured")
        with open(os.path.join(out, written[0]), encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual((3, "harbour"), (json.loads(text)["queries"], json.loads(text)["library"]))
        for secret in ("Quay", "Mole", "Ferry", "Dune", "Place", self.home.root):
            self.assertNotIn(secret, text)
            self.assertNotIn(secret, printed.getvalue().replace(work, "").replace(out, ""))
        self.assertIn("| 0.35 (Suggest's) |", printed.getvalue())


    def test_a_work_folder_kept_from_another_library_is_refused_not_measured(self):
        """#2642: a copy kept (--keep-work) from one library was measured, unlabelled, by a run naming another."""
        work, out = tempfile.mkdtemp(prefix="clip_cut_work_"), tempfile.mkdtemp(prefix="clip_cut_out_")
        self.addCleanup(shutil.rmtree, work, True)
        self.addCleanup(shutil.rmtree, out, True)
        shutil.rmtree(work)
        other = self.home.library("lakeside.db")
        shutil.copyfile(self.path, other)

        def run(source):   # each run in the home it was started in: a run enters its work folder as its home
            with mock.patch.dict(os.environ), mock.patch.object(Runtime, "faces", return_value=NoFaces()), \
                    contextlib.redirect_stdout(io.StringIO()):
                holdouts.clips_main(Namespace(work=work, sample=10, seed=1, keep_work=True), os.path.abspath(source), out)
        run(self.path)
        with self.assertRaises(SystemExit) as refused:
            run(other)
        self.assertIn("holds a copy of another library", str(refused.exception))
        self.assertEqual(1, len([name for name in os.listdir(out) if name.startswith("holdout-clips-")]))


if __name__ == "__main__":
    unittest.main()
