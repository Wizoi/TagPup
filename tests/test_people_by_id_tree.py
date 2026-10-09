"""People by id, stage 2, phase 5 (docs/ARCHITECTURE.md, "Tree operations, by id"): rename, move, merge and delete a person's tag.

Every operation is one transaction for the database -- the tree row, the faces' cache of the name, the photos' lists -- with the
photo files rewritten by the keyword writer's own machinery (here stood in for, as tests/test_service_tags.py does: what is
checked is what each edit does to the tree, the faces and the lists, and what it asks to have rewritten). Two cousins called Sam
under two groups, a dog and a friend called Max.

The failure modes: interrupted (the rename's files), two at once (threads here; two real processes in tests/test_people_by_id_at_once.py), a read
that fails in the middle of an edit, the owner's rapid clicks, the old page that sends names.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from people_by_id import MAX_FRIEND, MAX_PET, SAM_I, SAM_T, TwoSams, look, write  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.core.result import NotFound, Refused, Result  # noqa: E402
from tagpup.services import tags as tags_service  # noqa: E402
from tagpup.store import db, faces, person_ids, photos, taxonomy  # noqa: E402


def stand_in_for_the_files(case):
    """The keyword writer, as tests/test_service_tags.py stands in for it: it asks, and the index is told what the file holds."""
    case.rewrites = []
    case.unwritable = set()

    def replace_tag(library, photo_paths, old, new, exiftool_path):
        case.rewrites.append((sorted(photo_paths), old, new))
        result = Result(attempted=len(photo_paths), changed=len([p for p in photo_paths if p not in case.unwritable]))
        for path in photo_paths:
            if path in case.unwritable:
                result.fail(path, "the file is open elsewhere")
            else:
                held = json.loads(look(case.path, "SELECT tags FROM photos WHERE path = ?", (path,))[0][0])
                photos.record_tags(library.path, path, vocabulary.retag(held, old, new)[0])
        return result

    patcher = mock.patch("tagpup.services.tagging.replace_tag", side_effect=replace_tag)
    patcher.start()
    case.addCleanup(patcher.stop)


class Operations(TwoSams, unittest.TestCase):
    def setUp(self):
        self.make_library()
        stand_in_for_the_files(self)
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        write(self.path, lambda conn: faces.name(conn, [self.faces[1]], self.sam_i))
        write(self.path, lambda conn: faces.name(conn, [self.faces[2]], self.sam_i))

    def rename(self, who, new):
        return tags_service.rename_person(self.library, who, new, "exiftool")

    def merge(self, source, target, **more):
        return tags_service.merge(self.library, source, target, "exiftool", **more)

    # ---- rename ----------------------------------------------------------------------------------

    def test_rename_renames_the_one_node_and_its_faces_follow_in_the_same_transaction(self):
        result = self.rename(self.sam_i, "Samuel")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(2, result.details["faces_renamed"])
        self.assertEqual(self.sam_i, self.node("Family/Ingersoll/Samuel"))
        self.assertEqual(self.sam_t, self.node(SAM_T), "the other Sam is untouched")
        self.assertEqual([(self.sam_t, "Sam"), (self.sam_i, "Samuel"), (self.sam_i, "Samuel")],
                         [self.face(each)[:2] for each in self.faces])
        self.assertEqual([(self.sam_t, "Sam", "keyword"), (self.sam_i, "Samuel", "face")], self.listed(self.photo))

    def test_renaming_a_group_counts_the_faces_under_it_after_the_write(self):
        """Fix round 1: faces_renamed was counted before the write, and a group (not a person) counted none."""
        result = tags_service.rename(self.library, self.node("Family/Ingersoll"), "Ingersoll House", "exiftool")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(2, result.details["faces_renamed"], "the two faces of the Sam under it")
        self.assertEqual(self.sam_i, self.node("Family/Ingersoll House/Sam"))
        self.assertEqual([(self.sam_i, "Sam")] * 2, [self.face(self.faces[1])[:2], self.face(self.faces[2])[:2]])

    def unresolved_face(self, name="Zed Mercer"):
        def add(conn):
            face = faces.insert(conn, self.other, [40, 0, 50, 10], b"\x06" * 8)
            conn.execute("UPDATE faces SET name = ?, tag_id = NULL, name_source = 'manual' WHERE id = ?", (name, face))
            return face
        return write(self.path, add)

    def test_renaming_a_name_no_tag_has_into_a_persons_name_gives_the_faces_that_person(self):
        """Fix round 3: the faces were written the new NAME alone -- on no person's page, in no list, linked by nothing later."""
        wren = self.node("People/Wren Halloway")
        face = self.unresolved_face()
        result = self.rename("Zed Mercer", "Wren Halloway")
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, result.details["faces_renamed"])
        self.assertEqual((wren, "Wren Halloway", "manual"), self.face(face), "the writer knew the id and wrote it")
        self.assertIn((wren, "Wren Halloway", "face"), self.listed(self.other))

    def test_renaming_a_name_no_tag_has_into_a_name_two_people_have_is_refused_naming_them(self):
        face = self.unresolved_face()
        with self.assertRaises(Refused) as why:
            self.rename("Zed Mercer", "Sam")
        self.assertIn(SAM_T, str(why.exception))
        self.assertIn(SAM_I, str(why.exception))
        self.assertEqual((None, "Zed Mercer", "manual"), self.face(face), "nothing was written")

    def test_renaming_a_name_no_tag_has_into_a_name_nobody_has_stays_a_name(self):
        face = self.unresolved_face()
        self.assertTrue(self.rename("Zed Mercer", "Zed Marlowe").ok)
        self.assertEqual((None, "Zed Marlowe", "manual"), self.face(face))

    def test_rename_never_merges_into_a_person_who_is_there(self):
        result = self.rename(self.sam_i, "Wren Halloway")      # a free name: fine
        self.assertTrue(result.ok)
        taxonomy_before = look(self.path, "SELECT id, tag FROM tag_taxonomy ORDER BY id")
        refused = self.rename(self.sam_i, "Wren Halloway")      # now that is the name it already has
        self.assertEqual(0, refused.changed)
        write(self.path, lambda conn: taxonomy.add_node(conn, "Family/Ingersoll/Cora"))
        taxonomy_before = look(self.path, "SELECT id, tag FROM tag_taxonomy ORDER BY id")
        into = self.rename(self.sam_i, "Cora")
        self.assertIn("merge them instead", into.refused)
        self.assertEqual(taxonomy_before, look(self.path, "SELECT id, tag FROM tag_taxonomy ORDER BY id"))
        self.assertEqual(self.sam_i, self.face(self.faces[1])[0])

    def test_rename_by_a_shared_name_is_refused_with_the_candidates_and_by_a_stale_id_is_not_found(self):
        with self.assertRaises(Refused) as why:
            self.rename("Sam", "Samuel")
        self.assertIn(SAM_T, str(why.exception))
        self.assertIn(SAM_I, str(why.exception))
        with self.assertRaises(NotFound):
            self.rename(99999, "Samuel")
        self.assertEqual(self.sam_t, self.node(SAM_T))

    def test_a_pet_and_a_friend_called_alike_are_renamed_one_at_a_time(self):
        max_pet, max_friend = self.node(MAX_PET), self.node(MAX_FRIEND)
        self.assertTrue(self.rename(max_friend, "Maxwell").ok)
        self.assertEqual(max_pet, self.node(MAX_PET))
        self.assertEqual("Max", look(self.path, "SELECT name FROM tag_taxonomy WHERE id = ?", (max_pet,))[0][0])

    def test_interrupted_between_the_tree_and_the_files_the_database_is_whole(self):
        """The photos' files are rewritten after the tree: a photo that could not be is reported, the tree and the faces have the
        new name, and running the rename again changes nothing it should not (it finds the node already renamed)."""
        carrying = os.path.join(os.path.dirname(self.path), "Pictures", "regatta_003.jpg")
        write(self.path, lambda conn: __import__("photo_rows").add_read(conn, carrying, {"XMP:Subject": [SAM_I]}))
        self.unwritable.add(carrying)
        result = self.rename(self.sam_i, "Samuel")
        self.assertFalse(result.ok)
        self.assertIn("could not be rewritten", result.message())
        self.assertEqual(self.sam_i, self.node("Family/Ingersoll/Samuel"))
        self.assertEqual([(self.sam_i, "Samuel")], [self.face(self.faces[1])[:2]])
        in_step = lambda: [person_ids.out_of_step(_conn(self.path), table).rows for table in person_ids.TABLES]  # noqa: E731
        self.assertEqual([0, 0], in_step(), "no face has a name no node has")
        # The photo still holds the old path (a stale keyword, finding "stale keyword of a renamed shared leaf"): no node has
        # the path, so the index lists it as the name its keyword spells, by the rule that settles every name with no id
        # (the leaf's one person, if the leaf is one person's now) -- until its file is rewritten, which the journal resumes.
        self.assertEqual(1, look(self.path, "SELECT COUNT(*) FROM tag_taxonomy WHERE name = 'Samuel'")[0][0])
        self.assertEqual(0, look(self.path, "SELECT COUNT(*) FROM tag_taxonomy WHERE tag = ?", (SAM_I,))[0][0])
        self.assertEqual([("Sam", "keyword")], [row[1:] for row in self.listed(carrying)], "the name its keyword spells")

    def test_a_read_that_fails_in_the_middle_of_the_edit_leaves_the_tree_as_it_was(self):
        real = person_ids.People.read.__func__
        calls = []

        def fails_the_second_time(cls, conn):
            calls.append(1)
            if len(calls) > 1:
                raise RuntimeError("the library went away")
            return real(cls, conn)

        before = look(self.path, "SELECT id, tag, name FROM tag_taxonomy ORDER BY id")
        with mock.patch.object(person_ids.People, "read", classmethod(fails_the_second_time)):
            with self.assertRaises(RuntimeError):
                write(self.path, lambda conn: taxonomy.move_branch(conn, SAM_I, "Family/Ingersoll/Samuel"))
        self.assertEqual(before, look(self.path, "SELECT id, tag, name FROM tag_taxonomy ORDER BY id"), "rolled back")
        self.assertEqual((self.sam_i, "Sam"), self.face(self.faces[1])[:2])

    # ---- move ------------------------------------------------------------------------------------

    def test_a_move_between_groups_keeps_the_id_and_the_faces_follow(self):
        result = self.merge(SAM_I, "Family/Thackeray/Samuel", apply=True)     # a free place: a move, not a join
        self.assertTrue(result.ok, result.message())
        self.assertEqual(self.sam_i, self.node("Family/Thackeray/Samuel"))
        self.assertEqual([(self.sam_i, "Samuel")] * 2, [self.face(self.faces[1])[:2], self.face(self.faces[2])[:2]])

    # ---- merge -----------------------------------------------------------------------------------

    def test_the_plan_of_a_merge_says_how_many_photos_would_list_one_person_twice(self):
        plan = self.merge(SAM_T, SAM_I)
        self.assertFalse(plan.details["applied"])
        self.assertEqual(1, plan.details["photos_listing_both"], "the regatta photo lists both Sams")
        self.assertEqual(1, plan.details["photos_with_a_face_of_each"])
        self.assertEqual(self.sam_t, self.node(SAM_T), "a plan writes nothing")

    def test_a_merge_repoints_the_faces_and_the_lists_before_the_node_goes(self):
        result = self.merge(SAM_T, SAM_I, apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertIsNone(self.node(SAM_T))
        self.assertEqual([(self.sam_i, "Sam")] * 3, [self.face(each)[:2] for each in self.faces])
        self.assertEqual([(self.sam_i, "Sam", "keyword")], self.listed(self.photo),
                         "a photo that named both lists the person once; both faces stay named")
        self.assertEqual(0, person_ids.out_of_step(_conn(self.path), "faces").rows)

    def test_a_merge_into_something_that_is_no_person_is_refused_while_faces_name_the_tag(self):
        result = self.merge(SAM_T, "Places/Coast", apply=True)
        self.assertFalse(result.ok)
        self.assertIn("face(s) are named", result.message())
        self.assertEqual(self.sam_t, self.node(SAM_T))
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0])

    def the_state(self):
        """Everything a refused edit must leave alone: the tree, the faces, the photos' index rows, the lists."""
        return (look(self.path, "SELECT id, tag, parent_id, has_face FROM tag_taxonomy ORDER BY id"),
                look(self.path, "SELECT id, tag_id, name, name_source FROM faces ORDER BY id"),
                look(self.path, "SELECT path, tags FROM photos ORDER BY path"),
                look(self.path, "SELECT photo_id, position, tag_id, name, source FROM photo_people ORDER BY photo_id, position"))

    def test_a_merge_or_a_move_onto_a_tag_that_is_no_person_is_refused_before_a_file_is_written(self):
        """Fix round 1: the refusal came after the photos were rewritten and the target made, so the files carried a tag the tree
        then refused, and the retry found nothing carrying the old one. Every refusal is now before the first write."""
        before = self.the_state()
        for target in ("Places/Coast", "Places/NewShore", "Family/Thackeray", "Family", "Places"):
            with self.subTest(target=target):
                result = self.merge(SAM_T, target, apply=True)
                self.assertFalse(result.ok)
                self.assertEqual([], self.rewrites, "no file was written")
                self.assertEqual(before, self.the_state(), "the tree, the faces and the index are as they were")
        moved = tags_service.delete(self.library, self.sam_t, "move", "Places/Coast", "exiftool")
        self.assertFalse(moved.ok)
        self.assertEqual([], self.rewrites)
        self.assertEqual(before, self.the_state())

    def test_a_merge_onto_a_person_still_goes_through(self):
        result = self.merge(SAM_T, SAM_I, apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, len(self.rewrites), "the photo carrying the tag was rewritten once")

    # ---- delete ----------------------------------------------------------------------------------

    def test_deleting_a_person_faces_name_is_refused_with_the_count(self):
        result = tags_service.delete(self.library, self.sam_i, "remove", None, "exiftool")
        self.assertIn("2 face(s) are named", result.refused)
        self.assertIn("about 6 rows", result.refused, "the cost of force in History is said before it is asked for")
        self.assertEqual(self.sam_i, self.node(SAM_I))
        self.assertEqual(self.sam_i, self.face(self.faces[1])[0])
        usage = tags_service.usage(self.library, self.sam_i)
        self.assertEqual(2, usage["faces_named"], "the editor is told before it asks")
        self.assertEqual(6, usage["history_rows_if_forced"])

    def test_a_force_delete_is_one_journaled_change_of_the_faces_and_history_puts_them_back(self):
        """Fix round 1 (the single owner of an undoable record of a face's identity change is the journal): the faces a force
        delete unnames are ONE change in the same transaction as the node goes; History puts their names and decisions back,
        and with the node gone they are an unresolved name -- never the other person called alike."""
        from tagpup.services import journal as journal_service
        from tagpup.store import journal
        written = look(self.path, "SELECT id, name, name_source FROM faces ORDER BY id")
        decided = [row for row in written if row[0] in (self.faces[1], self.faces[2])]
        self.assertTrue(all(row[2] == "manual" for row in decided), decided)
        self.assertTrue(tags_service.delete(self.library, self.sam_i, "remove", None, "exiftool", force=True).ok)
        changes = look(self.path, "SELECT id, summary FROM changes WHERE operation = ?", (journal.PERSON_DELETED,))
        self.assertEqual(1, len(changes))
        summary = json.loads(changes[0][1])
        self.assertEqual({"faces": 2, "rows": {"faces": 2}}, {key: summary[key] for key in ("faces", "rows")}, "counts only")
        self.assertIn("not restored", summary["note"], "History says what an undo does not give back")
        self.assertEqual(6, look(self.path, "SELECT COUNT(*) FROM change_rows WHERE change_id = ?", (changes[0][0],))[0][0],
                         "three rows a face: what the refusal and the dry run say")
        # The other Sam is called the same. The undo puts the two faces back as they were -- a hand decision of a name -- and
        # the person who held it is gone: they are unresolved names, NOT given to the other Sam (nothing links a name that
        # merely became one person's: person_ids.link_added).
        undone = journal_service.undo(self.library, changes[0][0], apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual([(None, "Sam", "manual")] * 2, [self.face(self.faces[1]), self.face(self.faces[2])])
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0], "the other Sam's face is theirs and no other is")
        self.assertEqual(0, look(self.path, "SELECT COUNT(*) FROM faces WHERE tag_id = ? AND id <> ?", (self.sam_t, self.faces[0]))[0][0],
                         "no face was given to the other Sam")

    def test_the_undo_of_a_merge_of_two_people_called_alike_returns_the_faces_as_unresolved_names(self):
        from tagpup.services import journal as journal_service
        from tagpup.store import journal
        self.assertTrue(self.merge(SAM_I, SAM_T, apply=True).ok)
        change = look(self.path, "SELECT id FROM changes WHERE operation = ?", (journal.PERSON_MERGED,))[0][0]
        undone = journal_service.undo(self.library, change, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual([(None, "Sam", "manual")] * 2, [self.face(self.faces[1]), self.face(self.faces[2])],
                         "the merged person is not restored, and the faces are not given to the person they were merged into")

    def test_the_undo_of_a_force_delete_nobody_else_shares_puts_the_decision_back_as_a_name(self):
        from tagpup.services import journal as journal_service
        from tagpup.store import journal
        wren = self.node("People/Wren Halloway")
        face = write(self.path, lambda conn: faces.insert(conn, self.other, [40, 0, 50, 10], b"\x05" * 8))
        write(self.path, lambda conn: faces.name(conn, [face], wren))
        self.assertEqual((wren, "Wren Halloway", "manual"), self.face(face))
        self.assertTrue(tags_service.delete(self.library, wren, "remove", None, "exiftool", force=True).ok)
        change = look(self.path, "SELECT id FROM changes WHERE operation = ?", (journal.PERSON_DELETED,))[0][0]
        undone = journal_service.undo(self.library, change, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual((None, "Wren Halloway", "manual"), self.face(face), "the decision is back, a name no person is filed under")
        write(self.path, lambda conn: taxonomy.add_path(conn, "People/Wren Halloway"))
        # (a node made for the name links it: tests/test_tree_edit_links_only_what_it_adds.py)

    def test_a_merge_records_the_faces_it_gave_to_the_other_person(self):
        from tagpup.store import journal
        self.assertTrue(self.merge(SAM_I, SAM_T, apply=True).ok)
        changes = look(self.path, "SELECT summary FROM changes WHERE operation = ?", (journal.PERSON_MERGED,))
        summaries = [json.loads(each[0]) for each in changes]
        self.assertEqual([{"faces": 2, "rows": {"faces": 2}}], [{key: each[key] for key in ("faces", "rows")} for each in summaries])
        self.assertIn("the merged person and the photos' keywords are not restored", summaries[0]["note"])

    def test_force_unnames_the_faces_in_the_same_transaction_and_they_are_not_nobody(self):
        result = tags_service.delete(self.library, self.sam_i, "remove", None, "exiftool", force=True)
        self.assertTrue(result.ok, result.message())
        self.assertIsNone(self.node(SAM_I))
        self.assertEqual([(None, None, None)] * 2, [self.face(self.faces[1]), self.face(self.faces[2])],
                         "unreviewed: they come back in Identify Faces and Re-examine may name them again")
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0], "the other Sam's faces are not touched")
        self.assertEqual([(self.sam_t, "Sam", "keyword")], self.listed(self.photo))
        self.assertEqual(0, person_ids.out_of_step(_conn(self.path), "faces").rows)

    def test_the_trigger_is_the_backstop_for_any_connection(self):
        conn = db.connect(self.path)
        try:
            with self.assertRaises(Exception) as why:
                conn.execute("DELETE FROM tag_taxonomy WHERE id = ?", (self.sam_t,))
            self.assertIn("person_tag_not_deleted_while_named", str(why.exception))
            conn.rollback()
        finally:
            conn.close()
        self.assertEqual(self.sam_t, self.node(SAM_T))

    # ---- the two rules, as refusals on the owner's actions ----------------------------------------

    def test_rule_a_a_person_faces_carry_gets_no_children_from_the_tree_view(self):
        result = tags_service.create(self.library, "Swim Team", parent_id=self.sam_t)
        self.assertIn("Sam is a person (1 face(s), 1 photo(s)); a person cannot have tags under them. Choose another group.",
                      result.refused)
        self.assertIsNone(self.node("Family/Thackeray/Sam/Swim Team"))

    def test_rule_a_a_person_nothing_carries_yet_can_become_a_group(self):
        write(self.path, lambda conn: taxonomy.add_node(conn, "Family/Thackeray/Wren"))
        wren = self.node("Family/Thackeray/Wren")
        self.assertTrue(tags_service.create(self.library, "Baby photos", parent_id=wren).ok)

    def test_rule_a_a_move_under_a_person_is_refused(self):
        result = self.merge("Places/Coast", SAM_I + "/Coast", apply=True)
        self.assertIn("a person cannot have tags under them", result.refused)
        self.assertIsNotNone(self.node("Places/Coast"))

    def test_rule_a_does_not_convert_a_violation_that_is_there(self):
        write(self.path, lambda conn: taxonomy.add_node(conn, SAM_T + "/Swim Team"))      # what a file read made
        self.assertEqual(self.sam_t, self.face(self.faces[0])[0], "the face still names the node")
        again = tags_service.create(self.library, "Swim Team", parent_id=self.sam_t)
        self.assertTrue(again.ok, "a tag that is a node already is not a new child")
        self.assertEqual(0, again.changed)

    def test_rule_a_the_keyword_writer_refuses_a_new_tag_under_a_person(self):
        from tagpup.services import tagging
        sentence = tagging.refuse_under_people(self.library, [SAM_T + "/Swim Team", "Places/Seattle"])
        self.assertIn("a person cannot have tags under them", sentence)
        self.assertIsNone(tagging.refuse_under_people(self.library, ["Places/Seattle", SAM_T]))
        self.assertIsNone(tagging.refuse_under_people(self.library, ["Sam/Swim"]))

    def test_rule_a_is_never_applied_to_what_a_file_holds(self):
        """The indexer makes the nodes a file's keywords name, a child of a person included."""
        tree = taxonomy.TagTaxonomy(self.path)
        tree.load()
        tree.add_tag(SAM_T + "/Swim Team")
        tree.save()
        self.assertIsNotNone(self.node(SAM_T + "/Swim Team"))


def _conn(path):
    return db.connect(db.readonly_uri(path), uri=True)


class TheOwnersRapidClicks(TwoSams, unittest.TestCase):
    """The owner renames a person and, before it ends, names faces and renames again; a second window deletes one: whatever order
    they land in, no face holds a name that is not its node's and no one is made by a click."""

    def setUp(self):
        self.make_library()
        stand_in_for_the_files(self)
        self.sam_t, self.sam_i = self.node(SAM_T), self.node(SAM_I)

    def test_threads_renaming_and_naming_leave_every_name_the_nodes(self):
        import threading
        from tagpup.services import faces as faces_service
        failures = []

        def renames():
            for number in range(6):
                try:
                    tags_service.rename_person(self.library, self.sam_i, "Samuel %d" % number, "exiftool")
                except Exception as problem:   # a rename to a name it already has is no error
                    failures.append(problem)

        def names():
            for face in self.faces * 3:
                try:
                    faces_service.name_face(self.library, face, self.sam_i)
                except Exception as problem:
                    failures.append(problem)

        threads = [threading.Thread(target=renames), threading.Thread(target=names)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(120)
        self.assertEqual([], [f for f in failures if not isinstance(f, (Refused, NotFound))], failures)
        conn = _conn(self.path)
        try:
            self.assertEqual([0, 0], [person_ids.out_of_step(conn, table).rows for table in person_ids.TABLES])
            names_held = {name for (name,) in conn.execute("SELECT name FROM faces WHERE tag_id = ?", (self.sam_i,))}
            node_name = conn.execute("SELECT name FROM tag_taxonomy WHERE id = ?", (self.sam_i,)).fetchone()[0]
        finally:
            conn.close()
        self.assertLessEqual(names_held, {node_name})

    def test_a_person_merged_in_another_window_is_not_found_by_a_page_that_still_sends_them(self):
        write(self.path, lambda conn: faces.name(conn, [self.faces[0]], self.sam_t))
        self.assertTrue(tags_service.merge(self.library, SAM_T, SAM_I, "exiftool", apply=True).ok)
        from tagpup.services import faces as faces_service
        with self.assertRaises(NotFound):
            faces_service.name_face(self.library, self.faces[1], self.sam_t)
        self.assertEqual(self.sam_i, self.face(self.faces[0])[0], "the faces went with the merge")
        self.assertIsNone(self.node(SAM_T), "and nobody was made of the stale id")


if __name__ == "__main__":
    unittest.main()
