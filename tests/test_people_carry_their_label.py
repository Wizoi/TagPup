"""Every answer that carries a person carries the `person` the pages label them by (docs/ARCHITECTURE.md, "People by id,
stage 2", phase 3): `{id, name, tag, group, shared}`, `shared` and `group` decided once, in
tagpup.core.vocabulary.person_labels, over everyone in the library. The answers keep every field they had (the pickers
read `person` from part C); a name two people are called is `shared` with no id and no tag, since a name alone cannot say
which; a name no person tag has, a group tag and a bucket are None.

Photos are rows as the indexer records them (tests/view_library.py); the tree's nodes are made by taxonomy.add_path.
Fictional names: two cousins called Sam under two groups, and a group of people (Marlowe) with someone under it.
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_client  # noqa: E402
from face_rows import add_face  # noqa: E402
from view_library import ViewLibrary  # noqa: E402

from tagpup.services import faces as face_service  # noqa: E402
from tagpup.services import identify, library_view, selection  # noqa: E402
from tagpup.services import people as people_service  # noqa: E402
from tagpup.store import person_ids, taxonomy  # noqa: E402

SAM_T, SAM_I = "Family/Thackeray/Sam", "Family/Ingersoll/Sam"
WREN, CORA, GROUP = "Family/Thackeray/Wren", "Family/Marlowe/Tamsin Marlowe", "Family/Marlowe"


def unit(*values):
    return np.array(values, dtype=np.float32).tobytes()


class Library(unittest.TestCase):
    def setUp(self):
        self.vl = ViewLibrary(self)
        self.vl.tree(SAM_T, SAM_I, WREN, CORA, face_root="Family")
        self.vl.tree("Friends/Max", face_root="Friends")
        self.library = self.vl.library
        self.sam_t = self.id_of(SAM_T)
        self.sam_i = self.id_of(SAM_I)
        self.wren = self.id_of(WREN)

    def id_of(self, tag):
        return self.vl.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", tag)[0][0]

    def person(self, tag, **more):
        node = self.id_of(tag)
        name = tag.split("/")[-1]
        return dict({"id": node, "name": name, "tag": tag, "group": "", "shared": False}, **more)

    def sam_thackeray(self):
        return self.person(SAM_T, group="Thackeray", shared=True)

    def sam_ingersoll(self):
        return self.person(SAM_I, group="Ingersoll", shared=True)

    def shared_sam(self):
        """A row that names Sam by the name alone: two people are called it."""
        return {"id": None, "name": "Sam", "tag": None, "group": "", "shared": True}


class TheDirectory(Library):
    def read(self):
        return person_ids.Directory.read(self.vl.conn)

    def test_everyone_is_told_with_the_group_when_a_leaf_is_shared_and_only_then(self):
        self.assertEqual([self.person("Friends/Max"), self.sam_ingersoll(), self.sam_thackeray(), self.person(CORA),
                          self.person(WREN)], self.read().records(), "by name, then by group")

    def test_a_group_of_people_is_nobody(self):
        names = [each["tag"] for each in self.read().records()]
        self.assertNotIn(GROUP, names)
        self.assertNotIn("Family", names, "nor is a root")
        self.assertIsNone(self.read().of_name("Marlowe"))
        self.assertIsNone(self.read().of_tag(GROUP))

    def test_a_name_is_the_person_two_people_or_nobody(self):
        found = self.read()
        self.assertEqual(self.person(WREN), found.of_name("wren"), "without case")
        self.assertEqual(self.shared_sam(), found.of_name("Sam"))
        self.assertIsNone(found.of_name("Nobody At All"))
        self.assertIsNone(found.of_name(None))
        self.assertIsNone(found.of_name(""))

    def test_a_tag_is_the_person_filed_there_even_when_the_leaf_is_shared(self):
        found = self.read()
        self.assertEqual(self.sam_ingersoll(), found.of_tag("Family/Ingersoll/Sam"))
        self.assertEqual(self.sam_ingersoll(), found.of_tag("family\\ingersoll\\sam"))
        self.assertIsNone(found.of_tag("Family/Ingersoll"))

    def test_what_is_handed_out_is_a_copy(self):
        found = self.read()
        found.records()[0]["name"] = "Changed"
        found.of_name("Wren")["name"] = "Changed"
        self.assertEqual("Wren", found.of_name("Wren")["name"])
        self.assertNotIn("Changed", [each["name"] for each in found.records()])

    def test_annotate_gives_each_row_its_person_and_changes_nothing_else(self):
        rows = [{"name": "Wren", "count": 3}, {"name": "Sam", "count": 1}, {"name": "Unknown Faces", "count": 9}]
        self.read().annotate(rows)
        self.assertEqual([("Wren", 3, self.person(WREN)), ("Sam", 1, self.shared_sam()), ("Unknown Faces", 9, None)],
                         [(each["name"], each["count"], each["person"]) for each in rows])

    def test_a_library_with_no_tree_has_no_one(self):
        other = ViewLibrary(self, "bare")
        self.assertEqual([], person_ids.Directory.read(other.conn).records())

    def test_a_person_gaining_a_tag_under_them_is_a_group_and_leaves(self):
        taxonomy.add_path(self.vl.conn, WREN + "/Baby photos")
        self.vl.conn.commit()
        self.assertNotIn(WREN, [each["tag"] for each in self.read().records()])
        self.assertIsNone(self.read().of_name("Wren"))


class TheAnswers(Library):
    def setUp(self):
        super().setUp()
        self.photo = self.vl.photo("A", "sams.jpg", tags=[SAM_T, WREN])
        self.other = self.vl.photo("A", "other.jpg", tags=[SAM_I])
        self.path = self.vl.path_of(self.photo)
        self.sam_face = add_face(self.vl.conn, self.path, name="Sam", embedding=unit(1, 0, 0, 0), box=(0, 0, 10, 10))
        self.wren_face = add_face(self.vl.conn, self.path, name="Wren", embedding=unit(0, 1, 0, 0), box=(20, 0, 30, 10))
        self.stranger = add_face(self.vl.conn, self.path, embedding=unit(0, 1, 0, 0.1), box=(40, 0, 50, 10))
        # Wren on another photo too: a suggestion is the nearest name ELSEWHERE in the library.
        add_face(self.vl.conn, self.vl.path_of(self.other), name="Wren", embedding=unit(0, 1, 0, 0))
        self.vl.conn.commit()

    def test_review_peoples_list(self):
        listed = {each["name"]: each for each in people_service.with_counts(self.library)}
        self.assertEqual(self.shared_sam(), listed["Sam"]["person"])
        self.assertEqual(self.person(WREN), listed["Wren"]["person"])
        self.assertEqual(2, listed["Wren"]["count"], "the fields it had are still there")

    def test_the_navigators_people(self):
        listed = {each["name"]: each for each in library_view.navigator(self.library, "people")["people"]}
        self.assertEqual(self.person(WREN), listed["Wren"]["person"])
        self.assertEqual(self.shared_sam(), listed["Sam"]["person"])
        self.assertEqual("Family/Thackeray", listed["Wren"]["group"], "the navigator's own `group` is untouched")

    def test_the_selections_tally(self):
        tally = selection.tally(self.library, selection.read(self.library, {"ids": [self.photo, self.other]}))
        listed = {each["name"]: each for each in tally["people"]}
        self.assertEqual(self.person(WREN), listed["Wren"]["person"])
        self.assertEqual(self.shared_sam(), listed["Sam"]["person"])
        self.assertTrue(listed["Wren"]["has_node"])

    def test_the_five_nearest(self):
        def named():
            return [1, 2], ["Wren", "Sam"], np.array([[0, 1, 0, 0], [1, 0, 0, 0]], dtype=np.float32)

        found = identify.face_matches(self.library, self.stranger, named)
        self.assertEqual(["Wren", "Sam"], [each["name"] for each in found])
        self.assertEqual([self.person(WREN), self.shared_sam()], [each["person"] for each in found])
        self.assertTrue(all("similarity" in each and "band" in each for each in found))

    def test_the_identify_pages_photo(self):
        def named():
            return [1], ["Wren"], np.array([[0, 1, 0, 0]], dtype=np.float32)

        details = identify.photo_details(self.library, self.path, named)
        faces = {each["id"]: each for each in details["faces"]}
        self.assertEqual(self.shared_sam(), faces[self.sam_face]["person"])
        self.assertEqual(self.person(WREN), faces[self.wren_face]["person"])
        self.assertIsNone(faces[self.stranger]["person"], "an unnamed face is nobody")
        self.assertEqual(["Sam", "Wren"], sorted(details["people"]), "the photo's list of names is as it was")

    def test_the_face_panel_names_a_face_and_what_it_suggests(self):
        panel = face_service.panel(self.library, self.path)
        faces = {each["id"]: each for each in panel["faces"]}
        self.assertEqual(self.shared_sam(), faces[self.sam_face]["person"])
        self.assertEqual(self.person(WREN), faces[self.wren_face]["person"])
        stranger = faces[self.stranger]
        self.assertIsNone(stranger["person"])
        self.assertEqual("Wren", stranger["suggestion"])
        self.assertEqual(self.person(WREN), stranger["suggestion_person"])
        self.assertIsNone(faces[self.sam_face]["suggestion_person"])

    def test_suggest_chips(self):
        status = {"status": "completed", "suggestions": {
            self.path: {"tags": [{"tag": "Trips/Coast", "score": 0.9}], "people": [
                # As offered() writes them: the person's TAG PATH in `name` (suggester.py item['tag']); a bare name too.
                {"name": WREN, "score": 0.8}, {"name": SAM_I, "score": 0.7}, {"name": "Family/Nobody/A New Face", "score": 0.6},
                {"name": "Sam", "score": 0.5}]},
            "failed.jpg": {"tags": [], "people": [], "error": "unreadable"}, "odd.jpg": "not a dict"}}
        people_service.annotate_suggestions(self.library, status)
        chips = status["suggestions"][self.path]["people"]
        self.assertEqual([self.person(WREN), self.sam_ingersoll(), None, self.shared_sam()],
                         [each["person"] for each in chips], "a path is exact even for a shared leaf")
        self.assertEqual([0.8, 0.7, 0.6, 0.5], [each["score"] for each in chips])
        self.assertNotIn("person", status["suggestions"][self.path]["tags"][0])
        self.assertEqual({"status": "idle"}, people_service.annotate_suggestions(self.library, {"status": "idle"}))


class OnTheWire(unittest.TestCase):
    def setUp(self):
        self.app, self.home = web_client.app_for(self, "tuner")
        self.client = self.app.test_client()
        self.vl = ViewLibrary(self, "library", home=self.home)
        self.vl.tree(SAM_T, SAM_I, WREN, face_root="Family")

    def test_both_apps_list_the_people_with_their_fields(self):
        for kind in ("tuner", "tagpup"):
            with self.subTest(kind):
                app, home = web_client.app_for(self, kind)
                vl = ViewLibrary(self, "library", home=home)
                vl.tree(SAM_T, SAM_I, WREN, face_root="Family")
                answer = app.test_client().get("/library/api/people?records=1")
                self.assertEqual(200, answer.status_code)
                found = answer.get_json()
                self.assertEqual([("Sam", "Ingersoll", True), ("Sam", "Thackeray", True), ("Wren", "", False)],
                                 [(each["name"], each["group"], each["shared"]) for each in found])
                self.assertTrue(all(set(each) == {"id", "name", "tag", "group", "shared"} for each in found))

    def test_the_plain_list_of_names_is_as_it_was(self):
        self.vl.photo("A", "a.jpg", tags=[SAM_T])
        self.assertEqual(["Sam"], self.client.get("/library/api/people").get_json())

    def test_a_hidden_person_is_left_out_unless_asked_for(self):
        taxonomy.set_branch_flags(self.vl.conn, WREN, hidden=1)
        self.vl.conn.commit()
        listed = lambda query: [each["name"] for each in self.client.get("/library/api/people" + query).get_json()]  # noqa: E731
        self.assertEqual(["Sam", "Sam"], listed("?records=1"))
        self.assertEqual(["Sam", "Sam", "Wren"], listed("?records=1&include_hidden=1"))

    def test_the_identify_queue_is_labelled_from_the_tree_as_it_is_now_not_as_it_was_cached(self):
        for number in (1, 2):
            path = self.vl.path_of(self.vl.photo("A", "wren%d.jpg" % number, tags=[WREN]))
            add_face(self.vl.conn, path, embedding=unit(0, 1, 0, 0))
        self.vl.conn.commit()
        first = self.client.get("/library/api/unmatched-faces/people").get_json()
        wren = [each for each in first if each["name"] == "Wren"][0]
        self.assertEqual(("Wren", "", False), (wren["person"]["name"], wren["person"]["group"], wren["person"]["shared"]))
        self.assertEqual(2, wren["count"], "its own fields are as they were")
        buckets = [each for each in first if each["name"] != "Wren"]
        self.assertTrue(all(each["person"] is None for each in buckets), "a bucket is nobody")
        # Another Wren is filed; no face and no photo changed, so the queue itself is the cached one.
        taxonomy.add_path(self.vl.conn, "Family/Ingersoll/Wren")
        self.vl.conn.commit()
        second = self.client.get("/library/api/unmatched-faces/people").get_json()
        wren = [each for each in second if each["name"] == "Wren"][0]
        self.assertEqual((None, True), (wren["person"]["id"], wren["person"]["shared"]))

    def test_naming_faces_in_bulk_says_whom(self):
        path = self.vl.path_of(self.vl.photo("A", "wren.jpg", tags=[WREN]))
        face = add_face(self.vl.conn, path, embedding=unit(0, 1, 0, 0))
        self.vl.conn.commit()
        answer = self.client.post("/library/api/faces/match-bulk", json={"face_ids": [face], "person_name": "Wren"})
        self.assertEqual(200, answer.status_code, answer.get_json())
        reply = answer.get_json()
        self.assertEqual(self.vl.rows("SELECT id FROM tag_taxonomy WHERE tag = ?", WREN)[0][0], reply["person"]["id"])
        self.assertEqual(1, reply["matched"])


if __name__ == "__main__":
    unittest.main()
