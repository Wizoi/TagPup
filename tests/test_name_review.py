"""People by id, stage 2, phase 7 (docs/ARCHITECTURE.md, "Names to review"; docs/findings.md, #985): the list of names that no person's
tag is, and the owner's four choices for one -- make a person, link the name to one, unname the faces, set it aside.

Nothing is converted by itself: reading the list writes nothing, and each choice is one journaled change run by hand (a rehearsal
first). Two cousins called Sam under two groups, a person typed with a slip of the pen, a name no tag has.

Failure modes: a choice interrupted in the middle (all or nothing), two windows choosing at once, a stale person or group, a name settled
elsewhere meanwhile, a dismissed name that gains rows.
"""
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import MAX_FRIEND, MAX_PET, SAM_I, SAM_T, WREN, TwoSams, look, write  # noqa: E402

from tagpup.core.result import NotFound, Refused  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import inspect, name_review  # noqa: E402
from tagpup.store import journal, people, taxonomy  # noqa: E402

QUILL = "Wren Quill"          # a name no tag has
SLIP = "Wren Halowway"        # a slip of the pen for WREN's name


def name_without_a_person(conn, face_ids, name, source="manual"):
    """Faces named as an older version of the app, or a copied library, left them: a name, no id."""
    for face_id in face_ids:
        conn.execute("UPDATE faces SET name = ?, tag_id = NULL, name_source = ? WHERE id = ?", (name, source, face_id))
    people.rebuild_photos(conn, [path for (path,) in conn.execute(
        "SELECT DISTINCT p.path FROM faces f JOIN photos p ON p.id = f.photo_id WHERE f.id IN (%s)"
        % ",".join("?" * len(face_ids)), list(face_ids))])


class NamesToReview(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        self.exiftool = "exiftool"
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[0]], QUILL))
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[2]], QUILL, None))
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "Sam", None))

    def listed_entry(self, name, **more):
        found = name_review.entries(self.library, **more)
        return next((each for each in found["entries"] if each["name"] == name), None)

    def rows_of(self, sql, params=()):
        return look(self.path, sql, params)

    def changes(self, operation):
        return [row[0] for row in self.rows_of("SELECT id FROM changes WHERE operation = ?", (operation,))]

    # ---- the list --------------------------------------------------------------------------------

    def test_the_list_holds_a_name_no_tag_has_and_a_name_two_tags_have_with_what_each_could_mean(self):
        found = name_review.entries(self.library)
        self.assertEqual(2, found["count"])
        quill = self.listed_entry(QUILL)
        self.assertEqual(("none", 2, 1, 2), (quill["why"], quill["faces"], quill["faces_by_hand"], quill["listed"]))
        self.assertEqual(sorted([self.faces[0], self.faces[2]]), quill["face_ids"])
        sam = self.listed_entry("Sam")
        self.assertEqual(("several", 1), (sam["why"], sam["faces"]))
        self.assertEqual({SAM_T, SAM_I}, {person["tag"] for person in sam["candidates"]})
        self.assertTrue(all(person["shared"] for person in sam["candidates"]), "the page labels each with its group")
        self.assertEqual({"Family", "Pets", "Friends", "People", "Family/Thackeray", "Family/Ingersoll"},
                         {group["tag"] for group in found["groups"]}, "a person is no place for a person")

    def test_a_name_close_to_a_person_offers_them(self):
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[2]], SLIP))
        slip = self.listed_entry(SLIP)
        self.assertEqual([WREN], [person["tag"] for person in slip["candidates"]])

    def test_reading_the_list_writes_nothing(self):
        before = (self.rows_of("SELECT id, name, tag_id, name_source FROM faces ORDER BY id"),
                  self.rows_of("SELECT COUNT(*) FROM changes"), self.rows_of("SELECT COUNT(*) FROM tag_taxonomy"))
        name_review.entries(self.library)
        name_review.count(self.library)
        name_review.resolve(self.library, QUILL, name_review.LINK, person_id=self.node(WREN))   # a rehearsal
        name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Friends"))
        name_review.resolve(self.library, QUILL, name_review.UNNAME)
        self.assertEqual(before, (self.rows_of("SELECT id, name, tag_id, name_source FROM faces ORDER BY id"),
                                  self.rows_of("SELECT COUNT(*) FROM changes"), self.rows_of("SELECT COUNT(*) FROM tag_taxonomy")))

    def test_a_listed_row_naming_a_group_is_the_rebuilds_not_an_entry(self):
        def stale(conn):
            photo_id = conn.execute("SELECT id FROM photos WHERE path = ?", (self.photo,)).fetchone()[0]
            conn.execute("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, 99, 'Thackeray', 'keyword', NULL)",
                         (photo_id,))
        write(self.path, stale)
        found = name_review.entries(self.library)
        self.assertEqual(2, found["count"])
        self.assertEqual(1, found["stale_group_rows"])
        self.assertIsNone(self.listed_entry("Thackeray"))

    # ---- make a person ---------------------------------------------------------------------------

    def test_make_a_person_under_the_group_picked_links_the_name_and_is_one_change_the_history_undoes(self):
        rehearsal = name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Friends"))
        self.assertTrue(rehearsal.ok, rehearsal.message())
        self.assertFalse(rehearsal.details["applied"])
        self.assertIn("Friends/Wren Quill", rehearsal.details["sentence"])
        self.assertIsNone(self.node("Friends/Wren Quill"))
        done = name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Friends"), apply=True)
        self.assertTrue(done.ok, done.message())
        made = self.node("Friends/Wren Quill")
        self.assertIsNotNone(made)
        self.assertEqual(2, done.changed, "the faces linked, counted after the write")
        self.assertEqual((made, QUILL, "manual"), self.face(self.faces[0]))
        self.assertEqual((made, QUILL, None), self.face(self.faces[2]))
        self.assertIsNone(self.listed_entry(QUILL))
        change = self.changes(journal.PERSON_MADE)
        self.assertEqual([done.details["change"]], change)
        undone = journal_service.undo(self.library, change[0], apply=True, exiftool_path=self.exiftool)
        self.assertEqual([], undone.errors)
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))
        self.assertEqual("one", self.listed_entry(QUILL)["why"], "the tag stays in the tree; the name is a tag's again, unlinked")

    def test_make_is_only_for_a_name_no_tag_has_and_under_a_group_that_is_there(self):
        refused = name_review.resolve(self.library, "Sam", name_review.MAKE, group_id=self.node("Friends"), apply=True)
        self.assertIn("link the name to that person", refused.refused)
        with self.assertRaises(NotFound):
            name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node(SAM_T), apply=True)   # a person is no group
        with self.assertRaises(NotFound):
            name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=99999, apply=True)
        with self.assertRaises(NotFound):
            name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=None, apply=True)
        self.assertIsNone(self.node("Friends/Wren Quill"))
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))

    # ---- link to a person ------------------------------------------------------------------------

    def test_link_a_name_to_the_person_picked_gives_the_faces_their_id_and_name_in_one_change(self):
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[2]], SLIP))
        wren = self.node(WREN)
        done = name_review.resolve(self.library, SLIP, name_review.LINK, person_id=wren, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual((wren, "Wren Halloway", "manual"), self.face(self.faces[2]))
        self.assertIn((wren, "Wren Halloway", "face"), self.listed(self.other))
        self.assertIsNone(self.listed_entry(SLIP))
        self.assertEqual(1, len(self.changes(journal.PERSON_LINKED)))
        self.assertEqual(0, done.details["keywords_kept"], "only a face held the name: no file keeps it")

    def test_link_one_of_two_cousins_called_alike_is_the_one_picked(self):
        sam_i = self.node(SAM_I)
        done = name_review.resolve(self.library, "Sam", name_review.LINK, person_id=sam_i, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual((sam_i, "Sam"), self.face(self.faces[1])[:2])
        self.assertIsNone(self.listed_entry("Sam"))

    def test_link_to_a_person_that_is_gone_or_a_group_is_refused_and_writes_nothing(self):
        with self.assertRaises(NotFound):
            name_review.resolve(self.library, QUILL, name_review.LINK, person_id=99999, apply=True)
        with self.assertRaises(Refused) as why:
            name_review.resolve(self.library, QUILL, name_review.LINK, person_id=self.node("Family/Thackeray"), apply=True)
        self.assertIn("group", str(why.exception))
        with self.assertRaises(NotFound):
            name_review.resolve(self.library, QUILL, name_review.LINK, apply=True)
        self.assertEqual([], self.changes(journal.PERSON_LINKED))
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))

    # ---- unname ----------------------------------------------------------------------------------

    def test_unname_the_faces_returns_them_to_the_faces_waiting_and_the_history_undoes_it(self):
        done = name_review.resolve(self.library, QUILL, name_review.UNNAME, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(2, done.changed)
        self.assertEqual((None, None, None), self.face(self.faces[0]), "not 'nobody': Identify Faces offers them again")
        self.assertIsNone(self.listed_entry(QUILL))
        change = self.changes(journal.NAME_UNNAMED)
        self.assertEqual(1, len(change))
        journal_service.undo(self.library, change[0], apply=True, exiftool_path=self.exiftool)
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))
        self.assertIsNotNone(self.listed_entry(QUILL))

    # ---- set aside -------------------------------------------------------------------------------

    def test_a_name_set_aside_is_hidden_and_comes_back_when_it_gains_rows_or_is_restored(self):
        name_review.resolve(self.library, QUILL, name_review.DISMISS, apply=True)
        found = name_review.entries(self.library)
        self.assertEqual((1, 1), (found["count"], found["dismissed"]))
        self.assertIsNone(self.listed_entry(QUILL))
        self.assertTrue(self.listed_entry(QUILL, include_dismissed=True)["dismissed"])
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], QUILL))   # a new row (it was Sam's)
        self.assertEqual(1, name_review.count(self.library))
        self.assertEqual(3, self.listed_entry(QUILL)["faces"], "it gained rows: new work")
        name_review.resolve(self.library, QUILL, name_review.DISMISS, apply=True)
        self.assertEqual(0, name_review.count(self.library))
        name_review.resolve(self.library, QUILL, name_review.RESTORE, apply=True)
        self.assertEqual(1, name_review.count(self.library))
        self.assertEqual([], self.changes(journal.PERSON_LINKED), "a preference, not a change of a face")

    def test_a_library_that_cannot_remember_what_was_set_aside_says_so_and_changes_nothing(self):
        write(self.path, lambda conn: conn.execute("DROP TABLE name_review_dismissals"))
        with mock.patch("tagpup.store.schema.ensure"):
            done = name_review.resolve(self.library, QUILL, name_review.DISMISS, apply=True)
        self.assertIn("not been brought up to date", done.refused)
        self.assertFalse(done.details["applied"])
        self.assertEqual(2, name_review.count(self.library), "nothing was set aside")

    # ---- where the owner and the tools see the count ---------------------------------------------

    def test_the_doctor_and_the_tools_count_the_names_and_name_them_only_when_asked(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
        import doctor
        said = io.StringIO()
        with redirect_stdout(said):
            doctor.report(self.path)
        self.assertIn("names to review: 2 waiting, 0 set aside", " ".join(said.getvalue().split()))
        counted = inspect.all_checks(self.library)["names_to_review"]
        self.assertEqual((2, 0, {"none": 1, "several": 1}, 5), (counted["waiting"], counted["set_aside"], counted["by_reason"], counted["rows"]))
        self.assertNotIn("name", counted["entries"][0], "counts only, names with reveal")
        self.assertNotIn(QUILL, str(counted))
        self.assertIn(QUILL, [each["name"] for each in inspect.all_checks(self.library, reveal=True)["names_to_review"]["entries"]])
        name_review.resolve(self.library, QUILL, name_review.DISMISS, apply=True)
        self.assertEqual((1, 1), (inspect.all_checks(self.library)["names_to_review"]["waiting"],
                                  inspect.all_checks(self.library)["names_to_review"]["set_aside"]))

    def test_a_name_in_two_spellings_is_one_entry_and_a_link_takes_both(self):
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "wren quill", None))
        found = name_review.entries(self.library)
        quill = next(each for each in found["entries"] if each["key"] == "wren quill")
        self.assertEqual((3, ["Wren Quill", "wren quill"]), (quill["faces"], quill["spellings"]))
        done = name_review.resolve(self.library, "wren quill", name_review.LINK, person_id=self.node(WREN), apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual({(self.node(WREN), "Wren Halloway")}, {self.face(each)[:2] for each in self.faces})
        self.assertEqual(1, len(self.changes(journal.PERSON_LINKED)), "one change for the name, whatever its spellings")

    def test_each_library_has_its_own_list_and_its_own_set_aside_names(self):
        from tagpup.core.library import Library
        from tagpup.store import schema
        other_path = self.home.library("second.db")
        schema.ensure(other_path)
        other = Library(other_path)
        name_review.resolve(self.library, QUILL, name_review.DISMISS, apply=True)
        self.assertEqual(1, name_review.count(self.library))
        self.assertEqual(0, name_review.count(other), "another library holds none of this library's names")
        self.assertEqual([], look(other_path, "SELECT * FROM name_review_dismissals"))

    def test_a_person_renamed_while_the_list_is_open_is_still_the_one_chosen(self):
        wren = self.node(WREN)
        name_review.entries(self.library)               # the dialog is open, and offers Wren Halloway
        write(self.path, lambda conn: taxonomy.move_branch(conn, WREN, "People/Wren Ashdown"))      # renamed in another window
        done = name_review.resolve(self.library, QUILL, name_review.LINK, person_id=wren, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertIn("People/Wren Ashdown", done.details["sentence"], "the sentence says who they are now")
        self.assertEqual((wren, "Wren Ashdown"), self.face(self.faces[0])[:2])

    # ---- keyword rows: their identity is the path ------------------------------------------------

    def keyword_photo(self, keyword, name="Sam", filename="regatta_003.jpg"):
        """A photo whose keyword is `keyword`, listing `name` with no id from that keyword: what a library read before the ids
        holds (the real pet and friend entry: 1 face and 12 such rows)."""
        import photo_rows
        path = os.path.join(os.path.dirname(self.photo), filename)

        def seed(conn):
            photo_rows.add_read(conn, path, {"XMP:Subject": [keyword]})
            photo_id = conn.execute("SELECT id FROM photos WHERE path = ?", (path,)).fetchone()[0]
            conn.execute("DELETE FROM photo_people WHERE photo_id = ?", (photo_id,))
            conn.execute("INSERT INTO photo_people (photo_id, position, name, source, tag_id) VALUES (?, 0, ?, 'keyword', NULL)",
                         (photo_id, name))
        write(self.path, seed)
        return path

    def test_keyword_rows_are_counted_apart_they_are_not_rows_to_settle(self):
        self.keyword_photo(SAM_I)
        sam = self.listed_entry("Sam")
        self.assertEqual((1, 0, 1), (sam["faces"], sam["listed"], sam["keyword_photos"]))
        self.assertEqual(2, sam["rows"])

    def test_link_does_not_give_the_photos_a_keyword_names_to_the_person_picked(self):
        path = self.keyword_photo(SAM_I)
        sam_t, sam_i = self.node(SAM_T), self.node(SAM_I)
        done = name_review.resolve(self.library, "Sam", name_review.LINK, person_id=sam_t, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual((sam_t, "Sam"), self.face(self.faces[1])[:2], "the face is the person's")
        rows = self.listed(path)
        self.assertNotIn((sam_t, "Sam", "keyword"), rows, "a keyword names the other Sam by its path")
        self.assertEqual([(None, "Sam", "keyword")], rows, "untouched: only the owner's rebuild writes it, by the path")
        name_review.resolve(self.library, "Sam", name_review.REBUILD, apply=True)
        self.assertEqual([(sam_i, "Sam", "keyword")], self.listed(path), "the photo lists who its keyword says, by the one rule")

    def test_a_link_with_only_the_face_changed_does_not_promise_an_undo_it_did_not_journal(self):
        self.keyword_photo(SAM_I)
        write(self.path, lambda conn: conn.execute("UPDATE faces SET name = NULL, tag_id = NULL WHERE id = ?", (self.faces[1],)))
        self.assertIsNotNone(self.listed_entry("Sam"), "the keyword row still waits")
        done = name_review.resolve(self.library, "Sam", name_review.LINK, person_id=self.node(SAM_T), apply=True)
        self.assertIsNone(done.details["change"])
        self.assertNotIn("History", done.details["undo"])

    def test_rebuild_these_photos_lists_by_the_path_rule_and_says_it_is_not_journaled(self):
        path = self.keyword_photo(SAM_I)
        sam_i = self.node(SAM_I)
        rehearsal = name_review.resolve(self.library, "Sam", name_review.REBUILD)
        self.assertFalse(rehearsal.details["applied"])
        self.assertEqual([(None, "Sam", "keyword")], self.listed(path))
        done = name_review.resolve(self.library, "Sam", name_review.REBUILD, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual(1, done.changed)
        self.assertEqual([(sam_i, "Sam", "keyword")], self.listed(path))
        self.assertIsNone(done.details["change"])
        self.assertIn("not journaled", done.details["undo"])
        self.assertEqual(0, self.listed_entry("Sam")["keyword_photos"])

    def test_a_misspelt_keyword_beside_a_correct_person_is_left_for_the_owner(self):
        path = self.keyword_photo("Samm", name="Samm")
        done = name_review.resolve(self.library, "Samm", name_review.REBUILD, apply=True)
        self.assertEqual(1, done.changed, "the stale row of a keyword that is no person's is dropped by the rule")
        self.assertEqual([], self.listed(path))
        self.assertIsNone(self.listed_entry("Samm"))
        # A Make by the owner must not write a keyword row by name either.
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "Samm", None))
        write(self.path, lambda conn: conn.execute(
            "INSERT INTO photo_people (photo_id, position, name, source, tag_id) SELECT id, 0, 'Samm', 'keyword', NULL FROM photos WHERE path = ?",
            (path,)))
        self.assertEqual(1, self.listed_entry("Samm")["keyword_photos"])
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "Samm", None))
        made = name_review.resolve(self.library, "Samm", name_review.MAKE, group_id=self.node("Friends"), apply=True)
        self.assertTrue(made.ok, made.message())
        friend = self.node("Friends/Samm")
        self.assertEqual((friend, "Samm"), self.face(self.faces[1])[:2])
        self.assertEqual([(friend, "Samm", "keyword")], self.listed(path), "the tree edit's rebuild gave it, by the rule: a bare keyword one person has")

    # ---- how it fails ----------------------------------------------------------------------------

    def test_a_name_settled_in_another_window_is_refused_the_second_time_and_nothing_is_done_twice(self):
        first = name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Friends"), apply=True)
        self.assertTrue(first.ok)
        again = name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Pets"), apply=True)
        self.assertIn("settled in another window", again.refused)
        self.assertIsNone(self.node("Pets/Wren Quill"))
        self.assertEqual(1, len(self.changes(journal.PERSON_MADE)))

    def test_a_choice_interrupted_part_way_leaves_nothing(self):
        with mock.patch.object(journal, "record_faces", side_effect=RuntimeError("the disk went away")):
            with self.assertRaises(RuntimeError):
                name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node("Friends"), apply=True)
        self.assertIsNone(self.node("Friends/Wren Quill"), "the tree row went back with the rest")
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))
        with mock.patch.object(journal, "record_faces", side_effect=RuntimeError("the disk went away")):
            with self.assertRaises(RuntimeError):
                name_review.resolve(self.library, QUILL, name_review.UNNAME, apply=True)
        self.assertEqual((None, QUILL, "manual"), self.face(self.faces[0]))

    def test_two_windows_choosing_at_once_one_wins_and_the_other_is_told(self):
        results, errors = [], []

        def choose(group):
            try:
                results.append(name_review.resolve(self.library, QUILL, name_review.MAKE, group_id=self.node(group), apply=True))
            except Exception as why:   # the test reports it
                errors.append(why)

        threads = [threading.Thread(target=choose, args=(group,)) for group in ("Friends", "Pets")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual([], errors)
        self.assertEqual(1, sum(1 for each in results if each.ok and each.details["applied"]))
        self.assertEqual(1, len([tag for tag in ("Friends/Wren Quill", "Pets/Wren Quill") if self.node(tag)]))
        self.assertEqual(1, len(self.changes(journal.PERSON_MADE)))

    def test_a_pet_and_a_friend_called_alike_the_name_is_linked_to_the_one_picked(self):
        write(self.path, lambda conn: name_without_a_person(conn, [self.faces[1]], "Max", None))
        pet, friend = self.node(MAX_PET), self.node(MAX_FRIEND)
        self.assertEqual({pet, friend}, {person["id"] for person in self.listed_entry("Max")["candidates"]})
        done = name_review.resolve(self.library, "Max", name_review.LINK, person_id=pet, apply=True)
        self.assertTrue(done.ok, done.message())
        self.assertEqual((pet, "Max"), self.face(self.faces[1])[:2])


if __name__ == "__main__":
    unittest.main()
