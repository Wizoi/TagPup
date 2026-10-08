"""Folder ids: a `.tagpup` marker in each leaf folder, and a folder followed by it
(tagpup.services.folder_ids, tagpup.files.folder_marker, tagpup.store.folder_ids).

Made on disk after the rows were made as the indexer makes them (tests/photo_rows): folders of
photos, markers written by the command, folders renamed and moved outside the apps. ExifTool is
stood in for. Fictional names only.
"""
import os
import shutil
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.core import processes  # noqa: E402
from tagpup.core.library import Library  # noqa: E402
from tagpup.files import folder_marker  # noqa: E402
from tagpup.services import folder_ids  # noqa: E402
from tagpup.services import journal as journal_service  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import settings as settings_service  # noqa: E402
from tagpup.store import added_folders, db, journal  # noqa: E402
from tagpup.store import folder_ids as store  # noqa: E402

THEN = 1_700_000_000
DATE = "2026:01:31 08:%02d:00"
OTHER_LIBRARY = "11111111-2222-4333-8444-555555555555"
OTHER_FOLDER = "66666666-7777-4888-9999-aaaaaaaaaaaa"


def slurp(path):
    with open(path, "rb") as handle:
        return handle.read()


def overwrite(path, data):
    """Replace what a marker holds, as an editor does: a hidden file is opened to be written, not made again."""
    with open(path, "r+b" if os.path.exists(path) else "wb") as handle:
        handle.truncate(0)
        handle.write(data)


class Case(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="folder_ids_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        os.makedirs(self.pictures)
        self.db_path = self.home.library("library.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        #: {file name: what ExifTool answers for it}
        self.truth = {}

    # ---- making things ----------------------------------------------------------------

    def photo(self, folder, name, number, dated=True, doc=None):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * (100 + number))
        os.utime(path, (THEN, THEN))
        fields = {}
        if dated:
            fields["EXIF:DateTimeOriginal"] = DATE % number
        if doc:
            fields["XMP:DocumentID"] = doc
        self.truth[name] = fields
        return path

    def index(self, *photo_paths, library=None):
        conn = db.connect((library or self.library).path)
        try:
            ids = {path: photo_rows.add_read(conn, path, self.truth[os.path.basename(path)])
                   for path in photo_paths}
            conn.commit()
        finally:
            conn.close()
        return ids

    def meet(self, name, count=3, parent=None, **kwargs):
        """Photos in a folder of the library (made on disk and indexed); the folder's path is in .folder."""
        folder = os.path.join(parent or self.pictures, name)
        made = [self.photo(folder, "IMG_%04d.jpg" % n, n, **kwargs) for n in range(1, count + 1)]
        self.index(*made)
        return made

    def face(self, photo_path, name="Rowan Thackeray"):
        conn = db.connect(self.db_path)
        try:
            add_face(conn, photo_path, box="[1,2,3,4]", name=name, name_source="manual")
            conn.commit()
        finally:
            conn.close()

    def at(self, name, parent=None):
        return os.path.join(parent or self.pictures, name)

    def rename(self, old, new):
        os.rename(self.at(old), self.at(new))

    # ---- running things ---------------------------------------------------------------

    def mark(self, apply=False, library=None):
        return folder_ids.mark(library or self.library, apply=apply)

    def follow(self, apply=False, trees=None, places=None, library=None, rehearse=False):
        truth = self.truth

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(inner, batch, tags=None):
                return [{"SourceFile": path.replace(os.sep, "/"), **truth.get(os.path.basename(path), {})}
                        for path in batch]

        with mock.patch("tagpup.files.exiftool_session.ExifToolSession", Session):
            return folder_ids.follow(library or self.library, places or (), trees or (), None, apply=apply,
                                     exiftool_path="exiftool", rehearse=rehearse)

    def query(self, sql, params=(), path=None):
        conn = db.connect(db.readonly_uri(path or self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def identity(self, path=None):
        conn = db.connect(db.readonly_uri(path or self.db_path), uri=True)
        try:
            return store.identity(conn)
        finally:
            conn.close()

    def ids(self):
        """{folder: id} the library has recorded."""
        return {path.replace("/", os.sep): folder_id for folder_id, path in self.query("SELECT id, path FROM folder_ids")}

    def entries(self, folder):
        return folder_marker.read(folder).entries

    def paths_by_id(self):
        return dict(self.query("SELECT id, path FROM photos"))

    def second_library(self, name="other.db"):
        path = self.home.library(name)
        library_actions.create(path)
        return Library(path)


class TheServicesImportInAnyOrder(unittest.TestCase):
    """Sync, the move of a folder, the journal and folder ids import one another through the maintenance scaffold; each
    is imported first in an interpreter of its own (a constant of one read from another at import time failed when
    folder_moves was the first)."""

    def test_each_module_can_be_the_first(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("folder_moves", "folder_ids", "sync", "journal", "maintenance"):
            done = processes.run([sys.executable, "-I", "-c", "import sys; sys.path.insert(0, %r); "
                                  "import tagpup.services.%s" % (root, name)], capture_output=True, text=True,
                                 timeout=120)
            self.assertEqual(0, done.returncode, "%s first: %s" % (name, done.stderr[-400:]))


class TheMarkerFile(unittest.TestCase):
    """tagpup.files.folder_marker: what a marker is, and that it is replaced whole."""

    def setUp(self):
        self.folder = os.path.join(own_home.for_test(self, prefix="marker_").root, "Pictures")
        os.makedirs(self.folder)

    def write(self, data):
        with open(os.path.join(self.folder, folder_marker.NAME), "wb") as handle:
            handle.write(data)

    def test_a_list_of_entries_parses_and_anything_else_does_not(self):
        line = ("%s %s" % (OTHER_LIBRARY, OTHER_FOLDER)).encode()
        self.assertEqual([(OTHER_LIBRARY, OTHER_FOLDER)], folder_marker.parse(line + b"\n"))
        self.assertEqual([(OTHER_LIBRARY, OTHER_FOLDER)], folder_marker.parse(line), "a last line needs no newline")
        self.assertEqual([(OTHER_LIBRARY, OTHER_FOLDER)], folder_marker.parse(line + b"\r\n"))
        for bad in (b"", b"\n", b"hello\n", b"\xef\xbb\xbf" + line, line + b"\n\n", line + b" extra\n",
                    line.upper() + b"\n", line + b"\n" + line + b"\n"):
            self.assertIsNone(folder_marker.parse(bad), bad)
        self.assertIsNone(folder_marker.parse(b"a" * (folder_marker.MAX_BYTES + 1)))

    def test_reading_says_what_the_file_is(self):
        self.assertEqual(folder_marker.ABSENT, folder_marker.read(self.folder).state)
        self.write(b"not a marker\n")
        found = folder_marker.read(self.folder)
        self.assertEqual((folder_marker.MALFORMED, b"not a marker\n"), (found.state, found.data))
        self.write(("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode())
        found = folder_marker.read(self.folder)
        self.assertEqual((folder_marker.OK, OTHER_FOLDER), (found.state, found.entry_of(OTHER_LIBRARY)))
        self.assertIsNone(found.entry_of("someone else"))

    def test_a_folder_called_tagpup_is_not_a_marker(self):
        os.makedirs(os.path.join(self.folder, folder_marker.NAME))
        self.assertEqual(folder_marker.MALFORMED, folder_marker.read(self.folder).state)

    def test_a_read_that_fails_is_unreadable_and_not_absent(self):
        self.write(b"x")
        with mock.patch("builtins.open", side_effect=PermissionError("locked")):
            found = folder_marker.read(self.folder)
        self.assertEqual(folder_marker.UNREADABLE, found.state)
        with mock.patch("os.lstat", side_effect=OSError("share gone")):
            self.assertEqual(folder_marker.UNREADABLE, folder_marker.read(self.folder).state)

    def test_an_added_entry_keeps_every_other_byte(self):
        mine = ("%s %s" % ("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "ffffffff-0000-4111-8222-333333333333")).encode()
        theirs = ("%s %s" % (OTHER_LIBRARY, OTHER_FOLDER)).encode()
        self.assertEqual(theirs + b"\r\n" + mine + b"\n", folder_marker.with_entry(
            theirs + b"\r\n", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "ffffffff-0000-4111-8222-333333333333"))
        self.assertEqual(theirs + b"\n" + mine + b"\n", folder_marker.with_entry(
            theirs, "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "ffffffff-0000-4111-8222-333333333333"))

    def test_a_staged_file_is_hidden_and_replaces_the_marker_whole(self):
        self.write(b"old")
        temp = folder_marker.stage(self.folder, b"new")
        self.assertEqual(b"old", slurp(folder_marker.location(self.folder)), "staging changed the file")
        self.assertTrue(folder_marker.is_hidden(temp))
        folder_marker.publish(temp, self.folder)
        self.assertEqual(b"new", slurp(folder_marker.location(self.folder)))
        self.assertTrue(folder_marker.is_hidden(folder_marker.location(self.folder)))
        self.assertEqual([folder_marker.NAME], os.listdir(self.folder), "a temporary file was left")

    def test_a_marker_with_the_read_only_attribute_is_refused_and_not_made_writable(self):
        self.write(b"old")
        os.chmod(folder_marker.location(self.folder), stat.S_IREAD)
        self.addCleanup(os.chmod, folder_marker.location(self.folder), stat.S_IWRITE)
        self.assertFalse(folder_marker.read(self.folder).writable)
        temp = folder_marker.stage(self.folder, b"new")
        with self.assertRaises(PermissionError):
            folder_marker.publish(temp, self.folder)
        folder_marker.discard(temp)
        self.assertEqual(b"old", slurp(folder_marker.location(self.folder)))
        self.assertFalse(folder_marker.read(self.folder).writable, "the attribute was changed")

    def test_a_crashed_runs_temporary_file_goes_when_old_and_not_before(self):
        temp = folder_marker.stage(self.folder, b"x")
        self.assertEqual(0, folder_marker.clear_stale(self.folder))
        self.assertEqual(1, folder_marker.clear_stale(self.folder, now=os.stat(temp).st_mtime + 7200))
        self.assertEqual([], os.listdir(self.folder))
        other = os.path.join(self.folder, ".tagpup.notours.tmp")
        open(other, "wb").close()
        self.assertEqual(0, folder_marker.clear_stale(self.folder, now=os.stat(other).st_mtime + 7200))

    def test_finding_lists_each_folder_once_and_reads_only_markers(self):
        deep = os.path.join(self.folder, "a", "b")
        os.makedirs(deep)
        with open(os.path.join(deep, folder_marker.NAME), "wb") as handle:
            handle.write(("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode())
        with open(os.path.join(self.folder, "a", folder_marker.NAME), "wb") as handle:
            handle.write(b"hand edited\n")
        found, stats = folder_marker.find([self.folder, self.folder], OTHER_LIBRARY)
        self.assertEqual({OTHER_FOLDER: [deep]}, {k: [os.path.normpath(p) for p in v] for k, v in found.items()})
        self.assertEqual((3, 2, 1), (stats["folders"], stats["markers"], stats["malformed"]))
        self.assertEqual({}, folder_marker.find([self.folder], "11111111-2222-4333-8444-000000000000")[0])

    def test_a_folder_that_cannot_be_listed_is_counted_and_decides_nothing(self):
        with mock.patch("os.scandir", side_effect=OSError("share gone")):
            found, stats = folder_marker.find([self.folder], OTHER_LIBRARY)
        self.assertEqual(({}, 0, 1), (found, stats["folders"], stats["unreadable"]))


class Marking(Case):

    def test_a_dry_run_writes_nothing_and_stamps_nothing(self):
        self.meet("2026-01-31 Parkrun")
        result = self.mark()
        counts = result.details["counts"]
        self.assertEqual((1, 1, 1), (counts["leaf_folders"], counts["new"], counts["would_stamp"]))
        self.assertTrue(result.details["dry_run"])
        self.assertEqual([], os.listdir(self.at("2026-01-31 Parkrun")) and
                         [n for n in os.listdir(self.at("2026-01-31 Parkrun")) if n.startswith(".")])
        self.assertIsNone(self.identity())
        self.assertEqual([], self.query("SELECT * FROM folder_ids"))
        self.assertEqual(0, self.query("SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0])

    def test_opening_a_library_and_syncing_it_stamp_nothing(self):
        self.meet("2026-01-31 Parkrun")
        self.assertIsNone(self.identity())
        from tagpup.services import sync
        sync.sync(self.library, apply=True, exiftool_path="exiftool", roots=[self.pictures])
        self.assertIsNone(self.identity())

    def test_applying_marks_each_leaf_folder_hidden_and_not_the_folder_above(self):
        self.meet("2026-01-31 Parkrun")
        self.meet("Harbour", parent=self.at("Trips"))
        self.meet("Coast", parent=self.at("Trips"))
        result = self.mark(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(3, result.changed)
        self.assertEqual({"markers": 3, "ids": 3}, result.details["changed"])
        library_id = self.identity()
        self.assertTrue(folder_marker.is_id(library_id))
        recorded = self.ids()
        self.assertEqual(3, len(recorded))
        for folder in (self.at("2026-01-31 Parkrun"), self.at("Harbour", self.at("Trips")),
                       self.at("Coast", self.at("Trips"))):
            marker = folder_marker.read(folder)
            self.assertEqual(folder_marker.OK, marker.state)
            self.assertEqual([library_id], [library for library, _id in marker.entries])
            self.assertEqual(marker.entry_of(library_id), recorded[folder])
            self.assertTrue(folder_marker.is_hidden(folder_marker.location(folder)))
        self.assertFalse(os.path.exists(folder_marker.location(self.at("Trips"))), "a folder holding only folders")
        self.assertFalse(os.path.exists(folder_marker.location(self.pictures)))
        self.assertEqual(1, self.query("SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0])

    def test_the_second_run_finds_nothing_to_do_and_makes_no_second_id(self):
        self.meet("2026-01-31 Parkrun")
        self.mark(apply=True)
        before, identity = self.ids(), self.identity()
        again = self.mark(apply=True)
        self.assertEqual((0, 1), (again.changed, again.details["counts"]["already"]))
        self.assertEqual((before, identity), (self.ids(), self.identity()))
        self.assertEqual(1, self.query("SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0])

    def test_undoing_the_change_takes_the_ids_back_and_a_new_run_adopts_the_markers(self):
        folder = self.meet("2026-01-31 Parkrun")[0]
        done = self.mark(apply=True)
        written = folder_marker.read(os.path.dirname(folder)).entries
        undone = journal_service.undo(self.library, done.details["change"], apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertEqual([], self.query("SELECT * FROM folder_ids"))
        self.assertEqual(written, folder_marker.read(os.path.dirname(folder)).entries, "the marker is the owner's file")
        again = self.mark(apply=True)
        self.assertEqual(1, again.details["counts"]["adopt"])
        self.assertEqual(written, folder_marker.read(os.path.dirname(folder)).entries, "adopting made a second id")
        self.assertEqual({os.path.dirname(folder): written[0][1]}, self.ids())

    def test_a_crashed_runs_temporary_file_goes_from_a_folder_already_marked_and_one_to_adopt(self):
        made = self.meet("2026-01-31 Parkrun")
        other = self.meet("Harbour")
        self.mark(apply=True)
        stale = []
        for path in (made[0], other[0]):
            temp = folder_marker.stage(os.path.dirname(path), b"x")
            os.utime(temp, (THEN, THEN))
            stale.append(temp)
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM folder_ids WHERE id = ?", (self.ids()[os.path.dirname(other[0])],))   # Harbour: a marker to adopt
            conn.commit()
        finally:
            conn.close()
        result = self.mark(apply=True)
        self.assertEqual((1, 1), (result.details["counts"]["already"], result.details["counts"]["adopt"]))
        self.assertEqual([], [temp for temp in stale if os.path.exists(temp)])

    def test_a_folder_the_library_ignores_is_not_marked(self):
        made = self.meet("Not these")
        settings_service.change(self.library, {settings_service.IGNORED: os.path.dirname(made[0])}, apply=True)
        result = self.mark(apply=True)
        self.assertEqual((1, 0), (result.details["counts"]["ignored"], result.changed))
        self.assertFalse(os.path.exists(folder_marker.location(os.path.dirname(made[0]))))

    def test_a_folder_gone_from_disk_is_counted_and_nothing_is_made_for_it(self):
        made = self.meet("2026-01-31 Parkrun")
        shutil.rmtree(os.path.dirname(made[0]))
        result = self.mark(apply=True)
        self.assertEqual((1, 0), (result.details["counts"]["gone"], result.changed))
        self.assertEqual([], self.query("SELECT * FROM folder_ids"))
        self.assertIsNone(self.identity(), "stamped with nothing to carry it")

    def test_smugmug_files_are_left_alone(self):
        made = self.meet("2026-01-31 Parkrun")
        smug = os.path.join(os.path.dirname(made[0]), ".SMUGMUG.INI")
        with open(smug, "wb") as handle:
            handle.write(b"[SmugMug]\nAlbumKey=abc\n")
        os.utime(smug, (THEN, THEN))
        self.mark(apply=True)
        self.assertEqual((b"[SmugMug]\nAlbumKey=abc\n", THEN), (slurp(smug), int(os.stat(smug).st_mtime)))
        self.assertEqual(sorted(["IMG_0001.jpg", "IMG_0002.jpg", "IMG_0003.jpg", ".SMUGMUG.INI", ".tagpup"]),
                         sorted(os.listdir(os.path.dirname(made[0]))))


class SeveralLibraries(Case):

    def test_two_libraries_holding_one_folder_each_keep_a_line_and_never_rewrite_the_others(self):
        other = self.second_library()
        made = self.meet("2026-01-31 Parkrun")
        self.index(*made, library=other)
        folder = os.path.dirname(made[0])
        self.mark(apply=True)
        first = slurp(folder_marker.location(folder))
        self.mark(apply=True, library=other)
        second = slurp(folder_marker.location(folder))
        self.assertTrue(second.startswith(first), "the first library's line was not kept byte for byte")
        mine, theirs = self.identity(), self.identity(other.path)
        self.assertNotEqual(mine, theirs)
        entries = folder_marker.read(folder).entries
        self.assertEqual([mine, theirs], [library for library, _id in entries])
        ids = {self.query("SELECT id FROM folder_ids")[0][0], self.query("SELECT id FROM folder_ids", path=other.path)[0][0]}
        self.assertEqual(ids, {folder_id for _lib, folder_id in entries})
        self.assertEqual(2, len(ids), "one id for two libraries")
        # Neither run again changes the file, and the first library's id is as it was.
        self.mark(apply=True)
        self.mark(apply=True, library=other)
        self.assertEqual(second, slurp(folder_marker.location(folder)))

    def test_a_library_never_rewrites_another_librarys_entry_even_when_it_holds_its_own_id(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        theirs = ("%s %s\r\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode()
        overwrite(folder_marker.location(folder), theirs)
        result = self.mark(apply=True)
        self.assertEqual(1, result.details["counts"]["shared_with_other_libraries"])
        data = slurp(folder_marker.location(folder))
        self.assertTrue(data.startswith(theirs))
        self.assertEqual(2, len(folder_marker.parse(data)))

    def test_a_library_file_copied_for_a_trial_is_refused_and_named(self):
        self.meet("2026-01-31 Parkrun")
        self.mark(apply=True)
        copy = self.home.library("trial.db")
        shutil.copyfile(self.db_path, copy)
        self.assertEqual(self.identity(), self.identity(copy))
        for library in (self.library, Library(copy)):
            refused = self.mark(apply=True, library=library)
            self.assertIn("trial.db" if library is self.library else "library.db", refused.refused)
            self.assertEqual(0, refused.changed)
        self.assertEqual(["trial.db"], store.twins(self.db_path, self.identity()))
        self.assertEqual([], store.twins(self.db_path, None))

    def test_a_copy_of_a_marked_folder_is_reported_and_never_rewritten(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        self.mark(apply=True)
        copy = self.at("2026-01-31 Parkrun - copy")
        shutil.copytree(folder, copy)
        for name in os.listdir(copy):
            if name.endswith(".jpg"):
                self.truth[name] = self.truth[name]
        self.index(*[os.path.join(copy, os.path.basename(p)) for p in made])
        before = slurp(folder_marker.location(copy))
        result = self.mark(apply=True)
        self.assertEqual((1, 1), (result.details["counts"]["copy"], result.details["counts"]["already"]))
        self.assertEqual(before, slurp(folder_marker.location(copy)))
        self.assertEqual(1, len(self.ids()))


class WhatGoesWrong(Case):

    def test_a_marker_that_does_not_parse_is_never_overwritten_and_is_reported(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        for text in (b"\xef\xbb\xbfhello\n", b"my notes", b""):
            overwrite(folder_marker.location(folder), text)
            result = self.mark(apply=True)
            self.assertEqual((1, 0), (result.details["counts"]["malformed"], result.changed), text)
            self.assertEqual(text, slurp(folder_marker.location(folder)))
            self.assertEqual([], self.query("SELECT * FROM folder_ids"))
            self.assertEqual([folder], result.details["reveal"]["malformed"])
        self.assertIsNone(self.identity())

    def test_a_marker_with_the_read_only_attribute_is_left_and_counted(self):
        made = self.meet("2026-01-31 Parkrun")
        other = self.meet("Harbour")
        folder = os.path.dirname(made[0])
        theirs = ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode()
        target = folder_marker.location(folder)
        overwrite(target, theirs)
        os.chmod(target, stat.S_IREAD)
        self.addCleanup(os.chmod, target, stat.S_IWRITE)
        result = self.mark(apply=True)
        self.assertEqual((1, 1), (result.details["counts"]["unwritable"], result.changed))
        self.assertEqual(theirs, slurp(target))
        self.assertFalse(folder_marker.read(folder).writable, "made writable")
        self.assertEqual([os.path.dirname(other[0])], list(self.ids()))

    def test_a_place_that_cannot_be_written_is_counted_and_records_nothing_for_it(self):
        made = self.meet("2026-01-31 Parkrun")
        self.meet("Harbour")
        real = folder_marker.stage

        def stage(folder, data):
            if os.path.basename(folder) == "Harbour":
                raise PermissionError("the share is read-only")
            return real(folder, data)
        with mock.patch.object(folder_marker, "stage", stage):
            result = self.mark(apply=True)
        self.assertEqual({"markers": 1, "ids": 1}, result.details["changed"])
        self.assertEqual(1, result.details["not_written"]["refused"])
        self.assertEqual([os.path.dirname(made[0])], list(self.ids()))
        self.assertEqual([], [n for n in os.listdir(self.at("Harbour")) if n.startswith(".")], "something was left")
        # Writable again: the next run does it.
        again = self.mark(apply=True)
        self.assertEqual({"markers": 1, "ids": 1}, again.details["changed"])
        self.assertEqual(self.identity(), folder_marker.read(self.at("Harbour")).entries[0][0])

    def test_a_marker_locked_by_another_program_is_counted_unreadable_and_nothing_decided(self):
        made = self.meet("2026-01-31 Parkrun")
        real = folder_marker.read

        def read(folder):
            return folder_marker.Marker(folder_marker.UNREADABLE, error="locked")
        with mock.patch.object(folder_marker, "read", read):
            result = self.mark(apply=True)
        self.assertEqual((1, 0), (result.details["counts"]["unreadable"], result.changed))
        self.assertEqual(folder_marker.ABSENT, real(os.path.dirname(made[0])).state)
        self.assertEqual([], self.query("SELECT * FROM folder_ids"))

    def test_a_crash_before_the_record_leaves_no_marker_and_no_stamp(self):
        made = self.meet("2026-01-31 Parkrun")
        with mock.patch.object(journal, "apply", side_effect=RuntimeError("the machine went down")):
            result = self.mark(apply=True)
        self.assertFalse(result.ok)
        self.assertEqual([], [n for n in os.listdir(os.path.dirname(made[0])) if n.startswith(".")])
        self.assertIsNone(self.identity())
        self.assertEqual([], self.query("SELECT * FROM folder_ids"))

    def test_a_crash_between_the_record_and_the_file_is_finished_by_the_next_run_with_the_same_id(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        with mock.patch.object(folder_marker, "publish", side_effect=OSError("went down")):
            first = self.mark(apply=True)
        self.assertEqual(0, first.changed)
        self.assertEqual(1, first.details["not_written"]["refused"])
        recorded = self.ids()
        self.assertEqual(1, len(recorded))
        self.assertEqual(folder_marker.ABSENT, folder_marker.read(folder).state)
        self.assertEqual([], [n for n in os.listdir(folder) if n.startswith(".")], "a temporary file was left")
        again = self.mark(apply=True)
        self.assertEqual((1, 1), (again.details["counts"]["restore"], again.changed))
        self.assertEqual(recorded, self.ids(), "a second id was made")
        self.assertEqual(list(recorded.values()), [folder_id for _lib, folder_id in folder_marker.read(folder).entries])
        self.assertEqual(1, self.query("SELECT COUNT(*) FROM changes WHERE operation = 'mark_folders'")[0][0],
                         "restoring recorded another change")

    def test_a_crash_between_the_file_and_the_record_is_finished_by_the_next_run_adopting_it(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        self.mark(apply=True)
        written = folder_marker.read(folder).entries
        conn = db.connect(self.db_path)
        try:
            conn.execute("DELETE FROM folder_ids")
            conn.commit()
        finally:
            conn.close()
        again = self.mark(apply=True)
        self.assertEqual((1, 0), (again.details["counts"]["adopt"], again.changed))
        self.assertEqual({written[0][1]}, set(self.ids().values()))
        self.assertEqual(written, folder_marker.read(folder).entries)

    def test_a_file_changed_by_another_program_between_the_read_and_the_replace_is_kept(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        real = folder_marker.stage
        theirs = ("%s %s\n" % (OTHER_LIBRARY, OTHER_FOLDER)).encode()

        def stage(where, data):
            temp = real(where, data)
            overwrite(folder_marker.location(where), theirs)
            return temp
        with mock.patch.object(folder_marker, "stage", stage):
            result = self.mark(apply=True)
        self.assertEqual((0, 1), (result.changed, result.details["not_written"]["changed_meanwhile"]))
        self.assertEqual(theirs, slurp(folder_marker.location(folder)), "their file was replaced")
        self.assertEqual([".tagpup"], [n for n in os.listdir(folder) if n.startswith(".")])
        again = self.mark(apply=True)
        data = slurp(folder_marker.location(folder))
        self.assertEqual((1, True), (again.changed, data.startswith(theirs)))
        self.assertEqual(list(self.ids().values()), [folder_marker.parse(data)[1][1]])

    def test_two_runs_at_once_make_one_id_and_the_second_is_refused_cleanly(self):
        made = self.meet("2026-01-31 Parkrun")
        folder = os.path.dirname(made[0])
        real = journal.apply
        inner = {}

        def apply(*args, **kwargs):
            if not inner:
                inner["started"] = True
                inner["done"] = self.mark(apply=True)       # the other process finishes first
            return real(*args, **kwargs)
        with mock.patch.object(journal, "apply", apply):
            outer = self.mark(apply=True)
        self.assertEqual(1, inner["done"].changed)
        self.assertIsNotNone(outer.refused, "the second run was not refused")
        self.assertEqual(0, outer.changed)
        self.assertEqual(1, len(self.ids()))
        self.assertEqual(list(self.ids().values()), [folder_id for _lib, folder_id in folder_marker.read(folder).entries])
        self.assertEqual([".tagpup"], [n for n in os.listdir(folder) if n.startswith(".")], "a temporary file was left")

    def test_two_runs_stamping_a_library_never_leave_two_identifiers(self):
        made = self.meet("2026-01-31 Parkrun")
        conn = db.connect(self.db_path)
        try:
            self.assertEqual("one", store.stamp(conn, "one"))
            self.assertEqual("one", store.stamp(conn, "two"))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual([("one",)], self.query("SELECT id FROM library_identity"))
        self.assertTrue(made)


class FollowingAFolder(Case):

    def marked(self, name="2026-01-31 Parkrun", count=3):
        made = self.meet(name, count=count)
        self.mark(apply=True)
        return made

    def test_a_marked_folder_renamed_outside_the_apps_is_followed_exactly(self):
        made = self.marked()
        ids = self.index_ids = {path: photo_id for photo_id, path in self.paths_by_id().items()}
        self.face(made[0])
        recorded = self.ids()
        self.rename("2026-01-31 Parkrun", "Something quite different")
        dry = self.follow()
        self.assertEqual((1, 1, 3), (dry.details["counts"]["gone"], dry.details["counts"]["followed"],
                                     dry.details["counts"]["photos_moved"]))
        self.assertEqual(set(made), set(self.paths_by_id().values()), "a dry run wrote")
        done = self.follow(apply=True)
        self.assertTrue(done.ok, done.message())
        moved = self.paths_by_id()
        self.assertEqual(set(ids.values()), set(moved), "photo ids were not kept")
        for photo_id, path in moved.items():
            self.assertTrue(os.path.exists(path))
            self.assertEqual("Something quite different", os.path.basename(os.path.dirname(path)))
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces WHERE photo_id = ?",
                                                              (ids[made[0]],)))
        new_folder = self.at("Something quite different")
        self.assertEqual({new_folder: list(recorded.values())[0]},
                         self.ids())
        self.assertEqual("follow_folder_markers", self.query("SELECT operation FROM changes")[-1][0])
        self.assertEqual(0, self.follow().details["counts"]["gone"], "a second run found work")

    def test_the_change_is_undone_with_the_rows_the_ids_and_the_settings(self):
        made = self.marked()
        settings_service.change(self.library, {settings_service.IGNORED: self.at("2026-01-31 Parkrun")}, apply=True)
        added_folders_conn = db.connect(self.db_path)
        try:
            added_folders.record(added_folders_conn, self.at("2026-01-31 Parkrun"), subfolders=False)
            added_folders_conn.commit()
        finally:
            added_folders_conn.close()
        before = (self.paths_by_id(), self.ids())
        self.rename("2026-01-31 Parkrun", "Renamed")
        done = self.follow(apply=True)
        self.assertEqual(1, done.details["added_followed"])
        self.assertNotEqual(before, (self.paths_by_id(), self.ids()))
        undone = journal_service.undo(self.library, done.details["change"], apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertEqual(before, (self.paths_by_id(), self.ids()))
        self.assertEqual(1, undone.details["added_followed_back"])
        self.assertEqual(set(made), set(self.paths_by_id().values()))

    def test_a_folder_moved_to_another_parent_is_followed(self):
        made = self.marked()
        os.makedirs(self.at("Archive"))
        shutil.move(self.at("2026-01-31 Parkrun"), self.at("2026-01-31 Parkrun", self.at("Archive")))
        self.assertEqual(1, self.follow(trees=[self.at("Archive")]).details["counts"]["followed"])
        self.assertEqual(1, self.follow(places=[self.at("2026-01-31 Parkrun", self.at("Archive"))]
                                        ).details["counts"]["followed"], "a folder a sync found files moved to")
        self.assertEqual(1, self.follow().details["counts"]["not_found"], "beside it only, it is not at the parent")
        done = self.follow(apply=True, trees=[self.at("Archive")])
        self.assertEqual(3, done.details["changed"]["relinked"])
        self.assertEqual({os.path.join(self.at("Archive"), "2026-01-31 Parkrun", os.path.basename(p)) for p in made},
                         set(self.paths_by_id().values()))

    def test_a_renamed_parent_moves_every_marked_folder_under_it(self):
        self.marked("Trips/Harbour")
        self.marked("Trips/Coast")
        self.rename("Trips", "Trips 2026")
        done = self.follow(apply=True)
        self.assertEqual((2, 6), (done.details["counts"]["followed"], done.details["changed"]["relinked"]))
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))
        self.assertTrue(all(os.path.isdir(p) for p in self.ids()))

    def test_a_folder_renamed_and_its_photos_renamed_follows_by_the_evidence_of_each_photo(self):
        made = [self.photo(self.at("2026-02-07 Parkrun"), "IMG_%04d.jpg" % n, n, doc="xmp.did:%04d" % n)
                for n in range(1, 5)]
        self.index(*made)
        self.mark(apply=True)
        folder = self.at("2026-02-07 Parkrun")
        shutil.move(folder, folder + " Wet")
        for n in range(1, 4):
            os.rename(os.path.join(folder + " Wet", "IMG_%04d.jpg" % n), os.path.join(folder + " Wet", "Race %d.jpg" % n))
            self.truth["Race %d.jpg" % n] = self.truth["IMG_%04d.jpg" % n]
        done = self.follow(apply=True)
        self.assertEqual((4, 1, 3), (done.details["changed"]["relinked"], done.details["counts"]["by_name"],
                                     done.details["counts"]["by_evidence"]))
        self.assertTrue(all(os.path.exists(p) for p in self.paths_by_id().values()))
        self.assertEqual(4, len(self.paths_by_id()))

    def test_a_photo_with_no_evidence_in_the_new_folder_keeps_its_row_where_it_was(self):
        made = self.marked(count=3)
        self.rename("2026-01-31 Parkrun", "Renamed")
        os.remove(os.path.join(self.at("Renamed"), "IMG_0003.jpg"))
        done = self.follow(apply=True)
        self.assertEqual(2, done.details["changed"]["relinked"])
        self.assertEqual(made[2], [p for p in self.paths_by_id().values() if p.endswith("IMG_0003.jpg")][0])

    def test_a_file_that_already_has_a_row_is_never_a_destination(self):
        made = self.marked(count=2)
        self.rename("2026-01-31 Parkrun", "Renamed")
        # The new folder's first file was indexed as a new photo before anything followed (the 233 duplicate faces).
        stray = os.path.join(self.at("Renamed"), "IMG_0001.jpg")
        self.index(stray)
        before = len(self.paths_by_id())
        done = self.follow(apply=True)
        self.assertEqual(before, len(self.paths_by_id()))
        paths_now = sorted(self.paths_by_id().values())
        self.assertEqual(len(paths_now), len(set(p.lower() for p in paths_now)), "two rows for one file")
        self.assertEqual(1, done.details["counts"]["occupied"])
        self.assertIn(made[0], paths_now, "the row whose destination had a row was moved onto it")

    def test_a_copy_beside_a_folder_that_is_there_moves_nothing(self):
        made = self.marked()
        copy = self.at("2026-01-31 Parkrun - copy")
        shutil.copytree(os.path.dirname(made[0]), copy)
        dry = self.follow()
        self.assertEqual(0, dry.details["counts"]["gone"])
        # A second folder lost: the walk now sees the copied marker, whose id is recorded for a folder that is there.
        other = self.marked("Harbour")
        self.rename("Harbour", "Harbour 2")
        done = self.follow(apply=True)
        self.assertEqual((1, 1), (done.details["counts"]["followed"], done.details["counts"]["copies"]))
        self.assertEqual(set(made), {p for p in self.paths_by_id().values() if "Parkrun" in p})
        self.assertTrue(other)

    def test_the_original_gone_and_two_copies_standing_is_ambiguous_and_nothing_follows(self):
        made = self.marked()
        folder = os.path.dirname(made[0])
        shutil.copytree(folder, self.at("Copy A"))
        shutil.copytree(folder, self.at("Copy B"))
        shutil.rmtree(folder)
        done = self.follow(apply=True)
        self.assertEqual((1, 0, 1), (done.details["counts"]["ambiguous"], done.changed, done.details["counts"]["gone"]))
        self.assertEqual(set(made), set(self.paths_by_id().values()))

    def test_the_original_gone_and_one_copy_standing_takes_the_link(self):
        made = self.marked()
        folder = os.path.dirname(made[0])
        shutil.copytree(folder, self.at("Copy A"))
        shutil.rmtree(folder)
        done = self.follow(apply=True)
        self.assertEqual(3, done.details["changed"]["relinked"])
        self.assertTrue(all("Copy A" in p for p in self.paths_by_id().values()))

    def test_a_folder_not_found_anywhere_under_the_walked_folders_moves_nothing(self):
        made = self.marked()
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        shutil.move(os.path.dirname(made[0]), elsewhere)
        done = self.follow(apply=True)
        self.assertEqual((1, 1, 0), (done.details["counts"]["gone"], done.details["counts"]["not_found"], done.changed))
        self.assertEqual(set(made), set(self.paths_by_id().values()))

    def test_a_drive_or_share_that_is_not_there_is_not_a_folder_gone(self):
        made = self.marked()
        row_folder = os.path.dirname(made[0])
        conn = db.connect(self.db_path)
        try:
            conn.execute("UPDATE folder_ids SET path = ?", ("Z:/nowhere/at/all",))
            conn.commit()
        finally:
            conn.close()
        with mock.patch("os.path.isdir", lambda p: p.replace("\\", "/").startswith(row_folder.replace("\\", "/"))
                        or os.path.exists(p)):
            done = self.follow(apply=True)
        self.assertEqual((0, 0), (done.details["counts"]["gone"], done.changed))

    def test_a_marker_a_hand_edit_made_unreadable_is_never_followed(self):
        made = self.marked()
        self.rename("2026-01-31 Parkrun", "Renamed")
        overwrite(folder_marker.location(self.at("Renamed")), b"edited by hand\n")
        done = self.follow(apply=True)
        self.assertEqual((1, 1, 0), (done.details["counts"]["not_found"], done.details["counts"]["malformed"],
                                     done.changed))
        self.assertEqual(set(made), set(self.paths_by_id().values()))

    def test_a_folder_holding_a_marker_another_row_records_is_a_conflict_and_moves_nothing(self):
        made = self.marked()
        self.marked("Harbour")
        folder_id = self.ids()[os.path.dirname(made[0])]
        # Harbour's marker is replaced by the Parkrun one while Parkrun is gone.
        shutil.rmtree(os.path.dirname(made[0]))
        overwrite(folder_marker.location(self.at("Harbour")), ("%s %s\n" % (self.identity(), folder_id)).encode())
        beside = self.follow(apply=True)
        self.assertEqual((1, 0, 0), (beside.details["counts"]["not_found"], beside.details["counts"]["conflicts"],
                                     beside.changed), "a folder another row records is not read beside the lost one")
        done = self.follow(apply=True, places=[self.at("Harbour")])
        self.assertEqual((1, 0), (done.details["counts"]["conflicts"], done.changed))

    def test_a_folder_whose_rows_cannot_go_because_its_files_have_rows_is_left_and_not_said_followed(self):
        """The watcher queued the renamed folder as new before anything followed (no DocumentID: 97% of real rows):
        every file there has a row of its own. Moving the folder's id would leave the old rows, and the faces named
        on them, missing for good and say it was followed (review of 00c5d16)."""
        made = self.marked(count=3)
        self.face(made[0])
        recorded, rows = self.ids(), self.paths_by_id()
        self.rename("2026-01-31 Parkrun", "Renamed")
        fresh = []
        for n, path in enumerate(made, 1):
            new = os.path.join(self.at("Renamed"), "Finish %d.jpg" % n)
            os.rename(os.path.join(self.at("Renamed"), os.path.basename(path)), new)
            os.utime(new, (THEN + 5, THEN + 5))
            self.truth[os.path.basename(new)] = self.truth[os.path.basename(path)]
            fresh.append(new)
        self.index(*fresh)
        done = self.follow(apply=True)
        counts = done.details["counts"]
        self.assertEqual((0, 1, 0), (counts["followed"], counts["left"], done.changed))
        self.assertEqual(3, done.details["reveal"]["left"][0]["files_with_rows"])
        self.assertEqual(recorded, self.ids(), "the folder's id moved though no row could")
        self.assertEqual(3, len([p for p in self.paths_by_id().values() if p in made]), "an old row was moved")
        self.assertEqual(set(rows), {i for i, p in self.paths_by_id().items() if p in made})
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces"))

    def burst(self, name, count):
        """Photos of one burst: the same size and the same Date Taken, and a DocumentID on none."""
        folder = self.at(name)
        os.makedirs(folder)
        made = []
        for n in range(1, count + 1):
            path = os.path.join(folder, "BURST_%04d.jpg" % n)
            with open(path, "wb") as handle:
                handle.write(b"x" * 300)
            os.utime(path, (THEN, THEN))
            self.truth[os.path.basename(path)] = {"EXIF:DateTimeOriginal": DATE % 1}
            made.append(path)
        return made

    def test_a_burst_of_equal_size_and_date_taken_pairs_nothing_by_guess(self):
        """Live data has 88 (size, Date Taken) groups of 205 photos: evidence that is not one to one is no evidence."""
        made = self.burst("2026-03-07 Burst", 2)
        self.index(*made)
        self.mark(apply=True)
        self.rename("2026-03-07 Burst", "Renamed")
        for n in (1, 2):
            os.rename(os.path.join(self.at("Renamed"), "BURST_%04d.jpg" % n), os.path.join(self.at("Renamed"), "Shot %d.jpg" % n))
            self.truth["Shot %d.jpg" % n] = self.truth["BURST_%04d.jpg" % n]
        done = self.follow(apply=True)
        self.assertEqual((0, 1, 0), (done.details["counts"]["followed"], done.details["counts"]["left"], done.changed))
        self.assertEqual(set(made), set(self.paths_by_id().values()), "a row was given a file by guess")

    def test_two_rows_and_one_file_of_one_burst_pair_nothing(self):
        made = self.burst("2026-03-07 Burst", 2)
        self.index(*made)
        self.mark(apply=True)
        self.rename("2026-03-07 Burst", "Renamed")
        os.remove(os.path.join(self.at("Renamed"), "BURST_0002.jpg"))
        os.rename(os.path.join(self.at("Renamed"), "BURST_0001.jpg"), os.path.join(self.at("Renamed"), "Shot 1.jpg"))
        self.truth["Shot 1.jpg"] = self.truth["BURST_0001.jpg"]
        done = self.follow(apply=True)
        self.assertEqual((0, 1, 0), (done.details["counts"]["followed"], done.details["counts"]["left"], done.changed))
        self.assertEqual(set(made), set(self.paths_by_id().values()))

    def test_a_dry_run_of_following_writes_nothing_and_the_rehearsal_says_the_undo_is_exact(self):
        self.marked()
        self.rename("2026-01-31 Parkrun", "Renamed")
        before = self.query("SELECT COUNT(*) FROM changes")[0][0]
        result = self.follow(rehearse=True)
        self.assertTrue(result.details["rehearsal"]["exact"])
        self.assertEqual(before, self.query("SELECT COUNT(*) FROM changes")[0][0])

    def test_a_library_that_never_marked_follows_nothing(self):
        self.meet("2026-01-31 Parkrun")
        self.rename("2026-01-31 Parkrun", "Renamed")
        done = self.follow(apply=True)
        self.assertEqual((0, 0), (done.details["counts"]["marked"], done.changed))
        self.assertIsNone(self.identity())


if __name__ == "__main__":
    unittest.main()
