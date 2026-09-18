"""Tests for the face identity resolution rules in faces.py.

`test_stability.py` already covers strict tag enforcement, the similarity threshold
and era-aware centroids. This file covers the rest of the resolution pipeline: how
anchors are found, how clusters vote, how conflicts inside a single photo are broken,
which faces are dismissed as background noise, and how remaining faces are matched to
leftover name tags.

Embeddings are synthetic unit vectors arranged so DBSCAN's grouping is unambiguous:
faces of one identity sit within the 0.48 euclidean epsilon of each other, and
different identities sit far outside it. That keeps every assertion about the
*decision rules* rather than about clustering luck.

No model is loaded -- FaceProcessor only initialises MTCNN and FaceNet inside
detect_and_embed_faces, which these tests never call.
"""
import os
import sys
import json
import shutil
import sqlite3
import tempfile
import unittest

import numpy as np

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from index import PhotoIndex
from taxonomy import TagTaxonomy
from faces import FaceProcessor

FACE_DIM = 512
DBSCAN_EPS = 0.48


def identity_vector(seed):
    """A well-separated identity centre."""
    rng = np.random.default_rng(seed * 7919)
    vec = rng.standard_normal(FACE_DIM).astype(np.float32)
    return (vec / np.linalg.norm(vec)).astype(np.float32)


def near(vec, jitter_seed, distance=0.05):
    """A vector within DBSCAN's epsilon of `vec`, so the two cluster together."""
    rng = np.random.default_rng(jitter_seed * 104729)
    noise = rng.standard_normal(FACE_DIM).astype(np.float32)
    noise -= np.dot(noise, vec) * vec           # keep the perturbation orthogonal
    noise /= np.linalg.norm(noise)
    out = vec + distance * noise
    return (out / np.linalg.norm(out)).astype(np.float32)


class FaceClusteringTestBase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tagpup_faces_")
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = os.path.join(self.tmpdir, "faces_test.db")

        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        self.addCleanup(self._close_index)

        self.taxonomy = TagTaxonomy(file_path=os.path.join(self.tmpdir, "tax.json"))
        self.taxonomy.paths = set()

    def _close_index(self):
        try:
            self.index.close()
        except Exception:
            pass

    # ---------- fixture helpers ----------

    def add_photo(self, name, people=(), year=None):
        path = os.path.join(self.tmpdir, name).replace("\\", "/")
        raw_meta = {}
        if year is not None:
            raw_meta["EXIF:DateTimeOriginal"] = f"{year}:06:15 12:00:00"
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO photos (path, mtime, size, tags, people, captions, raw_metadata)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (path, 1.0, 1, json.dumps([]), json.dumps(list(people)), json.dumps([]),
             json.dumps(raw_meta)),
        )
        conn.commit()
        conn.close()
        return path

    def add_face(self, photo_path, embedding, box=(0, 0, 100, 100)):
        conn = sqlite3.connect(self.db_path)
        cur = conn.execute(
            "INSERT INTO faces (photo_path, box, embedding, name, prob) VALUES (?, ?, ?, NULL, ?)",
            (photo_path, json.dumps(list(box)), embedding.tobytes(), 0.99),
        )
        face_id = cur.lastrowid
        conn.commit()
        conn.close()
        return face_id

    def resolve(self, max_iterations=5):
        """Reload the index so it sees the fixtures, then run identity resolution."""
        self.index.close()
        self.index = PhotoIndex(db_path=self.db_path)
        self.index.load()
        processor = FaceProcessor.__new__(FaceProcessor)  # no model initialisation
        return processor.cluster_and_resolve_identities(
            self.index, self.taxonomy, max_iterations=max_iterations
        )

    def name_of(self, face_id):
        conn = sqlite3.connect(self.db_path)
        row = conn.execute("SELECT name FROM faces WHERE id = ?", (face_id,)).fetchone()
        conn.close()
        return row[0] if row else None

    def traces(self):
        trace_path = os.path.join(os.path.dirname(self.db_path), "face_resolution_trace.json")
        if not os.path.exists(trace_path):
            return {}
        with open(trace_path, encoding="utf-8") as f:
            return {t["face_id"]: t for t in json.load(f)}


class TestDirectAnchors(FaceClusteringTestBase):
    def test_a_lone_face_in_a_singly_tagged_photo_anchors_its_identity(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        anchor_face = self.add_face(anchor_photo, jane)

        self.resolve()
        self.assertEqual(self.name_of(anchor_face), "Jane Doe")

    def test_the_anchor_propagates_to_other_faces_in_its_cluster(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        anchor_face = self.add_face(anchor_photo, jane)

        other_photo = self.add_photo("other.jpg", people=["Jane Doe"])
        other_face = self.add_face(other_photo, near(jane, 2))

        self.resolve()
        self.assertEqual(self.name_of(anchor_face), "Jane Doe")
        self.assertEqual(self.name_of(other_face), "Jane Doe")

    def test_a_photo_with_two_names_cannot_anchor(self):
        """One face but two names is ambiguous, so it must not become an anchor."""
        jane = identity_vector(1)
        photo = self.add_photo("ambiguous.jpg", people=["Jane Doe", "Bob Roe"])
        face = self.add_face(photo, jane)

        self.resolve()
        traces = self.traces()
        self.assertNotEqual(
            traces.get(face, {}).get("resolution_method"),
            "direct_anchor",
            "an ambiguous photo was treated as an anchor",
        )

    def test_a_photo_with_two_faces_cannot_anchor(self):
        jane, bob = identity_vector(1), identity_vector(2)
        photo = self.add_photo("group.jpg", people=["Jane Doe"])
        face_a = self.add_face(photo, jane)
        face_b = self.add_face(photo, bob)

        self.resolve()
        for face in (face_a, face_b):
            self.assertNotEqual(
                self.traces().get(face, {}).get("resolution_method"), "direct_anchor"
            )

    def test_an_anchor_records_its_trigger_photo_in_the_trace(self):
        jane = identity_vector(1)
        photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        face = self.add_face(photo, jane)

        self.resolve()
        trace = self.traces()[face]
        self.assertEqual(trace["assigned_name"], "Jane Doe")
        self.assertIn(photo, trace["trigger_photos"])


class TestStrictTagEnforcement(FaceClusteringTestBase):
    def test_a_name_is_never_applied_to_an_untagged_photo(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        untagged_photo = self.add_photo("untagged.jpg", people=[])
        untagged_face = self.add_face(untagged_photo, near(jane, 3))

        self.resolve()
        self.assertIsNone(
            self.name_of(untagged_face),
            "a face in an untagged photo was named from its cluster",
        )

    def test_the_trace_explains_why_an_untagged_face_was_skipped(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)
        untagged_face = self.add_face(self.add_photo("untagged.jpg"), near(jane, 3))

        self.resolve()
        method = self.traces()[untagged_face]["resolution_method"]
        self.assertIn("strict_tag_enforcement", method)

    def test_a_name_absent_from_the_photo_tags_is_not_applied(self):
        """The cluster says Jane, but this photo is tagged only with Bob."""
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        mismatched_photo = self.add_photo("mismatch.jpg", people=["Bob Roe"])
        mismatched_face = self.add_face(mismatched_photo, near(jane, 4))

        self.resolve()
        self.assertNotEqual(self.name_of(mismatched_face), "Jane Doe")


class TestSameNameConflictInOnePhoto(FaceClusteringTestBase):
    """One person cannot appear twice in a photo, at any stage of resolution."""

    def test_a_name_is_never_assigned_to_two_faces_of_one_photo(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        # Two near-identical faces in a photo tagged only with Jane. The iterative
        # loop clears both; the final pass may re-match at most one of them.
        conflict_photo = self.add_photo("conflict.jpg", people=["Jane Doe"])
        face_a = self.add_face(conflict_photo, near(jane, 5), box=(0, 0, 100, 100))
        face_b = self.add_face(conflict_photo, near(jane, 6), box=(200, 0, 300, 100))

        self.resolve()
        named = [f for f in (face_a, face_b) if self.name_of(f) == "Jane Doe"]
        self.assertLessEqual(
            len(named), 1, "the same person was assigned to two faces in one photo"
        )

    def test_the_second_face_is_left_unresolved_rather_than_mislabelled(self):
        """Losing the contest must mean no name, not someone else's name."""
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        conflict_photo = self.add_photo("conflict.jpg", people=["Jane Doe"])
        face_a = self.add_face(conflict_photo, near(jane, 5), box=(0, 0, 100, 100))
        face_b = self.add_face(conflict_photo, near(jane, 6), box=(200, 0, 300, 100))

        self.resolve()
        names = {self.name_of(face_a), self.name_of(face_b)}
        self.assertTrue(
            names <= {"Jane Doe", None},
            f"a face was given an unrelated identity: {names}",
        )

    def test_a_genuine_second_person_still_gets_their_own_name(self):
        """The guard must not starve a photo that really does contain two people."""
        jane, bob = identity_vector(1), identity_vector(2)

        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)

        pair = self.add_photo("pair.jpg", people=["Jane Doe", "Bob Roe"])
        jane_face = self.add_face(pair, near(jane, 21), box=(0, 0, 200, 200))
        bob_face = self.add_face(pair, near(bob, 22), box=(300, 0, 500, 200))

        self.resolve()
        self.assertEqual(self.name_of(jane_face), "Jane Doe")
        self.assertEqual(self.name_of(bob_face), "Bob Roe")


class TestBackgroundFaceFiltering(FaceClusteringTestBase):
    def test_a_tiny_background_face_is_never_named_by_elimination(self):
        """Noise faces are excluded from the process-of-elimination matching.

        Under 10% of the largest face AND under 2000px counts as background. Without
        the filter the leftover "Bob Roe" tag would be handed to the tiny face simply
        because it is the only unassigned face left.
        """
        jane, stranger = identity_vector(1), identity_vector(3)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        photo = self.add_photo("crowd.jpg", people=["Jane Doe", "Bob Roe"])
        big = self.add_face(photo, near(jane, 7), box=(0, 0, 400, 400))       # 160000
        tiny = self.add_face(photo, stranger, box=(0, 0, 20, 20))             # 400

        self.resolve()
        self.assertEqual(self.name_of(big), "Jane Doe")
        self.assertIsNone(
            self.name_of(tiny), "a background face was handed the leftover name"
        )

    def test_a_tiny_face_can_still_be_matched_with_high_confidence(self):
        """The counterpart: the final pass ignores the size filter.

        A small face that is visually confident (>= 0.80), confirmed by the photo's
        own tags, and whose name is not already taken by another face is still named
        -- the desired outcome for someone genuinely small in frame.
        """
        jane, bob = identity_vector(1), identity_vector(2)
        self.add_face(self.add_photo("jane.jpg", people=["Jane Doe"]), jane)
        self.add_face(self.add_photo("bob.jpg", people=["Bob Roe"]), bob)

        photo = self.add_photo("crowd.jpg", people=["Jane Doe", "Bob Roe"])
        self.add_face(photo, near(bob, 7), box=(0, 0, 400, 400))     # Bob, prominent
        tiny = self.add_face(photo, near(jane, 8), box=(0, 0, 20, 20))  # Jane, distant

        self.resolve()
        self.assertEqual(self.name_of(tiny), "Jane Doe")

    def test_a_large_face_is_kept_even_beside_a_much_larger_one(self):
        jane = identity_vector(1)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        photo = self.add_photo("pair.jpg", people=["Jane Doe"])
        big = self.add_face(photo, near(jane, 9), box=(0, 0, 400, 400))
        medium = self.add_face(photo, near(jane, 10), box=(0, 0, 100, 100))   # 10000 > 2000

        self.resolve()
        # Both are valid candidates, so the same-name conflict rule clears both;
        # the point here is that `medium` was not silently dropped as noise.
        self.assertNotIn(
            "background", str(self.traces().get(medium, {}).get("resolution_method", ""))
        )
        self.assertIsNotNone(big)


class TestProcessOfElimination(FaceClusteringTestBase):
    def test_a_lone_unassigned_face_takes_the_lone_unused_name(self):
        """Jane is identified; the one remaining face must be the one remaining name."""
        jane, bob = identity_vector(1), identity_vector(2)

        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        pair_photo = self.add_photo("pair.jpg", people=["Jane Doe", "Bob Roe"])
        jane_face = self.add_face(pair_photo, near(jane, 11), box=(0, 0, 200, 200))
        bob_face = self.add_face(pair_photo, bob, box=(300, 0, 500, 200))

        self.resolve()
        self.assertEqual(self.name_of(jane_face), "Jane Doe")
        self.assertEqual(
            self.name_of(bob_face), "Bob Roe", "the leftover name was not deduced"
        )

    def test_elimination_does_not_invent_names_for_untagged_photos(self):
        jane, bob = identity_vector(1), identity_vector(2)
        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        untagged = self.add_photo("untagged.jpg", people=[])
        a = self.add_face(untagged, near(jane, 12), box=(0, 0, 200, 200))
        b = self.add_face(untagged, bob, box=(300, 0, 500, 200))

        self.resolve()
        self.assertIsNone(self.name_of(a))
        self.assertIsNone(self.name_of(b))


class TestMetadataConflictOverride(FaceClusteringTestBase):
    def test_an_assignment_contradicting_the_photo_tags_is_cleared(self):
        jane = identity_vector(1)

        anchor_photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        self.add_face(anchor_photo, jane)

        # Same visual identity, but this photo insists it contains only Bob.
        bob_photo = self.add_photo("bob_only.jpg", people=["Bob Roe"])
        disputed = self.add_face(bob_photo, near(jane, 13))

        self.resolve()
        self.assertNotEqual(
            self.name_of(disputed), "Jane Doe", "photo metadata did not win"
        )


class TestResolutionOutputs(FaceClusteringTestBase):
    def test_statistics_count_the_faces_resolved_per_person(self):
        jane = identity_vector(1)
        a = self.add_photo("a.jpg", people=["Jane Doe"])
        self.add_face(a, jane)
        b = self.add_photo("b.jpg", people=["Jane Doe"])
        self.add_face(b, near(jane, 14))

        stats = self.resolve()
        self.assertEqual(stats.get("Jane Doe"), 2)

    def test_an_empty_index_resolves_to_nothing(self):
        self.assertEqual(self.resolve(), {})

    def test_a_trace_file_is_written_next_to_the_database(self):
        jane = identity_vector(1)
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        self.add_face(photo, jane)

        self.resolve()
        trace_path = os.path.join(os.path.dirname(self.db_path), "face_resolution_trace.json")
        self.assertTrue(os.path.exists(trace_path), "no trace file written")

    def test_every_face_appears_in_the_trace(self):
        jane, bob = identity_vector(1), identity_vector(2)
        photo = self.add_photo("a.jpg", people=["Jane Doe"])
        face_a = self.add_face(photo, jane)
        face_b = self.add_face(self.add_photo("b.jpg"), bob)

        self.resolve()
        traces = self.traces()
        self.assertIn(face_a, traces)
        self.assertIn(face_b, traces)

    def test_anchor_only_mode_still_assigns_direct_anchors(self):
        """max_iterations=0 skips propagation but must keep the anchor itself."""
        jane = identity_vector(1)
        photo = self.add_photo("anchor.jpg", people=["Jane Doe"])
        face = self.add_face(photo, jane)

        self.resolve(max_iterations=0)
        self.assertEqual(self.name_of(face), "Jane Doe")


class TestDistinctIdentitiesStaySeparate(FaceClusteringTestBase):
    def test_two_different_people_are_not_merged(self):
        jane, bob = identity_vector(1), identity_vector(2)

        jane_photo = self.add_photo("jane.jpg", people=["Jane Doe"])
        jane_face = self.add_face(jane_photo, jane)
        bob_photo = self.add_photo("bob.jpg", people=["Bob Roe"])
        bob_face = self.add_face(bob_photo, bob)

        self.resolve()
        self.assertEqual(self.name_of(jane_face), "Jane Doe")
        self.assertEqual(self.name_of(bob_face), "Bob Roe")

    def test_synthetic_identities_are_actually_separable(self):
        """Guards the fixtures themselves: if this fails the tests above prove nothing."""
        jane, bob = identity_vector(1), identity_vector(2)
        self.assertLess(np.linalg.norm(near(jane, 20) - jane), DBSCAN_EPS)
        self.assertGreater(np.linalg.norm(bob - jane), DBSCAN_EPS)


if __name__ == "__main__":
    unittest.main()
