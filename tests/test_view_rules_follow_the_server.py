"""The rules a page copies from the server are the server's (tests/fixtures/view_rules.json).

The search page refuses, before it asks, a union of more than MAX_MEMBERS sources, words longer than MAX_WORDS and a kind or
an order the server does not know, and leaves out a typed term that says nothing (web/tagpup/library-source.js). Each is a
copy of a server rule: tagpup.services.library_view, tagpup.store.library_view and tagpup.store.search_index.terms. This
holds the table to the server; tests/frontend/search-page.test.mjs holds the page to the table, so a rule changed on one
side fails a test until the other follows.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tagpup.services import library_view  # noqa: E402
from tagpup.store import library_view as store  # noqa: E402
from tagpup.store import search_index  # noqa: E402

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "view_rules.json"), encoding="utf-8") as _file:
    TABLE = json.load(_file)


class TheTableIsTheServers(unittest.TestCase):
    def test_the_caps_kinds_orders_and_parts(self):
        self.assertEqual(library_view.MAX_MEMBERS, TABLE["max_members"])
        self.assertEqual(library_view.MAX_WORDS, TABLE["max_words"])
        self.assertEqual(list(store.KINDS), TABLE["kinds"])
        self.assertEqual(list(store.MEMBER_KINDS), TABLE["member_kinds"])
        self.assertEqual(list(store.ORDERS), TABLE["orders"])
        self.assertEqual(library_view.order_of(None), TABLE["default_order"])
        self.assertEqual(list(library_view.SEARCH_PARTS), TABLE["search_parts"])

    def test_a_typed_term_says_something_where_the_server_looks_for_it(self):
        said = [each for each in TABLE["terms"] if each["says_something"]]
        self.assertTrue(said and len(said) < len(TABLE["terms"]), "the table holds terms of both kinds")
        for each in TABLE["terms"]:
            with self.subTest(term=each["term"]):
                self.assertEqual(each["says_something"], bool(search_index.terms(each["term"])))


if __name__ == "__main__":
    unittest.main()
