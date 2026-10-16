"""Scenario P6, two cousins called alike (docs/ARCHITECTURE.md, "People by id, stage 2"): two cousins called Sam under two groups,
a dog and a friend called Max, one photo, two faces.

Naming a face by id names exactly that person; a typed bare 'Sam' is refused naming both paths; a group is refused as a person;
both cousins on one photo get both tags; every refusal writes nothing; and a bare keyword is a tag of its own that names nobody
(DECISIONS 2026-10-11, 2026-10-15: no write rewrites one into a person's path). The shared leaf is live: photo_index.db holds two
leaf names that two people have (counted 2026-10-15).

Lifted: test_people_by_id_writers.py (Writers, TheCacheFollowsTheNode, ThePhotosList: 18 tests; the renames and moves are
test_people_by_id_tree.py's), test_two_people_one_leaf_on_a_photo.py (this file, the route tests kept), test_an_ambiguous_person_gets_no_path.py
(#27, the last class). Mutation, each failing the test named: person_ids.People.person falling back to the leaf when no id is given
(a name two people have answers with the first) -> test_every_refusal_writes_nothing and the route tests that refuse a shared name;
vocabulary.names_same_person comparing leaves always -> test_both_cousins_on_one_photo_get_both_tags and
test_unnaming_one_takes_only_their_tag; the bare-keyword test fails on the code before this commit (git archive of HEAD).

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
from face_rows import add_face  # noqa: E402

from people_by_id import MAX_FRIEND, MAX_PET, SAM_I, SAM_T, TwoSams, look, write  # noqa: E402
from test_face_clustering_rules import identity_vector as clustering_vector, near as near_vector  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402
from test_organize_faces import WREN  # noqa: E402

import numpy as np  # noqa: E402
from unittest import mock  # noqa: E402

from tagpup.core import clustering  # noqa: E402
from tagpup.core.result import NotFound  # noqa: E402
from tagpup.services import faces as faces_service  # noqa: E402
from tagpup.services import inspect, tagging  # noqa: E402
from tagpup.services.suggester import TagSuggester  # noqa: E402
from tagpup.core.vocabulary import Ref  # noqa: E402
from tagpup.store import db, faces, people, person_ids, removals, schema, taxonomy  # noqa: E402
from tagpup.store.taxonomy import TagTaxonomy  # noqa: E402


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

    def test_an_old_page_with_only_a_name_one_person_has_still_works_and_a_loose_row_is_nobodys(self):
        """An open page sends a name until it is reloaded: for a name one person has it works, as a read and a write. A face named
        only 'Sam' with no id (an automatic guess from before ids) is in neither Sam's page, however it is asked for."""
        def wren(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People", root_has_face=1)
                taxonomy.add_path(conn, "People/Wren Halloway")
            return add_face(conn, self.photo_path, box=(120, 10, 160, 60), name="Sam", name_source="auto", tag_id=None)
        loose = db.write_with_connection(self.path, wren)
        self.assertEqual((None, "Sam"), self.row(loose)[3:4] + self.row(loose)[0:1], "the guess has a name and no id")
        node = self.node("People/Wren Halloway")
        written = self.match(self.first, person_name="Wren Halloway")
        self.assertEqual(200, written.status_code, written.get_json())
        self.assertEqual(node, self.row(self.first)[3])
        read = self.tuner_client.get("/library/api/person-faces", query_string={"name": "Wren Halloway", "limit": -1})
        self.assertEqual([self.first], [face["id"] for face in read.get_json()["faces"]])
        for sam in (self.sam_t, self.sam_i):
            page = self.tuner_client.get("/library/api/person-faces", query_string={"person_id": sam, "limit": -1}).get_json()
            self.assertNotIn(loose, [face["id"] for face in page["faces"]], "a name alone is nobody's face")
        by_name = self.tuner_client.get("/library/api/person-faces", query_string={"name": "Sam", "limit": -1})
        self.assertEqual(400, by_name.status_code, "and asking for the name is asked which one")

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


class ABareKeywordNamesNobody(InStep):
    def test_an_edit_leaves_a_bare_keyword_as_it_is_beside_the_person_of_that_name(self):
        """A bare keyword beside the person of that name: the writer rewrote it into their path, and a photo the owner had
        tagged by hand gained the person (and a people row) with no click."""
        photo = self.held("quay_20.jpg", [WREN])
        done = tagging.change_tags(self.library, [photo], ["Regatta"], [], "exiftool")
        self.assertEqual(1, done.changed, done.errors)
        self.assertEqual({WREN, "Regatta"}, set(self.tags(photo)), "no person's path was added")
        self.assertEqual([], self.indexed_people(), "and no people row")


class TwoSamsInTheStore(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)

    def counts(self):
        return look(self.path, "SELECT (SELECT COUNT(*) FROM tag_taxonomy), (SELECT COUNT(*) FROM photo_people),"
                               " (SELECT COUNT(*) FROM faces WHERE tag_id IS NOT NULL OR name IS NOT NULL)")[0]

    def test_naming_by_id_names_exactly_that_person(self):
        faces_service.name_face(self.library, self.faces[0], self.sam_i)
        self.assertEqual((self.sam_i, "Sam", "manual"), self.face(self.faces[0]))
        self.assertEqual([(self.sam_t, "Sam", "keyword"), (self.sam_i, "Sam", "face")], self.listed(self.photo),
                         "the photo lists two people with one leaf, each by id")
        again = faces_service.name_face(self.library, self.faces[1], self.sam_i)
        self.assertIn("already tagged on another face", again.refused)
        self.assertEqual(1, faces_service.name_face(self.library, self.faces[1], self.sam_t).changed)
        self.assertEqual(1, look(self.path, "SELECT COUNT(*) FROM faces WHERE tag_id = ?", (self.sam_i,))[0][0], "counted by node")

    def test_every_refusal_writes_nothing(self):
        before = self.counts()
        by_name = faces_service.name_face(self.library, self.faces[0], "Sam")
        self.assertTrue(SAM_T in by_name.refused and SAM_I in by_name.refused, by_name.refused)
        self.assertIn("/", faces_service.name_face(self.library, self.faces[0], SAM_I).refused, "a path is a tag, not a name")
        group = faces_service.name_face(self.library, self.faces[0], self.node("Family/Thackeray"))
        self.assertIn("Family/Thackeray is a group of people, not a person", group.refused)
        self.assertIn("group of people", faces_service.name_face(self.library, self.faces[0], "Thackeray").refused)
        with self.assertRaises(NotFound):
            faces_service.name_face(self.library, self.faces[0], 99999)
        with self.assertRaises(person_ids.AmbiguousPerson):
            write(self.path, lambda conn: faces.insert(conn, self.other, [5, 5, 9, 9], b"\x03" * 8, name="Sam"))
        with self.assertRaises(ValueError):
            write(self.path, lambda conn: faces.name(conn, [self.faces[0]], "Vessels/Gull"))
        self.assertEqual(before, self.counts())

    def test_a_bare_keyword_names_neither_sam_and_a_face_that_says_which_makes_the_person_the_photos(self):
        photo_rows.tag(self.path, self.other, ["Sam"])
        self.assertEqual([], self.listed(self.other))
        faces_service.name_face(self.library, self.faces[2], self.sam_i)
        self.assertEqual([(self.sam_i, "Sam", "face")], self.listed(self.other))

    def test_a_pet_and_a_friend_called_alike_are_told_apart_and_a_guess_is_taken_back_by_id(self):
        pet, friend = self.node(MAX_PET), self.node(MAX_FRIEND)
        write(self.path, lambda conn: faces.set_names(conn, {self.faces[0]: pet, self.faces[2]: friend},
                                                      {self.faces[0]: None, self.faces[2]: None}))
        self.assertEqual([(pet, "Max"), (friend, "Max")], [self.face(self.faces[0])[:2], self.face(self.faces[2])[:2]])
        write(self.path, lambda conn: faces.name_unnamed(conn, {self.faces[1]: self.sam_i}))
        self.assertEqual([], write(self.path, lambda conn: faces.revert_automatic(conn, {self.faces[1]: self.sam_t})),
                         "it is not Sam Thackeray's guess")
        self.assertEqual([self.faces[1]], write(self.path, lambda conn: faces.revert_automatic(conn, {self.faces[1]: self.sam_i})))

    def test_the_id_is_the_key_and_the_name_a_cache_the_next_settle_puts_right(self):
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        write(self.path, lambda conn: conn.execute("UPDATE faces SET name = 'Max' WHERE id = ?", (self.faces[0],)))
        person_ids.repair(self.path)
        self.assertEqual((self.sam_t, "Sam", "manual"), self.face(self.faces[0]))
        write(self.path, lambda conn: taxonomy.add_node(conn, "Friends/Sam"))
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Friends/Sam"))
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0], "a second node of the name does not take the id away")


TWICE = {"People/Harbour/Rowan Thackeray", "People/School/Rowan Thackeray", "People/Imogen Vale"}


def tree_of(tag_paths):
    tree = TagTaxonomy(os.path.join(os.path.dirname(os.path.abspath(__file__)), "no such library.db"))
    tree.paths = set(tag_paths)
    return tree


def unit(vector):
    vector = np.asarray(vector, dtype=np.float32)
    return vector / np.linalg.norm(vector)


class NoIndex:
    conn = None
    db_path = "no such library.db"

    def search(self, embedding, k=15):
        return []


class OneFace:
    def ready(self):
        """The run's turn on the graphics card (tagpup.runtime._OnTheCard): nothing to wait for here."""

    def detect_and_embed_faces(self, path):
        return [{"box": [0, 0, 100, 100], "embedding": unit([1, 0, 0]).tolist(), "prob": 0.99}]


class AFilingTwiceIsSuggestedByTheNode(unittest.TestCase):
    """docs/findings.md, #27: a lookup of a person's path returned the first path it met in a set, so a person filed twice was
    suggested under one path on one run and the other on the next. The suggester takes the tag of the person's node."""

    def test_a_name_filed_twice_has_both_paths_and_once_its_path(self):
        self.assertEqual(["People/Harbour/Rowan Thackeray", "People/School/Rowan Thackeray"],
                         tree_of(TWICE).person_paths("Rowan Thackeray"))
        self.assertEqual(["People/Imogen Vale"], tree_of(TWICE).person_paths("imogen vale"))

    def suggested(self, tags_by_id, person_id):
        known = clustering.KnownFaces.of([(person_id, unit([1, 0, 0]), None, r"D:\Pictures\Before\a.jpg",
                                           Ref(person_id, "Imogen Vale"))])
        run = TagSuggester(NoIndex(), tree_of(TWICE), faces=OneFace())
        with mock.patch.object(faces_service, "known_faces", return_value=known), \
                mock.patch.object(TagSuggester, "_tags_by_id", return_value=tags_by_id), \
                mock.patch.object(faces_service, "record_detected", return_value=0):
            result = run.suggest_for_photo(r"D:\Pictures\Run\01.jpg", [1.0, 0.0, 0.0])
        return [t["tag"] for t in result["suggested_tags"]]

    def test_a_face_of_a_person_the_tree_files_is_offered_under_their_path(self):
        by_id = {7: "People/Imogen Vale", 8: "People/School/Imogen Vale"}
        self.assertIn("People/Imogen Vale", self.suggested(by_id, 7))
        self.assertIn("People/School/Imogen Vale", self.suggested(by_id, 8))


class TwoSamsAreGuessedApart(unittest.TestCase):
    """The indexer's clustering (identities.resolve) when two people share a leaf under two groups, as photo_index does (two
    leaves, four reference faces, counted 2026-10-15): Family/Sam Ashby and Friends/Sam Ashby each have two faces named by hand
    and photos whose keywords name them, with faces nobody named. Each such face is guessed as the person it looks like and
    its photo names -- never as the other Sam -- and the run says it wrote exactly the faces that changed.

    Fails on the code before it (docs/findings.md #1357): clustering keyed people by NAME, pooled the two into one "Sam
    Ashby", whose save then resolved to nobody (a name two people have), so neither was ever guessed; and each nameless face
    was written nobody over nobody and counted in "Wrote N face(s)"."""

    FAMILY, FRIENDS = "Family/Sam Ashby", "Friends/Sam Ashby"

    def setUp(self):
        self.home = own_home.for_test(self)
        self.path = self.home.library("harbour.db")
        schema.ensure(self.path)
        folder = os.path.join(os.path.dirname(self.path), "Pictures")
        looks = {self.FAMILY: clustering_vector(1), self.FRIENDS: clustering_vector(2)}

        def seed(conn):
            with people.tree_edit(conn):
                for root_tag in ("Family", "Friends"):
                    taxonomy.add_path(conn, root_tag, root_has_face=1)
                for tag in looks:
                    taxonomy.add_path(conn, tag)
            made = {}

            def photo(name, keywords, *faces_of):
                path = os.path.join(folder, name)
                photo_rows.add_read(conn, path, {"XMP:Subject": list(keywords)})
                return [faces.insert(conn, path, [120 * n, 0, 120 * n + 100, 100], near_vector(looks[who], jitter).tobytes())
                        for n, (who, jitter) in enumerate(faces_of)]

            for tag, offset in ((self.FAMILY, 10), (self.FRIENDS, 20)):
                references = photo("ref_%d_a.jpg" % offset, [tag], (tag, offset + 1)) + photo(
                    "ref_%d_b.jpg" % offset, [tag], (tag, offset + 2))
                faces.name(conn, references, conn.execute("SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,)).fetchone()[0])
                made[tag] = photo("new_%d.jpg" % offset, [tag], (tag, offset + 3))
            made["both"] = photo("both.jpg", [self.FAMILY, self.FRIENDS], (self.FAMILY, 31), (self.FRIENDS, 32))
            return made

        self.made = db.write_with_connection(self.path, seed)
        self.family, self.friends = (look(self.path, "SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))[0][0]
                                     for tag in (self.FAMILY, self.FRIENDS))

    def faces_now(self):
        return dict(((face_id, (tag_id, name)) for face_id, tag_id, name in look(self.path, "SELECT id, tag_id, name FROM faces")))

    def test_each_sam_is_guessed_on_their_own_photos_and_never_as_the_other(self):
        from tagpup.services import identities
        from tagpup.services.search import PhotoIndex
        before = self.faces_now()
        index = PhotoIndex(db_path=self.path)
        index.load()
        try:
            with self.assertLogs("tagpup_cli.faces", "INFO") as heard:
                stats = identities.resolve(index)
        finally:
            index.close()
        after = self.faces_now()
        family_face, friends_face = self.made[self.FAMILY][0], self.made[self.FRIENDS][0]
        left, right = self.made["both"]

        self.assertEqual({family_face: self.family, left: self.family, friends_face: self.friends, right: self.friends},
                         {face_id: after[face_id][0] for face_id in (family_face, left, friends_face, right)},
                         "each Sam is guessed on the faces that look like them and their photos name")
        mixed = look(self.path, "SELECT COUNT(*) FROM faces WHERE tag_id = ? AND id IN (?, ?)", (self.friends, family_face, left))
        self.assertEqual(0, mixed[0][0], "a guess for one Sam never lands on the other")
        self.assertEqual({self.FAMILY: 2, self.FRIENDS: 2}, stats, "the summary names each person by their path")
        changed = sum(1 for face_id in after if after[face_id] != before[face_id])
        wrote = [line for line in heard.output if "Wrote " in line]
        self.assertEqual(["INFO:tagpup_cli.faces:Wrote %d face(s); 0 left as they were." % changed], wrote,
                         "the run counts the faces it changed, not the faces it meant")

    def test_clustering_s_write_takes_a_person_s_id_and_refuses_a_name_before_it_writes(self):
        """The write path resolves by id (the People charter's invariant): a name, or a tag path, is never matched to one of
        the two Sams by chance; it is refused and nothing is written."""
        face_id = self.made[self.FAMILY][0]
        before = self.faces_now()
        for named in ("Sam Ashby", self.FAMILY):
            with self.assertRaises(TypeError):
                write(self.path, lambda conn: faces.set_names(conn, {face_id: named}, {face_id: None}))
        self.assertEqual(before, self.faces_now(), "a refused write wrote nothing")


if __name__ == "__main__":
    unittest.main()
