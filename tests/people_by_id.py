"""A library with two people called alike, for the tests of People by id, stage 2 (docs/ARCHITECTURE.md): the cousins
Sam Thackeray-side and Sam Ingersoll-side, a dog and a friend called Max, and a group.

The tree is made as TagTuner's tree view makes it (taxonomy.add_path inside people.tree_edit); a photo is what the indexer
records of a read (tests/photo_rows.py); a face is what the store inserts (tagpup.store.faces.insert). Nothing here is
seeded through the helper under test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db, faces, people, schema, taxonomy  # noqa: E402

SAM_T, SAM_I = "Family/Thackeray/Sam", "Family/Ingersoll/Sam"
MAX_PET, MAX_FRIEND = "Pets/Max", "Friends/Max"
WREN = "People/Wren Halloway"
TREE = (SAM_T, SAM_I, MAX_PET, MAX_FRIEND, WREN, "Places/Coast")


def look(path, sql, params=()):
    conn = db.connect(db.readonly_uri(path), uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def write(path, operation):
    return db.write_with_connection(path, operation)


class TwoSams:
    """Mixin for a unittest.TestCase: `self.path`, `self.library`, `self.photo` (a photo whose keyword is Sam Thackeray's path),
    `self.other`, and `self.faces` (three faces of `photo`, `photo`, `other`), unnamed."""

    def make_library(self, name="harbour.db"):
        self.home = own_home.for_test(self)
        self.path = self.home.library(name)
        schema.ensure(self.path)
        self.library = Library(self.path)
        root = os.path.dirname(self.path)
        self.photo = os.path.join(root, "Pictures", "regatta_001.jpg")
        self.other = os.path.join(root, "Pictures", "regatta_002.jpg")

        def seed(conn):
            with people.tree_edit(conn):
                for root_tag, has_face in (("Family", 1), ("Pets", 1), ("Friends", 1), ("People", 1), ("Places", 0)):
                    taxonomy.add_path(conn, root_tag, root_has_face=has_face)
                for tag in TREE:
                    taxonomy.add_path(conn, tag)
            photo_rows.add_read(conn, self.photo, {"XMP:Subject": [SAM_T, "Places/Coast"]})
            photo_rows.add_read(conn, self.other, {"XMP:Subject": []})
            return [faces.insert(conn, self.photo, [0, 0, 10, 10], b"\x00" * 8),
                    faces.insert(conn, self.photo, [20, 0, 30, 10], b"\x01" * 8),
                    faces.insert(conn, self.other, [0, 0, 10, 10], b"\x02" * 8)]

        self.faces = write(self.path, seed)

    def node(self, tag):
        found = look(self.path, "SELECT id FROM tag_taxonomy WHERE tag = ?", (tag,))
        return found[0][0] if found else None

    def face(self, face_id):
        """(tag_id, name, name_source) of a face."""
        return look(self.path, "SELECT tag_id, name, name_source FROM faces WHERE id = ?", (face_id,))[0]

    def listed(self, photo):
        """[(tag_id, name, source)] of the people a photo lists."""
        return look(self.path, "SELECT pp.tag_id, pp.name, pp.source FROM photo_people pp JOIN photos p ON p.id = pp.photo_id"
                               " WHERE p.path = ? ORDER BY pp.position", (photo,))
