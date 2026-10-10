"""Where two people share a leaf, each is shown with the group that tells them apart, and only then
(docs/ARCHITECTURE.md, "People by id, stage 2", "Showing the group"; the owner, 2026-10-09: `Sam · Thackeray`).

The decision is `tagpup.core.vocabulary.person_labels`, made over every person of the library. The page's string is
`personLabel` in web/common/vocabulary.js; tests/fixtures/person_labels.json is the table both are held to
(tests/frontend/person-labels.test.mjs is the page's half).
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core import vocabulary  # noqa: E402

TABLE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "person_labels.json"),
                       encoding="utf-8"))


class TheTable(unittest.TestCase):
    def test_every_case_of_the_table(self):
        for case in TABLE["cases"]:
            with self.subTest(case["why"]):
                tags = case["people"]
                labels = vocabulary.person_labels(list(enumerate(tags)))
                self.assertEqual(len(tags), len(labels))
                for number, tag in enumerate(tags):
                    expected = case["expect"][tag]
                    self.assertEqual((expected["shared"], expected["group"]), tuple(labels[number]), tag)

    def test_the_table_names_each_person_it_lists_and_its_titles_are_their_tags(self):
        for case in TABLE["cases"]:
            with self.subTest(case["why"]):
                self.assertEqual(sorted(case["people"]), sorted(case["expect"]))
                for tag, expected in case["expect"].items():
                    self.assertEqual(tag, expected["title"])
                    leaf = vocabulary.leaf_of(tag)
                    self.assertEqual(leaf + " · " + expected["group"] if expected["shared"] else leaf,
                                     expected["label"])


class OverEveryoneInTheLibrary(unittest.TestCase):
    PEOPLE = [(1, "Family/Thackeray/Sam"), (2, "Family/Ingersoll/Sam"), (3, "Family/Thackeray/Wren")]

    def test_a_list_of_one_sam_still_says_which(self):
        everyone = vocabulary.person_labels(self.PEOPLE)
        shown = {person_id: everyone[person_id] for person_id in (2,)}
        self.assertEqual({2: (True, "Ingersoll")}, {k: tuple(v) for k, v in shown.items()})
        alone = vocabulary.person_labels([self.PEOPLE[1]])
        self.assertEqual((False, ""), tuple(alone[2]), "computed over the one shown, Sam would not say")

    def test_a_library_with_no_shared_leaf_has_no_label_at_all(self):
        # kr-track and renton_parkrun: 66 and 257 person leaves, none shared (counted 2026-10-09).
        labels = vocabulary.person_labels([(n, "People/Person %d" % n) for n in range(300)])
        self.assertFalse(any(label.shared or label.group for label in labels.values()))

    def test_no_one_is_no_one(self):
        self.assertEqual({}, vocabulary.person_labels([]))

    def test_a_tag_that_is_nothing_is_left_out_not_a_failure(self):
        self.assertEqual({}, vocabulary.person_labels([(1, ""), (2, None), (3, " / ")]))

    def test_a_tag_is_read_as_the_vocabulary_reads_it(self):
        labels = vocabulary.person_labels([(1, " Family | Thackeray / Sam "), (2, "Family\\Ingersoll\\sam")])
        self.assertEqual({1: (True, "Thackeray"), 2: (True, "Ingersoll")}, {k: tuple(v) for k, v in labels.items()})

    def test_the_real_shape_one_leaf_under_two_roots(self):
        # photo_index: one shared leaf, its two tags under two different roots (counted 2026-10-09).
        labels = vocabulary.person_labels([(10, "Family/Ash"), (11, "Friends/Ash"), (12, "Family/Wren")])
        self.assertEqual({10: (True, "Family"), 11: (True, "Friends"), 12: (False, "")},
                         {k: tuple(v) for k, v in labels.items()})


if __name__ == "__main__":
    unittest.main()
