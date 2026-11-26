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
import logging
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402
import photo_rows  # noqa: E402
import roots_library as rl  # noqa: E402
from face_rows import add_face, people_of  # noqa: E402

import numpy as np  # noqa: E402
from face_rows import add_vector  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.core.paths import key as paths_key  # noqa: E402
from tagpup.core.result import Result  # noqa: E402
from tagpup.files import images, shares  # noqa: E402
from tagpup.files import metadata  # noqa: E402
from tagpup.jobs import watching  # noqa: E402
from tagpup.services import libraries as library_actions  # noqa: E402
from tagpup.services import sync  # noqa: E402
from tagpup.store import db, faces_detected, journal, people, taxonomy  # noqa: E402

#: When every file was last written, before the test changes any.
THEN = 1_700_000_000


class Reads:
    """What the stand-in ExifTool was asked: files read whole, and sessions opened for
    the identities."""

    def __init__(self, truth):
        self.truth = truth
        self.read, self.sessions = [], 0

    def whole(self, reader, file_paths, people=None, bakes=False):
        self.read.extend(file_paths)
        return [metadata.structure(path, dict(self.truth.get(os.path.basename(path), {})), people,
                                   metadata.stamp_before_read(path)) for path in file_paths]

    def session(self, *args, **kwargs):
        reads = self

        class Session:
            def __enter__(self):
                reads.sessions += 1
                return self

            def __exit__(self, *exc):
                return False

            def get_tags(self, batch, tags=None, params=None):
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
        """Rows for the photos, as a whole index leaves each: read, its vector kept, looked at for faces."""
        conn = db.connect(self.db_path)
        try:
            ids = [photo_rows.add_indexed(conn, path, self.truth[os.path.basename(path)]) for path in photo_paths]
            conn.commit()
        finally:
            conn.close()
        return ids

    def run_sync(self, apply=False, folder=None, queue=None, roots=(), ignored=(), **more):
        self.reads = Reads(self.truth)
        with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                        side_effect=self.reads.whole), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=self.reads.session):
            return sync.sync(self.library, folder=folder, apply=apply, exiftool_path="exiftool", queue=queue,
                             roots=roots, ignored=ignored, **more)

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
        import migration_names
        from tagpup.store import schema

        older = self.home.library("older.db")
        schema.ensure(older)
        self.enterContext(migration_names.ahead())   # the code knows a step the library has not had
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
        with mock.patch("tagpup.store.job_runs.record_sync", side_effect=OSError("locked")):
            result = self.run_sync(apply=True)
        self.assertTrue(result.ok, result.message())
        self.assertEqual(1, result.changed)
        self.assertEqual(["Weather/Rain"], self.rows()[path][3])
        self.assertEqual(1, len(result.details["warnings"]))


class WhatASyncLogs(SyncTestCase):
    """A sync's lines in the server's log, which keeps INFO and up: a folder's sync writes only its end, at INFO when it
    was worth a record -- even when the record could not be written -- and at DEBUG when it found nothing (the
    watcher's, each time a folder settles); a whole sync writes its begin line too."""

    def lines(self, level, **sync_args):
        with self.assertLogs("tagpup.services.sync", level="DEBUG") as logged:
            result = self.run_sync(apply=True, **sync_args)
        self.assertTrue(result.ok, result.message())
        return [(record.levelno, record.getMessage()) for record in logged.records if record.levelno >= level]

    def test_a_folder_sync_that_changed_a_row_ends_at_info_though_its_record_failed(self):
        path = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        self.indexed(path)
        self.photo(self.meet, "IMG_0001.jpg", body=b"jpeg, edited", **{"XMP:Subject": ["Weather/Rain"]})
        with mock.patch("tagpup.store.job_runs.record_sync", side_effect=OSError("locked")):
            said = self.lines(logging.INFO, folder=self.meet)
        self.assertEqual(1, len(said), said)
        self.assertTrue(said[0][1].startswith("Sync of %s ended in " % self.meet), said)
        self.assertIn("changed 1 row(s)", said[0][1])

    def test_a_folder_sync_writes_no_begin_line_and_one_that_found_nothing_ends_at_debug(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        self.run_sync(apply=True)     # the newest record says in step: a folder settling on nothing is not worth one
        said = self.lines(logging.DEBUG, folder=self.meet)
        self.assertEqual([logging.DEBUG], [level for level, _message in said], said)
        self.assertIn(" ended in ", said[0][1])

    def test_a_whole_sync_writes_its_begin_and_its_end(self):
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        said = self.lines(logging.INFO)
        self.assertEqual(["Sync of the whole library began.", "Sync of the whole library ended"],
                         [message[:len("Sync of the whole library ended")] if " ended" in message else message
                          for _level, message in said])


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

    def test_with_no_roots_a_new_subfolder_of_a_held_folder_is_listed_to_review_never_queued(self):
        """A library whose roots are not set yet keeps its held folders in step as indexing
        walked them: new files in a held folder are queued; a new subfolder is a folder to review, as it is
        under a root, and is never queued unasked (#395, the owner 2026-10-08)."""
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
        self.assertEqual((1, 1, 1), (counts["new"], counts["review_folders"], counts["review_photos"]))
        self.assertEqual([self.trip], asked, "the subfolder was queued unasked")
        self.assertEqual([{"path": sub, "photos": 1}], result.details["reveal"]["review"])
        self.assertEqual({"folders": [{"path": sub, "photos": 1}], "photos": 1}, sync.review(self.library, []))

    def test_with_no_roots_a_subfolder_to_review_can_be_included(self):
        self.indexed(self.photo(self.trip, "IMG_0002.jpg"))
        sub = os.path.join(self.trip, "day 2")
        os.makedirs(sub)
        self.photo(sub, "IMG_0101.jpg")
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=1, changed=1)

        self.assertEqual(1, sync.include(self.library, sub, [], queue).changed)
        self.assertEqual([sub], asked)
        elsewhere = os.path.join(self.home.root, "Elsewhere")
        os.makedirs(elsewhere)
        self.assertTrue(sync.include(self.library, elsewhere, [], queue).refused, "a folder beside none the library holds")
        # One the library ignores is not offered, nor taken by hand (#906: include passes the ignored list as look does).
        self.assertTrue(sync.include(self.library, sub, [], queue, ignored=[sub]).refused)
        asked.clear()
        self.assertTrue(sync.include(self.library, sub, [], queue, ignored=[self.home.root + "-elsewhere"]).changed)

    def test_a_new_folder_the_queue_found_gone_is_skipped_and_said_so(self):
        """#2229: the queue's `invalid` folders (gone or away between the walk and the queue, others queued: the shape
        jobs/indexing.py `_enqueue` makes, which refuses outright when none is valid) were never read; the run's skipped
        list names each, the queued one is not named, and the run is recorded not in step. Break: dropping
        outcome.details["invalid"] on the floor."""
        self.indexed(self.photo(self.meet, "IMG_0001.jpg"))
        self.indexed(self.photo(self.trip, "IMG_0002.jpg"))
        self.photo(self.meet, "IMG_0100.jpg")
        self.photo(self.trip, "IMG_0100.jpg")

        def queue(folders):
            # The trip folder went away between the walk and the queue; the meet folder was queued.
            queued = Result(attempted=len(folders), changed=1)
            queued.details["invalid"] = [folder for folder in folders if paths_key(folder) == paths_key(self.trip)]
            return queued

        result = self.run_sync(apply=True, queue=queue, roots=[self.pictures])
        self.assertEqual([(self.trip, sync.GONE_BEFORE_QUEUED)], result.skipped)
        self.assertEqual(1, result.details["queued"])
        self.assertFalse(result.details["in_step"], "two folders of new files, one never queued")
        self.assertIsNotNone(result.details.get(sync.RECORD), "the run is recorded")
        recorded = sync.last(self.library)["last_run"]
        self.assertEqual((False, 1), (recorded["in_step"], recorded["changed"]["queued_folders"]))

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
        self.assertEqual({"from_files": 1, "relinked": 0, "removed": 0, "removed_faces": 0, "removed_named": 0},
                         result.details["changed"])
        row = self.rows()[path]
        self.assertEqual((photo_id, THEN + 60, len(b"jpeg, tagged")), row[:3])
        self.assertEqual(["Events/Invitational", "Weather/Rain"], row[3])
        self.assertEqual([("sync", "applied")], self.query(
            "SELECT operation, status FROM changes WHERE id = ?", (result.details["change"],)))
        # Its vector's stamp is the file's before the edit: the index owes it a look (photo_fingerprints.owed), so the
        # folder is queued and the library is not in step until the index has settled it.
        self.assertEqual((1, [path]), (result.details["counts"]["owed_stale"], result.details["reveal"]["to_check"]))
        self.assertFalse(sync.last(self.library)["last_run"]["in_step"])

        again = self.run_sync()
        self.assertEqual([], self.reads.read, "the row describes its file again")
        self.assertEqual((0, 1, False), (again.details["counts"]["changed"], again.details["counts"]["to_check"],
                                         again.details["in_step"]))

    def edited_elsewhere(self, name, keywords):
        """A photo indexed with no keyword, then given `keywords` by another program: its file grows and its time
        moves. Returns (path, photo id)."""
        path = self.photo(self.meet, name)
        (photo_id,) = self.indexed(path)
        self.photo(self.meet, name, body=b"jpeg, tagged", **{"XMP:Subject": keywords})
        os.utime(path, (THEN + 60, THEN + 60))
        return path, photo_id

    def test_a_row_the_app_saves_while_sync_reads_keeps_the_save_and_the_others_are_written(self):
        """The owner saves a photo in TagPup while an applied sync is reading the changed files: writing the older
        read over the row would take the save back. That row is skipped and named, the other changed row is written
        (refusing the whole change for one save threw away the reading, perhaps every time on a library in use), and
        the next sync reads the saved row again. Undone, the written row goes back and the save stays."""
        saved, saved_id = self.edited_elsewhere("IMG_0001.jpg", ["Weather/Rain"])
        other, other_id = self.edited_elsewhere("IMG_0002.jpg", ["Weather/Sun"])
        before = self.rows()
        reads = Reads(self.truth)

        def read_then_the_app_saves(reader, file_paths, people=None):
            records = reads.whole(reader, file_paths, people)
            conn = db.connect(self.db_path)
            try:
                conn.execute("UPDATE photos SET tags = ?, mtime = ? WHERE id = ?",
                             (json.dumps(["Activity/Rowing"]), THEN + 120, saved_id))
                conn.commit()
            finally:
                conn.close()
            return records

        with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                        side_effect=read_then_the_app_saves), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=reads.session):
            result = sync.sync(self.library, apply=True, exiftool_path="exiftool")
        self.assertEqual(sorted([saved, other]), sorted(reads.read))
        rows = self.rows()
        self.assertEqual((THEN + 120, ["Activity/Rowing"]), (rows[saved][1], rows[saved][3]),
                         "the app's save was overwritten with the older read")
        self.assertEqual([("photos %d" % saved_id, "not what the plan read: mtime, tags changed")], result.skipped)
        self.assertEqual({"from_files": 1, "relinked": 0, "removed": 0, "removed_faces": 0, "removed_named": 0},
                         result.details["changed"])
        self.assertEqual(["Weather/Sun"], rows[other][3])
        change = result.details["change"]
        self.assertEqual([[other_id]], journal.history(self.db_path, change_id=change)[0]["keys"]["photos"])

        journal.undo(self.db_path, change)
        rows = self.rows()
        self.assertEqual(before[other], rows[other])
        self.assertEqual(["Activity/Rowing"], rows[saved][3], "the undo took the save back")

        self.run_sync()
        self.assertEqual(sorted([saved, other]), sorted(self.reads.read), "the next sync reads the saved row again")

    def test_a_person_named_only_on_a_face_stays_in_the_rows_people_after_the_re_read(self):
        """The file names nobody in its keywords; the owner named a face in it. Sync reads the edited file again, and
        the row's people are the keywords' and the named faces', as every writer of them records them: the face's
        person is not taken off."""
        path, _photo_id = self.edited_elsewhere("IMG_0001.jpg", ["Weather/Rain"])
        conn = db.connect(self.db_path)
        try:
            add_face(conn, path, name="Rowan Thackeray", name_source="manual")
            conn.commit()
        finally:
            conn.close()
        result = self.run_sync(apply=True)
        self.assertEqual({"from_files": 1, "relinked": 0, "removed": 0, "removed_faces": 0, "removed_named": 0},
                         result.details["changed"])
        self.assertEqual(["Weather/Rain"], self.rows()[path][3])
        conn = db.connect(db.readonly_uri(self.db_path), uri=True)
        try:
            self.assertEqual(["Rowan Thackeray"], people_of(conn, path))
        finally:
            conn.close()


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
        self.assertEqual({"from_files": 0, "relinked": 1, "removed": 0, "removed_faces": 0, "removed_named": 0},
                         result.details["changed"])
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

    def test_a_file_renamed_by_tagpup_is_followed_inside_its_own_folder_only(self):
        """Lifted from test_relink_stays_in_its_folder: camera names repeat across a library, and a dead IMG_0421 in
        one folder was re-pointed -- with its named faces -- at another folder's IMG_0421 renamed. Break:
        relink_photos keys the lookup by stem alone (_by_original and pair)."""
        dead_here = self.photo(self.meet, "IMG_0421.jpg", body=b"a")
        dead_there = self.photo(self.trip, "IMG_0002.jpg", body=b"b")
        self.indexed(dead_here, dead_there)
        conn = db.connect(self.db_path)
        add_face(conn, dead_here, name="Rowan Thackeray", name_source="manual")
        conn.commit()
        conn.close()
        os.remove(dead_here)
        os.remove(dead_there)
        preserved = "XMP:PreservedFileName"
        self.photo(self.meet, "Invitational - 01.jpg", body=b"cc", **{preserved: "IMG_0001.CR3"})
        self.photo(self.trip, "Classic - 01.jpg", body=b"ddd", **{preserved: "IMG_0421.CR3"})
        beside = self.photo(self.trip, "Classic - 02.jpg", body=b"eeee", **{preserved: "IMG_0002.CR3"})
        result = self.run_sync(apply=True, queue=lambda folders: Result(attempted=len(folders), changed=len(folders)))
        self.assertTrue(result.ok, result.message())
        rows = self.rows()
        self.assertIn(dead_here, rows, "a row was re-pointed at another folder's file of the same camera name")
        self.assertNotIn(dead_there, rows)
        self.assertIn(beside, rows, "a row is still followed to the file renamed beside it")
        self.assertEqual((1, 1), (result.details["counts"]["moved"], result.details["counts"]["missing"]))
        self.assertEqual([("Rowan Thackeray",)], self.query(
            "SELECT f.name FROM faces f JOIN photos p ON p.id = f.photo_id WHERE p.path = ?", (dead_here,)))

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


class APictureToCheck(SyncTestCase):
    """A file written outside the app after its photo vector was made: the vector's stamp no longer describes it
    (photo_fingerprints.same_picture, the one rule; owner, DECISIONS 2026-10-27). Sync decodes nothing and decides
    nothing of the picture: its row is read again as any changed row is, its vector, faces and decided names are left,
    and its folder is queued for the index as a new file's is (`to_check`), whose decode tells another program's
    metadata write (the same picture, kept) from a crop (computed again). A sync before this counted a size change and
    left the photo for the owner to fix by hand (#336, #903, #904); a row it had read again was current from then on, its
    vector the old picture's (74 such in photo_index, 2026-10-27)."""

    def setUp(self):
        super().setUp()
        self.path = self.photo(self.meet, "IMG_0001.jpg", body=b"the picture as it was",
                               **{"XMP:Subject": ["Events/Invitational"]})
        self.other = self.photo(self.trip, "IMG_0002.jpg", body=b"another picture", **{"XMP:Subject": ["Weather/Rain"]})
        self.indexed(self.path, self.other)
        conn = db.connect(self.db_path)
        try:
            for path in (self.path, self.other):
                add_vector(conn, path, np.array([1.0, 0.0], dtype=np.float32).tobytes())
                add_face(conn, path, [0, 0, 10, 10])
                faces_detected.record(conn, path, faces_detected.detector({"min_face_size": 20}), None, 1)
            rowan = taxonomy.add_path(conn, "People/Rowan Thackeray", root_has_face=1)   # a face is named with its node's id
            conn.execute("UPDATE faces SET name = 'Rowan Thackeray', tag_id = ?, name_source = 'manual' WHERE photo_id ="
                         " (SELECT id FROM photos WHERE path = ?)", (rowan, self.path))
            conn.commit()
        finally:
            conn.close()
        # Sync decodes no picture: a decode is the index's.
        for name in ("opened", "fingerprint"):
            patcher = mock.patch.object(images, name, side_effect=AssertionError("sync decoded a picture"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def edit(self, path, body):
        """The file written elsewhere: `body` (more, fewer or as many bytes), a later time."""
        with open(path, "wb") as handle:
            handle.write(body)
        os.utime(path, (THEN + 100, THEN + 100))

    def held(self, path):
        """(faces, vectors, detection records, face names, the vector's stamp) the library holds of the photo."""
        return self.query(
            "SELECT (SELECT COUNT(*) FROM faces WHERE photo_id = p.id), (SELECT COUNT(*) FROM embeddings WHERE photo_id = p.id),"
            " (SELECT COUNT(*) FROM faces_detected WHERE photo_id = p.id),"
            " (SELECT GROUP_CONCAT(name) FROM faces WHERE photo_id = p.id),"
            " (SELECT mtime FROM embeddings WHERE photo_id = p.id) FROM photos p WHERE p.path = ?", (path,))[0]

    def queue(self):
        asked = []

        def queue(folders):
            asked.append(list(folders))
            return Result(attempted=len(folders), changed=len(folders))
        return asked, queue

    def test_an_outside_metadata_write_is_queued_and_never_decoded_and_the_owner_is_told_how_many_to_check(self):
        self.truth["IMG_0001.jpg"] = {"XMP:Subject": ["Events/Classic"]}
        self.edit(self.path, b"the picture as it was, and a keyword")
        before = self.held(self.path)
        rehearsal = self.run_sync()
        self.assertEqual((1, False, [self.meet]), (rehearsal.details["counts"]["to_check"], rehearsal.details["in_step"],
                                                   rehearsal.details["reveal"]["new_folders"]))
        self.assertEqual(before, self.held(self.path), "a dry run wrote nothing")

        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        self.assertTrue(result.ok, result.message())
        self.assertEqual((1, 1, [[self.meet]]), (result.details["counts"]["to_check"],
                                                 result.details["changed"]["from_files"], asked))
        tags = json.loads(self.query("SELECT tags FROM photos WHERE path = ?", (self.path,))[0][0])
        self.assertEqual((True, False), ("Events/Classic" in tags, "Events/Invitational" in tags), "its row read again")
        self.assertEqual(before, self.held(self.path), "its faces, names, vector and its stamp are the index's to settle")
        self.assertFalse(sync.last(self.library)["last_run"]["in_step"], "a picture to check is not in step")

        # The row read again describes its file; the vector's stamp still does not: queued again until the index settles
        # it (the shape of the 74 an earlier sync left current with the old picture's vector).
        asked, queue = self.queue()
        again = self.run_sync(apply=True, queue=queue)
        self.assertEqual((0, 1, [[self.meet]]), (again.details["counts"]["changed"], again.details["counts"]["to_check"],
                                                 asked))

        # The owner is told, in counts.
        from click.testing import CliRunner
        from tagpup_cli import cli
        with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                        side_effect=Reads(self.truth).whole), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=Reads(self.truth).session):
            said = CliRunner().invoke(cli, ["--db", self.db_path, "sync"])
        from tagpup_cli import OWED_LINE
        self.assertIn(OWED_LINE % (1, 1, 0, 1, 0, 0), said.output)

    def said(self):
        """What the CLI's `sync` (a dry run) tells the owner."""
        from click.testing import CliRunner
        from tagpup_cli import cli
        with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                        side_effect=Reads(self.truth).whole), \
                mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=Reads(self.truth).session):
            return CliRunner().invoke(cli, ["--db", self.db_path, "sync"]).output

    def unfinished(self, folder):
        """Two photos in `folder` an index left part done, as photo_index holds 223 (18 folders, 2026-10-27): one read
        and looked at for faces with no vector kept, one read with its vector kept and never looked at for faces."""
        bare, unlooked = self.photo(folder, "IMG_0003.jpg"), self.photo(folder, "IMG_0004.jpg")
        self.indexed(bare, unlooked)
        self.query_write("DELETE FROM embeddings WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (bare,))
        self.query_write("DELETE FROM faces_detected WHERE photo_id = (SELECT id FROM photos WHERE path = ?)", (unlooked,))
        return bare, unlooked

    def query_write(self, sql, params=()):
        conn = db.connect(self.db_path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_a_photo_with_no_vector_and_one_never_examined_are_queued_and_the_owner_is_told_why(self):
        """A sync said "In step" over 223 never-examined photos of photo_index and two with no vector (2026-10-27): it
        asked only whether a vector's stamp described its file. Now it asks photo_fingerprints.owed, the index's own
        answer, and says how many by reason."""
        regatta = os.path.join(self.pictures, "2025-12 Regatta")
        os.makedirs(regatta)
        self.unfinished(regatta)
        rehearsal = self.run_sync()
        counts = rehearsal.details["counts"]
        self.assertEqual((2, 1, 0, 1, 1, 0, False),
                         (counts["to_check"], counts["owed_no_vector"], counts["owed_stale"], counts["owed_unexamined"],
                          counts["owed_folders"], counts["owed_held_back"], rehearsal.details["in_step"]))
        self.assertEqual([], self.reads.read, "nothing read: the rows describe their files")

        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        self.assertEqual(([[regatta]], False), (asked, result.details["in_step"]), "its folder goes to the index")
        self.assertFalse(sync.last(self.library)["last_run"]["in_step"])
        from tagpup_cli import OWED_LINE
        said = self.said()
        self.assertIn(OWED_LINE % (2, 1, 1, 0, 1, 0), said)
        self.assertNotIn("In step.", said)
        self.assertNotIn("IMG_000", said, "counts, never a file")

    def test_a_folder_held_back_is_not_queued_for_the_work_owed_in_it_and_the_owner_is_told(self):
        regatta = os.path.join(self.pictures, "2025-12 Regatta")
        os.makedirs(regatta)
        self.unfinished(regatta)
        # A photo gone from its folder, and a copy of it in two folders: which one it moved to is the owner's to say.
        gone = self.photo(self.meet, "IMG_0009.jpg", body=b"the ninth")
        self.indexed(gone)
        os.remove(gone)
        self.photo(regatta, "IMG_0009.jpg", body=b"the ninth")
        self.photo(self.trip, "IMG_0009.jpg", body=b"the ninth")
        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        counts = result.details["counts"]
        self.assertEqual((2, 1, 1), (counts["to_check"], counts["owed_folders"], counts["owed_held_back"]))
        self.assertNotIn(regatta, [folder for folders in asked for folder in folders], "a held-back folder was indexed")
        from tagpup_cli import OWED_HELD_BACK, OWED_LINE
        self.assertIn(OWED_LINE % (2, 1, 1, 0, 1, 0) + OWED_HELD_BACK % 1, self.said())

    def test_a_photo_model_switched_owes_the_photos_its_pass_did_not_reach(self):
        """A switch of photo model (a journaled settings change, allowed once all but UNPASSED_SHARE_ALLOWED of the photos
        hold a vector under its key: settings.photo_switch_on) leaves the photos its pass did not reach with no vector
        under the key the library now names: sync owes them, folder by folder, and nothing else. Before, sync asked only
        the vectors it found and said "In step" (photo_index's 1 + 1 with no vector after its switch, 2026-10-27)."""
        from tagpup.services import settings as library_settings
        from tagpup.store import embeddings
        regatta, pier = os.path.join(self.pictures, "2025-12 Regatta"), os.path.join(self.trip, "Pier")
        photos = []
        for folder, count in ((regatta, 20), (pier, 1)):
            os.makedirs(folder)
            photos += [self.photo(folder, "IMG_%04d.jpg" % (100 + len(photos) + n)) for n in range(count)]
        self.indexed(*photos)
        switch = {"model.preserve_full_frame": "false"}
        new_key = library_settings.change(self.library, switch, acknowledged=["clip"], apply=False)
        new_key = new_key.details["photo_vectors"]["key"]
        conn = db.connect(self.db_path)
        try:   # the new model's pass (its keep_batch: no fingerprint taken, the row forgotten): every photo but the
            # one in the pier's folder, and the two photos set up
            for path in photos[:-1] + [self.path, self.other]:
                stat = os.stat(path)
                embeddings.put(conn, path, new_key, stat.st_mtime, stat.st_size, photo_rows.VECTOR, None)
            conn.commit()
        finally:
            conn.close()
        before = self.run_sync().details["counts"]
        self.assertEqual((0, 0, 0), (before["owed_no_vector"], before["owed_stale"], before["owed_unexamined"]),
                         "the new model's vectors are no work of the index's yet")
        switched = library_settings.change(self.library, switch, acknowledged=["clip"])
        self.assertTrue(switched.ok, switched.message())

        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        counts = result.details["counts"]
        # The pier's photo has no vector under the new key; each of the 22 the pass reached owes its file a bake under it.
        self.assertEqual((23, 1, 0, 0, 22, 4), (counts["to_check"], counts["owed_no_vector"], counts["owed_stale"],
                                                counts["owed_unexamined"], counts["owed_no_fingerprint"],
                                                counts["owed_folders"]))
        self.assertEqual([sorted([regatta, pier, self.meet, self.trip])], [sorted(folders) for folders in asked])
        self.assertEqual(([], False), (self.reads.read, result.details["in_step"]))

    def test_a_cropped_picture_is_queued_the_same_way(self):
        self.edit(self.path, b"cropped")
        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        self.assertEqual((1, [[self.meet]]), (result.details["counts"]["to_check"], asked))
        self.assertEqual((1, 1, 1, "Rowan Thackeray"), self.held(self.path)[:4], "never thrown away by sync")

    def test_a_current_vector_with_no_fingerprint_row_owes_its_file_a_bake_and_sync_decodes_nothing(self):
        """9,882 of photo_index's vectors are current by their stamps and have no fingerprint row (2026-10-28): made by
        Suggest's keep or a re-embedding pass, which take no fingerprint. Their stamps are trusted (no vector made
        again); their files were never baked, so sync queues their folders for the index's bake and is not in step."""
        self.query_write("DELETE FROM photo_fingerprints")
        asked, queue = self.queue()
        result = self.run_sync(apply=True, queue=queue)
        counts = result.details["counts"]
        self.assertEqual((2, 2, 0, 0, False), (counts["to_check"], counts["owed_no_fingerprint"], counts["owed_no_vector"],
                                               counts["owed_stale"], result.details["in_step"]))
        self.assertEqual(sorted([self.meet, self.trip]), sorted(folder for folders in asked for folder in folders))
        self.assertEqual([], self.reads.read)

    def test_a_write_that_kept_the_size_is_checked_and_a_file_exiftool_cannot_read_is_still_queued(self):
        self.edit(self.path, b"the picture as it was")       # the same length, a later time
        self.edit(self.other, b"another picture, longer")
        self.truth["IMG_0002.jpg"] = {}                      # ExifTool answers nothing for it
        asked, queue = self.queue()
        counts = self.run_sync(apply=True, queue=queue).details["counts"]
        self.assertEqual((2, 2, 1), (counts["changed"], counts["to_check"], counts["unreadable"]))
        self.assertEqual([[self.meet, self.trip]], asked)
        self.assertEqual([(1, 1, 1)] * 2, [self.held(self.path)[:3], self.held(self.other)[:3]])

    def test_a_share_that_does_not_answer_calls_nothing_changed_or_missing(self):
        self.edit(self.path, b"cropped")
        answers = {self.meet: "unanswered"}
        real = sync.shares.folder_state
        with mock.patch.object(sync.shares, "folder_state", side_effect=lambda f, *a: answers.get(f) or real(f, *a)):
            asked, queue = self.queue()
            result = self.run_sync(apply=True, queue=queue, folder=self.meet)
        counts = result.details["counts"]
        self.assertEqual((0, 0, 0, 1), (counts["to_check"], counts["changed"], counts["missing"], counts["not_looked_at"]))
        self.assertEqual([], asked)
        self.assertFalse(result.details["in_step"])

    def test_a_sync_stopped_mid_walk_leaves_nothing_marked_and_two_in_a_row_queue_the_same_folder(self):
        self.edit(self.path, b"cropped")
        before = (self.held(self.path), self.held(self.other), self.rows())
        def stops_part_way(folder, unlisted, listed=None):
            raise KeyboardInterrupt("the owner closed the window as the walk listed %s" % os.path.basename(folder))
        with mock.patch.object(images, "stamps_under", side_effect=stops_part_way), \
                self.assertRaises(KeyboardInterrupt):
            self.run_sync(apply=True, queue=self.queue()[1], roots=[self.pictures])
        self.assertEqual(before, (self.held(self.path), self.held(self.other), self.rows()), "nothing half-marked")

        # Two syncs before the index runs: each finds the same picture to check and queues its folder; the index
        # queue's own key keeps one run a folder (tagpup.jobs.indexing). The second writes nothing the first did not.
        first_asked, first = self.queue()
        second_asked, second = self.queue()
        one = self.run_sync(apply=True, queue=first)
        two = self.run_sync(apply=True, queue=second)
        self.assertEqual(([[self.meet]], [[self.meet]]), (first_asked, second_asked))
        self.assertEqual((1, 1, 0), (one.details["counts"]["to_check"], two.details["counts"]["to_check"], two.changed))


class MissingFiles(SyncTestCase):
    """A file the walk looked for and did not find is removed by a sync of the whole library, its root confirmed there
    (owner, DECISIONS 2026-10-28); a file not looked at -- a drive unplugged, a share that dropped, a root pointed at an
    empty folder, a sync of one folder -- is never removed. The removal is one journaled change that undoes."""

    def rooted(self, *photo_paths, named=()):
        """The photos indexed, a named face on each of `named` and an unnamed one on each photo, and the library
        held under root `pictures` at self.pictures, as the three live libraries are. {path: photo id}."""
        ids = dict(zip(photo_paths, self.indexed(*photo_paths)))
        conn = db.connect(self.db_path)
        try:
            for path in photo_paths:
                add_face(conn, path, [1, 2, 11, 12])
            for path in named:
                add_face(conn, path, [20, 20, 30, 30], name="Rowan Thackeray", name_source="manual")
            people.rebuild(conn, sorted(ids.values()))     # as a writer naming a face rebuilds its photo's people
            conn.commit()
        finally:
            conn.close()
        rl.convert_to_root(self.db_path, self.pictures)
        return ids

    def held_ids(self):
        return {photo_id for (photo_id,) in self.query("SELECT id FROM photos")}

    def test_a_file_deleted_under_a_root_that_is_there_is_removed_with_its_faces(self):
        gone, kept = self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.meet, "IMG_0002.jpg")
        ids = self.rooted(gone, kept, named=[gone])
        os.remove(gone)
        dry = self.run_sync(roots=[self.pictures])
        counts = dry.details["counts"]
        self.assertEqual((1, 1, 2, 1), (counts["missing"], counts["to_remove"], counts["to_remove_faces"],
                                        counts["to_remove_named"]))
        self.assertEqual([ids[gone]], dry.details["ids"]["to_remove"])
        self.assertFalse(dry.details["in_step"], "a row to remove is work left")
        self.assertTrue(dry.details["rehearsal"]["exact"] and dry.details["rehearsal"]["derived_exact"],
                        dry.details["rehearsal"])
        self.assertEqual(set(ids.values()), self.held_ids(), "a dry run removed a row")

        result = self.run_sync(apply=True, roots=[self.pictures])
        self.assertTrue(result.ok, result.message())
        changed = result.details["changed"]
        self.assertEqual((1, 2, 1), (changed["removed"], changed["removed_faces"], changed["removed_named"]))
        self.assertEqual({ids[kept]}, self.held_ids())
        self.assertEqual([], self.query("SELECT id FROM faces WHERE photo_id = ?", (ids[gone],)))
        self.assertTrue(result.details["in_step"])
        self.assertEqual(1, sync.last(self.library)["last_run"]["changed"]["removed"])
        again = self.run_sync(apply=True, roots=[self.pictures])
        self.assertEqual((0, 0, True), (again.details["counts"]["missing"], again.changed, again.details["in_step"]))

    def test_a_whole_sync_that_removes_rows_says_so_once_with_the_named_faces_they_held(self):
        """The watcher's whole sync (its catch-up at start) removing three rows, two of them holding a named face: the
        sync's one closing line says what it removed with the faces and the named ones (DECISIONS 2026-10-28), and the
        watcher writes no second closing line of its own."""
        gone = [self.photo(self.trip, "IMG_%04d.jpg" % n) for n in (1, 2, 3)]
        self.rooted(*gone, self.photo(self.meet, "IMG_0004.jpg"), named=gone[:2])
        for path in gone:
            os.remove(path)
        watcher = watching.Watcher(lambda: [self.library], lambda library: [self.pictures],
                                   lambda library, folder: self.run_sync(apply=True, folder=folder, roots=[self.pictures]),
                                   images.is_photo, debounce=0.5, recheck=0.2, tick=0.05)
        with self.assertLogs("tagpup", level="INFO") as logged:
            watcher._run(self.library, None)
        self.assertEqual(3, sync.last(self.library)["last_run"]["changed"]["removed"])
        said = [line for line in logged.output if "removed" in line]
        self.assertEqual(1, len(said), logged.output)
        self.assertTrue(said[0].startswith("INFO:tagpup.services.sync:Sync of the whole library ended in "), said[0])
        self.assertIn("changed 3 row(s), removed 3 row(s) with 5 face(s), 2 of them named, queued 0", said[0])
        self.assertEqual([], [line for line in logged.output if ":tagpup.jobs.watching:" in line])

    def test_a_folder_deleted_under_a_root_that_is_there_is_removed_with_its_rows(self):
        first, second = self.photo(self.trip, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg")
        kept = self.photo(self.meet, "IMG_0003.jpg")
        ids = self.rooted(first, second, kept)
        os.remove(first)
        os.remove(second)
        os.rmdir(self.trip)
        result = self.run_sync(apply=True, roots=[self.pictures])
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((2, 1, 1, 2), (counts["missing"], counts["missing_folders"], counts["folders_gone"],
                                        counts["to_remove"]))
        self.assertEqual({ids[kept]}, self.held_ids())
        self.assertEqual(2, result.details["changed"]["removed"])

    def test_a_removal_undone_puts_back_the_row_its_named_faces_and_its_tags(self):
        from tagpup.services import journal as library_journal
        gone = self.photo(self.meet, "IMG_0001.jpg", **{"XMP:Subject": ["Events/Invitational"]})
        ids = self.rooted(gone, self.photo(self.meet, "IMG_0002.jpg"), named=[gone])
        before = self.query("SELECT id, path, mtime, size, tags FROM photos WHERE id = ?", (ids[gone],))
        faces = self.query("SELECT id, name, tag_id, name_source FROM faces WHERE photo_id = ? ORDER BY id", (ids[gone],))
        tagged = self.query("SELECT tag_id FROM photo_tags WHERE photo_id = ? ORDER BY tag_id", (ids[gone],))
        people = self.query("SELECT name FROM photo_people WHERE photo_id = ?", (ids[gone],))
        self.assertTrue(tagged)
        self.assertEqual([("Rowan Thackeray",)], people)
        self.assertEqual(1, len([face for face in faces if face[1] == "Rowan Thackeray"]))
        os.remove(gone)
        result = self.run_sync(apply=True, roots=[self.pictures])
        self.assertEqual(1, result.details["changed"]["removed"])
        self.assertEqual([], self.query("SELECT tag_id FROM photo_tags WHERE photo_id = ?", (ids[gone],)))

        undone = library_journal.undo(self.library, result.details["change"], apply=True)
        self.assertTrue(undone.ok, undone.message())
        self.assertEqual(before, self.query("SELECT id, path, mtime, size, tags FROM photos WHERE id = ?", (ids[gone],)))
        self.assertEqual(faces, self.query("SELECT id, name, tag_id, name_source FROM faces WHERE photo_id = ?"
                                           " ORDER BY id", (ids[gone],)))
        self.assertEqual(people, self.query("SELECT name FROM photo_people WHERE photo_id = ?", (ids[gone],)))
        self.assertEqual(tagged, self.query("SELECT tag_id FROM photo_tags WHERE photo_id = ? ORDER BY tag_id",
                                            (ids[gone],)))
        self.assertEqual(2, self.query("SELECT COUNT(*) FROM face_embeddings WHERE face_id IN"
                                       " (SELECT id FROM faces WHERE photo_id = ?)", (ids[gone],))[0][0])

    def test_a_folder_renamed_in_explorer_pairs_as_a_move_and_only_the_unmatched_rest_is_removed(self):
        first, second = self.photo(self.trip, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg")
        ids = self.rooted(first, second, self.photo(self.meet, "IMG_0003.jpg"))
        renamed = os.path.join(self.pictures, "2025-11 Harbour Day")
        os.rename(self.trip, renamed)
        os.utime(os.path.join(renamed, "IMG_0002.jpg"), (THEN + 5, THEN + 5))     # saved again: nothing pairs it
        result = self.run_sync(apply=True, roots=[self.pictures])
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((1, 1, 1), (counts["moved"], counts["missing"], counts["to_remove"]))
        self.assertEqual([ids[second]], result.details["ids"]["to_remove"])
        self.assertEqual(sorted(ids.values()), sorted(self.held_ids() | {ids[second]}))
        self.assertNotIn(ids[second], self.held_ids())
        self.assertEqual([ids[first]], [row[0] for path, row in self.rows().items() if "Harbour Day" in path])

    def test_a_root_unplugged_removes_nothing(self):
        ids = self.rooted(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))
        os.rename(self.pictures, self.pictures + " (unplugged)")
        try:
            result = self.run_sync(apply=True, roots=[self.pictures], queue=lambda folders: self.fail("nothing is new"))
        finally:
            os.rename(self.pictures + " (unplugged)", self.pictures)
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((2, 0), (counts["missing"], counts["to_remove"]))
        self.assertEqual(set(ids.values()), self.held_ids(), "rows on an unplugged drive were removed")

    def test_a_folder_on_a_drive_unplugged_in_a_library_with_no_roots_removes_nothing(self):
        here = self.photo(self.meet, "IMG_0001.jpg")
        self.indexed(here)
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
        self.assertEqual((3, 1, 1, 1, 0), (counts["missing"], counts["folders_gone"], counts["roots_gone"],
                                           counts["folders_walked"], counts["to_remove"]))
        self.assertEqual([season], result.details["reveal"]["roots_gone"])
        self.assertEqual([], self.reads.read)
        self.assertEqual(4, len(self.rows()), "rows on an unplugged drive were removed")

    def test_a_root_repointed_at_an_empty_folder_removes_nothing(self):
        from tagpup import config
        ids = self.rooted(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))
        empty = os.path.join(self.home.root, "New drive", "Pictures")
        os.makedirs(empty)
        # The owner's edit of the map: the new place first, the old kept listed.
        with open(config.machine_roots_path(), "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "roots": {"pictures": [empty, self.pictures]}}, handle)
        result = self.run_sync(apply=True, roots=[empty])
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((2, 0), (counts["missing"], counts["to_remove"]))
        self.assertEqual(set(ids.values()), self.held_ids())

    def test_a_share_that_drops_mid_walk_removes_nothing(self):
        gone = self.photo(self.meet, "IMG_0001.jpg")
        ids = self.rooted(gone, self.photo(self.meet, "IMG_0002.jpg"), self.photo(self.trip, "IMG_0003.jpg"))
        os.remove(gone)
        real = images.stamps_under

        def drops(folder, unlisted, listed=None):
            found = real(folder, unlisted, listed)
            unlisted.append(self.trip)        # the share stopped answering as the walk listed it
            return {key: stamp for key, stamp in found.items() if not key.startswith(paths_key(self.trip) + os.sep)}

        with mock.patch.object(images, "stamps_under", side_effect=drops):
            result = self.run_sync(apply=True, roots=[self.pictures])
        counts = result.details["counts"]
        self.assertEqual((1, 1, 0), (counts["missing"], counts["not_looked_at"], counts["to_remove"]))
        self.assertFalse(result.details["in_step"])
        self.assertEqual(set(ids.values()), self.held_ids())

    def test_a_sync_of_one_folder_never_removes(self):
        gone = self.photo(self.meet, "IMG_0001.jpg")
        ids = self.rooted(gone, self.photo(self.meet, "IMG_0002.jpg"))
        os.remove(gone)
        result = self.run_sync(apply=True, folder=self.meet, roots=[self.pictures])
        self.assertTrue(result.ok, result.message())
        self.assertEqual((1, 0), (result.details["counts"]["missing"], result.details["counts"]["to_remove"]))
        self.assertEqual(set(ids.values()), self.held_ids())

    def renamed_in_place(self, *more):
        """trip's IMG_0001.jpg, with a named face, renamed in Explorer to 'Harbour finish.jpg' (a rename keeps the
        modified time); `more` are names of other new files made beside it at the same time and size. (ids, new path)"""
        renaming = self.photo(self.trip, "IMG_0001.jpg")
        ids = self.rooted(renaming, self.photo(self.trip, "IMG_0002.jpg", body=b"other"),
                          self.photo(self.meet, "IMG_0003.jpg"), named=[renaming])
        renamed = os.path.join(self.trip, "Harbour finish.jpg")
        os.rename(renaming, renamed)
        self.truth["Harbour finish.jpg"] = self.truth["IMG_0001.jpg"]
        for name in more:
            self.photo(self.trip, name)
        return ids[renaming], renamed

    def test_a_file_renamed_in_its_folder_keeps_its_row_and_its_named_face(self):
        photo_id, _renamed = self.renamed_in_place()
        result = self.run_sync(apply=True, roots=[self.pictures], queue=lambda folders: self.fail("it was renamed"))
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((1, 0, 0, 1), (counts["moved"], counts["missing"], counts["to_remove"], counts["moved_named"]))
        self.assertEqual(0, self.reads.sessions, "a rename matched by its stamp reads no file")
        self.assertEqual([photo_id], [row[0] for path, row in self.rows().items() if path.endswith("Harbour finish.jpg")])
        self.assertEqual([("Rowan Thackeray",)], self.query(
            "SELECT name FROM faces WHERE photo_id = ? AND name IS NOT NULL", (photo_id,)))
        self.assertTrue(result.details["in_step"])

    def test_two_files_of_one_stamp_beside_a_renamed_one_are_held_back_and_nothing_is_removed(self):
        photo_id, _renamed = self.renamed_in_place("Harbour finish (2).jpg")
        result = self.run_sync(apply=True, roots=[self.pictures], queue=lambda folders: self.fail("held back"))
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((0, 1, 2, 1, 0), (counts["moved"], counts["ambiguous_rows"], counts["ambiguous_files"],
                                           counts["held_back_folders"], counts["to_remove"]))
        self.assertIn(photo_id, self.held_ids(), "a row held back as ambiguous was removed")
        self.assertFalse(result.details["in_step"])

    def test_a_file_in_the_folder_with_another_time_is_not_taken_for_a_rename(self):
        photo_id, renamed = self.renamed_in_place()
        os.utime(renamed, (THEN + 5, THEN + 5))      # saved again after the rename, or another photo of that size
        asked = []

        def queue(folders):
            asked.extend(folders)
            return Result(attempted=len(folders), changed=len(folders))

        result = self.run_sync(apply=True, roots=[self.pictures], queue=queue)
        self.assertTrue(result.ok, result.message())
        counts = result.details["counts"]
        self.assertEqual((0, 1, 1), (counts["moved"], counts["new"], counts["to_remove"]))
        self.assertNotIn(photo_id, self.held_ids())
        self.assertEqual([self.trip], asked)

    def walks(self, **kwargs):
        """A whole applied sync under the root, counting its walks of the library: (Result, walks)."""
        with mock.patch.object(sync, "_scan", wraps=sync._scan) as scan:
            result = self.run_sync(apply=True, roots=[self.pictures], **kwargs)
        return result, scan.call_count

    def test_a_library_with_nothing_to_remove_is_walked_once(self):
        self.rooted(self.photo(self.meet, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg"))
        result, walks = self.walks()
        self.assertTrue(result.details["in_step"], result.details["counts"])
        self.assertEqual(1, walks, "a library in step was walked a second time")

    def test_a_file_gone_in_both_walks_is_removed(self):
        gone = self.photo(self.meet, "IMG_0001.jpg")
        ids = self.rooted(gone, self.photo(self.meet, "IMG_0002.jpg"))
        os.remove(gone)
        result, walks = self.walks()
        self.assertEqual(2, walks)
        self.assertEqual(1, result.details["changed"]["removed"])
        self.assertNotIn(ids[gone], self.held_ids())

    def test_a_file_moved_between_folders_during_the_walk_keeps_its_row_and_its_named_face(self):
        """Dragged in Explorer from a folder the walk had not listed yet into one it had: in neither listing of the
        first walk, so a whole sync took it for a file deleted. The second walk finds it, paired as a move, and the
        row is kept (missing, not removed); the next sync moves it with its faces."""
        moving = self.photo(self.trip, "IMG_0001.jpg")
        ids = self.rooted(moving, self.photo(self.trip, "IMG_0002.jpg"), self.photo(self.meet, "IMG_0003.jpg"),
                          named=[moving])
        moved = os.path.join(self.meet, "IMG_0001.jpg")
        real, listed = shares.listing, []

        def listing(folder):
            found = real(folder)
            if not listed and paths_key(folder) == paths_key(self.meet):
                listed.append(folder)
                os.rename(moving, moved)     # the meet folder is listed already, the trip folder not yet
            return found

        with mock.patch.object(shares, "listing", side_effect=listing):
            result, walks = self.walks(queue=lambda folders: self.fail("nothing is new: it moved"))
        self.assertTrue(result.ok, result.message())
        self.assertIn(ids[moving], self.held_ids(), "the row of a file moved during the walk was removed")
        self.assertEqual((1, 0), (result.details["counts"]["missing"], result.details["counts"]["to_remove"]))
        self.assertEqual(0, result.details["changed"]["removed"])
        self.assertEqual(2, walks)
        after = self.run_sync(apply=True, roots=[self.pictures], queue=lambda folders: self.fail("it moved"))
        self.assertEqual(1, after.details["counts"]["moved"])
        self.assertEqual([ids[moving]], [row[0] for path, row in self.rows().items()
                                         if 'Invitational' in path and path.endswith('IMG_0001.jpg')])
        self.assertEqual([("Rowan Thackeray",)], self.query(
            "SELECT name FROM faces WHERE photo_id = ? AND name IS NOT NULL", (ids[moving],)))

    def test_a_share_that_drops_in_the_second_walk_removes_nothing(self):
        gone = self.photo(self.meet, "IMG_0001.jpg")
        ids = self.rooted(gone, self.photo(self.meet, "IMG_0002.jpg"), self.photo(self.trip, "IMG_0003.jpg"))
        os.remove(gone)
        real, called = images.stamps_under, []

        def drops_the_second_time(folder, unlisted, listed=None):
            called.append(folder)
            found = real(folder, unlisted, listed)
            if len(called) > 1:
                unlisted.append(self.trip)     # the share stopped answering as the second walk listed it
                return {key: stamp for key, stamp in found.items() if not key.startswith(paths_key(self.trip) + os.sep)}
            return found

        with mock.patch.object(images, "stamps_under", side_effect=drops_the_second_time):
            result, walks = self.walks()
        self.assertTrue(result.ok, result.message())
        self.assertEqual(set(ids.values()), self.held_ids(), "a row was removed though the second walk did not look")
        self.assertEqual((1, 0), (result.details["counts"]["missing"], result.details["counts"]["to_remove"]))
        self.assertEqual(2, walks)

    def taken_meanwhile(self, between):
        """A whole sync whose plan is read, then `between()` runs before it writes: the row it planned to remove is
        taken meanwhile. (`between`'s Result, the photo ids.)"""
        gone, other = self.photo(self.trip, "IMG_0001.jpg"), self.photo(self.trip, "IMG_0002.jpg")
        ids = self.rooted(gone, other, self.photo(self.meet, "IMG_0003.jpg"), named=[gone])
        os.remove(gone)
        real_look, inner = sync.look, []

        def look_then_another(*args, **kwargs):
            plan = real_look(*args, **kwargs)
            if not inner:
                inner.append(None)
                inner[0] = between()
            return plan

        with mock.patch.object(sync, "look", side_effect=look_then_another):
            result = self.run_sync(apply=True, roots=[self.pictures])
        self.assertTrue(inner[0].ok, inner[0].message())
        self.assertFalse(result.refused, result.refused)
        self.assertEqual([journal.row_name("photos", (ids[gone],))], [what for what, _why in result.skipped])
        self.assertEqual((0, 0, 0), (result.details["changed"]["removed"], result.details["changed"]["removed_faces"],
                                     result.details["changed"]["removed_named"]))
        self.assertNotIn(ids[gone], self.held_ids())
        self.assertFalse(result.details["in_step"], "a skipped row is settled by the next sync")
        return inner[0], ids

    def test_the_command_says_what_it_removed_not_what_it_planned(self):
        """The CLI's --apply says the rows the change removed: here none, Remove Folder having taken the row between
        the plan and the write; its dry run says what it would remove."""
        from click.testing import CliRunner
        from tagpup.services import faces as face_service
        from tagpup_cli import cli
        gone = self.photo(self.trip, "IMG_0001.jpg")
        self.rooted(gone, self.photo(self.trip, "IMG_0002.jpg"), self.photo(self.meet, "IMG_0003.jpg"), named=[gone])
        os.remove(gone)

        def said(*arguments):
            with mock.patch("tagpup.files.field_values.Fields.whole", autospec=True,
                            side_effect=Reads(self.truth).whole), \
                    mock.patch("tagpup.files.exiftool_session.ExifToolSession", side_effect=Reads(self.truth).session):
                return CliRunner().invoke(cli, ["--db", self.db_path, "sync"] + list(arguments)).output

        self.assertIn("1 of the missing row(s) would be removed, with 2 face(s), 1 of them named", said())
        real_look, taken = sync.look, []

        def look_then_remove_folder(*args, **kwargs):
            plan = real_look(*args, **kwargs)
            if not taken:
                taken.append(face_service.remove_folder(self.library, self.trip))
            return plan

        with mock.patch.object(sync, "look", side_effect=look_then_remove_folder):
            output = said("--apply")
        self.assertTrue(taken[0].ok, taken[0].message())
        self.assertIn("0 of the missing row(s) were removed, with 0 face(s), 0 of them named", output)
        self.assertNotIn("1 of the missing row(s)", output)

    def test_two_syncs_at_once_remove_a_row_once_and_the_second_skips_it(self):
        # The stand-in ExifTool of the outer run answers the inner one too.
        first, _ids = self.taken_meanwhile(lambda: sync.sync(self.library, apply=True, exiftool_path="exiftool",
                                                             roots=[self.pictures]))
        self.assertEqual((1, 2, 1), (first.details["changed"]["removed"], first.details["changed"]["removed_faces"],
                                     first.details["changed"]["removed_named"]))

    def test_remove_folder_between_a_syncs_plan_and_its_write_is_skipped_not_refused(self):
        from tagpup.services import faces as face_service
        removed, _ids = self.taken_meanwhile(lambda: face_service.remove_folder(self.library, self.trip))
        self.assertEqual(2, removed.changed)


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
        # The folder picker's count too.
        from tagpup.services import photos as photo_actions
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
        listed = []

        def start(args, **_kwargs):
            with open(args[args.index(indexing.FOLDERS_FILE) + 1], encoding="utf-8") as handle:
                listed.append(handle.read().splitlines())
            return proc
        proc.stdout.readline.side_effect = ["done\n", ""]
        with mock.patch("subprocess.Popen", side_effect=start) as popen:
            indexing.index_folder(self.library, self.meet, "code", subfolders=False)
        self.assertEqual("--no-subfolders", popen.call_args[0][0][-1])
        self.assertEqual([[self.meet]], listed)
        # Several folders are one run of the indexer.
        proc.stdout.readline.side_effect = ["done\n", ""]
        with mock.patch("subprocess.Popen", side_effect=start) as popen:
            indexing.index_folder(self.library, [self.meet, self.trip], "code", subfolders=False)
        self.assertEqual(1, popen.call_count)
        self.assertEqual("--no-subfolders", popen.call_args[0][0][-1])
        self.assertEqual([self.meet, self.trip], listed[-1])

    def test_the_walk_stamps_each_photo_as_the_disk_does(self):
        path = self.photo(self.meet, "IMG_0001.jpg", body=b"12345")
        open(os.path.join(self.meet, "notes.txt"), "w").close()
        found = images.stamps_under(self.pictures)
        self.assertEqual(1, len(found))
        (stored, mtime, size), = found.values()
        self.assertEqual((path, THEN, 5), (stored, mtime, size))


if __name__ == "__main__":
    unittest.main()
