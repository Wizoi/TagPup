"""Library: one name for a photo library and the files that belong to it."""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db  # noqa: E402


class NamesALibrary(unittest.TestCase):
    def test_its_parts(self):
        library = Library(os.path.join("data", "photo_index.db"))
        self.assertEqual(library.name, "photo_index")
        self.assertEqual(library.folder, os.path.abspath("data"))
        self.assertEqual(library.taxonomy_file, os.path.join("data", "photo_index_taxonomy.json"))
        self.assertEqual(library.face_trace_file,
                         os.path.join("data", "photo_index_face_resolution_trace.json"))
        self.assertEqual(library.backups, os.path.join(os.path.abspath("data"), "backups"))
        self.assertEqual(library.locks, os.path.join(os.path.abspath("data"), "locks"))

    def test_one_key_for_every_spelling_of_one_file(self):
        relative = Library(os.path.join("data", "photo_index.db"))
        absolute = Library(os.path.abspath(os.path.join("data", "photo_index.db")))
        forward = Library(os.path.abspath("data") + "/photo_index.db")
        self.assertEqual(len({relative.key, absolute.key, forward.key}), 1)
        self.assertEqual(relative, forward)
        self.assertEqual(len({relative, absolute, forward}), 1)

    def test_the_key_is_what_the_servers_filed_state_under(self):
        # Unchanged, so nothing already filed under the old spelling is orphaned.
        path = os.path.join("data", "photo_index.db")
        self.assertEqual(Library(path).key, os.path.abspath(path).replace("\\", "/").lower())

    def test_two_libraries_are_two(self):
        self.assertNotEqual(Library("data/family.db"), Library("data/photo_index.db"))

    def test_no_file_is_no_library(self):
        with self.assertRaises(ValueError):
            Library("")


class BackupsGoBesideTheLibrary(unittest.TestCase):
    """db.backup put its copies two folders above its own module.

    That was the repository while the module was scripts/db.py, and became tagpup/ --
    inside the code -- when it moved to tagpup/store/db.py. The measurement sandbox
    copies tagpup/, so every sandbox would have carried every backup with it.
    """

    def test_a_backup_lands_beside_the_library(self):
        home = tempfile.mkdtemp(prefix="tagpup_backup_")
        self.addCleanup(shutil.rmtree, home, True)
        library = os.path.join(home, "family.db")
        conn = db.connect(library)
        conn.execute("CREATE TABLE t (x)")
        conn.commit()
        conn.close()
        stray = os.path.join(WORKSPACE_DIR, "tagpup", "backups")
        if not os.path.exists(stray):
            self.addCleanup(shutil.rmtree, stray, True)   # where it went wrong

        copy = db.backup(library, "test")

        self.assertEqual(os.path.dirname(copy), os.path.join(home, "backups"))
        self.assertTrue(os.path.basename(copy).startswith("family.before-test-"), copy)
        self.assertTrue(os.path.exists(copy))

    def test_into_still_chooses_the_folder(self):
        home = tempfile.mkdtemp(prefix="tagpup_backup_")
        self.addCleanup(shutil.rmtree, home, True)
        library = os.path.join(home, "family.db")
        db.connect(library).close()
        elsewhere = os.path.join(home, "kept")
        self.assertEqual(os.path.dirname(db.backup(library, "test", into=elsewhere)), elsewhere)


if __name__ == "__main__":
    unittest.main()
