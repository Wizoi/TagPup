"""CLIP is never asked whether a photo looks like a person's name.

Suggest adds every taxonomy leaf to the words CLIP scores a photo against, and skipped
people by a written-out list of roots: family, friends, pets. Everyone under People --
and under any face root a library made for itself -- was offered to CLIP by name.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tagpup_server import zero_shot_candidates  # noqa: E402


class FakeTaxonomy:
    paths = {"People", "People/Rowan Thackeray", "Crew", "Crew/Imogen Vale",
             "Activity", "Activity/Rowing", "Pets/Biscuit"}

    def people_roots(self):
        return {"people", "family", "friends", "crew"}


class ZeroShotSkipsPeople(unittest.TestCase):
    def test_no_person_is_a_candidate(self):
        candidates = zero_shot_candidates(FakeTaxonomy(), ["Beach"])
        self.assertIn("Rowing", candidates)
        self.assertIn("Beach", candidates)
        for name in ("Rowan Thackeray", "Imogen Vale", "Biscuit"):
            self.assertNotIn(name, candidates)


if __name__ == "__main__":
    unittest.main()
