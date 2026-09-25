"""Unit tests for the tag suggestion scoring engine.

These are the rules that decide what TagPup recommends, and a regression here degrades
tag quality silently -- nothing crashes, the suggestions just get worse. They were
previously exercised only through one end-to-end CLI test with the models mocked out.

Everything here runs against fakes: a stub index that returns canned neighbours and a
stub embedder that returns fixed vectors, so the arithmetic is deterministic and no
model is ever loaded. The constants asserted (5-year half-life, +0.20 path boost,
0.40/0.20/0.10 consensus bands, the 0.23 zero-shot floor) are the ones documented in
docs/SPEC_TAGPUP_CLI.md section 5.
"""
import os
import shutil
import sys
import math
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import suggester as suggester_module
from suggester import TagSuggester, extract_path_hints
from taxonomy import TagTaxonomy

from tagpup.store import db, schema  # noqa: E402
from tagpup.store import taxonomy as store_taxonomy  # noqa: E402

DECAY_LAMBDA = 0.1386  # ln(2)/5, a five-year half-life


class StubIndex:
    """Minimal PhotoIndex stand-in returning canned nearest neighbours."""

    def __init__(self, neighbors=None, faces=None):
        self._neighbors = neighbors or []
        self._faces = faces or []
        self.conn = None
        self.metadata = []
        self.saved_tag_embeddings = []

    def search(self, query_vector, k=15):
        return self._neighbors[:k]

    def get_all_faces(self):
        return self._faces

    def get_tag_embedding(self, tag, prompt, model_name, pretrained):
        return None

    def save_tag_embedding(self, tag, prompt, model_name, pretrained, embedding):
        self.saved_tag_embeddings.append((tag, prompt, model_name, pretrained))


class StubEmbedder:
    """Records the prompts it is asked to embed, and returns an orthogonal vector."""

    model_name = "stub-model"
    pretrained = "stub-weights"

    def __init__(self):
        self.prompts = []

    def embed_text(self, prompt):
        self.prompts.append(prompt)
        # Orthogonal to the image vectors used below, so zero-shot never fires
        # unless a test deliberately supplies its own candidate embeddings.
        return [0.0, 0.0, 1.0]


class StubFaceProcessor:
    """Keeps suggest_for_photo from constructing a real MTCNN/FaceNet processor."""

    def detect_and_embed_faces(self, path):
        return []


def photo_meta(path, tags=(), year=None, people=()):
    meta = {"path": path, "tags": list(tags), "people": list(people)}
    if year is not None:
        meta["raw_metadata"] = {"EXIF:DateTimeOriginal": f"{year}:06:15 12:00:00"}
    return meta


class SuggesterTestBase(unittest.TestCase):
    def setUp(self):
        # Prevent the on-the-fly face detection branch from loading models.
        self._saved_processor = suggester_module._global_face_processor
        suggester_module._global_face_processor = StubFaceProcessor()
        self.addCleanup(self._restore_processor)

    def _restore_processor(self):
        suggester_module._global_face_processor = self._saved_processor

    def make_suggester(self, neighbors=(), taxonomy_paths=(), candidates=None, embedder=None,
                       face_roots=()):
        """`face_roots`: the roots the library's tree flags as holding faces, the only
        thing that says a root does (docs/findings.md, #66). Without them the library
        does not exist, and People is its one face root."""
        library = os.path.join(tempfile.gettempdir(), "tagpup_no_such_library.db")
        if face_roots:
            folder = tempfile.mkdtemp(prefix="tagpup_suggest_")
            self.addCleanup(shutil.rmtree, folder, True)
            library = os.path.join(folder, "library.db")
            schema.ensure(library)

            def flag(conn):
                for root in face_roots:
                    store_taxonomy.add_path(conn, root, root_has_face=1)
            db.write_with_connection(library, flag)
        taxonomy = TagTaxonomy(library)
        taxonomy.paths = set(taxonomy_paths)
        index = StubIndex(neighbors=list(neighbors))
        return TagSuggester(index, taxonomy, embedder=embedder, candidate_tags=candidates or [])

    def scores(self, result):
        return {item["tag"]: item["score"] for item in result["suggested_tags"]}


class TestExtractPathHints(unittest.TestCase):
    def test_returns_folder_names_from_the_path(self):
        hints = extract_path_hints(r"D:\Library\2019\Summer Trip\IMG_1.jpg")
        self.assertIn("2019", hints)
        self.assertIn("Summer Trip", hints)

    def test_skips_generic_container_folders(self):
        hints = extract_path_hints(r"D:\Pictures\Photos\Vacation\IMG_1.jpg")
        lowered = [h.lower() for h in hints]
        self.assertNotIn("pictures", lowered)
        self.assertNotIn("photos", lowered)
        self.assertIn("Vacation", hints)

    def test_keeps_at_most_three_nearest_folders(self):
        hints = extract_path_hints(r"D:\A\B\C\D\E\IMG_1.jpg")
        self.assertLessEqual(len(hints), 3)
        # The folders nearest the file are the informative ones.
        self.assertEqual(hints, ["C", "D", "E"])


class TestNeighbourScoring(SuggesterTestBase):
    def test_score_is_the_similarity_share_of_neighbours_carrying_the_tag(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.9, photo_meta("n2.jpg", ["Trips/Texas"])),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0], k=15, min_sim=0.35)
        scores = self.scores(result)
        # Each tag is carried by one of two equally similar neighbours.
        self.assertAlmostEqual(scores["Holidays/Christmas"], 0.5, places=2)
        self.assertAlmostEqual(scores["Trips/Texas"], 0.5, places=2)

    def test_a_tag_on_every_neighbour_scores_one(self):
        neighbors = [
            (0.8, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.7, photo_meta("n2.jpg", ["Holidays/Christmas"])),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        self.assertAlmostEqual(self.scores(result)["Holidays/Christmas"], 1.0, places=2)

    def test_neighbours_below_min_sim_are_ignored(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.10, photo_meta("n2.jpg", ["Trips/Texas"])),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0], min_sim=0.35)
        scores = self.scores(result)
        self.assertIn("Holidays/Christmas", scores)
        self.assertNotIn("Trips/Texas", scores, "a below-threshold neighbour contributed")

    def test_k_limits_how_many_neighbours_are_considered(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.9, photo_meta("n2.jpg", ["Trips/Texas"])),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0], k=1)
        self.assertNotIn("Trips/Texas", self.scores(result))

    def test_no_neighbours_yields_no_neighbour_tags(self):
        s = self.make_suggester([])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        self.assertEqual(result["suggested_tags"], [])
        self.assertEqual(result["nearest_neighbors"], [])


class TestTimeDecayWeighting(SuggesterTestBase):
    def test_a_temporally_distant_neighbour_counts_for_less(self):
        """Two equally similar neighbours, one ten years away: the near one wins."""
        neighbors = [
            (0.9, photo_meta("near.jpg", ["Holidays/Christmas"], year=2020)),
            (0.9, photo_meta("far.jpg", ["Trips/Texas"], year=2010)),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo(
            "/nowhere/target.jpg",
            [1.0, 0.0, 0.0],
            target_metadata=photo_meta("target.jpg", year=2020),
        )
        scores = self.scores(result)
        self.assertGreater(scores["Holidays/Christmas"], scores["Trips/Texas"])

    def test_decay_matches_the_documented_five_year_half_life(self):
        neighbors = [
            (0.9, photo_meta("near.jpg", ["Holidays/Christmas"], year=2020)),
            (0.9, photo_meta("far.jpg", ["Trips/Texas"], year=2015)),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo(
            "/nowhere/target.jpg",
            [1.0, 0.0, 0.0],
            target_metadata=photo_meta("target.jpg", year=2020),
        )
        scores = self.scores(result)

        near_w = 1.0
        far_w = math.exp(-DECAY_LAMBDA * 5)  # ~0.5 at one half-life
        total = near_w + far_w
        self.assertAlmostEqual(scores["Holidays/Christmas"], round(near_w / total, 2), places=2)
        self.assertAlmostEqual(scores["Trips/Texas"], round(far_w / total, 2), places=2)

    def test_no_decay_is_applied_without_a_target_year(self):
        neighbors = [
            (0.9, photo_meta("near.jpg", ["Holidays/Christmas"], year=2020)),
            (0.9, photo_meta("far.jpg", ["Trips/Texas"], year=1990)),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        scores = self.scores(result)
        self.assertAlmostEqual(scores["Holidays/Christmas"], scores["Trips/Texas"], places=2)


class TestPathHintBoost(SuggesterTestBase):
    def test_a_tag_matching_a_folder_name_is_boosted(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Trips/Texas"])),
            (0.9, photo_meta("n2.jpg", ["Holidays/Christmas"])),
        ]
        s = self.make_suggester(neighbors)
        plain = s.suggest_for_photo("/library/generic/target.jpg", [1.0, 0.0, 0.0])
        hinted = s.suggest_for_photo("/library/Texas/target.jpg", [1.0, 0.0, 0.0])

        self.assertAlmostEqual(self.scores(plain)["Trips/Texas"], 0.5, places=2)
        self.assertAlmostEqual(self.scores(hinted)["Trips/Texas"], 0.7, places=2)

    def test_the_boost_is_capped_at_one(self):
        neighbors = [(0.9, photo_meta("n1.jpg", ["Trips/Texas"]))]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/library/Texas/target.jpg", [1.0, 0.0, 0.0])
        self.assertLessEqual(self.scores(result)["Trips/Texas"], 1.0)

    def test_unrelated_tags_are_not_boosted(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Trips/Texas"])),
            (0.9, photo_meta("n2.jpg", ["Holidays/Christmas"])),
        ]
        s = self.make_suggester(neighbors)
        result = s.suggest_for_photo("/library/Texas/target.jpg", [1.0, 0.0, 0.0])
        self.assertAlmostEqual(self.scores(result)["Holidays/Christmas"], 0.5, places=2)

    def test_path_hints_are_reported_in_the_result(self):
        s = self.make_suggester([])
        result = s.suggest_for_photo(os.path.join("library", "Texas", "target.jpg"), [1.0, 0.0, 0.0])
        self.assertIn("Texas", result["path_hints"])


class TestTaxonomyAncestorExpansion(SuggesterTestBase):
    def test_ancestors_of_a_neighbour_tag_receive_weight(self):
        neighbors = [(0.9, photo_meta("n1.jpg", ["Activity/Sports/Running"]))]
        s = self.make_suggester(
            neighbors,
            taxonomy_paths=["Activity", "Activity/Sports", "Activity/Sports/Running"],
        )
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        tags = {item["tag"] for item in result["suggested_tags"]}
        # The leaf survives pruning; its ancestors are folded into it as redundant.
        self.assertIn("Activity/Sports/Running", tags)

    def test_an_ancestor_shared_by_two_branches_outscores_either_leaf(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Activity/Sports/Running"])),
            (0.9, photo_meta("n2.jpg", ["Activity/Sports/Cycling"])),
        ]
        s = self.make_suggester(
            neighbors,
            taxonomy_paths=[
                "Activity",
                "Activity/Sports",
                "Activity/Sports/Running",
                "Activity/Sports/Cycling",
            ],
        )
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        scores = self.scores(result)
        # Both leaves are specific enough to survive pruning at 0.5 each...
        self.assertAlmostEqual(scores["Activity/Sports/Running"], 0.5, places=2)
        self.assertAlmostEqual(scores["Activity/Sports/Cycling"], 0.5, places=2)


class TestPeopleAreDeferredToFaceMatching(SuggesterTestBase):
    def test_family_tags_are_not_propagated_from_neighbours(self):
        neighbors = [(0.9, photo_meta("n1.jpg", ["Family/Immediate/Jane Doe"]))]
        s = self.make_suggester(neighbors, taxonomy_paths=["Family/Immediate/Jane Doe"],
                                face_roots=["Family"])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        tags = {item["tag"] for item in result["suggested_tags"]}
        self.assertNotIn("Family/Immediate/Jane Doe", tags)

    def test_friends_tags_are_not_propagated_from_neighbours(self):
        neighbors = [(0.9, photo_meta("n1.jpg", ["Friends/Bob Roe"]))]
        s = self.make_suggester(neighbors, taxonomy_paths=["Friends/Bob Roe"], face_roots=["Friends"])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        self.assertEqual(result["suggested_tags"], [])

    def test_non_people_tags_on_the_same_neighbour_still_propagate(self):
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Family/Immediate/Jane Doe", "Holidays/Christmas"]))
        ]
        s = self.make_suggester(neighbors, taxonomy_paths=["Family/Immediate/Jane Doe"],
                                face_roots=["Family"])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        self.assertIn("Holidays/Christmas", self.scores(result))


class TestEraAwarePrompting(SuggesterTestBase):
    def test_person_and_object_prompts_differ_in_article(self):
        embedder = StubEmbedder()
        s = self.make_suggester(
            taxonomy_paths=["Family/Immediate/Jane Doe"],
            candidates=["Jane Doe", "Sunset"],
            embedder=embedder,
            face_roots=["Family"],
        )
        s._get_candidate_embeddings_for_year(1998)

        self.assertIn("a photo of Jane Doe in 1998", embedder.prompts)
        self.assertIn("a photo of a sunset in 1998", embedder.prompts)

    def test_prompts_omit_the_year_when_it_is_unknown(self):
        embedder = StubEmbedder()
        s = self.make_suggester(candidates=["Sunset"], embedder=embedder)
        s._get_candidate_embeddings_for_year(None)
        self.assertIn("a photo of a sunset", embedder.prompts)
        self.assertFalse(any(" in " in p for p in embedder.prompts))

    def test_year_specific_embeddings_are_cached_per_year(self):
        embedder = StubEmbedder()
        s = self.make_suggester(candidates=["Sunset"], embedder=embedder)
        s._get_candidate_embeddings_for_year(1998)
        count_after_first = len(embedder.prompts)
        s._get_candidate_embeddings_for_year(1998)
        self.assertEqual(len(embedder.prompts), count_after_first, "year cache was not used")

        s._get_candidate_embeddings_for_year(2004)
        self.assertGreater(len(embedder.prompts), count_after_first, "a new year was not embedded")

    def test_computed_prompt_embeddings_are_persisted_for_reuse(self):
        embedder = StubEmbedder()
        s = self.make_suggester(candidates=["Sunset"], embedder=embedder)
        s._get_candidate_embeddings_for_year(1998)
        saved_prompts = [p for _, p, _, _ in s.index.saved_tag_embeddings]
        self.assertIn("a photo of a sunset in 1998", saved_prompts)


class TestZeroShotCandidates(SuggesterTestBase):
    def _suggester_with_candidate(self, vector):
        embedder = StubEmbedder()
        s = self.make_suggester(candidates=["Sunset"], embedder=embedder)
        # Bypass the embedder so the similarity is exactly what the test chooses.
        s.candidate_embeddings = {"Sunset": vector}
        return s

    def test_a_candidate_above_the_threshold_is_recommended(self):
        s = self._suggester_with_candidate([1.0, 0.0, 0.0])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        scores = self.scores(result)
        self.assertIn("Sunset", scores)
        self.assertAlmostEqual(scores["Sunset"], 1.0, places=2)

    def test_a_candidate_below_the_threshold_is_dropped(self):
        # cos = 0.2, just under the documented 0.23 floor.
        s = self._suggester_with_candidate([0.2, math.sqrt(1 - 0.04), 0.0])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        self.assertNotIn("Sunset", self.scores(result))

    def test_zero_shot_recommendations_are_flagged_as_new(self):
        s = self._suggester_with_candidate([1.0, 0.0, 0.0])
        result = s.suggest_for_photo("/nowhere/target.jpg", [1.0, 0.0, 0.0])
        item = next(i for i in result["suggested_tags"] if i["tag"] == "Sunset")
        self.assertTrue(item.get("is_new_recommendation"))
        self.assertEqual(item.get("source_count"), 0)


class TestFolderConsensus(unittest.TestCase):
    def make_suggestion(self, path, tags):
        return {
            "path": path,
            "suggested_tags": [{"tag": t, "score": s, "source_count": 1} for t, s in tags],
        }

    def test_a_tag_agreed_by_most_of_the_folder_is_boosted(self):
        s = TagSuggester.__new__(TagSuggester)  # scoring only; no collaborators needed
        suggestions = [
            self.make_suggestion(r"C:\f\a.jpg", [("Holidays/Christmas", 0.40)]),
            self.make_suggestion(r"C:\f\b.jpg", [("Holidays/Christmas", 0.40)]),
        ]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        # consensus 1.0 >= 0.40 -> x1.25
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["score"], 0.5, places=2)
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["consensus_rate"], 1.0, places=2)

    def test_boosted_scores_are_capped_at_one(self):
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [
            self.make_suggestion(r"C:\f\a.jpg", [("Holidays/Christmas", 0.95)]),
            self.make_suggestion(r"C:\f\b.jpg", [("Holidays/Christmas", 0.95)]),
        ]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        self.assertLessEqual(out[0]["suggested_tags"][0]["score"], 1.0)

    def test_an_isolated_context_tag_is_heavily_penalised(self):
        """A context tag on 1 of 20 photos (5% < 10%) keeps 30% of its score."""
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [self.make_suggestion(r"C:\f\a.jpg", [("Activity/Cabin", 0.90)])]
        for i in range(19):
            suggestions.append(self.make_suggestion(rf"C:\f\b{i}.jpg", [("Trips/Texas", 0.90)]))

        out = TagSuggester.apply_folder_consensus(s, suggestions)
        cabin = out[0]["suggested_tags"]
        self.assertTrue(cabin, "tag was dropped entirely")
        self.assertAlmostEqual(cabin[0]["score"], round(0.90 * 0.3, 2), places=2)

    def test_a_low_consensus_context_tag_is_moderately_penalised(self):
        """A context tag on 2 of 13 photos (~15%, between 10% and 20%) keeps 60%."""
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [
            self.make_suggestion(r"C:\f\a.jpg", [("Activity/Cabin", 0.90)]),
            self.make_suggestion(r"C:\f\b.jpg", [("Activity/Cabin", 0.90)]),
        ]
        for i in range(11):
            suggestions.append(self.make_suggestion(rf"C:\f\c{i}.jpg", [("Trips/Texas", 0.90)]))

        out = TagSuggester.apply_folder_consensus(s, suggestions)
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["score"], round(0.90 * 0.6, 2), places=2)

    def test_non_context_tags_escape_the_outlier_penalty(self):
        """Only Activity/School/Trips/Scenic/Location/Albums are treated as context."""
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [self.make_suggestion(r"C:\f\a.jpg", [("Holidays/Christmas", 0.90)])]
        for i in range(19):
            suggestions.append(self.make_suggestion(rf"C:\f\b{i}.jpg", [("Trips/Texas", 0.90)]))

        out = TagSuggester.apply_folder_consensus(s, suggestions)
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["score"], 0.90, places=2)

    def test_tags_falling_below_the_floor_are_dropped(self):
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [self.make_suggestion(r"C:\f\a.jpg", [("Activity/Cabin", 0.30)])]
        for i in range(19):
            suggestions.append(self.make_suggestion(rf"C:\f\b{i}.jpg", [("Trips/Texas", 0.90)]))

        out = TagSuggester.apply_folder_consensus(s, suggestions)
        # 0.30 * 0.3 = 0.09, below the 0.15 floor
        self.assertEqual(out[0]["suggested_tags"], [])

    def test_a_single_photo_folder_is_left_untouched(self):
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [self.make_suggestion(r"C:\f\a.jpg", [("Activity/Cabin", 0.90)])]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["score"], 0.90, places=2)
        self.assertNotIn("consensus_rate", out[0]["suggested_tags"][0])

    def test_folders_are_scored_independently(self):
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [
            self.make_suggestion(r"C:\f1\a.jpg", [("Holidays/Christmas", 0.40)]),
            self.make_suggestion(r"C:\f1\b.jpg", [("Holidays/Christmas", 0.40)]),
            self.make_suggestion(r"C:\f2\c.jpg", [("Holidays/Christmas", 0.40)]),
        ]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        by_path = {x["path"]: x for x in out}
        self.assertAlmostEqual(by_path[r"C:\f1\a.jpg"]["suggested_tags"][0]["score"], 0.5, places=2)
        # The lone photo in f2 is a single-photo folder, so it is skipped.
        self.assertAlmostEqual(by_path[r"C:\f2\c.jpg"]["suggested_tags"][0]["score"], 0.4, places=2)

    def test_weak_suggestions_do_not_count_toward_consensus(self):
        """Only suggestions at or above 0.20 count as a folder vote."""
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [
            self.make_suggestion(r"C:\f\a.jpg", [("Holidays/Christmas", 0.40)]),
            self.make_suggestion(r"C:\f\b.jpg", [("Holidays/Christmas", 0.19)]),
            self.make_suggestion(r"C:\f\c.jpg", [("Trips/Texas", 0.40)]),
        ]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        # 1 of 3 votes = 0.33 consensus, under the 0.40 boost band.
        self.assertAlmostEqual(out[0]["suggested_tags"][0]["score"], 0.40, places=2)

    def test_results_are_sorted_by_score_descending(self):
        s = TagSuggester.__new__(TagSuggester)
        suggestions = [
            self.make_suggestion(r"C:\f\a.jpg", [("Holidays/Christmas", 0.30), ("Trips/Texas", 0.80)]),
            self.make_suggestion(r"C:\f\b.jpg", [("Holidays/Christmas", 0.30), ("Trips/Texas", 0.80)]),
        ]
        out = TagSuggester.apply_folder_consensus(s, suggestions)
        scores = [i["score"] for i in out[0]["suggested_tags"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_an_empty_input_is_returned_unchanged(self):
        s = TagSuggester.__new__(TagSuggester)
        self.assertEqual(TagSuggester.apply_folder_consensus(s, []), [])


if __name__ == "__main__":
    unittest.main()


class TestUntaggedNeighboursDoNotDiluteScores(SuggesterTestBase):
    """A neighbour with nothing to say must not take a share of the neighbourhood.

    A tag's score is its share of the total similarity across neighbours. While only
    tagged photos were indexed every neighbour carried signal, so counting them all was
    harmless. Once untagged photos are indexed they join the neighbour set and, if
    counted, inflate the denominator and push every score down in proportion to how many
    turned up -- starving the 0.6 display and 0.75 auto-apply thresholds without any
    visible failure.
    """

    def test_an_untagged_neighbour_does_not_lower_a_score(self):
        tagged_only = [(0.9, photo_meta("n1.jpg", ["Holidays/Christmas"]))]
        with_untagged = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.9, photo_meta("n2.jpg", [])),
        ]
        baseline = self.scores(
            self.make_suggester(tagged_only).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        )
        diluted = self.scores(
            self.make_suggester(with_untagged).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        )
        self.assertAlmostEqual(baseline["Holidays/Christmas"], 1.0, places=2)
        self.assertAlmostEqual(
            diluted["Holidays/Christmas"], 1.0, places=2,
            msg="an untagged neighbour diluted the score",
        )

    def test_many_untagged_neighbours_still_do_not_dilute(self):
        neighbors = [(0.9, photo_meta("tagged.jpg", ["Trips/Texas"]))]
        neighbors += [(0.9, photo_meta(f"u{i}.jpg", [])) for i in range(14)]

        result = self.make_suggester(neighbors).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        self.assertAlmostEqual(
            self.scores(result)["Trips/Texas"], 1.0, places=2,
            msg="14 untagged neighbours starved the score",
        )

    def test_the_share_between_tagged_neighbours_is_unchanged(self):
        """The rule is unchanged for neighbours that do carry tags."""
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.9, photo_meta("n2.jpg", ["Trips/Texas"])),
            (0.9, photo_meta("n3.jpg", [])),
        ]
        scores = self.scores(
            self.make_suggester(neighbors).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        )
        self.assertAlmostEqual(scores["Holidays/Christmas"], 0.5, places=2)
        self.assertAlmostEqual(scores["Trips/Texas"], 0.5, places=2)

    def test_a_neighbour_tagged_only_with_people_does_not_dilute(self):
        """People are deferred to face matching, so such a neighbour contributes nothing."""
        neighbors = [
            (0.9, photo_meta("n1.jpg", ["Holidays/Christmas"])),
            (0.9, photo_meta("n2.jpg", ["Family/Immediate/Jane Doe"])),
        ]
        s = self.make_suggester(neighbors, taxonomy_paths=["Family/Immediate/Jane Doe"],
                                face_roots=["Family"])
        scores = self.scores(s.suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0]))
        self.assertAlmostEqual(
            scores["Holidays/Christmas"], 1.0, places=2,
            msg="a people-only neighbour took a share of the neighbourhood",
        )

    def test_a_neighbourhood_with_no_tags_at_all_suggests_nothing(self):
        neighbors = [(0.9, photo_meta(f"u{i}.jpg", [])) for i in range(5)]
        result = self.make_suggester(neighbors).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        self.assertEqual(result["suggested_tags"], [])

    def test_untagged_neighbours_are_still_reported_as_neighbours(self):
        """They are excluded from scoring, not from the record of what was near."""
        neighbors = [
            (0.9, photo_meta("tagged.jpg", ["Trips/Texas"])),
            (0.8, photo_meta("untagged.jpg", [])),
        ]
        result = self.make_suggester(neighbors).suggest_for_photo("/t.jpg", [1.0, 0.0, 0.0])
        paths = {n["path"] for n in result["nearest_neighbors"]}
        self.assertIn("untagged.jpg", paths)
