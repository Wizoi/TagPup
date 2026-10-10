"""People by id, stage 2, the pages' writes (docs/ARCHITECTURE.md, "People by id, stage 2"): two cousins called Sam under two groups,
one photo, two faces.

The keyword writer used to leave out a person "the file already names by their leaf": adding the second Sam to a photo that held
the first wrote nothing, and reported nothing wrong -- the second face could be named only on a photo that lost the tag it needed.
A person is the node now, so the two are two keywords. An old page (or the CLI, or the MCP) names a person by name: that works when
the name is one person's and is refused, naming the candidates, when two people have it; a page that has the person sends their id,
and an id that is nobody's (merged or deleted in another window) is a 404 that makes no one.

ExifTool is a table of files (tests/fake_exiftool.py); photos are rows as the indexer records them, faces as the code that makes them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import InStep  # noqa: E402

from tagpup.services import inspect  # noqa: E402
from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.store import db, people, removals, taxonomy  # noqa: E402

SAM_T, SAM_I = "Family/Thackeray/Sam", "Family/Ingersoll/Sam"


class TwoSamsOnAPhoto(InStep):
    def setUp(self):
        super().setUp()

        def tree(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "Family", root_has_face=1)
                for tag in (SAM_T, SAM_I):
                    taxonomy.add_path(conn, tag)

        db.write_with_connection(self.path, tree)
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)
        self.photo_path = self.held("regatta_001.jpg")
        self.first = self.face(self.photo_path, box=(10, 10, 60, 60))
        self.second = self.face(self.photo_path, box=(70, 10, 110, 60))

    def node(self, tag):
        return self.look("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))[0][0]

    def match(self, face, **who):
        return self.tuner_post("face/match", dict(face_id=face, **who))

    def test_both_cousins_on_one_photo_get_both_tags(self):
        """Fails on the code before part B: the second Sam's tag was skipped because the first, with the same leaf, was there."""
        self.assertEqual(200, self.match(self.first, person_id=self.sam_t).status_code)
        self.assertEqual([SAM_T], self.tags(self.photo_path))
        reply = self.match(self.second, person_id=self.sam_i)
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(1, reply.get_json()["tags_written"])
        self.assertEqual({SAM_T, SAM_I}, set(self.tags(self.photo_path)))
        self.assertEqual([(self.sam_t, "Sam"), (self.sam_i, "Sam")],
                         [self.row(self.first)[3:4] + self.row(self.first)[0:1], self.row(self.second)[3:4] + self.row(self.second)[0:1]])
        self.assertEqual([("Sam", "keyword"), ("Sam", "keyword")], self.indexed_people(), "a photo lists two Sams")

    def test_unnaming_one_takes_only_their_tag(self):
        self.match(self.first, person_id=self.sam_t)
        self.match(self.second, person_id=self.sam_i)
        reply = self.tuner_post("face/unmatch", {"face_id": self.first})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([SAM_I], self.tags(self.photo_path), "the other Sam stays")
        self.assertEqual((None, None), self.row(self.first)[0:1] + self.row(self.first)[3:4])
        self.assertEqual(self.sam_i, self.row(self.second)[3])

    def test_a_name_two_people_have_is_refused_naming_them_and_writes_nothing(self):
        reply = self.match(self.first, person_name="Sam")
        self.assertEqual(400, reply.status_code)
        error = reply.get_json()["error"]
        self.assertIn(SAM_T, error)
        self.assertIn(SAM_I, error)
        self.assertEqual(0, self.files.writes)
        self.assertIsNone(self.row(self.first)[0])

    def test_an_id_that_is_nobodys_is_not_found_and_makes_no_tag(self):
        before = self.look("SELECT COUNT(*) FROM tag_taxonomy")[0][0]
        reply = self.match(self.first, person_id=99999)
        self.assertEqual(404, reply.status_code)
        self.assertEqual(before, self.look("SELECT COUNT(*) FROM tag_taxonomy")[0][0])
        self.assertEqual(0, self.files.writes)
        self.assertIsNone(self.row(self.first)[0])

    def test_the_id_wins_when_a_page_sends_both(self):
        reply = self.match(self.first, person_id=self.sam_i, person_name="Wren Halloway")
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([SAM_I], self.tags(self.photo_path))

    def test_a_group_is_refused_as_a_person_by_id_and_by_name(self):
        group = self.node("Family/Thackeray")
        for who in ({"person_id": group}, {"person_name": "Thackeray"}):
            with self.subTest(who):
                reply = self.match(self.first, **who)
                self.assertEqual(400, reply.status_code)
                self.assertIn("group of people, not a person", reply.get_json()["error"])
        self.assertEqual(0, self.files.writes)

    def test_a_selection_is_assigned_to_the_person_picked(self):
        other = self.held("regatta_002.jpg")
        face = self.face(other)
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [self.first, face], "person_id": self.sam_i})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(2, reply.get_json()["matched"])
        self.assertEqual(self.sam_i, reply.get_json()["person"]["id"])
        self.assertEqual([SAM_I], self.tags(other))
        self.assertEqual([SAM_I], self.tags(self.photo_path))

    def test_a_bulk_assignment_to_a_name_two_people_have_is_refused_before_anything_is_written(self):
        reply = self.tuner_post("faces/match-bulk", {"face_ids": [self.first], "person_name": "Sam"})
        self.assertEqual(400, reply.status_code)
        self.assertIn(SAM_T, reply.get_json()["error"])
        self.assertEqual(0, self.files.writes)
        self.assertEqual([], self.look("SELECT 1 FROM job_runs WHERE job = 'assign faces'"), "no run for a refusal")

    def test_a_persons_faces_are_asked_for_by_id_and_a_shared_name_is_refused_naming_the_paths(self):
        self.match(self.first, person_id=self.sam_t)
        self.match(self.second, person_id=self.sam_i)
        found = self.tuner_client.get("/library/api/person-faces", query_string={"person_id": self.sam_i, "limit": -1}).get_json()
        self.assertEqual([self.second], [face["id"] for face in found["faces"]])
        # A page that has only the name is told which Sams there are (the page asks which one); nothing is read or linked.
        by_name = self.tuner_client.get("/library/api/person-faces", query_string={"name": "Sam", "limit": -1})
        self.assertEqual(400, by_name.status_code)
        self.assertIn(SAM_T, by_name.get_json()["error"])
        self.assertIn(SAM_I, by_name.get_json()["error"])
        self.assertEqual(self.sam_t, self.row(self.first)[3], "reading linked nobody")
        stale = self.tuner_client.get("/library/api/person-faces", query_string={"person_id": 99999})
        self.assertEqual(404, stale.status_code)

    def test_inspect_finds_the_photos_of_the_one_person_a_path_names_and_of_everyone_a_name_names(self):
        other = self.held("regatta_002.jpg")
        self.face(other)
        self.match(self.first, person_id=self.sam_t)
        self.tuner_post("face/match", {"face_id": self.face(other), "person_id": self.sam_i})
        by_path = inspect.photos(self.library, person=SAM_T)
        self.assertEqual(1, by_path["count"], by_path)
        self.assertEqual(2, inspect.photos(self.library, person="Sam")["count"], "a name is everyone called it")
        self.assertEqual(0, inspect.photos(self.library, person="Family/Nowhere/Sam")["count"],
                         "a path no node holds is nobody filed: only rows with no id, spelled as its leaf, would answer")

    def test_a_person_taken_off_a_photo_is_remembered_by_their_id_not_by_their_name(self):
        """Fix round 1: the record of a removal carried the name alone, so taking the first Sam off a photo blocked guessing the
        other Sam on it. It reads the id the journaled change recorded."""
        self.match(self.first, person_id=self.sam_t)
        reply = self.client.post("/library/api/photo/save-metadata", json={"path": self.photo_path, "title": "", "tags": []})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((None, "manual"), self.row(self.first)[:2])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        try:
            gone = removals.removed_people(conn, [self.first])[self.first]
        finally:
            conn.close()
        self.assertEqual(Ref(self.sam_t, "Sam"), gone)
        self.assertTrue(removals.same_person(gone, Ref(self.sam_t, "Sam")))
        self.assertFalse(removals.same_person(gone, Ref(self.sam_i, "Sam")), "the other Sam is not blocked")

    def test_the_queue_and_the_grid_tell_the_two_apart(self):
        """The queue lists a person by their node: two Sams are two rows, each opening its own grid."""
        for number in (1, 2, 3, 4):
            other = self.held("quay_%d.jpg" % number, [SAM_T] if number < 3 else [SAM_I])
            self.face(other)
        queue = self.tuner_client.get("/library/api/unmatched-faces/people").get_json()
        sams = sorted((row["person"]["tag"], row["person_id"], row["count"]) for row in queue if row["name"] == "Sam")
        self.assertEqual([(SAM_I, self.sam_i, 2), (SAM_T, self.sam_t, 2)], sams)
        grid = self.tuner_client.get("/library/api/unmatched-faces/person-matches", query_string={"person_id": self.sam_t}).get_json()
        self.assertEqual(2, grid["total_count"])

    def test_an_open_page_that_has_only_the_name_is_asked_which_one_for_a_name_two_people_have(self):
        """A name two people have is refused on a read and a write alike, naming their paths (owner, 2026-10-10: the leaf is
        never identity; a page asks which one and sends the id)."""
        for number in (1, 2, 3, 4):
            self.face(self.held("quay_%d.jpg" % number, [SAM_T] if number < 3 else [SAM_I]))
        by_name = self.tuner_client.get("/library/api/unmatched-faces/person-matches", query_string={"name": "Sam"})
        self.assertEqual(400, by_name.status_code, by_name.get_data(as_text=True))
        self.assertIn(SAM_I, by_name.get_json()["error"])
        status = self.tuner_client.get("/library/api/unmatched-faces/build-status", query_string={"name": "Sam"})
        self.assertEqual(200, status.status_code)
        from tagpup.services import identify as identify_service
        faces_by_key = identify_service.for_pages(self.library, {self.sam_t: 11, self.sam_i: 12})
        self.assertEqual({"id:%d" % self.sam_t: 11, "id:%d" % self.sam_i: 12}, faces_by_key, "a shared name keys neither")
        write = self.match(self.first, person_name="Sam")
        self.assertEqual(400, write.status_code, "a write with the shared name still names the candidates")
        self.assertIn(SAM_I, write.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
