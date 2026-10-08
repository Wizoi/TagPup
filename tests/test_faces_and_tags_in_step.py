"""docs/findings.md, #861: a face and the photo's person tag are one decision, in both directions and whichever way a face is
named.

The owner's screenshot: a face named (the box green, the strip's card showing the name) and the photo saying "No people tags".
TagTuner's Detected Faces strip (Match, AutoMatch All) named the face only. Now naming a face puts the person on the photo, the tag
first (a tag that cannot be written names no face), and taking a name off, ruling a face out or taking every name off a photo
takes the person off the photo unless another face of it still carries them, the face first (the tag is the part that may fail
without making the drift worse). Whichever app asks, through the one owner (tagpup.services.face_people); TagPup's own page
says it writes the photo's tags itself (`page_writes_tags`) and is told which to take off.

ExifTool is a table of files (tests/fake_exiftool.py); the photos are real small JPEGs and rows as the indexer records them
(tests/photo_rows.py), the tree by tagpup.store.taxonomy, the faces by tagpup.store.faces.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_organize_faces import Case, ODA, WREN  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import checks, db, faces, people, taxonomy  # noqa: E402

PEOPLE = "People/"


class InStep(Case):
    def setUp(self):
        super().setUp()
        self.tuner_client = self.tuner()

    def held(self, name, keywords=(), **picture):
        """A photo as the indexer records it, the file ExifTool answers for holding the same keywords."""
        photo_path = self.photo(name, keywords, **picture)
        self.files.keep_tags(photo_path, keywords)
        return photo_path

    def tuner_post(self, route, body):
        return self.tuner_client.post("/library/api/" + route, json=body)

    def tags(self, photo_path):
        return self.files.tags_of(photo_path)

    def indexed_people(self):
        return self.look("SELECT name, source FROM photo_people ORDER BY photo_id, position")

    def journal_operations(self):
        return [row[0] for row in self.look("SELECT operation FROM changes ORDER BY id")
                if row[0].startswith("person ")]


class NamingAddsThePersonToThePhoto(InStep):
    def test_tagtuners_match_writes_the_tag_then_names_the_face(self):
        photo = self.held("strip_001.jpg")
        face = self.face(photo)
        reply = self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(1, reply.get_json()["tags_written"])
        self.assertEqual([PEOPLE + WREN], self.tags(photo), "the file holds the person")
        self.assertEqual((WREN, "manual", 0), self.row(face)[:3])
        self.assertEqual([(WREN, "keyword")], self.indexed_people(), "the library records the person as the photo's keyword")

    def test_tagpups_own_calls_find_the_tag_there_and_write_nothing_more(self):
        photo = self.held("strip_002.jpg", [PEOPLE + WREN])
        face = self.face(photo, box=(10, 10, 60, 60))
        self.face(photo, box=(70, 10, 110, 60))      # two unnamed faces: the tag alone names neither (#788)
        reply = self.post("face/match", {"face_id": face, "person_name": WREN})
        self.assertEqual({"success": True, "changed": 1, "tags_written": 0}, reply.get_json())
        self.assertEqual(0, self.files.writes, "the photo named the person already")

    def test_a_tag_that_cannot_be_written_names_no_face(self):
        photo = self.held("strip_003.jpg")
        face = self.face(photo)
        self.files.fails.add(paths.key(photo))
        reply = self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        self.assertGreaterEqual(reply.status_code, 400)
        self.assertIn("locked", json.dumps(reply.get_json()))
        self.assertIsNone(self.row(face)[0], "the face is not named: the owner is not told a name the photo does not carry")
        self.assertEqual([], self.tags(photo))

    def test_a_person_the_tree_files_in_two_places_names_no_face_and_writes_nothing(self):
        def two_places(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People/Pairs/" + ODA)
                taxonomy.add_path(conn, "People/Others/" + ODA)
        db.write_with_connection(self.path, two_places)
        photo = self.held("strip_004.jpg")
        face = self.face(photo)
        reply = self.tuner_post("face/match", {"face_id": face, "person_name": ODA})
        self.assertEqual(400, reply.status_code)
        self.assertIn("more than one place", reply.get_json()["error"])
        self.assertIsNone(self.row(face)[0])
        self.assertEqual(0, self.files.writes)

    def test_what_naming_would_refuse_is_refused_before_a_tag_is_written(self):
        photo = self.held("strip_005.jpg")
        first, second = self.face(photo), self.face(photo, box=(70, 10, 110, 60))
        self.assertEqual(200, self.tuner_post("face/match", {"face_id": first, "person_name": WREN}).status_code)
        writes = self.files.writes
        # The person is on the first face: a second face cannot be them, and the photo is not written for it.
        self.assertEqual(400, self.tuner_post("face/match", {"face_id": second, "person_name": WREN}).status_code)
        self.assertEqual(writes, self.files.writes)
        excluded = self.face(photo, box=(120, 10, 160, 60), excluded=1)
        self.assertEqual(409, self.tuner_post("face/match", {"face_id": excluded, "person_name": ODA}).status_code)
        self.assertEqual(writes, self.files.writes)

    def test_renaming_a_face_does_not_leave_the_one_face_rule_naming_the_other_face_instead(self):
        # Two faces, one named: a tag for the new person is "one face to be named and one person no face carries" to the rule
        # of #788, which would give the OTHER face the name before the face chosen is named, and refuse the choice.
        photo = self.held("strip_008.jpg", [PEOPLE + ODA])
        chosen = self.face(photo, name=ODA, name_source="manual")
        other = self.face(photo, box=(70, 10, 110, 60))
        reply = self.tuner_post("face/match", {"face_id": chosen, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual((WREN, "manual"), self.row(chosen)[:2])
        self.assertEqual((None, None), self.row(other)[:2], "the face not chosen was given the name as a guess")

    def test_a_face_renamed_takes_the_old_person_off_the_photo_unless_another_face_is_them(self):
        photo = self.held("strip_009.jpg", [PEOPLE + ODA, "Regatta"])
        chosen = self.face(photo, name=ODA, name_source="manual")
        reply = self.tuner_post("face/match", {"face_id": chosen, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)), "the face is Wren's now; Oda is on no face of the photo")
        self.assertEqual(1, reply.get_json()["tags_removed"])

    def test_the_old_person_stays_while_another_face_is_them(self):
        photo = self.held("strip_010.jpg", [PEOPLE + ODA])
        chosen = self.face(photo, name=ODA, name_source="manual")
        self.face(photo, box=(70, 10, 110, 60), name=ODA)
        reply = self.tuner_post("face/match", {"face_id": chosen, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual({PEOPLE + ODA, PEOPLE + WREN}, set(self.tags(photo)))

    def test_tagpups_page_that_wrote_the_tag_itself_writes_no_file_here_and_is_told_the_old_person(self):
        photo = self.held("strip_011.jpg", [PEOPLE + ODA, PEOPLE + WREN])       # the page's save is recorded: both are on it
        chosen = self.face(photo, name=ODA, name_source="manual")
        reply = self.post("face/match", {"face_id": chosen, "person_name": WREN, "page_writes_tags": True})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(0, self.files.writes, "the page writes the photo's tags with its queue and its stamp")
        self.assertEqual({photo: [PEOPLE + ODA]}, reply.get_json()["untag"])
        self.assertEqual((WREN, "manual"), self.row(chosen)[:2])

    def test_the_change_is_journaled_so_history_can_take_it_back(self):
        photo = self.held("strip_006.jpg")
        self.tuner_post("face/match", {"face_id": self.face(photo), "person_name": WREN})
        self.assertEqual(["person added for a named face"], self.journal_operations())

    def test_undoing_the_tag_in_history_leaves_the_face_named_and_the_doctor_counts_it(self):
        # Decided, as every Undo of a tag write: it takes the tag off and nothing unnames a face (#834). The drift it leaves is
        # the one `tags-from-faces` mends and the doctor counts.
        photo = self.held("strip_012.jpg")
        face = self.face(photo)
        self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        change = self.look("SELECT id FROM changes WHERE operation = 'person added for a named face'")[0][0]
        undone = journal_service.undo(self.library, change, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual([], self.tags(photo))
        self.assertEqual(WREN, self.row(face)[0])
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        self.assertEqual((1, 1), checks.people_on_faces_alone(conn))

    def test_a_named_face_whose_photo_lacks_the_tag_is_mended_by_choosing_the_name_again(self):
        # The state the owner found: named by the strip before this, the photo without the person.
        photo = self.held("strip_007.jpg")
        face = self.face(photo, name=WREN, name_source="manual")
        self.assertEqual([(WREN, "face")], self.indexed_people())
        reply = self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        self.assertEqual(200, reply.status_code)
        self.assertEqual(1, reply.get_json()["tags_written"])
        self.assertEqual([(WREN, "keyword")], self.indexed_people())


class TakingANameOffTakesThePersonOff(InStep):
    def test_unmatch_takes_the_tag_off_the_photo(self):
        photo = self.held("off_001.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        reply = self.tuner_post("face/unmatch", {"face_id": face})
        self.assertEqual(200, reply.status_code)
        self.assertEqual(1, reply.get_json()["tags_removed"])
        self.assertEqual((None, "manual"), self.row(face)[:2])
        self.assertEqual(["Regatta"], self.tags(photo), "only the person went")
        self.assertEqual([], self.indexed_people())

    def test_every_spelling_of_the_person_goes(self):
        photo = self.held("off_002.jpg", [PEOPLE + WREN, WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        self.tuner_post("face/unmatch", {"face_id": face})
        self.assertEqual(["Regatta"], self.tags(photo))

    def test_the_tag_stays_while_another_face_still_carries_the_person(self):
        photo = self.held("off_003.jpg", [PEOPLE + WREN])
        first = self.face(photo, name=WREN, name_source="manual")
        second = self.face(photo, box=(70, 10, 110, 60), name=WREN)      # a clustering name for the same person
        self.tuner_post("face/unmatch", {"face_id": first})
        self.assertEqual([PEOPLE + WREN], self.tags(photo), "the person is still on the other face")
        self.assertEqual(0, self.files.writes)
        self.tuner_post("face/unmatch", {"face_id": second})
        self.assertEqual([], self.tags(photo), "no face carries them now")

    def test_a_face_unmatched_that_carried_no_name_changes_nothing(self):
        photo = self.held("off_004.jpg", [PEOPLE + WREN])
        face = self.face(photo)
        self.tuner_post("face/unmatch", {"face_id": face})
        self.assertEqual([PEOPLE + WREN], self.tags(photo))
        self.assertEqual(0, self.files.writes)

    def test_a_tag_that_cannot_be_taken_off_leaves_the_face_unnamed_and_says_so(self):
        photo = self.held("off_005.jpg", [PEOPLE + WREN])
        face = self.face(photo, name=WREN, name_source="manual")
        self.files.unreadable.add(paths.key(photo))        # the file went bad since: ExifTool answers it is no photo
        reply = self.tuner_post("face/unmatch", {"face_id": face})
        self.assertEqual(200, reply.status_code)
        self.assertIn("could not be taken off", reply.get_json()["warning"])
        self.assertEqual((None, "manual"), self.row(face)[:2], "the face is unnamed: a tag left behind is a state the app allows")
        self.assertEqual([PEOPLE + WREN], self.tags(photo))

    def test_tagpups_page_writes_the_tag_itself_and_is_told_which(self):
        photo = self.held("off_006.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        reply = self.post("face/unmatch", {"face_id": face, "page_writes_tags": True})
        self.assertEqual(200, reply.status_code)
        self.assertEqual({photo: [PEOPLE + WREN]}, reply.get_json()["untag"])
        self.assertEqual(0, self.files.writes, "the page's own save writes it, with its queue and its undo")

    def test_not_important_on_a_named_face_takes_the_person_off_and_on_an_unnamed_one_changes_no_tag(self):
        photo = self.held("off_007.jpg", [PEOPLE + WREN])
        named = self.face(photo, name=WREN, name_source="manual")
        other = self.face(photo, box=(70, 10, 110, 60))
        self.assertEqual(1, self.tuner_post("faces/exclude", {"face_ids": [other]}).get_json()["excluded"])
        self.assertEqual([PEOPLE + WREN], self.tags(photo))
        reply = self.tuner_post("faces/exclude", {"face_ids": [named]})
        self.assertEqual(1, reply.get_json()["excluded"])
        self.assertEqual(1, reply.get_json()["tags_removed"])
        self.assertEqual([], self.tags(photo))

    def test_tagpups_page_is_told_which_tags_go_when_it_rules_a_face_out_too(self):
        photo = self.held("off_010.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        reply = self.post("faces/exclude", {"face_ids": [face], "page_writes_tags": True})
        self.assertEqual({photo: [PEOPLE + WREN]}, reply.get_json()["untag"])
        self.assertEqual(0, self.files.writes)
        self.assertEqual(1, reply.get_json()["excluded"])

    def test_ruling_out_nameless_faces_reads_and_writes_no_photo(self):
        photo = self.held("off_011.jpg", [PEOPLE + WREN])
        faces_here = [self.face(photo, box=(10 + 40 * n, 10, 40 + 40 * n, 40)) for n in range(3)]
        reply = self.tuner_post("faces/exclude", {"face_ids": faces_here, "reason": "ignored cluster"})
        self.assertEqual(3, reply.get_json()["excluded"])
        self.assertEqual({}, reply.get_json().get("untag", {}))
        self.assertEqual((0, 0), (self.files.writes, self.files.read_calls), "a photo was read for faces that carried no name")

    def test_unmatch_all_takes_off_every_person_the_names_gave(self):
        photo = self.held("off_008.jpg", [PEOPLE + WREN, PEOPLE + ODA, "Regatta"])
        self.face(photo, name=WREN, name_source="manual")
        self.face(photo, box=(70, 10, 110, 60), name=ODA)
        reply = self.tuner_post("photo/unmatch-all", {"photo_path": photo})
        self.assertEqual(200, reply.status_code)
        self.assertEqual(["Regatta"], self.tags(photo))
        self.assertEqual([], self.look("SELECT name FROM faces WHERE name IS NOT NULL"))

    def test_taking_the_name_off_and_the_tag_with_it_is_two_changes_of_history_one_for_the_tag(self):
        photo = self.held("off_009.jpg", [PEOPLE + WREN])
        face = self.face(photo, name=WREN, name_source="manual")
        self.tuner_post("face/unmatch", {"face_id": face})
        self.assertEqual(["person taken off for an unnamed face"], self.journal_operations())


class AutomatchAddsThePeopleItNamed(InStep):
    def two_photos(self):
        reference = self.held("auto_ref.jpg", [PEOPLE + WREN])
        self.face(reference, degrees=0, name=WREN, name_source="manual")
        photo = self.held("auto_001.jpg")
        return photo, self.face(photo, degrees=3)

    def test_automatch_all_names_the_face_and_puts_the_person_on_the_photo(self):
        photo, face = self.two_photos()
        reply = self.tuner_post("photo/automatch", {"photo_path": photo})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual(1, reply.get_json()["matched_count"])
        self.assertEqual(WREN, self.row(face)[0])
        self.assertEqual([PEOPLE + WREN], self.tags(photo))

    def test_a_tag_that_cannot_be_written_takes_the_names_back(self):
        photo, face = self.two_photos()
        self.files.fails.add(paths.key(photo))
        reply = self.tuner_post("photo/automatch", {"photo_path": photo})
        self.assertEqual(500, reply.status_code)
        self.assertIsNone(self.row(face)[0], "no face is left named that the photo does not carry")
        self.assertEqual((None, None), self.row(face)[:2])

    def test_a_name_a_person_gave_meanwhile_is_not_taken_back(self):
        photo, face = self.two_photos()
        self.files.fails.add(paths.key(photo))
        confirmed = {}

        def in_the_middle(path, n):
            # Between the names and the tag: a person confirms the guess in the other app.
            if not confirmed:
                confirmed[0] = True
                db.write_with_connection(self.path, lambda conn: faces.confirm(conn, face))
        self.files.on_read = in_the_middle
        self.tuner_post("photo/automatch", {"photo_path": photo})
        self.assertEqual((WREN, "manual"), self.row(face)[:2])


if __name__ == "__main__":
    unittest.main()
