"""TagPup's people cache notices a tree edited by TagTuner.

TagPup resolves every keyword it writes through a cache of who the tree says each
person is (tagpup.store.taxonomy.people_paths). It cleared that cache when it edited
the tree itself, and never otherwise: TagTuner is another process, so a person renamed
there stayed under the old path in TagPup until it restarted, and TagPup wrote the old
path back into the photos it saved.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from tagpup.services.search import PhotoIndex  # noqa: E402

from tagpup.core import vocabulary  # noqa: E402
from tagpup.store import db as tagpup_db  # noqa: E402
from tagpup.store.taxonomy import forget_people_paths, people_paths  # noqa: E402


class PeopleCacheSeesOtherProcesses(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="people_cache_")
        self.db_path = os.path.join(self.dir, "lib.db")
        index = PhotoIndex(self.db_path)
        index.load()
        index.close()
        self.edit("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face) VALUES"
                  " (1, 'People', 'People', NULL, 1),"
                  " (2, 'People/Rowan Thackeray', 'Rowan Thackeray', 1, 1)")
        forget_people_paths()

    def tearDown(self):
        forget_people_paths()
        shutil.rmtree(self.dir, ignore_errors=True)

    def edit(self, sql):
        """A write through its own connection, as another process makes it."""
        conn = tagpup_db.connect(self.db_path)
        try:
            conn.execute(sql)
            conn.commit()
        finally:
            conn.close()

    def test_a_person_renamed_elsewhere_resolves_to_the_new_path(self):
        self.assertEqual("People/Rowan Thackeray", people_paths(self.db_path).get("rowan thackeray"))

        self.edit("UPDATE tag_taxonomy SET tag = 'Family/Rowan Thackeray' WHERE id = 2")
        self.edit("INSERT INTO tag_taxonomy (id, tag, name, parent_id, has_face)"
                  " VALUES (3, 'Family', 'Family', NULL, 1)")

        self.assertEqual("Family/Rowan Thackeray", people_paths(self.db_path).get("rowan thackeray"))
        # What every writer does before writing (tagpup.services.tagging).
        self.assertEqual(["Family/Rowan Thackeray"],
                         vocabulary.resolve_people(["Rowan Thackeray"], people_paths(self.db_path)))

    def test_an_unchanged_tree_is_not_read_again(self):
        first = people_paths(self.db_path)
        self.assertIs(first, people_paths(self.db_path))


if __name__ == "__main__":
    unittest.main()
