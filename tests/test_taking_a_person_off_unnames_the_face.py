"""docs/findings.md, #908 (owner, 2026-10-08): taking a person's tag off a photo -- the pill, a tag taken off a selection, History's
Undo of an add -- also unnames that photo's face, as a decision ("this is nobody": name_source 'manual', name NULL), journaled so
History can take it back; and what is meant to name a face or write a tag from a face (Re-examine, the one-face rule of a save,
`tags-from-faces`) then does not put the person back.

ExifTool is a table of files (tests/fake_exiftool.py); photos are rows as the indexer records them and faces as the code that makes
them makes them (tests/test_faces_and_tags_in_step.py's harness).
"""
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import PEOPLE, InStep  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402

from tagpup.core import paths  # noqa: E402
from tagpup.services import face_people, faces_from_tags, tags_from_faces  # noqa: E402
from tagpup.services import faces as faces_service  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, people, taxonomy  # noqa: E402

UNNAMED = "face unnamed for a person taken off the photo"


class Taken(InStep):
    def save(self, photo, tags):
        reply = self.client.post("/library/api/photo/save-metadata", json={"path": photo, "title": "", "tags": list(tags)})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def unnaming_changes(self):
        return [row[0] for row in self.look("SELECT id FROM changes WHERE operation = ?", (UNNAMED,))]


class ThePillTakesTheFacesNameOff(Taken):
    def test_saving_a_photo_without_the_person_unnames_their_face_as_a_decision(self):
        photo = self.held("pill_001.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        reply = self.save(photo, ["Regatta"])
        self.assertEqual((None, "manual"), self.row(face)[:2], "the face is nobody, by the owner's hand")
        self.assertEqual([{"id": face, "name": WREN}], reply["unnamed_faces"])
        self.assertEqual(["Regatta"], self.tags(photo))
        self.assertEqual([], self.indexed_people(), "the library lists nobody on the photo")
        self.assertEqual(1, len(self.unnaming_changes()), "one change in History")

    def test_a_face_a_guess_named_is_a_decision_too(self):
        photo = self.held("pill_002.jpg", [PEOPLE + WREN])
        face = self.face(photo, name=WREN)       # clustering's or the tag's guess: name_source NULL
        self.save(photo, [])
        self.assertEqual((None, "manual"), self.row(face)[:2])

    def test_every_face_that_carried_the_person_is_unnamed(self):
        photo = self.held("pill_003.jpg", [PEOPLE + WREN])
        first = self.face(photo, name=WREN, name_source="manual")
        second = self.face(photo, box=(70, 10, 110, 60), name=WREN)
        self.save(photo, [])
        self.assertEqual([(None, "manual"), (None, "manual")], [self.row(first)[:2], self.row(second)[:2]])

    def test_taking_another_tag_off_leaves_the_faces_alone(self):
        photo = self.held("pill_004.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        reply = self.save(photo, [PEOPLE + WREN])
        self.assertEqual((WREN, "manual"), self.row(face)[:2])
        self.assertNotIn("unnamed_faces", reply)
        self.assertEqual([], self.unnaming_changes())

    def test_another_person_stays_named(self):
        photo = self.held("pill_005.jpg", [PEOPLE + WREN, PEOPLE + ODA])
        wren = self.face(photo, name=WREN, name_source="manual")
        oda = self.face(photo, box=(70, 10, 110, 60), name=ODA, name_source="manual")
        self.save(photo, [PEOPLE + ODA])
        self.assertEqual([(None, "manual"), (ODA, "manual")], [self.row(wren)[:2], self.row(oda)[:2]])

    def test_a_person_the_photo_still_names_under_another_spelling_keeps_the_face(self):
        photo = self.held("pill_006.jpg", [PEOPLE + WREN, WREN])
        face = self.face(photo, name=WREN, name_source="manual")
        self.save(photo, [WREN])         # the path went, the bare name stays: the person is on the photo still
        self.assertEqual(WREN, self.row(face)[0])

    def test_a_branch_of_the_tree_is_never_a_person(self):
        def add_branch(conn):
            with people.tree_edit(conn):
                taxonomy.add_path(conn, "People/Pairs/Cleo")
        db.write_with_connection(self.path, add_branch)
        photo = self.held("pill_007.jpg", [PEOPLE + "Pairs/Cleo", PEOPLE + "Pairs"])
        branch_face = self.face(photo, name="Pairs", name_source="manual")        # a name nothing should have given
        self.save(photo, [PEOPLE + "Pairs/Cleo"])
        self.assertEqual("Pairs", self.row(branch_face)[0], "the branch tag was not a person's tag")

    def test_a_photo_whose_file_cannot_be_written_keeps_the_face_named(self):
        # Reported as changed, not attempted: the save failed, the tag is on, the face is still the person's.
        photo = self.held("pill_008.jpg", [PEOPLE + WREN, "Regatta"])
        face = self.face(photo, name=WREN, name_source="manual")
        self.files.fails.add(paths.key(photo))
        reply = self.client.post("/library/api/photo/save-metadata", json={"path": photo, "title": "", "tags": ["Regatta"]})
        self.assertGreaterEqual(reply.status_code, 400)
        self.assertEqual((WREN, "manual"), self.row(face)[:2])
        self.assertEqual([PEOPLE + WREN, "Regatta"], self.tags(photo))
        self.assertEqual([], self.unnaming_changes())

    def test_a_face_renamed_between_the_read_and_the_write_is_left_alone(self):
        photo = self.held("pill_009.jpg", [PEOPLE + WREN])
        face = self.face(photo, name=WREN, name_source="manual")
        real = face_people._faces_to_unname

        def read_then_somebody_renames(library, removed):
            found = real(library, removed)
            faces_service.name_face(library, face, ODA)       # in TagTuner, at that moment
            return found

        with unittest.mock.patch.object(face_people, "_faces_to_unname", read_then_somebody_renames):
            reply = self.save(photo, [])
        self.assertEqual((ODA, "manual"), self.row(face)[:2], "the owner's newer decision stands")
        self.assertNotIn("unnamed_faces", reply, "it reports what it changed, not what it planned")

    def test_a_selection_a_person_is_taken_off_has_its_faces_unnamed(self):
        first = self.held("sel_001.jpg", [PEOPLE + WREN, "Regatta"])
        second = self.held("sel_002.jpg", [PEOPLE + WREN])
        a = self.face(first, name=WREN, name_source="manual")
        b = self.face(second, name=WREN)
        reply = self.client.post("/library/api/photos/bulk-tags",
                                 json={"paths": [first, second], "add_tags": [], "remove_tags": [PEOPLE + WREN]})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([(None, "manual"), (None, "manual")], [self.row(a)[:2], self.row(b)[:2]])
        self.assertEqual(sorted([{"id": a, "name": WREN}, {"id": b, "name": WREN}], key=lambda each: each["id"]),
                         sorted(reply.get_json()["unnamed_faces"], key=lambda each: each["id"]))

    def test_a_selection_one_of_whose_files_fails_unnames_only_the_photos_written(self):
        written = self.held("sel_003.jpg", [PEOPLE + WREN, "Regatta"])
        failing = self.held("sel_004.jpg", [PEOPLE + WREN, "Regatta"])
        kept = self.face(failing, name=WREN, name_source="manual")
        gone = self.face(written, name=WREN, name_source="manual")
        self.files.fails.add(paths.key(failing))
        with unittest.mock.patch("tagpup.services.tagging.logger"):
            self.client.post("/library/api/photos/bulk-tags",
                             json={"paths": [written, failing], "add_tags": [], "remove_tags": [PEOPLE + WREN]})
        self.assertEqual((WREN, "manual"), self.row(kept)[:2], "its file was not written: the person is on the photo still")
        self.assertEqual((None, "manual"), self.row(gone)[:2])


class HistoryTakesItBack(Taken):
    def test_undoing_the_change_names_the_face_again_as_it_was(self):
        photo = self.held("hist_001.jpg", [PEOPLE + WREN])
        face = self.face(photo, name=WREN)       # a guess, to see who-decided come back too
        self.save(photo, [])
        change = self.unnaming_changes()[0]
        undone = journal_service.undo(self.library, change, apply=True)
        self.assertEqual([], undone.errors, undone.errors)
        self.assertEqual((WREN, None), self.row(face)[:2])

    def test_history_undoing_an_add_takes_the_person_off_and_unnames_the_face(self):
        photo = self.held("hist_002.jpg")
        face = self.face(photo)
        self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        added = self.look("SELECT id FROM changes WHERE operation = 'person added for a named face'")[0][0]
        undone = journal_service.undo(self.library, added, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors)
        self.assertEqual([], self.tags(photo))
        self.assertEqual((None, "manual"), self.row(face)[:2], "the owner took the person off, with History's Undo")
        self.assertEqual([{"id": face, "name": WREN}], undone.details["unnamed_faces"])
        self.assertEqual(1, len(self.unnaming_changes()))

    def test_an_undo_the_file_refuses_unnames_nothing(self):
        photo = self.held("hist_003.jpg")
        face = self.face(photo)
        self.tuner_post("face/match", {"face_id": face, "person_name": WREN})
        added = self.look("SELECT id FROM changes WHERE operation = 'person added for a named face'")[0][0]
        self.files.keep_tags(photo, [PEOPLE + WREN, "Added since, elsewhere"])     # another program changed the file
        journal_service.undo(self.library, added, apply=True, exiftool_path="exiftool")
        self.assertEqual((WREN, "manual"), self.row(face)[:2])
        self.assertEqual([], self.unnaming_changes())


class WhatIsMeantToPutThePersonBackDoesNot(Taken):
    def removed(self):
        photo = self.held("rec_001.jpg", [PEOPLE + WREN])
        face = self.face(photo, degrees=0, name=WREN, name_source="manual")
        self.save(photo, [])
        return photo, face

    def test_tags_from_faces_finds_nothing_to_write(self):
        photo, _face = self.removed()
        self.assertEqual(0, tags_from_faces.plan(self.library, guesses=True).details["counts"]["people_to_write"])
        self.assertEqual([], self.tags(photo))

    def test_re_examine_does_not_name_the_face_again(self):
        reference = self.held("rec_ref.jpg", [PEOPLE + WREN])
        self.face(reference, degrees=0, name=WREN, name_source="manual")
        photo, face = self.removed()
        result = faces_service.automatch_folder(self.library, self.folder, lambda: self._decided())
        self.assertEqual(0, result.changed, "a face the owner called nobody is not offered a name")
        self.assertEqual((None, "manual"), self.row(face)[:2])
        self.assertEqual([], self.tags(photo))

    def _decided(self):
        from tagpup.services import identify
        return identify.decided_faces(self.library)[1]

    def test_a_tag_put_back_by_hand_names_no_face(self):
        photo, face = self.removed()
        self.save(photo, [PEOPLE + WREN])           # the owner puts the pill back himself
        self.assertEqual((None, "manual"), self.row(face)[:2], "a face was named from an old record (round two of the review)")

    def test_faces_from_tags_names_nothing_for_it(self):
        _photo, face = self.removed()
        planned = faces_from_tags.plan(self.library)
        self.assertNotIn(face, planned.ids["faces"])


if __name__ == "__main__":
    unittest.main()
