"""dedupe_faces keeps the copy a person decided about, and never drops an exclusion.

Of tagpup.services.duplicate_faces, which scripts/dedupe_faces.py runs.

Two copies of one face -- same photo, same box -- are merged by keeping the copy that
"knows something". A name counted for more than anything else, so a name clustering
gave outranked a person's own decision: an excluded passer-by, or a face marked
nobody, lost to its unexcluded, auto-named twin, and the decision went with the row.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import duplicate_faces  # noqa: E402
from tagpup.services.search import PhotoIndex  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from face_rows import add_face  # noqa: E402

PHOTO = r"D:\Pictures\Regatta\start.jpg"


class DedupeKeepsDecisions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dedupe_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.db = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db)
        index.load()
        index.close()
        self.execute("INSERT INTO photos (path) VALUES (?)", (PHOTO,))

    def execute(self, sql, params=()):
        conn = tagpup_db.connect(self.db)
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def face(self, name=None, source=None, excluded=0):
        conn = tagpup_db.connect(self.db)
        try:
            face_id = add_face(conn, PHOTO, box="[1, 2, 3, 4]", name=name, name_source=source,
                               excluded=excluded)
            conn.commit()
            return face_id
        finally:
            conn.close()

    def plan(self):
        result = duplicate_faces.dedupe_faces(Library(self.db))
        return set(result.details["ids"]["redundant"]), result.details["ids"]["disputed"]

    def test_an_exclusion_is_not_dropped_for_an_auto_named_copy(self):
        self.face(name="Rowan Thackeray")
        excluded = self.face(source="manual", excluded=1)
        redundant, disputed = self.plan()
        self.assertNotIn(excluded, redundant, "the exclusion was the copy thrown away")

    def test_a_named_copy_beside_an_excluded_one_is_left_for_a_person(self):
        self.face(name="Rowan Thackeray")
        self.face(source="manual", excluded=1)
        redundant, disputed = self.plan()
        self.assertEqual(set(), redundant)
        self.assertEqual(1, len(disputed))

    def test_a_face_marked_nobody_outranks_a_name_clustering_gave(self):
        auto = self.face(name="Rowan Thackeray")
        nobody = self.face(source="manual")
        redundant, _ = self.plan()
        self.assertEqual({auto}, redundant)
        self.assertNotIn(nobody, redundant)

    def test_a_bare_redetection_still_goes(self):
        named = self.face(name="Rowan Thackeray", source="manual")
        bare = self.face()
        redundant, _ = self.plan()
        self.assertEqual({bare}, redundant)
        self.assertNotIn(named, redundant)


if __name__ == "__main__":
    unittest.main()
