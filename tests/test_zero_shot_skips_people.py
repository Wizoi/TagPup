"""CLIP is never asked whether a photo looks like a person's name.

Suggest adds every taxonomy leaf to the words CLIP scores a photo against, and skipped
people by a written-out list of roots: family, friends, pets. Everyone under People --
and under any face root a library made for itself -- was offered to CLIP by name.
"""
import unittest

from tagpup.core import suggesting

# The server and the CLI both ask tagpup.core.suggesting.zero_shot_words (#74).


class FakeTaxonomy:
    paths = {"People", "People/Rowan Thackeray", "Crew", "Crew/Imogen Vale",
             "Activity", "Activity/Rowing", "Pets/Biscuit"}

    def people_roots(self):
        # The roots this library's tree flags as holding faces, Pets among them: the
        # servers added pets themselves, and now only the tree says (docs/findings.md, #66).
        return {"people", "crew", "pets"}


class ZeroShotSkipsPeople(unittest.TestCase):
    def test_no_person_is_a_candidate(self):
        taxonomy = FakeTaxonomy()
        candidates = suggesting.zero_shot_words(["Beach"], taxonomy.paths, taxonomy.people_roots())
        self.assertIn("Rowing", candidates)
        self.assertIn("Beach", candidates)
        for name in ("Rowan Thackeray", "Imogen Vale", "Biscuit"):
            self.assertNotIn(name, candidates)


if __name__ == "__main__":
    unittest.main()
