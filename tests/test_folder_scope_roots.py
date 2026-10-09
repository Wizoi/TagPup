"""docs/findings.md, #994: a folder given to a backfill is read through the library's roots, as every folder is.

The real libraries hold `@name/relative` rows. A folder typed in the OLD place of a root (the map still lists it after a move) is
the first place's folder (tagpup.services.roots.canonical), and the photos found under it are the rows of that folder; a folder
under no root, or never indexed, is refused.
"""
import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.services import faces_from_tags, folder_scope, tags_from_faces  # noqa: E402
from tagpup.store import db  # noqa: E402

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AFolderOfARootedLibrary(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="scope_roots_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=3, outside=2)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.old = self.side.pictures
        self.first = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.old, self.first, copy_function=shutil.copy2)
        config.set_location("pictures", self.first)       # the map lists the new place first, the old one after it
        self.folder = rl.FOLDERS[0]

    def rows_under(self, folder_name):
        wanted = "@pictures/" + folder_name.replace(os.sep, "/") + "/"
        return [path for (path,) in self.side.rows("SELECT path FROM photos") if path.startswith(wanted)]

    def test_rows_are_held_as_root_relative_paths(self):
        self.assertTrue(self.rows_under(self.folder), "the fixture holds @pictures/ rows")

    def test_the_old_places_spelling_is_the_first_places_folder(self):
        old_spelling = os.path.join(self.old, self.folder)
        first_spelling = os.path.join(self.first, self.folder)
        by_old = folder_scope.resolve(self.library, old_spelling)
        by_first = folder_scope.resolve(self.library, first_spelling)
        self.assertEqual(first_spelling, by_old.folder)
        self.assertEqual(sorted(by_first.photo_ids), sorted(by_old.photo_ids))
        self.assertEqual(len(self.rows_under(self.folder)), len(by_old.photo_ids))

    def test_a_plan_by_the_old_spelling_is_the_plan_by_the_first(self):
        old_spelling = os.path.join(self.old, self.folder)
        first_spelling = os.path.join(self.first, self.folder)
        a = faces_from_tags.plan(self.library, folder=old_spelling)
        b = faces_from_tags.plan(self.library, folder=first_spelling)
        self.assertIsNone(a.refused)
        self.assertEqual(b.counts, a.counts)
        c = tags_from_faces.plan(self.library, folder=old_spelling)
        d = tags_from_faces.plan(self.library, folder=first_spelling)
        self.assertIsNone(c.refused)
        self.assertEqual(d.details["counts"], c.details["counts"])

    def test_a_folder_under_no_root_is_refused(self):
        outside = self.side.outside_folder
        with self.assertRaises(folder_scope.NoPhotosThere):
            folder_scope.resolve(self.library, os.path.join(self.home.root, "Elsewhere entirely"))
        # Its rows exist (under no root) and are found by their own spelling, as the library holds them.
        self.assertTrue(folder_scope.resolve(self.library, outside).photo_ids)

    def test_a_folder_the_library_never_indexed_is_refused(self):
        refused = faces_from_tags.faces_from_tags(self.library, folder=os.path.join(self.first, "Never indexed"))
        self.assertIn("holds no photo", refused.refused)

    def test_the_whole_root_holds_every_photo_under_it_and_not_the_outside_ones(self):
        scope = folder_scope.resolve(self.library, self.first)
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            total = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(total - len(self.side.outside), len(scope.photo_ids))


if __name__ == "__main__":
    unittest.main()
