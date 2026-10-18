"""No writer leaves a person without the id of a node (owner, 2026-10-11: "we should only use the id").

A photo_people row, or a face with a name, holds `tag_id`. Each writer is run here over the library of two people called alike
(tests/people_by_id.py) with the inputs that used to leave a name and no id: a name two people have, a group's name, a bare
keyword that is a person's name, a keyword under a person faces use. A writer either gives the name a node or refuses the
write; it never leaves a row that names a person by name alone. Photos are what the indexer records of a read
(tests/photo_rows.py), faces and tags are written by the store and the services.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import people_by_id  # noqa: E402
from face_rows import FACENET  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.services import tags as tag_service  # noqa: E402
from tagpup.store import faces, person_ids  # noqa: E402


class NoRowWithoutAnId(unittest.TestCase, people_by_id.TwoSams):
    def setUp(self):
        self.make_library()
        self.later = os.path.join(os.path.dirname(self.photo), "regatta_010.jpg")

    def none_without_an_id(self, what):
        """Fails (naming the input `what`) when a listed person or a face with a name holds no id; the rows are cleared so the
        next input is judged alone."""
        def count(conn):
            return (conn.execute("SELECT COUNT(*) FROM photo_people WHERE tag_id IS NULL").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM faces WHERE name IS NOT NULL AND name != '' AND tag_id IS NULL").fetchone()[0])

        def clear(conn):
            found = count(conn)
            conn.execute("DELETE FROM photo_people WHERE tag_id IS NULL")
            conn.execute("UPDATE faces SET name = NULL WHERE tag_id IS NULL")
            return found
        with self.subTest(what):
            self.assertEqual((0, 0), people_by_id.write(self.path, clear))

    def read(self, subject=()):
        fields = {"XMP:Subject": list(subject)}
        people_by_id.write(self.path, lambda conn: photo_rows.add_read(conn, self.later, fields))

    def use_sam(self):
        node = self.node(people_by_id.SAM_T)
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[0]], node))

    def test_a_keyword_read_by_the_indexer(self):
        self.use_sam()
        for tag in ("Wren Halloway", "Sam", "Family/Thackeray", "Family", "Places", "People/Zed Quill", "Pets/Rex Quill",
                    "Trips/Harbour", people_by_id.SAM_T + "/Swim Team", "Thackeray", "Max"):
            self.read(subject=[tag])
            self.none_without_an_id(tag)

    def test_a_face_named_by_a_typed_name(self):
        for name in ("Zed Quill", "Sam", "Thackeray", "Places", "Family", "Max"):
            try:
                people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[1]], name))
            except (person_ids.PersonProblem, ValueError):
                pass   # the write is refused: a name two people have, a group's, or no person's node (face_people.made makes one)
            self.none_without_an_id(name)

    def test_a_face_detected_with_a_name(self):
        for name in ("Zed Quill", "Sam", "Thackeray", "Places"):
            try:
                people_by_id.write(self.path, lambda conn: faces.insert(conn, self.other, [5, 5, 9, 9], b"\x09" * 8, name=name, model=FACENET))
            except (person_ids.PersonProblem, ValueError):
                pass
            self.none_without_an_id(name)

    def test_a_tag_made_renamed_and_merged_in_the_tree(self):
        self.use_sam()
        made = tag_service.create(self.library, "People/Zed Quill", has_face=1)
        self.none_without_an_id("made")
        tag_service.rename_person(self.library, made.details["id"], "Zed Quill Jr", None)
        self.none_without_an_id("renamed")
        tag_service.merge(self.library, "People/Zed Quill Jr", people_by_id.WREN, None, apply=True)
        self.none_without_an_id("merged")

    def test_the_undo_of_a_person_deleted_with_force_or_merged_away(self):
        from tagpup.services import journal as journal_service
        wren = self.node(people_by_id.WREN)
        people_by_id.write(self.path, lambda conn: faces.name(conn, [self.faces[1]], wren))
        tag_service.delete(self.library, wren, "remove", None, "exiftool", force=True)
        for change, in people_by_id.look(self.path, "SELECT id FROM changes ORDER BY id"):
            journal_service.undo(self.library, change, apply=True, exiftool_path="exiftool")   # refused: the person is gone
            self.none_without_an_id("undo of change %d" % change)

    def test_a_photo_read_again_after_a_person_is_renamed(self):
        self.read(subject=[people_by_id.WREN])
        tag_service.rename_person(self.library, self.node(people_by_id.WREN), "Wren Halloway Jr", None)
        self.read(subject=[people_by_id.WREN])
        self.none_without_an_id("read after rename")


if __name__ == "__main__":
    unittest.main()
