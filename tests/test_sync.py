"""Sync brings a library's rows back in step with its folders (docs/ARCHITECTURE.md,
phase 8; tagpup.services.sync).

Each kind of drift, made on disk after the rows were made as the indexer makes them
(tests/photo_rows): a new file, a changed file, a moved file, a missing file, a folder
wholly gone, and a root that is not there at all, as a folder on an unplugged drive is
not. And a library that has not drifted costs one walk: no file is read.

ExifTool is stood in for: the reader answers from a table of what each file holds, and
counts what it was asked to read.
"""
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.files import images  # noqa: E402
from tagpup.files.metadata import MetadataExtractor  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db  # noqa: E402

#: When every file was last written, before the test changes any.
THEN = 1_700_000_000


class Reads:
    """What the stand-in ExifTool was asked: files read whole, and sessions opened for
    the identities."""

    def __init__(self, truth):
        self.truth = truth
        self.read, self.sessions = [], 0

    def batch_read(self, extractor, file_paths, people=None):
        assert not extractor.mint_identities, "sync must never write to a photo"
        self.read.extend(file_paths)
        return [MetadataExtractor._structure(extractor, path, dict(self.truth.get(os.path.basename(path), {})),
                                             people) for path in file_paths]

    def session(self, *args, **kwargs):
        reads = self

        class Session:
            def __enter__(self):
                reads.sessions += 1
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(self, batch, tags=None):
                reads.read.extend(batch)
                return [{"SourceFile": path.replace(os.sep, "/"), **reads.truth.get(os.path.basename(path), {})}
                        for path in batch]

        return Session()


class SyncTestCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="sync_")
        self.pictures = os.path.join(self.home.root, "Pictures")
        self.meet = os.path.join(self.pictures, "2025-10 Invitational")
        self.trip = os.path.join(self.pictures, "2025-11 Harbour")
        for folder in (self.meet, self.trip):
            os.makedirs(folder)
        self.db_path = self.home.library("library.db")
        library_actions.create(self.db_path)
        self.library = Library(self.db_path)
        #: {file name: what ExifTool answers for it}
        self.truth = {}

    def photo(self, folder, name, body=b"jpeg", **fields):
        """A file on disk, written at THEN, and what reading it answers."""
        path = os.path.join(folder, name)
        with open(path, "wb") as handle:
            handle.write(body)
        os.utime(path, (THEN, THEN))
        self.truth[name] = dict(fields)
        return path

    def indexed(self, *photo_paths):
        """Rows for the photos, as the indexer records a read of each."""
        conn = db.connect(self.db_path)
        try:
            ids = [photo_rows.add_read(conn, path, self.truth[os.path.basename(path)]) for path in photo_paths]
            conn.commit()
        finally:
            conn.close()
        return ids

    def run_sync(self, apply=False, folder=None, queue=None, roots=(), ignored=()):
        self.reads = Reads(self.truth)
        with mock.patch("tagpup.files.metadata.MetadataExtractor.batch_read", autospec=True,
                        side_effect=self.reads.batch_read), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=self.reads.session):
            return sync.sync(self.library, folder=folder, apply=apply, exiftool_path="exiftool", queue=queue,
                             roots=roots, ignored=ignored)

    def rows(self):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return {path: (photo_id, mtime, size, json.loads(tags)) for photo_id, path, mtime, size, tags
                    in conn.execute("SELECT id, path, mtime, size, tags FROM photos")}
        finally:
            conn.close()

    def query(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()


class NothingChanged(SyncTestCase):
    def test_a_library_in_step_costs_one_walk_and_no_read(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]}),
                     self.photo(self.trip, "IMG_0002.jpg"))
        result = self.run_sync()
        self.assertEqual([], self.reads.read, "a file was read when nothing had changed")
        self.assertEqual(0, self.reads.sessions)
        counts = result.details["counts"]
        self.assertEqual((2, 2, 0, 0, 0, 0), (counts["rows"], counts["files"], counts["new"], counts["changed"],
                                              counts["moved"], counts["missing"]))
        self.assertTrue(result.details["in_step"])
        self.assertEqual(0, result.attempted)

    def test_an_applied_sync_records_when_the_library_was_last_in_step(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        self.assertEqual({"last_run": None, "last_in_step": None}, sync.last(self.library))
        result = self.run_sync(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(0, result.changed)
        found = sync.last(self.library)
        self.assertIsNotNone(found["last_in_step"])
        self.assertTrue(found["last_run"]["in_step"])
        self.assertTrue(found["last_run"]["whole"])
        self.assertEqual(1, found["last_run"]["found"]["rows"])
        self.assertEqual(0, found["last_run"]["changed"]["rows"])

    def test_a_dry_run_records_nothing_and_writes_no_file(self):
        path = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(path)
        self.photo(self.meet, "IMG_0009.jpg")   # new
        before = self.rows()
        stamp = os.stat(path).st_mtime
        self.run_sync()
        self.assertEqual(before, self.rows())
        self.assertEqual({"last_run": None, "last_in_step": None}, sync.last(self.library))
        self.assertEqual(stamp, os.stat(path).st_mtime)


class ALibraryThatIsBehind(SyncTestCase):
    """A dry run writes nothing, and a library behind this version's migrations is not
    brought up to date by one: the rehearsal would run in a migrated library, so it waits
    until an app opens it, and the dry run says so."""

    def test_a_dry_run_on_a_library_behind_leaves_its_file_as_it_was(self):
        from test_migrations import at_version
        from tagpup.store import schema

        older = self.home.library("older.db")
        at_version(older, len(schema.MIGRATIONS) - 1)
        path = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        self.db_path = older
        self.indexed(path)
        self.library = Library(older)
        self.photo(self.meet, "IMG_0001.jpg", body=b"jpeg, edited", **{"XMP:Subject": ["Weather/Rain"]})   # changed: a rehearsal would be due
        with open(older, "rb") as handle:
            before = handle.read()
        result = self.run_sync()
        with open(older, "rb") as handle:
            self.assertEqual(before, handle.read(), "a dry run wrote to a library that is behind")
        self.assertEqual(1, len(schema.pending(older)))
        self.assertEqual(1, result.details["behind"])
        self.assertEqual(1, result.details["counts"]["to_write"])
        self.assertNotIn("rehearsal", result.details)


class TheRecord(SyncTestCase):
    """A run is recorded when it was applied and its write went through: a refused or
    failed apply is not a sync of the library. And a record that cannot be written after
    the rows were is a warning, not a failure of the write."""

    def changed_file(self):
        path = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        self.indexed(path)
        self.photo(self.meet, "IMG_0001.jpg", body=b"jpeg, edited", **{"XMP:Subject": ["Weather/Rain"]})
        return path

    def test_a_refused_apply_is_not_recorded(self):
        self.changed_file()
        elsewhere = os.path.join(self.pictures, "Elsewhere")
        os.makedirs(elsewhere)
        result = self.run_sync(apply=True, folder=elsewhere)
        self.assertTrue(result.refused)
        self.assertIsNone(sync.last(self.library)["last_run"])

    def test_a_failed_write_is_not_recorded(self):
        self.changed_file()
        with mock.patch("tagpup.store.journal.apply", side_effect=OSError("disk full")):
            result = self.run_sync(apply=True)
        self.assertFalse(result.ok)
        self.assertIsNone(sync.last(self.library)["last_run"])

    def test_a_record_that_cannot_be_written_is_a_warning(self):
        path = self.changed_file()
        with mock.patch("tagpup.store.sync_runs.record", side_effect=OSError("locked")):
            result = self.run_sync(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, result.changed)
        self.assertEqual(["Weather/Rain"], self.rows()[path][3])
        self.assertEqual(1, len(result.details["warnings"]))


class NewFiles(SyncTestCase):
    def test_a_new_file_is_queued_for_indexing_not_indexed(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        sub = os.path.join(self.trip, "day 2")
        os.makedirs(sub)
        self.photo(self.trip, "IMG_0100.jpg")
        self.photo(sub, "IMG_0101.jpg")
        self.indexed(self.photo(self.trip, "IMG_0002.jpg"))

        dry = self.run_sync(roots=[self.pictures])
        self.assertEqual(1, dry.details["counts"]["new"])
        self.assertEqual(1, dry.details["counts"]["new_folders"])
        # A subfolder holding no indexed photo is a folder to review, not indexed on its own.
        self.assertEqual(1, dry.details["counts"]["review_folders"])
        self.assertFalse(dry.details["in_step"])
        self.assertEqual([], self.reads.read, "a new file is the indexer's to read")

        asked = []

        def queue(folders):
            asked.append(list(folders))
            return Result(attempted=len(folders), changed=len(folders))

        result = self.run_sync(apply=True, queue=queue, roots=[self.pictures])
        self.assertTrue(result.ok, result.message())
        self.assertEqual([[self.trip]], asked)
        self.assertEqual(1, result.details["queued"])
        self.assertEqual(0, result.changed, "no row was written: indexing makes them")
        self.assertEqual(2, len(self.rows()))
        found = sync.last(self.library)
        self.assertFalse(found["last_run"]["in_step"])
        self.assertIsNone(found["last_in_step"])
        self.assertEqual(1, found["last_run"]["changed"]["queued_folders"])

    def test_with_no_roots_a_new_subfolder_of_a_held_folder_is_queued_and_nothing_reviewed(self):
        """A library whose roots are not set yet keeps its held folders in step as indexing
        walked them: a new subfolder too."""
        self.indexed(self.photo(self.trip, "IMG_0002.jpg"))
        sub = os.path.join(self.trip, "day 2")
        os.makedirs(sub)
        self.photo(self.trip, "IMG_0100.jpg")
        self.photo(sub, "IMG_0101.jpg")
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=len(folders), changed=len(folders))

        result = self.run_sync(apply=True, queue=queue)
        counts = result.details["counts"]
        self.assertEqual((2, 0), (counts["new"], counts["review_folders"]))
        self.assertEqual(sorted([self.trip, sub]), sorted(asked))
        self.assertEqual([], sync.review(self.library, [])["folders"])

    def test_without_a_queue_new_files_are_reported(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        self.photo(self.meet, "IMG_0100.jpg")
        result = self.run_sync(apply=True)
        self.assertEqual(0, result.details["queued"])
        self.assertEqual(1, result.details["counts"]["new"])
        self.assertEqual([self.meet], result.details["reveal"]["new_folders"])


class ChangedFiles(SyncTestCase):
    def test_a_file_edited_elsewhere_has_its_row_read_again(self):
        path = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        untouched = self.photo(self.meet, "IMG_0002.jpg")
        (photo_id, _other) = self.indexed(path, untouched)
        # Another program adds a keyword: the file grows and its time moves.
        self.photo(self.meet, "IMG_0001.jpg", body=b"jpeg, tagged",
                   **{"XMP:Subject": ["Events/Invitational", "Weather/Rain"]})
        os.utime(path, (THEN + 60, THEN + 60))

        dry = self.run_sync()
        self.assertEqual([path], self.reads.read, "only the changed file is read")
        self.assertEqual(1, dry.details["counts"]["changed"])
        self.assertEqual([photo_id], dry.details["ids"]["to_write"])
        self.assertEqual(["Events/Invitational"], self.rows()[path][3], "a dry run wrote the row")
        self.assertTrue(dry.details["rehearsal"]["exact"])

        result = self.run_sync(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, result.changed)
        self.assertEqual({"from_files": 1, "relinked": 0}, result.details["changed"])
        row = self.rows()[path]
        self.assertEqual((photo_id, THEN + 60, len(b"jpeg, tagged")), row[:3])
        self.assertEqual(["Events/Invitational", "Weather/Rain"], row[3])
        self.assertEqual([("sync", "applied")], self.query(
            "SELECT operation, status FROM changes WHERE id = ?", (result.details["change"],)))
        self.assertTrue(sync.last(self.library)["last_run"]["in_step"])

        again = self.run_sync()
        self.assertEqual([], self.reads.read, "the row describes its file again")
        self.assertTrue(again.details["in_step"])


class MovedFiles(SyncTestCase):
    def test_a_moved_file_keeps_its_row_and_its_faces(self):
        old = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:DocumentID": "uuid:3f1c-moved"})
        self.indexed(old, self.photo(self.trip, "IMG_0002.jpg"))
        conn = db.connect(self.db_path)
        add_face(conn, old, name="Rowan Thackeray", name_source="manual")
        conn.commit()
        conn.close()
        photo_id = self.rows()[old][0]
        new = os.path.join(self.trip, "Harbour - 01.jpg")
        os.rename(old, new)
        self.truth["Harbour - 01.jpg"] = self.truth.pop("IMG_0001.jpg")

        dry = self.run_sync()
        counts = dry.details["counts"]
        self.assertEqual((1, 0, 0, 1, 1), (counts["moved"], counts["new"], counts["missing"], counts["moved_faces"],
                                           counts["moved_named"]))
        self.assertIn(old, self.rows(), "a dry run moved the row")

        result = self.run_sync(apply=True, queue=lambda folders: self.fail("nothing is new: it moved"))
        self.assertTrue(result.ok, result.message())
        self.assertEqual({"from_files": 0, "relinked": 1}, result.details["changed"])
        rows = self.rows()
        self.assertNotIn(old, rows)
        self.assertEqual(photo_id, rows[new][0])
        self.assertEqual([("Rowan Thackeray",)], self.query("SELECT name FROM faces WHERE photo_id = ?", (photo_id,)))
        self.assertTrue(result.details["in_step"])

    def test_a_missing_row_with_nothing_new_reads_no_file(self):
        gone = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:DocumentID": "uuid:3f1c-gone"})
        self.indexed(gone, self.photo(self.meet, "IMG_0002.jpg"))
        os.remove(gone)
        self.run_sync()
        self.assertEqual(0, self.reads.sessions, "identities are read only when something is new")


class MovedWithoutAnIdentity(SyncTestCase):
    """Most rows have no DocumentID (67,204 of photo_index's 68,387): a file moved out of
    their folder is matched by its name, size and modified time, which the walk and the
    rows already hold, when that match is the only one on both sides."""

    def move(self, path, folder):
        moved = os.path.join(folder, os.path.basename(path))
        os.rename(path, moved)   # a move keeps the file's modified time
        return moved

    def test_a_moved_file_with_no_identity_follows_by_name_size_and_time(self):
        old = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(old, self.photo(self.trip, "IMG_0002.jpg"))
        photo_id = self.rows()[old][0]
        new = self.move(old, self.trip)
        dry = self.run_sync()
        self.assertEqual(0, self.reads.sessions, "a move matched by its stamp reads no file")
        counts = dry.details["counts"]
        self.assertEqual((1, 0, 0), (counts["moved"], counts["new"], counts["missing"]))
        result = self.run_sync(apply=True, queue=lambda folders: self.fail("nothing is new: it moved"))
        self.assertTrue(result.ok, result.message())
        self.assertEqual(photo_id, self.rows()[new][0])
        self.assertNotIn(old, self.rows())
        self.assertTrue(result.details["in_step"])

    def test_twins_are_reported_not_guessed_and_their_folders_not_queued(self):
        old = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(old, self.photo(self.trip, "IMG_0002.jpg"))
        copies = []
        for day in ("day 1", "day 2"):
            folder = os.path.join(self.trip, "copies", day)
            os.makedirs(folder)
            copies.append(folder)
            self.photo(folder, "IMG_0001.jpg")
        os.remove(old)
        self.photo(self.meet, "IMG_0200.jpg")   # new, beside the missing row
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=len(folders), changed=len(folders))

        result = self.run_sync(apply=True, queue=queue)
        counts = result.details["counts"]
        self.assertEqual((0, 1, 1, 2), (counts["moved"], counts["missing"], counts["ambiguous_rows"],
                                        counts["ambiguous_files"]))
        self.assertIn(old, self.rows(), "a row was moved onto one of two copies")
        self.assertEqual([self.meet], asked, "a folder holding a possible copy of a missing photo was queued")
        self.assertFalse(result.details["in_step"])

    def test_identities_are_read_only_for_what_the_stamps_did_not_settle(self):
        plain = self.photo(self.meet, "IMG_0001.jpg")
        renamed = self.photo(self.meet, "IMG_0002.jpg", **{"XMP:DocumentID": "uuid:7a2e-renamed"})
        self.indexed(plain, renamed, self.photo(self.trip, "IMG_0003.jpg"))
        self.move(plain, self.trip)
        new_name = os.path.join(self.trip, "Harbour - 02.jpg")
        os.rename(renamed, new_name)
        self.truth["Harbour - 02.jpg"] = self.truth["IMG_0002.jpg"]
        result = self.run_sync()
        self.assertEqual([new_name], self.reads.read, "only the file the stamps did not settle is read")
        self.assertEqual(2, result.details["counts"]["moved"])


class MissingFiles(SyncTestCase):
    def test_a_missing_file_is_reported_never_removed(self):
        gone = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(gone, self.photo(self.meet, "IMG_0002.jpg"))
        photo_id = self.rows()[gone][0]
        os.remove(gone)
        result = self.run_sync(apply=True)
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((1, 1, 0), (counts["missing"], counts["missing_folders"], counts["folders_gone"]))
        self.assertEqual([photo_id], result.details["ids"]["missing"])
        self.assertIn(gone, self.rows())
        self.assertEqual(0, result.changed)
        self.assertTrue(result.details["in_step"], "a missing file is reported; it does not hold sync back")

    def test_a_folder_wholly_gone_is_named_as_gone(self):
        first, second = self.photo(self.trip, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg")
        self.indexed(first, second, self.photo(self.meet, "IMG_0003.jpg"))
        os.remove(first)
        os.remove(second)
        os.rmdir(self.trip)
        result = self.run_sync(apply=True)
        counts = result.details["counts"]
        self.assertEqual((2, 1, 1), (counts["missing"], counts["missing_folders"], counts["folders_gone"]))
        self.assertEqual([{"folder": self.trip, "rows": 2, "gone": True}], result.details["reveal"]["missing_folders"])
        self.assertIn(first, self.rows())
        self.assertIn(second, self.rows())

    def test_a_root_that_is_not_there_is_reported_like_an_unplugged_drive(self):
        here = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(here)
        # A drive that is not plugged in: its rows name a root that does not exist.
        drive = os.path.join(self.home.root, "Backup drive")
        season = os.path.join(drive, "Photos", "2019")
        os.makedirs(season)
        away = [self.photo(season, "IMG_%04d.jpg" % n) for n in range(3)]
        self.indexed(*away)
        os.rename(drive, drive + " (unplugged)")
        try:
            result = self.run_sync(apply=True, queue=lambda folders: self.fail("nothing is new"))
        finally:
            os.rename(drive + " (unplugged)", drive)
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((3, 1, 1, 1), (counts["missing"], counts["folders_gone"], counts["roots_gone"],
                                        counts["folders_walked"]))
        self.assertEqual([season], result.details["reveal"]["roots_gone"])
        self.assertEqual([], self.reads.read)
        self.assertEqual(4, len(self.rows()), "rows on an unplugged drive were removed")


class Roots(SyncTestCase):
    """The library's roots are walked, not only the folders it holds photos in: a new
    folder beside the indexed ones is seen -- and listed to review, never indexed on its
    own. A folder under an ignored one is passed over."""

    def setUp(self):
        super().setUp()
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))

    def queue(self, folders):
        self.fail("a folder to review was queued: %s" % folders)

    def test_a_new_folder_beside_the_indexed_ones_is_listed_to_review(self):
        lights = os.path.join(self.pictures, "2025-12 Harbour Lights")
        os.makedirs(os.path.join(lights, "day 2"))
        self.photo(lights, "IMG_0300.jpg")
        self.photo(os.path.join(lights, "day 2"), "IMG_0301.jpg")
        unseen = self.run_sync()
        self.assertEqual(0, unseen.details["counts"]["review_folders"], "without roots, only indexed folders are walked")
        result = self.run_sync(apply=True, queue=self.queue, roots=[self.pictures])
        counts = result.details["counts"]
        self.assertEqual((0, 1, 2), (counts["new"], counts["review_folders"], counts["review_photos"]))
        self.assertEqual([{"path": lights, "photos": 2}], result.details["reveal"]["review"])
        self.assertTrue(result.details["in_step"], "a folder to review is the owner's question, not drift")
        self.assertEqual({"folders": [{"path": lights, "photos": 2}], "photos": 2},
                         sync.review(self.library, [self.pictures]))

    def test_a_folder_under_an_ignored_one_is_passed_over(self):
        scans = os.path.join(self.pictures, "Scans")
        os.makedirs(os.path.join(scans, "1998"))
        self.photo(os.path.join(scans, "1998"), "IMG_0400.jpg")
        result = self.run_sync(roots=[self.pictures], ignored=[scans])
        self.assertEqual((0, 1), (result.details["counts"]["review_folders"], result.details["counts"]["ignored_files"]))
        self.assertEqual([], sync.review(self.library, [self.pictures], [scans])["folders"])

    def test_a_folder_to_review_can_be_synced_alone(self):
        lights = os.path.join(self.pictures, "2025-12 Harbour Lights")
        os.makedirs(lights)
        self.photo(lights, "IMG_0300.jpg")
        result = self.run_sync(folder=lights, roots=[self.pictures])
        self.assertFalse(result.refused, result.refused)
        self.assertEqual(1, result.details["counts"]["review_folders"])

    def test_one_folder_alone_keeps_the_row_of_a_file_moved_in_from_another(self):
        old = os.path.join(self.meet, "IMG_0001.jpg")
        photo_id = self.rows()[old][0]
        new = os.path.join(self.trip, "IMG_0001.jpg")
        os.rename(old, new)
        result = self.run_sync(apply=True, folder=self.trip, queue=lambda folders: self.fail("it moved"),
                               roots=[self.pictures])
        self.assertEqual((1, 0), (result.details["counts"]["moved"], result.details["counts"]["new"]))
        self.assertEqual(photo_id, self.rows()[new][0])
        self.assertEqual(0, self.reads.sessions)

    def test_a_held_folder_outside_every_root_is_kept_in_step_and_nothing_beside_it_reviewed(self):
        # A folder indexed by hand elsewhere (C:/localdepot, say): not under a root.
        depot = os.path.join(self.home.root, "localdepot")
        os.makedirs(os.path.join(depot, "extras"))
        kept = self.photo(depot, "IMG_0600.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        self.indexed(kept)
        self.photo(depot, "IMG_0600.jpg", body=b"jpeg, edited", **{"XMP:Subject": ["Weather/Rain"]})
        self.photo(depot, "IMG_0601.jpg")
        self.photo(os.path.join(depot, "extras"), "IMG_0602.jpg")
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=len(folders), changed=len(folders))

        result = self.run_sync(apply=True, queue=queue, roots=[self.pictures])
        counts = result.details["counts"]
        self.assertEqual((1, 1, 0, 1), (counts["changed"], counts["new"], counts["review_folders"],
                                        counts["outside_roots_files"]))
        self.assertEqual([depot], asked, "the held folder's new file is indexed, alone")
        self.assertEqual(["Weather/Rain"], self.rows()[kept][3])
        self.assertEqual([], sync.review(self.library, [self.pictures])["folders"])

    def test_include_queues_a_folder_under_a_root_and_refuses_one_outside(self):
        lights = os.path.join(self.pictures, "2025-12 Harbour Lights")
        os.makedirs(lights)
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=1, changed=1)

        self.assertEqual(1, sync.include(self.library, lights, [self.pictures], queue).changed)
        self.assertEqual([lights], asked)
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        self.assertTrue(sync.include(self.library, elsewhere, [self.pictures], queue).refused)
        self.assertTrue(sync.include(self.library, "Harbour Lights", [self.pictures], queue).refused)


class Folders(SyncTestCase):
    def test_a_folder_is_walked_once_from_the_topmost_indexed(self):
        sub = os.path.join(self.meet, "finals")
        self.assertEqual([self.meet, self.trip], sync.walk_roots([sub, self.trip, self.meet]))
        self.assertEqual([self.pictures], sync.walk_roots([self.meet, self.pictures, self.trip]))

    def test_one_folder_looks_only_at_its_rows(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))
        os.remove(os.path.join(self.meet, "IMG_0001.jpg"))
        self.photo(self.trip, "IMG_0100.jpg")
        result = self.run_sync(folder=self.trip)
        counts = result.details["counts"]
        self.assertEqual((1, 1, 0), (counts["rows"], counts["new"], counts["missing"]))
        found = self.run_sync(folder=self.meet).details["counts"]
        self.assertEqual((1, 0, 1), (found["rows"], found["new"], found["missing"]))

    @unittest.skipUnless(sys.platform == "win32", "a directory junction is Windows'")
    def test_a_junction_is_not_walked_into(self):
        """os.scandir's is_symlink() is False for a junction on Python 3.11, so a junction
        back up the tree was walked round and round until the path was too long, each
        lap finding the same photos under a longer name -- and the indexer's walk made a
        row for each of them."""
        import _winapi

        path = self.photo(self.meet, "IMG_0001.jpg")
        loop = os.path.join(self.meet, "loop")
        _winapi.CreateJunction(self.meet, loop)
        self.addCleanup(os.rmdir, loop)   # the junction alone, never what it points at
        self.assertEqual([path], images.photos_under(self.meet))
        self.assertEqual([path], [stored for stored, _m, _s in images.stamps_under(self.meet).values()])
        # Relink's walk and the folder picker's count too.
        from tagpup.services import photos as photo_actions
        from tagpup.services import relink_photos
        self.assertEqual([path], relink_photos._photos_under(self.meet))
        self.assertEqual((1, False), photo_actions.count_photos(self.meet))

    def test_a_folder_alone_is_indexed_without_its_subfolders(self):
        """Sync queues an indexed folder's new files with that folder alone: its subfolders
        may be folders to review, or ignored, and indexing takes them otherwise."""
        from unittest.mock import MagicMock
        from tagpup.services import indexing

        path = self.photo(self.meet, "IMG_0001.jpg")
        os.makedirs(os.path.join(self.meet, "finals"))
        self.photo(os.path.join(self.meet, "finals"), "IMG_0002.jpg")
        self.assertEqual([path], images.photos_in(self.meet))
        proc = MagicMock(returncode=0)
        proc.stdout.readline.side_effect = ["done\n", ""]
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            indexing.index_folder(self.library, self.meet, "code", subfolders=False)
        self.assertEqual(["index", self.meet, "--no-subfolders"], popen.call_args[0][0][-3:])
        # Several folders are one run of the indexer.
        proc.stdout.readline.side_effect = ["done\n", ""]
        with mock.patch("subprocess.Popen", return_value=proc) as popen:
            indexing.index_folder(self.library, [self.meet, self.trip], "code", subfolders=False)
        self.assertEqual(1, popen.call_count)
        self.assertEqual(["index", self.meet, self.trip, "--no-subfolders"], popen.call_args[0][0][-4:])

    def test_the_walk_stamps_each_photo_as_the_disk_does(self):
        path = self.photo(self.meet, "IMG_0001.jpg", body=b"12345")
        open(os.path.join(self.meet, "notes.txt"), "w").close()
        found = images.stamps_under(self.pictures)
        self.assertEqual(1, len(found))
        (stored, mtime, size), = found.values()
        self.assertEqual((path, THEN, 5), (stored, mtime, size))


if __name__ == "__main__":
    unittest.main()
