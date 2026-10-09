"""People by id, stage 2, phase 4a (docs/ARCHITECTURE.md, "People by id, stage 2"): the id is the person, the name beside it a cache.

Two cousins called Sam under two groups, a dog and a friend called Max. Each case is a way it goes wrong: a name two people
have, an id that is nobody's any more, a group put forward as a person, two of one leaf on one photo, a keyword path that
names exactly one of them, a rename between a naming and its undo.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import MAX_FRIEND, MAX_PET, SAM_I, SAM_T, TwoSams, look, write  # noqa: E402

from tagpup.core.result import NotFound  # noqa: E402
from tagpup.services import faces as faces_service  # noqa: E402
from tagpup.store import faces, people, person_ids, taxonomy  # noqa: E402


class Writers(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)

    def test_naming_by_id_names_exactly_that_person(self):
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_i))
        self.assertEqual((self.sam_i, "Sam", "manual"), self.face(self.faces[0]))
        self.assertEqual([(self.sam_t, "Sam", "keyword"), (self.sam_i, "Sam", "face")], self.listed(self.photo))

    def test_a_bare_name_two_people_have_is_refused_naming_them(self):
        result = faces_service.name_face(self.library, self.faces[0], "Sam")
        self.assertIn(SAM_T, result.refused)
        self.assertIn(SAM_I, result.refused)
        self.assertEqual((None, None, None), self.face(self.faces[0]), "nothing was written")

    def test_a_tag_path_is_a_tag_and_not_a_name(self):
        """The id is how a page names one of two people alike; a path typed as a name is refused as any name with a "/" is."""
        result = faces_service.name_face(self.library, self.faces[0], SAM_I)
        self.assertIn("/", result.refused)
        self.assertEqual((None, None, None), self.face(self.faces[0]))

    def test_a_stale_id_is_not_found_and_makes_nobody(self):
        before = look(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0]
        with self.assertRaises(NotFound):
            faces_service.name_face(self.library, self.faces[0], 99999)
        self.assertEqual(before, look(self.path, "SELECT COUNT(*) FROM tag_taxonomy")[0][0])
        self.assertEqual((None, None, None), self.face(self.faces[0]))

    def test_a_group_is_not_a_person(self):
        group = self.node("Family/Thackeray")
        result = faces_service.name_face(self.library, self.faces[0], group)
        self.assertIn("Family/Thackeray is a group of people, not a person", result.refused)
        self.assertEqual((None, None, None), self.face(self.faces[0]))
        by_name = faces_service.name_face(self.library, self.faces[0], "Thackeray")
        self.assertIn("group of people", by_name.refused, "a bare name only a group has is refused too")

    def test_a_photo_lists_two_people_with_one_leaf(self):
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1]], self.sam_i))
        self.assertEqual([(self.sam_t, "Sam", "keyword"), (self.sam_i, "Sam", "face")], self.listed(self.photo))
        self.assertEqual('["Sam","Sam"]', look(self.path, "SELECT json_group_array(name) FROM (SELECT name FROM photo_people"
                                                          " WHERE photo_id = (SELECT id FROM photos WHERE path = ?)"
                                                          " ORDER BY position)", (self.photo,))[0][0])

    def test_one_face_of_each_is_not_a_refusal_but_the_same_person_twice_is(self):
        faces_service.name_face(self.library, self.faces[0], self.sam_t)
        again = faces_service.name_face(self.library, self.faces[1], self.sam_t)
        self.assertIn("already tagged on another face", again.refused)
        done = faces_service.name_face(self.library, self.faces[1], self.sam_i)
        self.assertEqual(1, done.changed)

    def test_a_keyword_path_gives_the_exact_person_and_a_bare_one_nobody(self):
        """The photo's keyword is Sam Thackeray's PATH: the list holds his id. A bare 'Sam' keyword, which two people are
        called, is the name alone; beside a face that says which, it is that person's."""
        from tagpup.store import photos
        photos.record_tags(self.path, self.other, ["Sam"])
        self.assertEqual([(None, "Sam", "keyword")], self.listed(self.other))
        write(self.path, lambda conn: faces.name(conn, [self.faces[2]], self.sam_i))
        self.assertEqual([(self.sam_i, "Sam", "keyword")], self.listed(self.other))

    def test_a_pet_and_a_friend_called_alike_are_told_apart(self):
        max_pet, max_friend = self.node(MAX_PET), self.node(MAX_FRIEND)
        write(self.path, lambda conn: faces.set_names(conn, {self.faces[0]: max_pet, self.faces[2]: max_friend}))
        self.assertEqual([(max_pet, "Max"), (max_friend, "Max")],
                         [self.face(self.faces[0])[:2], self.face(self.faces[2])[:2]])
        conn = _conn(self.path)
        try:
            self.assertEqual({max_pet}, person_ids.given(conn, [max_pet]))
            self.assertEqual({max_pet: 1, max_friend: 1}, person_ids.faces_using(conn, [max_pet, max_friend]))
            self.assertEqual(1, faces.count_named(conn, max_pet), "a person's faces are counted by the node, not the name")
        finally:
            conn.close()

    def test_unnaming_clears_both(self):
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        write(self.path, lambda conn: faces.unname(conn, [self.faces[0]]))
        self.assertEqual((None, None, "manual"), self.face(self.faces[0]))

    def test_a_guess_and_taking_it_back_are_by_id(self):
        write(self.path, lambda conn: faces.name_unnamed(conn, {self.faces[0]: self.sam_i, self.faces[2]: self.sam_t}))
        self.assertEqual((self.sam_i, "Sam", None), self.face(self.faces[0]))
        reverted = write(self.path, lambda conn: faces.revert_automatic(conn, {self.faces[0]: self.sam_t}))
        self.assertEqual([], reverted, "it is not Sam Thackeray's guess")
        reverted = write(self.path, lambda conn: faces.revert_automatic(conn, {self.faces[0]: self.sam_i}))
        self.assertEqual([self.faces[0]], reverted)
        self.assertEqual((None, None, None), self.face(self.faces[0]))

    def test_a_removed_person_is_not_given_back(self):
        prior = {self.faces[0]: (self.sam_i, "manual")}
        write(self.path, lambda conn: taxonomy.remove_node(conn, SAM_I))
        self.assertEqual([], write(self.path, lambda conn: faces.reinstate(conn, prior)))
        self.assertEqual((None, None, None), self.face(self.faces[0]))

    def test_a_detected_face_given_a_name_two_people_have_is_refused_not_guessed(self):
        with self.assertRaises(person_ids.AmbiguousPerson):
            write(self.path, lambda conn: faces.insert(conn, self.other, [5, 5, 9, 9], b"\x03" * 8, name="Sam"))


def _conn(path):
    from tagpup.store import db
    return db.connect(db.readonly_uri(path), uri=True)


class TheCacheFollowsTheNode(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1]], self.sam_i))

    def test_a_rename_changes_the_name_and_never_the_id(self):
        write(self.path, lambda conn: taxonomy.move_branch(conn, SAM_I, "Family/Ingersoll/Samuel"))
        self.assertEqual((self.sam_i, "Samuel", "manual"), self.face(self.faces[1]))
        self.assertEqual((self.sam_t, "Sam", "manual"), self.face(self.faces[0]), "the other Sam is not renamed")
        self.assertEqual([(self.sam_t, "Sam", "keyword"), (self.sam_i, "Samuel", "face")], self.listed(self.photo))

    def test_a_move_between_groups_keeps_the_id_and_writes_no_face(self):
        generation = look(self.path, "SELECT value FROM generations WHERE name = 'faces'")[0][0]
        write(self.path, lambda conn: taxonomy.move_branch(conn, SAM_I, "Family/Thackeray/Samwise"))
        self.assertEqual((self.sam_i, "Samwise", "manual"), self.face(self.faces[1]))
        self.assertEqual(self.sam_i, self.node("Family/Thackeray/Samwise"))
        move = write(self.path, lambda conn: taxonomy.move_branch(conn, "Family/Thackeray/Samwise", "Friends/Samwise"))
        self.assertEqual(1, move)
        self.assertEqual(self.sam_i, self.face(self.faces[1])[0])
        self.assertGreater(look(self.path, "SELECT value FROM generations WHERE name = 'faces'")[0][0], generation)

    def test_a_name_written_without_the_id_does_not_change_whom_a_face_names(self):
        """The id is the key: a name written by something that knows nothing of ids is a cache the next settle puts right."""
        write(self.path, lambda conn: conn.execute("UPDATE faces SET name = 'Max' WHERE id = ?", (self.faces[0],)))
        self.assertEqual(1, person_ids.out_of_step(_conn(self.path), "faces").rows)
        person_ids.repair(self.path)
        self.assertEqual((self.sam_t, "Sam", "manual"), self.face(self.faces[0]))

    def test_a_second_node_of_a_name_does_not_take_the_id_away(self):
        write(self.path, lambda conn: taxonomy.add_node(conn, "Friends/Sam"))
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0])
        write(self.path, lambda conn: taxonomy.remove_node(conn, "Friends/Sam"))
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0])


class ThePhotosList(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()

    def test_the_rule_lists_each_person_once_by_id(self):
        sam_t = self.node(SAM_T)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], sam_t))
        write(self.path, lambda conn: faces.name_unnamed(conn, {self.faces[1]: sam_t}))   # unreachable: one photo, one person
        self.assertEqual([(sam_t, "Sam", "keyword")], self.listed(self.photo))

    def test_the_rebuild_the_doctor_runs_changes_nothing_when_it_is_in_step(self):
        sam_t = self.node(SAM_T)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], sam_t))
        self.assertEqual([], write(self.path, lambda conn: people.stale(conn)))


if __name__ == "__main__":
    unittest.main()
