"""The lists the server sorts for the pages are alphabetical in the pages' order
(tagpup.core.vocabulary.tag_sort_key): the autocomplete, the people offered while a name is typed,
Review People's counts (most first, the alphabet for a tie) and Review Tags' list and buckets.

The pages sort what they show themselves (web/common/vocabulary.js); the server's order is the
same one, so a list the page does not re-sort -- the picker's source, a bucket -- agrees with it.
Binary order put "Zoo" before "apple" and "Trip 10" before "Trip 3".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from service_fixture import TempLibrary  # noqa: E402
from photo_rows import add_read  # noqa: E402

from tagpup.services import people as people_service, tags as tags_service  # noqa: E402
from tagpup.store import db  # noqa: E402

IN_ORDER_OF_ADDING = ["Zoo", "Trip 10", "apple", "Trip 3", "Éclair", "Places/Beach", "People/Wen Zhao", "People/anh Tran"]


class ListsAreAlphabetical(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("a.jpg")
        self.tree("People", has_face=1)
        self.tree("People/Wen Zhao", has_face=1, parent="People")
        self.tree("People/anh Tran", has_face=1, parent="People")
        conn = db.connect(self.lib.library.path)
        try:
            add_read(conn, self.photo, {"XMP:Subject": IN_ORDER_OF_ADDING})
            conn.commit()
        finally:
            conn.close()

    def tree(self, tag, has_face=0, parent=None):
        parent_id = self.lib.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", (parent,))[0][0] if parent else None
        self.lib.execute("INSERT INTO tag_taxonomy (tag, name, parent_id, has_face) VALUES (?, ?, ?, ?)",
                         (tag, tag.rsplit("/", 1)[-1], parent_id, has_face))

    def test_the_autocomplete_is_alphabetical_and_holds_the_same_tags(self):
        offered = tags_service.autocomplete(self.lib.library)
        self.assertEqual(sorted(offered), sorted(set(IN_ORDER_OF_ADDING) | {"People"}))
        self.assertLess(offered.index("apple"), offered.index("Zoo"))
        self.assertLess(offered.index("Trip 3"), offered.index("Trip 10"))
        self.assertLess(offered.index("Éclair"), offered.index("Trip 3"))
        self.assertLess(offered.index("People"), offered.index("People/anh Tran"))
        self.assertLess(offered.index("People/anh Tran"), offered.index("People/Wen Zhao"))

    def test_the_people_offered_are_alphabetical(self):
        for name in ("Zoe Abbott", "émile Roy", "anh Tran", "Bao Le"):
            self.lib.add_face(self.photo, [0, 0, 10, 10], name=name)
        self.assertEqual(people_service.names(self.lib.library), ["anh Tran", "Bao Le", "émile Roy", "Zoe Abbott"])

    def test_review_peoples_counts_are_most_first_with_the_alphabet_for_a_tie(self):
        for name, faces in (("Zoe Abbott", 1), ("émile Roy", 2), ("anh Tran", 1), ("Bao Le", 2)):
            for _ in range(faces):
                self.lib.add_face(self.photo, [0, 0, 10, 10], name=name)
        self.assertEqual([(each["name"], each["count"]) for each in people_service.with_counts(self.lib.library)],
                         [("Bao Le", 2), ("émile Roy", 2), ("anh Tran", 1), ("Zoe Abbott", 1)])

    def test_review_tags_list_and_buckets_are_alphabetical(self):
        listing = tags_service.listing(self.lib.library, include_people=False)
        shown = [each["tag"] for each in listing["tags"]]
        self.assertLess(shown.index("apple"), shown.index("Zoo"))
        self.assertLess(shown.index("Trip 3"), shown.index("Trip 10"))
        flat = listing["buckets"]["flat"]
        self.assertEqual(flat, ["apple", "Éclair", "Trip 3", "Trip 10", "Zoo"])
        self.assertEqual(listing["buckets"]["used_once"], sorted(
            listing["buckets"]["used_once"], key=lambda tag: [shown.index(tag)]))


if __name__ == "__main__":
    unittest.main()
