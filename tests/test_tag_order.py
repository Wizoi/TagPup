"""The order tags are shown in on the server (tagpup.core.vocabulary.tag_sort_key) is the page's.

tests/fixtures/tag_order.json is one table for both: tests/frontend/tag-order.test.mjs holds
compareTagNames / sortedTags (web/common/vocabulary.js) to it, this holds tag_sort_key, so a
list the server sorts (the autocomplete, Review Tags) and the same list the page sorts agree.
"""
import json
import os
import unittest

from tagpup.core import vocabulary

TABLE = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "tag_order.json"), encoding="utf-8"))


class TagOrder(unittest.TestCase):
    def test_every_case_of_the_shared_table(self):
        for each in TABLE["cases"]:
            with self.subTest(each["name"]):
                self.assertEqual(sorted(each["given"], key=vocabulary.tag_sort_key), each["shown"])
                self.assertEqual(sorted(reversed(each["given"]), key=vocabulary.tag_sort_key), each["shown"])

    def test_a_missing_tag_is_the_empty_one(self):
        self.assertEqual(sorted([None, "a", ""], key=vocabulary.tag_sort_key), [None, "", "a"])

    def test_the_key_is_total_and_does_not_depend_on_the_machine(self):
        self.assertNotEqual(vocabulary.tag_sort_key("apple"), vocabulary.tag_sort_key("Apple"))
        self.assertEqual(vocabulary.tag_sort_key("Apple"), vocabulary.tag_sort_key("Apple"))


if __name__ == "__main__":
    unittest.main()
