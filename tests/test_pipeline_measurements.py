"""The phase 0 measurements of the AI pipeline (scripts/measure_pipeline_stages.py, measure_pipeline_holdouts.py,
the helpers they share in scripts/sandbox.py; docs/AI_PIPELINE.md): what they draw and how they count, on made-up
numbers. Nothing here loads a model, opens a library or starts a process: the runs themselves are the owner's, in a
window he names.

The sample a person relies on: a third or more at 10 faces or more, the worst photo in it, each stratum spread from
its smallest picture to its largest, a sideways photo when there is one. The hold-out by photo: a face is never
matched with its own photo's faces, and a person with no face elsewhere is counted apart, not as a miss.
"""
import json
import os
import random
import sys
import shutil
import tempfile
import unittest
from argparse import Namespace
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import numpy as np  # noqa: E402

import measure_pipeline_holdouts as holdouts  # noqa: E402
import measure_pipeline_stages as stages  # noqa: E402
import sandbox  # noqa: E402
from tagpup.core import clustering, holdout  # noqa: E402


def library_rows(rng):
    """A library's census rows: most photos with a face or three, a few crowds, one of 759 faces."""
    rows = []
    for photo_id in range(1, 2001):
        faces = rng.choice([0] * 3 + [1, 2, 3] * 4 + [4, 6, 9] + [12, 30])
        rows.append({"id": photo_id, "faces": faces, "megapixels": rng.uniform(0.5, 40.0),
                     "turned": photo_id % 600 == 0})
    rows.append({"id": 9999, "faces": 759, "megapixels": 24.0, "turned": False})
    return rows


class TheSample(unittest.TestCase):
    def setUp(self):
        self.rows = library_rows(random.Random(7))
        self.chosen = sandbox.choose_sample(self.rows, 300, worst=10, rng=random.Random(1))

    def test_a_third_or_more_has_ten_faces_or_more_and_the_worst_photo_is_in_it(self):
        many = [row for row in self.chosen if row["faces"] >= 10]
        self.assertGreaterEqual(len(many), 100)
        self.assertIn(9999, [row["id"] for row in self.chosen if row["stratum"] == "worst"])
        self.assertEqual(10, sum(1 for row in self.chosen if row["stratum"] == "worst"))

    def test_every_stratum_is_drawn_once_each_and_the_size_is_kept(self):
        self.assertEqual(len(self.chosen), len({row["id"] for row in self.chosen}))
        self.assertAlmostEqual(300, len(self.chosen), delta=2)
        for name in sandbox.STRATA:
            self.assertTrue(any(row["stratum"] == name for row in self.chosen), name)
        for row in self.chosen:
            if row["stratum"] != "worst":
                self.assertEqual(row["stratum"], sandbox.stratum(row["faces"]))

    def test_each_stratum_spans_its_smallest_and_largest_picture(self):
        for name in ("none", "1-3", "4-9"):
            pool = [row["megapixels"] for row in self.rows if sandbox.stratum(row["faces"]) == name]
            drawn = [row["megapixels"] for row in self.chosen if row["stratum"] == name]
            self.assertIn(max(pool), drawn, name)

    def test_a_turned_photo_is_drawn_when_the_stratum_has_one(self):
        turned_strata = {sandbox.stratum(row["faces"]) for row in self.rows if row["turned"]}
        for name in turned_strata:
            self.assertTrue(any(row["turned"] for row in self.chosen if row["stratum"] == name), name)

    def test_a_small_library_gives_what_it_has(self):
        rows = [{"id": i, "faces": i % 3, "megapixels": 1.0 + i, "turned": False} for i in range(1, 21)]
        chosen = sandbox.choose_sample(rows, 300, rng=random.Random(1))
        self.assertEqual(sorted(row["id"] for row in rows), sorted(row["id"] for row in chosen))


class TheCounts(unittest.TestCase):
    def test_summary_and_the_megapixel_bins(self):
        found = sandbox.summary([3.0, None, 1.0, 2.0, 10.0])
        self.assertEqual((4, 10.0, 16.0), (found["n"], found["max"], found["sum"]))
        self.assertEqual({"n": 0}, sandbox.summary([None]))
        bins = sandbox.megapixel_bins([0.3, 5.9, 6.0, 12.1, 45.0])
        self.assertEqual({"under 2": 1, "2-6": 1, "6-12": 1, "12-20": 1, "20-30": 0, "30 and over": 1}, bins)

    def test_the_card_is_idle_by_its_samples_inside_the_window(self):
        self.assertEqual((37.0, 4100.0), sandbox.parse_smi("37, 4100\n"))
        self.assertIsNone(sandbox.parse_smi("[N/A], 4100"))
        samples = [(1.0, 90, 5000), (2.0, 0, 6000), (3.0, 0, 6500), (4.0, 5, 6400), (5.0, 80, 9000)]
        found = sandbox.window(samples, 2.0, 4.0)
        self.assertEqual(3, found["samples"])
        self.assertAlmostEqual(2 / 3, found["idle_share"])
        self.assertEqual(1.0, found["under_10_share"])
        self.assertEqual(6500, found["peak_memory_mib"])
        self.assertEqual({"samples": 0}, sandbox.window(samples, 10, 11))

    def test_the_process_measured_is_the_one_that_wrote_its_id(self):
        # The venv's python.exe is a launcher: the child's own id comes from a file it writes.
        import tempfile
        folder = tempfile.mkdtemp(prefix="tagpup_pid_test_")
        try:
            sampler = sandbox.Sampler()
            sampler.watch(os.path.join(folder, "child.pid"))
            self.assertIsNone(sampler.watched())
            with open(os.path.join(folder, "child.pid"), "w", encoding="utf-8") as handle:
                handle.write("4242")
            self.assertEqual(4242, sampler.watched())
            sampler.watch(17)
            self.assertEqual(17, sampler.watched())
        finally:
            import shutil
            shutil.rmtree(folder, True)

    def test_output_goes_outside_the_repository(self):
        with self.assertRaises(SystemExit):
            sandbox.outside_the_repository(os.path.join(ROOT, "measurements"))

    def test_a_copy_goes_where_its_row_is(self):
        places = {"pictures": [os.path.join("D:" + os.sep, "Pictures")]}
        name, under = sandbox.owner_relative(os.path.join("D:" + os.sep, "Pictures", "2024", "a.jpg"), places)
        self.assertEqual(("pictures", os.path.join("2024", "a.jpg")), (name, under))
        self.assertIsNone(sandbox.owner_relative(os.path.join("E:" + os.sep, "a.jpg"), places))


class ThePhotoStages(unittest.TestCase):
    def test_each_stage_is_read_from_the_marks_and_a_load_is_not_a_decode(self):
        record = {"t0": 0.0, "decoded": 12.1, "clip_load": 12.0, "prepared": 12.2, "clip_start": 12.2,
                  "clip_end": 12.5, "identity write": 0.05, "f0": 0.0, "f_read": 0.1, "detect_in": 12.7,
                  "detect_out": 13.2, "encode": 0.04, "resnet_in": 13.3, "f_end": 13.35}
        found = stages.stages_of(record)
        self.assertAlmostEqual(0.1, found["decode for CLIP"])
        self.assertAlmostEqual(0.1, found["pad and resize"])
        self.assertAlmostEqual(0.3, found["CLIP forward"])
        self.assertAlmostEqual(0.1, found["decode for faces"])
        self.assertAlmostEqual(0.5, found["MTCNN"])
        self.assertAlmostEqual(0.06, found["crop and tensor"])
        self.assertAlmostEqual(0.04, found["crop JPEG encode"])
        self.assertAlmostEqual(0.05, found["recogniser forward"])

    def test_a_photo_with_no_face_has_no_recogniser_stage(self):
        found = stages.stages_of({"t0": 0, "decoded": 0.1, "prepared": 0.2, "clip_start": 0.2, "clip_end": 0.3,
                                  "f0": 0.0, "f_read": 0.1, "detect_in": 0.4, "detect_out": 0.6, "f_end": 0.6})
        self.assertNotIn("recogniser forward", found)
        self.assertAlmostEqual(0.0, found["crop and tensor"])

    def test_the_table_leaves_the_warm_up_out(self):
        photo = {"stratum": "1-3", "faces_found": 2, "megapixels": 12.0, "total": 1.0, "stages": {"MTCNN": 0.4}}
        warm_up = dict(photo, total=20.0, stages={"MTCNN": 9.0})
        table = stages.stage_table([warm_up, photo, dict(photo, stratum="10+")])
        self.assertEqual(2, table["all"]["photos"])
        self.assertEqual(1.0, table["all"]["total"]["max"])
        self.assertEqual(0.4, table["1-3"]["MTCNN"]["mean"])


class TheHooksReachTheModelsAsTheIndexCallsThem(unittest.TestCase):
    """The stages run's hooks wrap the models' own methods by name: a method renamed in tagpup.ml left the run timing
    nothing (the processor/card split, phase 1b). Each hook is put on a real model, its weights stood in for, and a
    photo is taken through it the way the index takes it -- the processor's half, then the card's."""

    def setUp(self):
        from PIL import Image
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.photo = os.path.join(folder.name, "quay.jpg")
        Image.new("RGB", (64, 48), "navy").save(self.photo, "JPEG")
        self.timeline = stages.Timeline(lambda: None)

    def test_clip_is_timed_from_its_decode_to_its_forward(self):
        import torch
        from tagpup.ml import clip
        from tagpup.services import search
        model = clip.ClipModel("ViT-B-32", "openai", True, 1.4, None, device="cpu")

        def load():
            model.model = type("Weights", (), {"encode_image": lambda self, batch: torch.ones(1, 4)})()
            model.preprocess = lambda picture: torch.zeros(3, 2, 2)
        model._init_model = load
        stages._watch_clip(model, self.timeline)
        made = search.PhotoEmbeddings.made
        self.addCleanup(setattr, search.PhotoEmbeddings, "made", made)
        stages._watch_made(search.PhotoEmbeddings, self.timeline)
        embeddings = search.PhotoEmbeddings(model, remember=False)
        self.assertEqual(4, len(embeddings.made(self.photo, embeddings.prepared(self.photo))))
        found = stages.stages_of(self.timeline.record(self.photo))
        for stage in ("decode for CLIP", "pad and resize", "CLIP forward"):
            self.assertIn(stage, found)
        self.assertEqual([64, 48], self.timeline.record(self.photo)["upright"])

    def test_faces_are_timed_from_their_decode_to_the_detector(self):
        import torch
        from tagpup.ml import faces
        model = faces.FaceModel(20, 0.85, [0.6, 0.7, 0.7], device="cpu")

        class Detector:
            def detect(self, pixels):
                return None, None

        def load():
            if model.mtcnn is None:
                model.mtcnn, model.resnet = Detector(), torch.nn.Identity()
        model._init_models = load
        stages._watch_faces(model, self.timeline)
        self.assertEqual([], model.detect_and_embed(model.picture(self.photo, max_side=None)))
        record = self.timeline.record(self.photo)
        self.assertEqual([64, 48], record["stored"])
        self.assertEqual(0, record["faces_found"])
        found = stages.stages_of(record)
        for stage in ("decode for faces", "MTCNN"):
            self.assertIn(stage, found)

    def test_the_cards_wait_for_each_photo_is_timed_and_its_begin_apart(self):
        """Phase 1c's question: what of the processor's work the read-ahead did not hide. The photos come back in
        their order through the wrapped iteration, each with its wait and its `begin`'s share of it."""
        from tagpup.core import read_ahead
        iterate = read_ahead.ReadAhead.__iter__
        self.addCleanup(setattr, read_ahead.ReadAhead, "__iter__", iterate)
        stages._watch_read_ahead(read_ahead.ReadAhead, self.timeline)
        metas = [{"path": self.photo + str(n)} for n in range(5)]
        with read_ahead.ReadAhead(metas, lambda meta: {}, lambda meta, begun, reserve: reserve(1) or meta["path"],
                                  threads=2, ahead=2, budget=10) as photos:
            seen = [result.result() for _meta, _begun, result in photos]
        self.assertEqual([meta["path"] for meta in metas], seen)
        for meta in metas:
            record = self.timeline.record(meta["path"])
            self.assertGreaterEqual(record["read wait"], record["begin"])
        self.assertEqual(5, len(self.timeline.calls["read-ahead begin"]))

    def test_the_waits_leave_the_warm_up_out_and_are_a_share_of_the_index(self):
        photos = [{"read_wait": 9.0, "begin": 1.0}, {"read_wait": 0.5, "begin": 0.1}, {"read_wait": 1.5, "begin": 0.1}]
        found = stages.waits(photos, 20.0)
        self.assertAlmostEqual(2.0, found["sum"])
        self.assertAlmostEqual(0.1, found["share"])
        self.assertEqual(2, found["read wait"]["n"])


class WhatACapLoses(unittest.TestCase):
    """Phase 1a: a capped run against the one without a cap, photo by photo by its number in the sample."""

    def photo(self, number, boxes, stored=(), stratum="10+"):
        return {"number": number, "stratum": stratum, "boxes": [list(b) for b in boxes], "stored": [list(s) for s in stored]}

    def test_a_face_is_one_face_at_half_overlap_each_paired_once(self):
        """The one pairing rule, from the package (tagpup.core.clustering): the measurement and the face pass both
        carry a face between two detections by it."""
        self.assertAlmostEqual(1.0, clustering.iou([0, 0, 10, 10], [0, 0, 10, 10]))
        self.assertEqual(0.0, clustering.iou([0, 0, 10, 10], [20, 20, 30, 30]))
        self.assertEqual({0: 1}, clustering.pairs([[0, 0, 10, 10]], [[40, 40, 50, 50], [1, 0, 11, 10]]))
        # Two boxes over one face: only one keeps it, the one overlapping it most.
        self.assertEqual({1: 0}, clustering.pairs([[1, 1, 10, 10], [0, 0, 10, 10]], [[0, 0, 10, 10]]))
        # Greedy, the largest overlap first: box 0 overlaps both, but box 1 takes the one it matches exactly.
        self.assertEqual({0: 1, 1: 0}, clustering.pairs([[0, 0, 10, 10], [2, 0, 12, 10]],
                                                        [[2, 0, 12, 10], [0, 0, 11, 10]]))
        # Under SAME_FACE (one third here) nothing is paired.
        self.assertEqual({}, clustering.pairs([[0, 0, 10, 10]], [[5, 0, 15, 10]]))
        # A tie is settled by the lower indexes, every run.
        self.assertEqual({0: 0}, clustering.pairs([[0, 0, 10, 10]], [[0, 0, 10, 10], [0, 0, 10, 10]]))
        self.assertEqual({0: 0}, clustering.pairs([[0, 0, 10, 10], [0, 0, 10, 10]], [[0, 0, 10, 10]]))

    def test_lost_faces_are_named_by_the_stored_face_they_match_and_gained_ones_are_counted(self):
        named, unnamed, excluded = [100, 100, 140, 150], [300, 300, 312, 316], [500, 500, 540, 550]
        off = {"photos": [self.photo(0, [named, unnamed, excluded],
                                     stored=[(named, True, False), (unnamed, False, False), (excluded, True, True)]),
                          self.photo(1, [[0, 0, 30, 30]], stratum="1-3")]}
        capped = {"photos": [self.photo(0, [excluded, [700, 700, 760, 770]]),
                             self.photo(1, [[1, 1, 30, 30]], stratum="1-3")]}
        table = stages.compared(off, capped)
        row = table["10+"]
        self.assertEqual((3, 1, 2), (row["found_off"], row["named_off"], row["found"]))
        # An excluded face is nobody: lost, it would count as unnamed.
        self.assertEqual((1, 1, 1), (row["lost_named"], row["lost_unnamed"], row["gained"]))
        self.assertEqual(50, row["lost_sides"]["max"])
        self.assertEqual((2, 1, 1), (table["all"]["photos"], table["all"]["lost_named"], table["all"]["gained"]))

    def test_a_run_without_its_faces_is_refused_not_compared_into_an_empty_table(self):
        """The run without a cap ran without faces (no boxes on any photo): compare says so."""
        with tempfile.TemporaryDirectory(prefix="pipeline_compare_") as folder:
            files, rest = [], {"table": {}, "cli": {"seconds": 1.0}, "card": {}}
            for name, run in (("off", dict(rest, photos=[{"number": 0, "stratum": "10+", "boxes": None}])),
                              ("cap", dict(rest, detect_max_side=2048, photos=[self.photo(0, [[0, 0, 30, 30]])]))):
                files.append(os.path.join(folder, name + ".json"))
                with open(files[-1], "w", encoding="utf-8") as handle:
                    json.dump(run, handle)
            with self.assertRaises(SystemExit) as refused:
                stages.compare(files, folder)
            self.assertIn("nothing to compare", str(refused.exception))
            self.assertEqual(["cap.json", "off.json"], sorted(os.listdir(folder)), "a comparison was written")


class TheNamedFaceHoldOut(unittest.TestCase):
    def test_a_face_is_never_matched_with_its_own_photo(self):
        # Person 1's two faces are in one photo; person 2 has faces in two photos; person 3 is near person 1's.
        matrix = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0.9, 0.1, 0]], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        people = np.array([1, 1, 2, 2, 3])
        photos = np.array([10, 10, 20, 21, 30])
        best, first, margin, reachable, chosen = holdout.matched_against(matrix, photos, matrix, people, photos,
                                                                         own=people, block=2)
        self.assertEqual([False, False, True, True, False], [bool(r) for r in reachable])   # 3 has one face
        self.assertEqual(3, best[0])            # its own photo's twin left out: the stranger is nearest
        self.assertEqual(2, best[2])
        # The face each was matched with, a row of the gallery, across blocks of two: person 2's faces each other's.
        self.assertEqual([4, 4, 3, 2, 0], chosen.tolist())
        years = np.array([2010, 2011, 2012, np.nan, 2019])
        self.assertEqual([9, 8], holdout.year_gap(years, years, chosen)[:2].tolist())
        self.assertTrue(np.isnan(holdout.year_gap(years, years, chosen)[2]))   # matched with an undated face
        self.assertAlmostEqual(1.0, first[2], places=5)
        self.assertGreater(margin[2], 0)   # clustering.margins: the margin the gates ask

    def test_a_face_held_out_by_folder_meets_none_of_its_events_faces(self):
        # A burst: person 1 twice in one folder, once in another; person 2 in a third folder.
        matrix = np.array([[1, 0], [1, 0], [0.8, 0.6], [0, 1]], dtype=np.float32)
        people = np.array([1, 1, 1, 2])
        by_photo = holdout.hold_out(matrix, people, np.array([1, 2, 3, 4]))
        by_folder = holdout.hold_out(matrix, people, np.array([10, 10, 11, 12]))
        self.assertEqual(3, by_photo["evaluated"])          # person 2 has one face only
        top = {point["cut"]: point for point in by_photo["curve"]["likeness"]}
        self.assertEqual(2, top[0.95]["suggested"])         # the burst twins reach 1.0; the third reaches 0.8
        tight = {point["cut"]: point for point in by_folder["curve"]["likeness"]}
        self.assertEqual(0, tight[0.95]["suggested"])       # across folders, 0.8 is the best there is

    def test_the_curve_counts_what_it_suggests_and_what_is_right(self):
        correct = np.array([True, True, False, True])
        likeness = np.array([0.95, 0.75, 0.72, 0.5])
        margin = np.array([0.3, 0.05, 0.01, 0.2])
        curve = holdout.precision_curve(correct, likeness, margin)
        at = {point["cut"]: point for point in curve["likeness"]}
        self.assertEqual((3, 2), (at[0.7]["suggested"], at[0.7]["right"]))
        self.assertEqual((1, 1), (at[0.8]["suggested"], at[0.8]["right"]))
        self.assertIsNone(at[0.95]["precision"] if at[0.95]["suggested"] == 0 else None)
        by_margin = {point["cut"]: point for point in curve["margin"]}
        self.assertEqual((2, 2), (by_margin[0.2]["suggested"], by_margin[0.2]["right"]))


class TheOtherChecks(unittest.TestCase):
    def test_cosines_and_their_floor(self):
        found = holdouts.cosine_summary(holdouts.cosines([[1, 0], [1, 1]], [[1, 0], [1, 0.9]]))
        self.assertEqual(2, found["n"])
        self.assertEqual(1, found["below"])
        self.assertAlmostEqual(1.0, found["max"])

    def test_turned_photos_are_counted_apart(self):
        kinds = [{"stratum": "none", "turned": True, "megapixels": 12.0},
                 {"stratum": "1-3", "turned": False, "megapixels": 3.0}]
        found = holdouts.turned_apart([0.81, 1.0], kinds)
        self.assertEqual((1, 1), (found["turned"]["n"], found["turned"]["below"]))
        self.assertEqual(0, found["not_turned"]["below"])
        self.assertEqual([0.81], [row["cosine"] for row in found["below"]])

    def test_suggest_never_sees_the_photo_it_scores(self):
        class Index:
            db_path = "x"

            def search(self, vector, k=15):
                return [(1.0, {"id": 5}), (0.9, {"id": 6}), (0.8, {"id": 7})][:k]
        leaving = holdouts.LeavingOut(Index())
        leaving.left_out = frozenset([5])
        self.assertEqual([6, 7], [record["id"] for _sim, record in leaving.search([0], k=2)])
        leaving.left_out = frozenset([5, 6])   # its folder: an event's photos are left out with it
        self.assertEqual([7], [record["id"] for _sim, record in leaving.search([0], k=2)])
        self.assertEqual([[0.9, 0.8], [0.8]], leaving.seen)
        self.assertEqual("x", leaving.db_path)

    def test_a_candidate_is_model_weights_and_size(self):
        self.assertEqual({"name": "ViT-B-16-SigLIP2", "pretrained": "webli", "size": None},
                         holdouts.candidate("ViT-B-16-SigLIP2:webli:"))
        self.assertEqual(224, holdouts.candidate("ViT-H-14:laion2b_s32b_b79k:224")["size"])
        with self.assertRaises(ValueError):
            holdouts.candidate("ViT-H-14:224")

    def test_a_pool_holds_the_held_out_photos_and_neighbours_up_to_its_size(self):
        rows = [{"id": n} for n in range(100)]
        held = [rows[7], rows[40]]
        pool = holdouts.pooled(rows, held, 10, seed=1)
        self.assertEqual(10, len(pool))
        self.assertEqual([7, 40], [row["id"] for row in pool[:2]])
        self.assertEqual(10, len({row["id"] for row in pool}))
        self.assertIs(rows, holdouts.pooled(rows, held, None, seed=1))
        self.assertEqual(100, len(holdouts.pooled(rows, held, 1000, seed=1)))

    def test_the_baseline_needs_suggests_own_cuts_and_a_model_with_higher_likenesses_needs_higher(self):
        def found(shift, floors, words):
            likeness = np.linspace(0.30, 0.90, 2001) + shift
            return {"suggest": {"by_folder": {
                        "neighbours": {"share_below_cut": float(np.mean(likeness < holdouts.NEIGHBOUR_CUT)),
                                       "percentiles": list(np.percentile(likeness, holdouts.PERCENTILES))},
                        "offered": {"precision": floors["0.60"]},
                        "floors": {cut: {"precision": precision} for cut, precision in floors.items()}}},
                    "words": {"cuts": {cut: {"pairs": pairs, "right": right} for cut, (pairs, right) in words.items()}}}
        baseline = found(0.0, {"0.55": 0.7, "0.60": 0.8, "0.65": 0.9},
                         {"0.12": (20, 9), "0.22": (10, 5), "0.23": (8, 6), "0.24": (4, 3)})
        itself = holdouts.cuts_needed(baseline, baseline)
        self.assertAlmostEqual(holdouts.NEIGHBOUR_CUT, itself["neighbour_cut"], places=3)
        self.assertEqual((0.60, 0.23), (itself["offered_floor"], itself["word_cut"]))
        higher = holdouts.cuts_needed(found(0.2, {"0.55": 0.6, "0.60": 0.7, "0.65": 0.8},
                                            {"0.12": (8, 6), "0.23": (2, 2)}), baseline)
        self.assertAlmostEqual(holdouts.NEIGHBOUR_CUT + 0.2, higher["neighbour_cut"], places=3)
        self.assertEqual((0.65, 0.12), (higher["offered_floor"], higher["word_cut"]))
        never = holdouts.cuts_needed(found(0.0, {"0.60": 0.1}, {"0.23": (0, 0)}), baseline)
        self.assertEqual((None, None), (never["offered_floor"], never["word_cut"]))

    def test_tag_scores(self):
        found = holdouts.tag_scores([{"Sport", "Sport/Track"}, set()], [{"Sport"}, {"Beach"}])
        self.assertEqual((2, 1, 2), (found["offered"], found["right"], found["truth"]))
        self.assertEqual(0.5, found["precision"])
        self.assertEqual(1, found["photos_with_a_right_tag"])

    def test_a_tag_under_a_branch_of_people_is_not_scored(self):
        self.assertTrue(holdouts._a_person("People/Rowan Thackeray", {"people"}))
        self.assertFalse(holdouts._a_person("Sport/Track", {"people"}))


class TheTableSaysWhatItPrints(unittest.TestCase):
    """docs/AI_PIPELINE.md section 7 states only numbers a printed table holds (#1825, #1826)."""

    def result(self, label, shift, word_pairs):
        likeness = np.linspace(0.40, 0.90, 2001) + shift   # none below Suggest's 0.35: the real run's shape
        scores = {"precision": 0.5, "recall": 0.4, "offered": 10, "right": 5, "truth": 12, "photos": 3,
                  "photos_with_a_right_tag": 2}
        suggest = {"offered": scores, "top_1": scores, "top_5": scores, "floors": {"0.60": scores},
                   "neighbours": {"share_below_cut": float(np.mean(likeness < holdouts.NEIGHBOUR_CUT)),
                                  "percentiles": list(np.percentile(likeness, holdouts.PERCENTILES))}}
        cuts = {"%.2f" % cut: {"pairs": word_pairs if cut <= 0.40 else 0, "right": 1 if cut <= 0.40 else 0}
                for cut in holdouts.WORD_CUTS}
        return {"candidate": label, "dimension": 8, "pool": 5, "held_out": 3, "card_mib": {"peak_over_before": 100},
                "timing": {"a_photo": {"mean": 0.2}, "decode_pad_resize": {"mean": 0.1}, "forward": {"mean": 0.1}},
                "pass": {"seconds": 1.0, "embedded": 10},
                "suggest": {"by_photo": suggest, "by_folder": suggest},
                "words": {"top_1": scores, "top_5": scores, "cuts": cuts}}

    def test_a_neighbour_cut_that_drops_nothing_says_so_and_the_word_pairs_and_folders_are_printed(self):
        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        baseline = holdouts.label_of(holdouts.candidate(holdouts.CANDIDATES[0]))
        found = {baseline: self.result(baseline, 0.0, 0), "other": self.result("other", 0.1, 2)}
        for label, each in found.items():
            with open(os.path.join(folder, "model-%s.json" % label.replace(" ", "_")), "w", encoding="utf-8") as handle:
                json.dump(each, handle)
        with open(os.path.join(folder, "models-sample.json"), "w", encoding="utf-8") as handle:
            json.dump({"photos_with_a_tag_not_a_person": 100, "their_folders": 7, "held_out": 3, "held_out_folders": 2},
                      handle)
        needed = holdouts.cuts_needed(found[baseline], found[baseline])
        self.assertTrue(needed["neighbour_drops_nothing"])
        self.assertAlmostEqual(0.40, needed["neighbour_cut"], places=3)
        lines = []
        holdouts.table(folder, lines.append)
        text = "\n".join(lines)
        self.assertIn("100 photos with a tag that is not a person, in 7 folders; 3 held out, in 2 folders", text)
        self.assertIn("0.400 (least; drops nothing)", text)
        self.assertIn("Word pairs at 0.40 or more", text)
        other = next(line for line in lines if line.startswith("| other | folder"))
        self.assertTrue(other.rstrip().endswith("| 2 (1) |"), other)


class ACopyThatAPassChangedIsNotTrustedAgain(unittest.TestCase):
    """A candidate's pass that made a row (a ghost) in the sandbox's copy stops the run and takes `copy-is-ready` away:
    the next run copies the library again instead of scoring a candidate with a tagless extra neighbour (#1827)."""

    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.ready = os.path.join(self.folder, "copy-is-ready")
        with open(self.ready, "w", encoding="utf-8"):
            pass
        self.models = [holdouts.candidate(text) for text in holdouts.CANDIDATES[:3]]

    def test_a_changed_copy_loses_its_mark_and_stops_the_run(self):
        ran = []

        def run(model):
            ran.append(holdouts.label_of(model))
            if len(ran) == 2:
                raise holdouts.CopyChanged("a vector made a row")
            return {"candidate": holdouts.label_of(model)}
        done = holdouts.run_each(self.models, self.folder, self.ready, run, lambda _line: None)
        self.assertFalse(os.path.exists(self.ready))
        self.assertEqual(2, len(ran))
        self.assertEqual(["written", "CopyChanged: a vector made a row"], list(done.values()))
        self.assertTrue(os.path.exists(os.path.join(self.folder, holdouts.slug(self.models[0]))))
        self.assertFalse(os.path.exists(os.path.join(self.folder, holdouts.slug(self.models[1]))))

    def test_a_model_that_does_not_load_leaves_the_copy_and_the_next_one_runs(self):
        def run(model):
            if model is self.models[0]:
                raise MemoryError("the card")
            return {}
        done = holdouts.run_each(self.models, self.folder, self.ready, run, lambda _line: None)
        self.assertTrue(os.path.exists(self.ready))
        self.assertEqual(["MemoryError: the card", "written", "written"], list(done.values()))


class TheWorkFolderIsDeletedOnlyIfThisProgramMadeIt(unittest.TestCase):
    """`--work` is deleted when every candidate is done: a run given the results folder, the library's folder or the
    TagPup home would delete what it was measuring into, what it was measuring, or the owner's home."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base, True)
        self.out = os.path.join(self.base, "results")
        self.library_folder = os.path.join(self.base, "libraries")
        self.home = os.path.join(self.base, "home")
        for folder in (self.out, self.library_folder, self.home):
            os.makedirs(folder)
        self.source = os.path.join(self.library_folder, "photo_index.db")
        for path in (os.path.join(self.out, "model-x.json"), self.source, os.path.join(self.home, "machine_roots.json")):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("kept")

    def run_models(self, work):
        args = Namespace(work=work, candidates=list(holdouts.CANDIDATES), packages=None, sample=1, timing=1, seed=1,
                         pool=None, keep_work=False)
        with mock.patch.dict(os.environ, {"TAGPUP_HOME": self.home}):
            holdouts.models_main(args, self.source, None, "photo_index", self.out)

    def test_a_work_folder_that_is_or_holds_what_the_run_keeps_is_refused_and_nothing_is_deleted(self):
        for work in (self.out, self.library_folder, self.home, self.base,
                     os.path.join(self.out, "inside"), os.path.join(self.home, "data")):
            with self.subTest(work=work), self.assertRaises(SystemExit):
                self.run_models(work)
        for path in (os.path.join(self.out, "model-x.json"), self.source, os.path.join(self.home, "machine_roots.json")):
            self.assertTrue(os.path.exists(path), path)
        self.assertEqual(["model-x.json"], os.listdir(self.out))

    def test_the_repository_and_a_folder_that_holds_it_are_refused(self):
        for work in (ROOT, os.path.dirname(ROOT), os.path.join(ROOT, "tests")):
            with self.subTest(work=work), self.assertRaises(SystemExit):
                self.run_models(work)

    def test_a_folder_with_files_this_program_did_not_put_there_is_refused_and_kept(self):
        work = os.path.join(self.base, "somebody's")
        os.makedirs(work)
        with open(os.path.join(work, "wedding.jpg"), "w", encoding="utf-8") as handle:
            handle.write("kept")
        with self.assertRaises(SystemExit):
            self.run_models(work)
        self.assertTrue(os.path.exists(os.path.join(work, "wedding.jpg")))
        self.assertIn("kept", holdouts.remove_work(work))
        self.assertTrue(os.path.exists(os.path.join(work, "wedding.jpg")))

    def test_a_folder_it_made_is_taken_up_again_and_deleted_at_the_end(self):
        work = os.path.join(self.base, "copy")
        for _round in range(2):   # a stopped run is taken up where it stopped
            holdouts.claim_work_folder(work, self.source, self.out)
        self.assertTrue(os.path.exists(os.path.join(work, holdouts.WORK_MARKER)))
        self.assertIn("deleted: True", holdouts.remove_work(work))
        self.assertFalse(os.path.exists(work))
        empty = os.path.join(self.base, "empty")
        os.makedirs(empty)
        holdouts.claim_work_folder(empty, self.source, self.out)   # empty: taken and marked
        self.assertTrue(os.path.exists(os.path.join(empty, holdouts.WORK_MARKER)))


if __name__ == "__main__":
    unittest.main()
