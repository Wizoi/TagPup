"""Scenarios V1 and V5: the owner looks for the photos with no tag (#2837).

A photo has no tag when the library holds no row of photo_tags for it (the owner's rule): a photo naming only a person is
tagged, the person being a node of the tag tree. The Keywords navigator counts them beside the tree, in the same snapshot as
the tree's counts, and the view of them (`kind=untagged`, no value) lists every one once -- a clip among them, under the
Photos / Videos filter its clips alone -- and the count, the list and the total agree. A photo tagged meanwhile leaves the
list and the count. The source is a member like any other: a union lists a photo in two of its members once, a search
looks for them within a folder or leaves them out, and two libraries in one home each answer their own.

New: on the code before it `kind=untagged` answered 400 ("kind must be one of ...") and the navigator's keywords had no
`untagged`, so every test here fails.

Photos are rows as the indexer records them (tests/view_library.py); fictional names only.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

WREN = "People/Wren Halloway"


class PhotosWithNoTag(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tagpup")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree("Trips/Coast", WREN, face_root="People")
        photo = self.vl.photo
        self.tagged = photo("Coast", "pier.jpg", "2024:01:01 10:00:00", tags=["Trips/Coast"])
        self.person_only = photo("Coast", "wren.jpg", "2024:01:02 10:00:00", tags=[WREN])
        self.branch_only = photo("Lake", "trip.jpg", "2024:01:03 10:00:00", tags=["Trips"])
        self.bare = photo("Coast", "dawn.jpg", "2024:02:01 10:00:00")
        self.undated = photo("Lake", "jetty.jpg")
        self.clip = photo("Lake", "swim.mp4")
        self.untagged = {self.bare, self.undated, self.clip}
        self.coast = os.path.join(self.vl.pictures, "Coast")

    def get(self, path, library="library", **query):
        reply = self.client.get("/%s%s" % (library, path), query_string=query)
        self.assertEqual(200, reply.status_code, reply.data)
        return reply.get_json()

    def ids(self, library="library", **query):
        return self.get("/api/library/ids", library, **query)

    def untagged_count(self, library="library"):
        return self.get("/api/library/navigator", library, section="keywords")["untagged"]

    def search(self, **parts):
        return self.ids(kind="search", value=json.dumps(parts))

    def test_the_navigators_count_the_list_and_its_total_are_the_same_photos_a_person_only_photo_being_tagged(self):
        found = self.ids(kind="untagged")
        self.assertEqual(self.untagged, set(found["ids"]))
        self.assertEqual((3, 3, True), (self.untagged_count(), found["total"], found["complete"]))
        self.assertEqual(len(found["ids"]), len(set(found["ids"])), "each photo once")
        for tagged in (self.person_only, self.branch_only, self.tagged):
            self.assertNotIn(tagged, found["ids"])
        self.assertEqual([self.bare], found["ids"][:1], "the dated first, in Date Taken order")
        self.assertEqual([self.clip], found["videos"])
        clips = self.ids(kind="untagged", media="video")
        self.assertEqual(([self.clip], 1, 3), (clips["ids"], clips["total"], clips["of"]))
        cards = self.get("/api/library/cards", ids=str(self.clip))["cards"]
        self.assertEqual(["video"], [card["media"] for card in cards])
        self.assertEqual({"kind": "untagged", "value": None, "recursive": False}, found["source"])
        self.assertEqual(self.ids(kind="untagged", value="ignored")["ids"], found["ids"], "it takes no value, as all does")

    def test_a_photo_tagged_meanwhile_leaves_the_list_and_the_count(self):
        self.assertEqual(3, self.untagged_count())
        self.vl.photo("Coast", "dawn.jpg", "2024:02:01 10:00:00", tags=["Trips/Coast"])   # the index reads it tagged
        found = self.ids(kind="untagged")
        self.assertEqual(({self.undated, self.clip}, 2, 2), (set(found["ids"]), found["total"], self.untagged_count()))
        self.assertIn(self.bare, self.ids(kind="keyword", value="Trips/Coast")["ids"])

    def test_with_other_rows_in_a_union_or_a_search(self):
        union = self.ids(kind="any_of", value=json.dumps([{"kind": "untagged"}, {"kind": "keyword", "value": "Trips/Coast"},
                                                           {"kind": "folder", "value": self.coast, "recursive": True}]))
        self.assertEqual(len(union["ids"]), len(set(union["ids"])), "a photo in two rows once")
        self.assertEqual(self.untagged | {self.tagged, self.person_only}, set(union["ids"]))
        self.assertEqual(len(union["ids"]), union["total"])
        within = self.search(all_of=[{"kind": "untagged"}, {"kind": "folder", "value": self.coast, "recursive": True}])
        self.assertEqual(([self.bare], 1), (within["ids"], within["total"]))
        none = self.search(none_of=[{"kind": "untagged"}])
        self.assertEqual({self.tagged, self.person_only, self.branch_only}, set(none["ids"]), "exactly the tagged")
        either = self.search(any_of=[{"kind": "untagged"}, {"kind": "person", "value": WREN}])
        self.assertEqual(self.untagged | {self.person_only}, set(either["ids"]))

    def test_two_libraries_in_one_home_each_answer_their_own(self):
        other = ViewLibrary(self, "meadow", home=self.home)
        other.tree("Trips/Coast")
        theirs = other.photo("Field", "kite.jpg", "2023:05:01 10:00:00")
        other.photo("Field", "picnic.jpg", "2023:05:02 10:00:00", tags=["Trips/Coast"])
        self.assertEqual(([theirs], 1), (self.ids("meadow", kind="untagged")["ids"], self.untagged_count("meadow")))
        self.assertEqual((self.untagged, 3), (set(self.ids(kind="untagged")["ids"]), self.untagged_count()))


if __name__ == "__main__":
    unittest.main()
