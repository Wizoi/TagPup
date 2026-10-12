"""Review of a1821cf (docs/findings.md, #950-#956): what a change of photo files really did to the people on its photos decides what
happens to their faces (one place: tagpup.services.face_people.follow_change); a tag put back names the face the removal unnamed;
(a bulk assignment's own undo is the change's, tests/test_bulk_faces_and_tags.py).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_faces_and_tags_in_step import PEOPLE, InStep  # noqa: E402
from test_organize_faces import ODA, WREN  # noqa: E402

from tagpup.services import bulk_edit  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.store import db, faces  # noqa: E402

UNNAMED = "face unnamed for a person taken off the photo"
RENAMED = "face named again for a person put back on the photo"


class Case(InStep):
    def save(self, photo, tags):
        reply = self.client.post("/library/api/photo/save-metadata", json={"path": photo, "title": "", "tags": list(tags)})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def bulk_tags(self, paths_, add=(), remove=()):
        reply = self.client.post("/library/api/photos/bulk-tags",
                                 json={"paths": list(paths_), "add_tags": list(add), "remove_tags": list(remove)})
        self.assertEqual(200, reply.status_code, reply.get_json())
        return reply.get_json()

    def change_ids(self, operation):
        return [row[0] for row in self.look("SELECT id FROM changes WHERE operation = ? ORDER BY id", (operation,))]


class OnlyWhatReallyChangedIsFollowed(Case):
    def photos(self):
        has = self.held("lost_has.jpg", [PEOPLE + WREN, "Regatta"])
        lacks = self.held("lost_lacks.jpg", ["Regatta"])
        return has, self.face(has, name=WREN, name_source="manual"), lacks, self.face(lacks, name=WREN, name_source="manual")

    def test_a_selection_taking_a_person_off_leaves_the_hand_named_face_of_a_photo_that_never_held_the_tag(self):
        has, on_has, lacks, on_lacks = self.photos()
        reply = self.bulk_tags([has, lacks], remove=[PEOPLE + WREN])
        self.assertEqual((None, "manual"), self.row(on_has)[:2], "the photo that held the tag lost the person")
        self.assertEqual((WREN, "manual"), self.row(on_lacks)[:2], "a hand-named face of a photo that never held the tag was unnamed")
        self.assertEqual([{"id": on_has, "name": WREN}], reply["unnamed_faces"])

    def test_the_bulk_edit_is_held_to_the_same(self):
        has, on_has, lacks, on_lacks = self.photos()
        ids = [row[0] for row in self.look("SELECT id FROM photos ORDER BY id")]
        edit = bulk_edit.prepare(self.library, bulk_edit.TAGS, {"remove": [PEOPLE + WREN]})
        out = bulk_edit.run(self.library, edit, ids, "exiftool")
        self.assertEqual(1, out.changed)
        self.assertEqual((None, "manual"), self.row(on_has)[:2])
        self.assertEqual((WREN, "manual"), self.row(on_lacks)[:2])


class APersonPutBackNamesNoFaceByItself(Case):
    """Round two: a decision made since is not in the journal, so a tag put back by hand never names a face from an old record;
    History's Undo of the removal is the one exact inverse, and the rules that name faces only block on the record."""

    def two_faces(self):
        photo = self.held("back_001.jpg", [PEOPLE + WREN, "Regatta"])
        return photo, self.face(photo, name=WREN, name_source="manual"), self.face(photo, box=(70, 10, 110, 60))

    def test_the_pill_off_and_on_names_no_face_and_the_rule_does_not_name_the_other(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.assertEqual((None, "manual"), self.row(wren)[:2])
        reply = self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertEqual((None, "manual"), self.row(wren)[:2], "a face was named from an old record")
        self.assertEqual((None, None), self.row(other)[:2], "the tag alone named the face that was never anybody's")
        self.assertNotIn("renamed_faces", reply)

    def test_control_z_in_tagpup_names_no_face_either(self):
        photo, wren, other = self.two_faces()
        self.bulk_tags([photo], remove=[PEOPLE + WREN])
        self.bulk_tags([photo], add=[PEOPLE + WREN])          # web/tagpup/undo.js: the undo re-adds through bulk-tags
        self.assertEqual([(None, "manual"), (None, None)], [self.row(wren)[:2], self.row(other)[:2]])

    def test_tagpups_order_save_then_match_ends_with_the_other_face_the_person(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.save(photo, ["Regatta", PEOPLE + WREN])           # the page writes the tag first ...
        reply = self.post("face/match", {"face_id": other, "person_name": WREN, "page_writes_tags": True})   # ... then names
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([(None, "manual"), (WREN, "manual")], [self.row(wren)[:2], self.row(other)[:2]])

    def test_tagtuners_order_the_server_writes_the_tag_then_names_the_face(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        reply = self.tuner_post("face/match", {"face_id": other, "person_name": WREN})
        self.assertEqual(200, reply.status_code, reply.get_json())
        self.assertEqual([(None, "manual"), (WREN, "manual")], [self.row(wren)[:2], self.row(other)[:2]])

    def test_naming_jobs_only_block_on_the_record(self):
        from tagpup.services import faces as faces_service
        from tagpup.services import faces_from_tags, identify
        reference = self.held("ref.jpg", [PEOPLE + WREN])
        self.face(reference, degrees=0, name=WREN, name_source="manual")
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        self.save(photo, ["Regatta", PEOPLE + WREN])
        self.assertNotIn(other, faces_from_tags.plan(self.library).ids["faces"])
        proposed = faces_service.automatch_folder(self.library, self.folder, lambda: identify.decided_faces(self.library)[1])
        self.assertEqual(0, proposed.details["faces"], "Re-examine named a face as the person the owner took off this photo")
        self.assertEqual((None, None), self.row(other)[:2])

    def test_historys_undo_of_the_removal_is_the_exact_inverse_and_names_the_face(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        removal = self.change_ids("save photo")[-1]
        unnamed = self.change_ids(UNNAMED)[-1]
        undone = journal_service.undo(self.library, removal, apply=True, exiftool_path="exiftool")
        self.assertEqual([], undone.errors, undone.errors)
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)))
        self.assertEqual([(WREN, "manual"), (None, None)], [self.row(wren)[:2], self.row(other)[:2]])
        self.assertEqual([{"id": wren, "name": WREN}], undone.details["renamed_faces"])
        self.assertTrue(journal_service.undo(self.library, unnamed, apply=True).refused, "undone twice")

    def test_a_decision_made_since_stops_the_inverse(self):
        photo, wren, other = self.two_faces()
        self.save(photo, ["Regatta"])
        removal = self.change_ids("save photo")[-1]
        db.write_with_connection(self.path, lambda conn: faces.name(conn, [wren], ODA))       # by hand, not journaled
        undone = journal_service.undo(self.library, removal, apply=True, exiftool_path="exiftool")
        self.assertEqual(ODA, self.row(wren)[0], "the owner's newer decision was overwritten")
        self.assertEqual({PEOPLE + WREN, "Regatta"}, set(self.tags(photo)), "the tag itself went back")
        self.assertIn("changed since", undone.details["faces_problem"])


class TheQueryIsFast(Case):
    def test_a_photos_named_faces_are_not_found_through_the_index_of_every_named_face(self):
        photo = self.held("plan.jpg")
        self.face(photo, name=WREN)
        statements = []
        conn = db.connect(db.readonly_uri(self.path), uri=True)
        self.addCleanup(conn.close)
        conn.set_trace_callback(statements.append)
        faces.named_in_photo(conn, photo)
        faces.nobody_in_photo(conn, photo)
        conn.set_trace_callback(None)
        self.assertTrue(statements)
        for sql in statements:
            if sql.lstrip().upper().startswith(("SELECT", "WITH")):
                plan = " ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql))
                self.assertNotIn("idx_faces_identify", plan, sql)


if __name__ == "__main__":
    unittest.main()
