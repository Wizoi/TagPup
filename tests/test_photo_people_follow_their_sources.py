"""A photo's people are what its keywords, its faces and the tag tree make them, however
any of the three changed (tagpup.store.people; docs/findings.md, #42, #63).

They were a list seven face actions patched by a rule of their own: unnaming a face took
the person off even when a keyword still named them, and a name was added by exact case
where the rule ignores case. Clustering, re-detection and tree edits changed who was in
a photo and updated nothing.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face, people_of  # noqa: E402
from service_fixture import TempLibrary  # noqa: E402

from tagpup.store import checks, db, faces, generations, people, photos, taxonomy  # noqa: E402

WREN = "Wren Halloway"
ODA = "Oda Castellane"


class PhotoPeople(unittest.TestCase):
    def setUp(self):
        self.lib = TempLibrary(self)
        self.photo = self.lib.photo("dunes.jpg")
        self.lib.add_row(self.photo, tags=["People/" + WREN, "Places/Dunes"])
        self.write(lambda conn: people.rebuild(conn))

    def write(self, operation):
        return db.write_with_connection(self.lib.library.path, operation)

    def listed(self):
        return self.read(lambda conn: people_of(conn, self.photo))

    def read(self, operation):
        conn = db.connect(db.readonly_uri(self.lib.library.path), uri=True)
        try:
            return operation(conn)
        finally:
            conn.close()

    def face(self, name=None):
        return self.write(lambda conn: add_face(conn, self.photo, name=name))

    def test_a_keyword_names_them(self):
        self.assertEqual([WREN], self.listed())

    def test_naming_a_face_adds_them(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], ODA))
        self.assertEqual([WREN, ODA], self.listed())

    def test_unnaming_a_face_keeps_someone_a_keyword_still_names(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], WREN))
        self.write(lambda conn: faces.unname(conn, [face_id]))
        self.assertEqual([WREN], self.listed())

    def test_a_face_named_in_another_case_is_not_listed_twice(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], WREN.upper()))
        self.assertEqual([WREN], self.listed())

    def test_excluding_a_face_takes_its_name_off(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], ODA))
        self.assertEqual([WREN, ODA], self.listed())
        self.write(lambda conn: faces.exclude(conn, [face_id], "stranger"))
        self.assertEqual([WREN], self.listed())

    def test_clustering_naming_faces_adds_them(self):
        face_id = self.face()
        self.write(lambda conn: faces.set_names(conn, {face_id: ODA}))
        self.assertEqual([WREN, ODA], self.listed())

    def test_detecting_the_faces_again_takes_off_whom_only_a_face_named(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], ODA))
        self.assertEqual([WREN, ODA], self.listed())
        self.write(lambda conn: faces.remove_for_photo(conn, self.photo))
        self.assertEqual([WREN], self.listed())

    def test_a_keyword_write_keeps_whom_a_face_names(self):
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], ODA))
        photos.record_tags(self.lib.library.path, self.photo, ["Places/Dunes"])
        self.assertEqual([ODA], self.listed())

    def test_a_root_flagged_as_holding_faces_names_the_people_under_it(self):
        # A tree edit changes who a keyword names, and the photos carrying it follow.
        self.lib.add_row(self.lib.photo("litter.jpg"), tags=["Pets/Biscuit"])
        self.write(lambda conn: (taxonomy.add_path(conn, "Pets/Biscuit"), people.rebuild(conn)))
        litter = os.path.join(self.lib.photos, "litter.jpg")
        self.assertEqual([], self.read(lambda conn: people_of(conn, litter)))
        self.write(lambda conn: taxonomy.set_branch_flags(conn, "Pets", has_face=1))
        self.assertEqual(["Biscuit"], self.read(lambda conn: people_of(conn, litter)))
        self.write(lambda conn: taxonomy.set_branch_flags(conn, "Pets", has_face=0))
        self.assertEqual([], self.read(lambda conn: people_of(conn, litter)))

    def test_a_keyword_that_is_only_a_separator_does_not_stop_a_tree_edit(self):
        # It has no first segment, and reading one failed the edit (#88).
        self.lib.add_row(self.lib.photo("odd.jpg"), tags=["/"])
        self.write(lambda conn: taxonomy.add_path(conn, "Pets"))
        self.write(lambda conn: taxonomy.set_branch_flags(conn, "Pets", has_face=1))

    def test_restoring_a_face_both_named_and_excluded_lists_them_again(self):
        # Such faces have happened (the doctor watches for them); restored, the face
        # counts again (#89).
        face_id = self.face()
        self.write(lambda conn: conn.execute("UPDATE faces SET name = ?, excluded = 1 WHERE id = ?", (ODA, face_id)))
        self.write(lambda conn: faces.restore(conn, [face_id]))
        self.assertEqual([WREN, ODA], self.listed())

    def test_what_a_time_shift_read_back_is_a_source_of_them_too(self):
        # A file's person fields name people as its keywords do (#89).
        stat = os.stat(self.photo)
        photos.record_reads(self.lib.library.path, [{
            "path": self.photo, "raw_metadata": {"XMP:PersonInImage": [ODA]},
            "mtime": stat.st_mtime, "size": stat.st_size}])
        self.assertIn(ODA, self.listed())

    def test_they_go_with_the_photo(self):
        self.write(lambda conn: conn.execute("DELETE FROM photos"))
        self.assertEqual([(0,)], self.lib.rows("SELECT COUNT(*) FROM photo_people"))

    def test_a_change_moves_the_photos_generation(self):
        before = self.read(lambda conn: generations.value(conn, "photos"))
        face_id = self.face()
        self.write(lambda conn: faces.name(conn, [face_id], ODA))
        self.assertGreater(self.read(lambda conn: generations.value(conn, "photos")), before)

    def test_readers_see_them_as_the_list_they_always_had(self):
        rows = self.read(lambda conn: photos.index_rows(conn, "any model"))
        self.assertEqual('["%s"]' % WREN, rows[0][4])

    def test_the_doctor_finds_a_photo_whose_people_were_written_by_hand(self):
        self.write(lambda conn: conn.execute("UPDATE photo_people SET name = 'Somebody Else'"))
        found = self.read(lambda conn: checks.people_out_of_date(conn))
        self.assertEqual(1, found.count)


if __name__ == "__main__":
    unittest.main()
