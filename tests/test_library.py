"""Library: one name for a photo library and the files that belong to it."""
import os
import shutil
import sys
import tempfile
import unittest

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

from tagpup.core import library  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.store import db  # noqa: E402


class NamesALibrary(unittest.TestCase):
    def test_its_parts(self):
        library = Library(os.path.join("libraries", "photo_index.db"))
        self.assertEqual(library.name, "photo_index")
        self.assertEqual(library.folder, os.path.abspath("libraries"))
        self.assertEqual(library.face_trace_file,
                         os.path.join("libraries", "photo_index_face_resolution_trace.json"))
        self.assertEqual(library.backups, os.path.join(os.path.abspath("libraries"), "backups"))
        self.assertEqual(library.locks, os.path.join(os.path.abspath("libraries"), "locks"))

    def test_one_key_for_every_spelling_of_one_file(self):
        relative = Library(os.path.join("libraries", "photo_index.db"))
        absolute = Library(os.path.abspath(os.path.join("libraries", "photo_index.db")))
        forward = Library(os.path.abspath("libraries") + "/photo_index.db")
        self.assertEqual(len({relative.key, absolute.key, forward.key}), 1)
        self.assertEqual(relative, forward)
        self.assertEqual(len({relative, absolute, forward}), 1)

    def test_the_key_is_what_the_servers_filed_state_under(self):
        # Unchanged, so nothing already filed under the old spelling is orphaned.
        path = os.path.join("libraries", "photo_index.db")
        self.assertEqual(Library(path).key, os.path.abspath(path).replace("\\", "/").lower())

    def test_two_libraries_are_two(self):
        self.assertNotEqual(Library("libraries/family.db"), Library("libraries/photo_index.db"))

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


class ThePicker(unittest.TestCase):
    """What both servers and the runner offer, choose and create -- one set of rules."""

    FOLDER = ["photo_index.db", "kr-track.db", "notes.txt", "test_photo_index.db",
              "test_kr-track.db"]

    def test_it_offers_the_libraries_and_nothing_else(self):
        self.assertEqual(library.picker_names(self.FOLDER, test_mode=False), ["kr-track", "photo_index"])

    def test_a_test_server_offers_only_the_test_libraries_by_their_plain_names(self):
        self.assertEqual(library.picker_names(self.FOLDER, test_mode=True), ["kr-track", "photo_index"])
        self.assertEqual(library.picker_names(["photo_index.db"], test_mode=True), ["photo_index"],
                         "an empty list offers the first library")

    def test_no_library_is_hidden_for_its_name(self):
        # These were hidden (NOT_LIBRARIES), and could not be created: files the tests
        # left in the checkout's data folder, and a cache nothing makes any more (#14).
        once_hidden = ["validation_index.db", "validation_perf.db", "multiple_db_startup.db",
                       "tag_emb_cache.db"]
        offered = sorted(name[:-3] for name in once_hidden)
        self.assertEqual(library.picker_names(once_hidden, test_mode=False), offered)
        self.assertEqual(library.picker_names(["test_" + name for name in once_hidden],
                                              test_mode=True), offered)
        for name in once_hidden:
            self.assertIsNone(library.problem_with_new_name(name), name)

    def test_a_name_from_the_page_names_a_file(self):
        self.assertEqual(library.file_name_for("Harbour"), "Harbour.db")
        self.assertEqual(library.file_name_for("test_Harbour.db"), "Harbour.db")
        self.assertEqual(library.picker_name("test_photo_index.db"), "photo_index")

    def test_a_new_library_needs_a_plain_name(self):
        self.assertIsNone(library.problem_with_new_name("kr-track_2.db"))
        self.assertIsNotNone(library.problem_with_new_name("kr track.db"))

    def test_a_name_the_urls_route_is_refused(self):
        # Created, it could never be opened: /api/ reaches the API (#73).
        for taken in ("api.db", "gui.db", "gui_tagpup.db", "API.db"):
            self.assertIsNotNone(library.problem_with_new_name(taken), taken)
        self.assertIsNone(library.problem_with_new_name("apiary.db"))


if __name__ == "__main__":
    unittest.main()
