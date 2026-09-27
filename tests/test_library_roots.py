"""A library's root folders and ignored folders: two settings (tagpup.services.settings;
docs/ARCHITECTURE.md, phase 8). A library has no roots until the owner sets them -- an
ordinary journaled change, which can be undone; nothing sets them on its own (owner,
2026-09-26: an automatic root of a subset library would have ignored 81,148 photos,
beyond undoing, had the server started before the owner set them).

Rows are made as Suggest makes them (tests/photo_rows): the path alone, which is all the
roots are computed from.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings  # noqa: E402
from tagpup.store import db  # noqa: E402

BS = chr(92)
NL = chr(10)


def path(*parts):
    return BS.join(parts)


class NoRootsUntilSet(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)

    def add(self, *photo_paths):
        conn = db.connect(self.db_path)
        try:
            for photo_path in photo_paths:
                photo_rows.add_unread(conn, photo_path)
            conn.commit()
        finally:
            conn.close()

    def held(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return dict(conn.execute("SELECT key, value FROM settings"))
        finally:
            conn.close()

    def test_a_library_holding_photos_has_no_roots_until_they_are_set(self):
        self.add(path("D:", "Photos", "2019", "IMG_0001.jpg"), path("D:", "Photos", "2020", "IMG_0002.jpg"))
        self.assertEqual([], settings.of(self.library).roots)
        self.assertEqual([], settings.read(self.library).roots)
        self.assertNotIn(settings.ROOTS, self.held())

    def test_roots_set_are_an_ordinary_change_that_is_undone(self):
        settings.of(self.library)
        changed = settings.change(self.library, {settings.ROOTS: path("D:", "Photos")})
        self.assertEqual([path("D:", "Photos")], settings.of(self.library).roots)
        entry = journal_service.history(self.library, changed.details["change"])["changes"][0]
        self.assertEqual(settings.CHANGE, entry["operation"])
        self.assertTrue(journal_service.undo(self.library, changed.details["change"], apply=True).ok)
        self.assertEqual([], settings.of(self.library).roots)

    def test_roots_set_to_none_stay_none(self):
        self.add(path("D:", "Photos", "2019", "IMG_0001.jpg"))
        settings.of(self.library)
        changed = settings.change(self.library, {settings.ROOTS: ""})
        self.assertTrue(changed.ok, changed.message())
        self.assertEqual([], settings.of(self.library).roots)

    def test_ignoring_a_folder_is_a_journaled_change_once(self):
        settings.of(self.library)
        folder = path("D:", "Photos", "Scans")
        first = settings.ignore_folder(self.library, folder)
        self.assertEqual(1, first.changed)
        self.assertIsNotNone(first.details["change"])
        self.assertEqual(0, settings.ignore_folder(self.library, path("d:", "photos", "scans")).changed)
        self.assertEqual([folder], settings.of(self.library).ignored)
        self.assertTrue(settings.ignore_folder(self.library, "Scans").refused)

    def test_the_owners_roots_are_taken(self):
        for value in (path("D:", "Training", "Pictures"),
                      path("D:", "Training", "Pictures", "Clients or Groups", "Cross Country"),
                      NL.join([path("D:", "Training", "Pictures"), path("C:", "localdepot")])):
            self.assertIsNone(settings.validation.problem("setting library.roots", value), value)

    def test_the_cli_sets_roots_as_a_dry_run_then_applied(self):
        from click.testing import CliRunner
        from tagpup_cli import cli

        settings.of(self.library)
        roots = os.path.join(self.home.root, "Training", "Pictures")
        os.makedirs(roots)
        dry = CliRunner().invoke(cli, ["--db", self.db_path, "settings", "set", settings.ROOTS, roots])
        self.assertEqual(0, dry.exit_code, dry.output)
        self.assertIn("Would change library.roots", dry.output)
        self.assertNotIn(settings.ROOTS, self.held())
        applied = CliRunner().invoke(cli, ["--db", self.db_path, "settings", "set", settings.ROOTS, roots, "--apply"])
        self.assertEqual(0, applied.exit_code, applied.output)
        self.assertEqual(roots, self.held()[settings.ROOTS])
        refused = CliRunner().invoke(cli, ["--db", self.db_path, "settings", "set", settings.ROOTS, "Pictures"])
        self.assertEqual(1, refused.exit_code, refused.output)
        shown = CliRunner().invoke(cli, ["--db", self.db_path, "settings"])
        self.assertIn("library.roots = " + roots, shown.output)

    def test_folders_are_changed_one_a_line_and_a_drive_is_refused(self):
        settings.of(self.library)
        folders = NL.join([path("D:", "Photos"), path("E:", "Meets")])
        self.assertTrue(settings.change(self.library, {settings.IGNORED: folders}).ok)
        self.assertEqual([path("D:", "Photos"), path("E:", "Meets")], settings.of(self.library).ignored)
        refused = settings.change(self.library, {settings.ROOTS: path("D:", "")})
        self.assertIn("A whole drive", refused.refused)


class ADryRunOnALibraryBehind(unittest.TestCase):
    """`settings set` without --apply writes nothing -- nor migrates a library behind this
    version, which reading its settings for a change would: it reads them without
    migrating and says how far behind the library is, as a maintenance dry run does."""

    def test_the_file_is_left_as_it_was(self):
        from test_migrations import at_version
        from tagpup.store import schema

        home = own_home.for_test(self)
        older = home.library("older.db")
        at_version(older, schema.LATEST - 1)
        conn = db.connect(older)
        try:
            conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?)", sorted(settings.STAMPED.items()))
            conn.commit()
        finally:
            conn.close()
        with open(older, "rb") as handle:
            before = handle.read()
        dry = settings.change(Library(older), {settings.ROOTS: os.path.join(home.root, "Pictures")}, apply=False)
        with open(older, "rb") as handle:
            self.assertEqual(before, handle.read(), "a dry run migrated the library")
        self.assertIsNone(dry.refused)
        self.assertEqual((True, 1, [settings.ROOTS]), (dry.details["dry_run"], dry.details["behind"],
                                                         dry.details["changed"]))


class FoldersNotAddedAreExcluded(unittest.TestCase):
    """The owner's rule (2026-09-26): "any folders not added assume excluded". When a
    library's roots are first set -- stamped, or changed -- every folder under a new root
    holding photos and no indexed photo is added to the ignored folders in the same
    journaled change; only a folder that appears later is offered for review."""

    def setUp(self):
        self.home = own_home.for_test(self)
        self.db_path = self.home.library("harbour.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.meet = self.folder("Meets", "2025-10 Invitational")
        conn = db.connect(self.db_path)
        try:
            photo_rows.add_unread(conn, self.photo(self.meet, "IMG_0001.jpg"))
            conn.commit()
        finally:
            conn.close()

    def folder(self, *parts):
        found = os.path.join(self.pictures, *parts)
        os.makedirs(found, exist_ok=True)
        return found

    def photo(self, folder, name):
        found = os.path.join(folder, name)
        with open(found, "wb") as handle:
            handle.write(b"jpeg")
        return found

    def review(self):
        found = settings.of(self.library)
        from tagpup.services import sync
        return [entry["path"] for entry in sync.review(self.library, found.roots, found.ignored)["folders"]]

    def test_a_folder_there_when_the_roots_are_set_is_ignored_and_a_later_one_offered(self):
        warmups = self.folder("Meets", "2025-10 Invitational", "warmups")
        self.photo(warmups, "IMG_0100.jpg")
        settings.of(self.library)
        self.assertTrue(settings.change(self.library, {settings.ROOTS: self.meet}).ok)
        found = settings.of(self.library)
        self.assertEqual(([self.meet], [warmups]), (found.roots, found.ignored))
        later = self.folder("Meets", "2025-10 Invitational", "finals")
        self.photo(later, "IMG_0200.jpg")
        self.assertEqual([later], self.review())

    def test_widening_the_roots_ignores_what_is_there_in_the_same_change(self):
        settings.of(self.library)
        settings.change(self.library, {settings.ROOTS: self.meet})
        opener = self.folder("Meets", "2025-09 Opener")
        self.photo(opener, "IMG_0100.jpg")
        scans = self.folder("Scans", "1998")
        self.photo(scans, "IMG_0300.jpg")
        meets = os.path.join(self.pictures, "Meets")
        with open(self.db_path, "rb") as handle:
            before = handle.read()
        dry = settings.change(self.library, {settings.ROOTS: meets}, apply=False)
        self.assertEqual((True, 1, 0), (dry.details["dry_run"], dry.details["ignored_added"], dry.changed))
        with open(self.db_path, "rb") as handle:
            self.assertEqual(before, handle.read(), "a dry run wrote")
        applied = settings.change(self.library, {settings.ROOTS: meets})
        self.assertEqual(2, applied.changed, "the roots and the ignored folders, in one change")
        found = settings.of(self.library)
        self.assertEqual([opener], found.ignored)
        self.assertEqual([], self.review())
        later = self.folder("Meets", "2025-11 Classic")
        self.photo(later, "IMG_0400.jpg")
        self.assertEqual([later], self.review())
        entry = journal_service.history(self.library, applied.details["change"])["changes"][0]
        self.assertEqual({"settings": {"update": 2}}, entry["rows"])


if __name__ == "__main__":
    unittest.main()
