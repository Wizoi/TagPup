"""The phase 0 measurements of the AI pipeline (scripts/measure_pipeline_stages.py, measure_pipeline_holdouts.py,
the helpers they share in scripts/sandbox.py; docs/AI_PIPELINE.md): what they draw and how they count, on made-up
numbers. Nothing here loads a model, opens a library or starts a process: the runs themselves are the owner's, in a
window he names.

The sample a person relies on: a third or more at 10 faces or more, the worst photo in it, each stratum spread from
its smallest picture to its largest, a sideways photo when there is one. The hold-out by photo: a face is never
matched with its own photo's faces, and a person with no face elsewhere is counted apart, not as a miss.
"""
import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import numpy as np  # noqa: E402

import measure_pipeline_holdouts as holdouts  # noqa: E402
import measure_pipeline_stages as stages  # noqa: E402
import sandbox  # noqa: E402


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
        record = {"t0": 0.0, "decoded": 12.1, "clip_load": 12.0, "clip_in": 12.2, "clip_end": 12.5,
                  "identity write": 0.05, "f0": 12.6, "detect_in": 12.7, "detect_out": 13.2, "encode": 0.04,
                  "resnet_in": 13.3, "f_end": 13.35}
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
        found = stages.stages_of({"t0": 0, "decoded": 0.1, "clip_in": 0.2, "clip_end": 0.3, "f0": 0.3,
                                  "detect_in": 0.4, "detect_out": 0.6, "f_end": 0.6})
        self.assertNotIn("recogniser forward", found)
        self.assertAlmostEqual(0.0, found["crop and tensor"])

    def test_the_table_leaves_the_warm_up_out(self):
        photo = {"stratum": "1-3", "faces_found": 2, "megapixels": 12.0, "total": 1.0, "stages": {"MTCNN": 0.4}}
        warm_up = dict(photo, total=20.0, stages={"MTCNN": 9.0})
        table = stages.stage_table([warm_up, photo, dict(photo, stratum="10+")])
        self.assertEqual(2, table["all"]["photos"])
        self.assertEqual(1.0, table["all"]["total"]["max"])
        self.assertEqual(0.4, table["1-3"]["MTCNN"]["mean"])


class TheNamedFaceHoldOut(unittest.TestCase):
    def test_a_face_is_never_matched_with_its_own_photo(self):
        # Person 1's two faces are in one photo; person 2 has faces in two photos; person 3 is near person 1's.
        matrix = np.array([[1, 0, 0], [1, 0, 0], [0, 1, 0], [0, 1, 0], [0.9, 0.1, 0]], dtype=np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        people = np.array([1, 1, 2, 2, 3])
        photos = np.array([10, 10, 20, 21, 30])
        best, first, second, reachable = holdouts.top_two(matrix, people, photos, block=2)
        self.assertEqual([False, False, True, True, False], [bool(r) for r in reachable])   # 3 has one face
        self.assertEqual(3, best[0])            # its own photo's twin left out: the stranger is nearest
        self.assertEqual(2, best[2])
        self.assertAlmostEqual(1.0, first[2], places=5)
        self.assertLess(second[2], first[2])

    def test_the_curve_counts_what_it_suggests_and_what_is_right(self):
        correct = np.array([True, True, False, True])
        likeness = np.array([0.95, 0.75, 0.72, 0.5])
        margin = np.array([0.3, 0.05, 0.01, 0.2])
        curve = holdouts.precision_curve(correct, likeness, margin)
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

    def test_suggest_never_sees_the_photo_it_scores(self):
        class Index:
            db_path = "x"

            def search(self, vector, k=15):
                return [(1.0, {"id": 5}), (0.9, {"id": 6}), (0.8, {"id": 7})][:k]
        leaving = holdouts.LeavingOut(Index())
        leaving.left_out = 5
        self.assertEqual([6, 7], [record["id"] for _sim, record in leaving.search([0], k=2)])
        self.assertEqual("x", leaving.db_path)

    def test_tag_scores(self):
        found = holdouts.tag_scores([{"Sport", "Sport/Track"}, set()], [{"Sport"}, {"Beach"}])
        self.assertEqual((2, 1, 2), (found["offered"], found["right"], found["truth"]))
        self.assertEqual(0.5, found["precision"])
        self.assertEqual(1, found["photos_with_a_right_tag"])

    def test_a_tag_under_a_branch_of_people_is_not_scored(self):
        self.assertTrue(holdouts._a_person("People/Rowan Thackeray", {"people"}))
        self.assertFalse(holdouts._a_person("Sport/Track", {"people"}))


if __name__ == "__main__":
    unittest.main()
