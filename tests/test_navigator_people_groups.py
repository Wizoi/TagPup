"""The navigator's People grouped under the branches of the tag tree they are filed in (tagpup.services.library_view.navigator
"people"; docs/ARCHITECTURE.md, phase 9, the owner's review #673): Family/Immediate apart from Friends, each branch with the
photos naming anyone under it, each photo once; a person is a leaf `has_face` node by the one rule (tagpup.store.person_ids),
so a branch is never a person and a name filed in two places, or nowhere, is not filed.

Photos are rows as the indexer records them (tests/view_library.py), tags nodes made by taxonomy.add_path. Fictional names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from view_library import ViewLibrary  # noqa: E402

from tagpup.services import library_view  # noqa: E402

ROWAN, HAZEL, WREN, ORLA, ASH = "Rowan Thackeray", "Hazel Brookmire", "Wren Halloway", "Orla Pemberley", "Ash Gantry"


class PeopleGroups(unittest.TestCase):
    def setUp(self):
        vl = self.vl = ViewLibrary(self)
        vl.tree("Family/Immediate/" + ROWAN, "Family/Cousins/" + HAZEL, face_root="Family")
        vl.tree("Friends/" + WREN, "Friends/" + ASH, face_root="Friends")
        vl.tree("Neighbours/" + ASH, face_root="Neighbours")   # one name filed twice: not filed
        self.both = vl.photo("A", "both.jpg", tags=["Family/Immediate/" + ROWAN, "Family/Cousins/" + HAZEL])
        self.rowan = vl.photo("A", "rowan.jpg", tags=["Family/Immediate/" + ROWAN])
        self.wren = vl.photo("A", "wren.jpg", tags=["Friends/" + WREN, "Family/Cousins/" + HAZEL])
        self.ash = vl.photo("A", "ash.jpg", tags=["Friends/" + ASH])
        self.people = library_view.navigator(vl.library, "people")

    def test_each_person_names_the_branch_they_are_filed_in(self):
        group = {each["name"]: each["group"] for each in self.people["people"]}
        self.assertEqual({ROWAN: "Family/Immediate", HAZEL: "Family/Cousins", WREN: "Friends"},
                         {name: tag for name, tag in group.items() if tag})
        self.assertIsNone(group.get(ASH), "a name two nodes are called is not filed")

    def test_the_branches_count_each_photo_once_and_are_a_tree(self):
        groups = {each["tag"]: (each["name"], each["parent"], each["count"]) for each in self.people["groups"]}
        self.assertEqual({"Family": ("Family", None, 3), "Family/Immediate": ("Immediate", "Family", 2),
                          "Family/Cousins": ("Cousins", "Family", 2), "Friends": ("Friends", None, 1)}, groups)
        self.assertEqual(1, self.people["unfiled"], "the photos naming someone not filed")

    def test_a_branch_tag_is_never_a_person(self):
        self.vl.tree("Family/Immediate/" + ROWAN + "/Baby photos", face_root="Family")   # Rowan's node becomes a branch
        people = library_view.navigator(self.vl.library, "people")
        group = {each["name"]: each["group"] for each in people["people"]}
        self.assertIsNone(group[ROWAN])
        self.assertNotIn("Family/Immediate", [each["tag"] for each in people["groups"]])

    def test_a_library_with_no_people_has_no_groups(self):
        other = ViewLibrary(self, "empty")
        other.photo("A", "x.jpg")
        self.assertEqual({"people": [], "groups": [], "unfiled": 0}, library_view.navigator(other.library, "people"))


if __name__ == "__main__":
    unittest.main()
