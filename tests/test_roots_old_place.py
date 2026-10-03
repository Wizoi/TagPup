"""A path spelled by an OLD place of a root never reaches a file as written (finding #465).

After a move the map lists the new place first and the old one after it, so that every path stays
recognised -- and the row of a path spelled by the old place is the same row (`to_row` recognises every
listed place). A page that still holds the old spelling, a bookmark, a second tab, must not write to the
OLD copy while the dialog says writes go to the first place: the file at the first place would be
untouched, the watcher would see the share's file, and the next sync would refresh the row from it and lose
the tag. Every service that touches a file or a row by a caller's path resolves it through the first place
(tagpup.services.roots.canonical_args) before it does anything.
"""
import hashlib
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import roots_library as rl  # noqa: E402

from tagpup import config  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import indexing, photos, tagging  # noqa: E402
from tagpup.services import roots as roots_service  # noqa: E402
from tagpup.services import sync as sync_service  # noqa: E402
from tagpup.store import photos as store_photos  # noqa: E402

WINDOWS = os.name == "nt"


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def keywords(path):
    with ExifToolSession(executable=rl.EXIFTOOL) as et:
        return et.get_tags([path], tags=["XMP:Subject"])[0].get("XMP:Subject", [])


@unittest.skipUnless(WINDOWS, "spellings below are Windows paths")
class AfterAMove(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="roots_old_")
        self.side = rl.Side(self.home, "photo_index", real=2, bulk=0, outside=0)
        self.assertIsNone(self.side.adopt().refused)
        self.library = self.side.library
        self.old = self.side.pictures
        self.first = os.path.join(self.home.root, "Copy", "Pictures")
        shutil.copytree(self.old, self.first, copy_function=shutil.copy2)
        config.set_location("pictures", self.first)
        self.held = list(self.side.real)           # what a page that was open before the move still holds
        self.moved = [self.first + path[len(self.old):] for path in self.held]
        self.before = {path: digest(path) for path in self.held}

    def old_untouched(self):
        for path in self.held:
            if os.path.exists(path):
                self.assertEqual(self.before[path], digest(path), "the OLD copy was written: " + path)

    def rows_of(self, moved):
        wanted = "@pictures/" + os.path.relpath(moved, self.first).replace(os.sep, "/")
        return [row for row in self.side.rows("SELECT path, mtime, size FROM photos") if row[0].lower() == wanted.lower()]

    def row_matches_the_first_place_file(self, moved):
        stamp = os.stat(moved)
        mine = self.rows_of(moved)
        self.assertEqual(1, len(mine), "the row for the photo exists once")
        self.assertTrue(store_photos.describes(mine[0][1], mine[0][2], (stamp.st_mtime, stamp.st_size)),
                        "the row's stamp is not the first-place file's")

    def nothing_to_refresh(self):
        found = sync_service.sync(self.library, apply=False, exiftool_path=rl.EXIFTOOL, roots=(self.first,))
        self.assertEqual(0, found.details["counts"]["changed"], "a sync would read the rows from the files again")

    def test_roots_canonical_resolves_the_old_spelling_and_leaves_everything_else(self):
        self.assertEqual(self.moved[0], roots_service.canonical(self.library, self.held[0]))
        self.assertEqual(self.moved[0], roots_service.canonical(self.library, self.moved[0]))
        self.assertEqual(self.moved[0].upper(), roots_service.canonical(self.library, self.moved[0].upper()))
        elsewhere = os.path.join(self.home.root, "Elsewhere", "a.jpg")
        self.assertEqual(elsewhere, roots_service.canonical(self.library, elsewhere))
        self.assertEqual("", roots_service.canonical(self.library, ""))
        self.assertEqual(paths.canonical(self.held[0], None), self.held[0])

    def test_a_save_with_the_old_spelling_writes_the_first_place(self):
        result = tagging.save_photo(self.library, self.held[0], "A title", ["Harbour"], None, rl.EXIFTOOL, "")
        self.assertEqual((True, 1), (result.ok, result.changed), result.message())
        self.assertIn("Harbour", keywords(self.moved[0]))
        self.old_untouched()
        self.row_matches_the_first_place_file(self.moved[0])
        self.nothing_to_refresh()

    def test_bulk_tags_with_the_old_spelling_write_the_first_place(self):
        result = tagging.change_tags(self.library, self.held, ["Harbour"], [], rl.EXIFTOOL)
        self.assertEqual(len(self.held), result.changed, result.message())
        for moved in self.moved:
            self.assertIn("Harbour", keywords(moved))
            self.row_matches_the_first_place_file(moved)
        self.old_untouched()
        self.nothing_to_refresh()

    def test_a_rename_with_the_old_spelling_renames_at_the_first_place(self):
        result = photos.smart_rename(self.library, self.held, "Regatta", "{grouping} - {index}", rl.EXIFTOOL)
        self.assertEqual((True, len(self.held)), (result.ok, result.changed), result.message())
        self.old_untouched()
        for new in result.details["renamed"].values():
            self.assertTrue(paths.is_under(new, self.first), new)
            self.assertTrue(os.path.exists(new))
        self.assertEqual([], [p for p in self.moved if os.path.exists(p)], "the first place's files were renamed")

    def test_a_delete_with_the_old_spelling_deletes_the_first_place_file_and_its_row(self):
        removed = []

        def recycle(path):
            removed.append(path)
            os.remove(path)
            return True

        with mock.patch("tagpup.files.recycle_bin.send_to_recycle_bin", side_effect=recycle):
            result = photos.delete(self.library, self.held[0])
        self.assertEqual(1, result.changed, result.message())
        self.assertEqual([self.moved[0]], removed)
        self.assertFalse(os.path.exists(self.moved[0]))
        self.old_untouched()
        self.assertTrue(os.path.exists(self.held[0]))
        self.assertEqual([], self.rows_of(self.moved[0]), "the row went with the file")
        self.assertEqual(1, len(self.rows_of(self.moved[1])))

    def test_a_rotate_with_the_old_spelling_turns_the_first_place_file(self):
        result = photos.rotate(self.library, self.held[0], "right", rl.EXIFTOOL)
        self.assertEqual(1, result.changed, result.message())
        self.assertNotEqual(self.before[self.held[0]], digest(self.moved[0]))
        self.old_untouched()
        self.row_matches_the_first_place_file(self.moved[0])
        self.nothing_to_refresh()

    def test_a_folder_made_later_under_the_old_place_is_added_and_indexed_at_the_first_place(self):
        later = os.path.join(self.old, "Made later")
        os.makedirs(later)
        queued = []

        class Done:
            details, skipped, errors, refused = {}, [], [], None

        library_actions.add(self.library, [later], lambda folders: queued.append(list(folders)) or Done())
        self.assertEqual([[os.path.join(self.first, "Made later")]], queued,
                         "the queue, and so the indexer, is handed the first place's folder")
        spawned = []

        class Child:
            returncode = 0
            stdout = mock.MagicMock()

            def wait(self):
                return None

        Child.stdout.readline.side_effect = [""]
        Child.stdout.__enter__ = lambda s: s
        Child.stdout.__exit__ = lambda s, *a: False
        with mock.patch.object(indexing.processes, "start", side_effect=lambda args, **kw: spawned.append(args) or Child()):
            indexing.index_folder(self.library, queued[0][0], os.getcwd())
        self.assertIn(os.path.join(self.first, "Made later"), spawned[0])
        self.assertNotIn(later, spawned[0])


if __name__ == "__main__":
    unittest.main()
