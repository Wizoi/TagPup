"""ExifTool stalls (a share gone away): an edit of a selection (tests/test_bulk_edits.py is the edit's own), the indexer's read
and sync's pairing read each cost one deadline and are never retried photo by photo, whatever the count; what the indexer did not
hear about is left unread, never stamped (docs/findings.md, #1473). An ExifTool that will not start: a read that decides
from its answer (sync's pairing, Smart Rename's captions) raises, never reads every photo as holding nothing, the index's
read leaves every photo unread, and a write is refused whole before its change is recorded. Fictional names only.
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_bulk_edits as T  # noqa: E402

import page_writes  # noqa: E402
import photo_rows  # noqa: E402
from tagpup.files import exiftool_session, field_values, metadata, names, shares  # noqa: E402
from tagpup.core import paths  # noqa: E402
from tagpup.services import batches, file_changes, journal, relink_photos, tagging  # noqa: E402

#: What a session says of a command a share gone away never answered.
STALLED = "ExifTool did not answer within 300s (a read); its process was killed."


class StalledSession:
    """An ExifTool session whose every read waits out its deadline, as one on a share that went away does."""
    calls = []
    timeout = 300

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_tags(self, batch, tags=None, params=None):
        StalledSession.calls.append(list(batch))
        raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")


class TheIndexAndSyncReadAShareThatStopsAnswering(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp(prefix="stalled_read_")
        self.addCleanup(shutil.rmtree, folder, True)
        self.photos = []
        for name in ("Quay.jpg", "Slipway.jpg", "Beacon.jpg"):
            path = os.path.join(folder, name)
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.photos.append(path)
        StalledSession.calls = []

    def test_the_indexers_read_waits_one_deadline_and_leaves_every_photo_unread(self):
        with mock.patch.object(exiftool_session, "ExifToolSession", StalledSession):
            records = photo_rows.read_whole(None, self.photos)
        self.assertEqual(1, len(StalledSession.calls), "one command for the three photos, not one more for each")
        self.assertEqual(self.photos, [record["path"] for record in records])
        # A record with no stamp is one nobody read: the indexer leaves its row as it was and reads it next time.
        self.assertEqual([(None, None)] * 3, [(record["mtime"], record["size"]) for record in records])
        self.assertTrue(all(record["read_error"] for record in records))

    def test_a_batch_refused_then_a_photo_of_it_not_answered_leaves_them_all_unread(self):
        """#1490: a batch ExifTool refuses is read again a photo at a time; the first of those it does not answer ends the
        read. It was a plain Unreadable, stamped holding nothing, and each next photo waited its own deadline."""
        class RefusesThenStalls(StalledSession):
            def get_tags(self, batch, tags=None, params=None):
                StalledSession.calls.append(list(batch))
                if len(batch) > 1:
                    raise RuntimeError("Error: one of them is no file")
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")
        with mock.patch.object(exiftool_session, "ExifToolSession", RefusesThenStalls):
            records = photo_rows.read_whole(None, self.photos)
        self.assertEqual(2, len(StalledSession.calls), "the batch, then one photo: no deadline more")
        self.assertEqual([(None, None, [], metadata.NOT_ANSWERED)] * 3,
                         [(r["mtime"], r["size"], r["tags"], r["unread"]) for r in records])

    def test_one_reader_left_open_answers_again_after_the_share_comes_back(self):
        """The index reads its batches through one Fields (runtime.fields): the share stops answering at the second photo
        of a batch, so that photo and the one after it are left unread with one deadline waited, the first recorded; the
        next batch, the share back, is read in full by the same reader, its ExifTool started again (ExifToolHelper's
        auto-start after the kill), never left dead for the rest of the run. The restart itself, a real kill and a real
        start, is judged by test_exiftool_session ("a kill, a restart"); this test judges that one reader keeps its
        session across calls and that the stalled call does not end the next."""
        starts = []

        class ShareAwayOnce(StalledSession):
            away = [self.photos[1]]

            def __enter__(self):
                starts.append(1)
                return self

            def get_tags(self, batch, tags=None, params=None):
                StalledSession.calls.append(list(batch))
                if len(batch) > 1 and ShareAwayOnce.away:
                    raise RuntimeError("Error: one of them is no file")
                if ShareAwayOnce.away and batch == ShareAwayOnce.away:
                    ShareAwayOnce.away = []
                    raise exiftool_session.ExifToolTimeout(STALLED)
                return [{"SourceFile": path, "File:MIMEType": "image/jpeg", "XMP:Subject": ["Harbour"]}
                        for path in batch]
        with mock.patch.object(exiftool_session, "ExifToolSession", ShareAwayOnce), \
                field_values.opened("exiftool") as reader:
            first, second = reader.whole(self.photos), reader.whole(self.photos)
        self.assertEqual([None, metadata.NOT_ANSWERED, metadata.NOT_ANSWERED], [r.get("unread") for r in first])
        self.assertEqual(["Harbour"], first[0]["tags"])
        self.assertEqual([[self.photos[0]], [self.photos[1]]], StalledSession.calls[1:3], "the photo after it not asked")
        self.assertEqual([["Harbour"]] * 3, [r["tags"] for r in second], "the next call answers")
        self.assertTrue(all(r["mtime"] is not None for r in second))
        self.assertEqual(1, len(starts), "one session for the reader's life")

    def test_one_reader_left_open_reads_fields_again_after_the_share_comes_back(self):
        """The same at the reads that answer by field (Fields.read): a stall answers that photo and every later one of
        the call NotAnswered, and the next call of the same reader answers them all."""
        starts, away = [], [self.photos[1]]

        class ShareAwayOnce(StalledSession):
            def __enter__(self):
                starts.append(1)
                return self

            def get_tags(self, batch, tags=None, params=None):
                StalledSession.calls.append(list(batch))
                if len(batch) > 1 and away:
                    raise RuntimeError("Error: one of them is no file")
                if away and batch == away:
                    away.clear()
                    raise exiftool_session.ExifToolTimeout(STALLED)
                return [{"SourceFile": path, "File:MIMEType": "image/jpeg", "XMP:Subject": ["Harbour"]} for path in batch]
        with mock.patch.object(exiftool_session, "ExifToolSession", ShareAwayOnce), \
                field_values.opened("exiftool") as reader:
            first = reader.read(self.photos, ["XMP:Subject"])
            second = reader.read(self.photos, ["XMP:Subject"])
        answers = [first[paths.key(path)] for path in self.photos]
        self.assertEqual({"XMP:Subject": ["Harbour"]}, answers[0])
        self.assertEqual([field_values.NotAnswered] * 2, [type(answer) for answer in answers[1:]])
        self.assertEqual([{"XMP:Subject": ["Harbour"]}] * 3, [second[paths.key(path)] for path in self.photos])
        self.assertEqual(1, len(starts), "one session for the reader's life")

    def test_syncs_pairing_read_waits_one_deadline_and_pairs_nothing(self):
        with mock.patch.object(exiftool_session, "ExifToolSession", StalledSession), field_values.opened(None) as fields:
            by_original, by_identity = relink_photos.claims_of(self.photos, fields)
        self.assertEqual(1, len(StalledSession.calls))
        self.assertEqual(({}, {}), (by_original, by_identity))


class WillNotStart:
    """An ExifTool that cannot be started: its program missing, or refused by the machine."""
    starts = 0

    def __init__(self, *args, **kwargs):
        WillNotStart.starts += 1
        raise FileNotFoundError("[WinError 2] The system cannot find the file specified")


class AnExifToolThatWillNotStart(T.Bulk):
    PHOTOS = 2

    def setUp(self):
        super().setUp()
        self.photos = [self.path(photo_id) for photo_id in self.ids]
        WillNotStart.starts = 0
        patcher = mock.patch.object(exiftool_session, "ExifToolSession", WillNotStart)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_syncs_pairing_read_raises_rather_than_pairing_nothing(self):
        # Pairing nothing would leave a whole sync to remove the rows a DocumentID could have relinked.
        with self.assertRaises(FileNotFoundError), field_values.opened(None) as fields:
            relink_photos.claims_of(self.photos, fields)

    def test_smart_renames_read_raises_rather_than_reading_no_caption(self):
        with self.assertRaises(FileNotFoundError):
            names.read_for_renaming(field_values.opened("exiftool"), self.photos)

    def test_one_reader_raises_on_a_read_leaves_the_index_read_unread_and_tries_the_start_again(self):
        with field_values.opened("exiftool") as reader:
            self.assertEqual({}, reader.read([], ["XMP:Subject"]), "nothing asked: nothing started")
            self.assertEqual(0, WillNotStart.starts)
            with self.assertRaises(FileNotFoundError):
                reader.read(self.photos, ["XMP:Subject"])
            records = reader.whole(self.photos)
        self.assertEqual([(None, metadata.NOT_STARTED)] * 2, [(r["mtime"], r["unread"]) for r in records])
        self.assertEqual(2, WillNotStart.starts, "each call tries the start again")

    def test_the_bake_after_an_index_fails_once_rather_than_calling_every_photo_unreadable(self):
        from tagpup.services import bake_after_index
        folder = os.path.dirname(self.photos[0])
        with self.assertRaises(field_values.Refused):
            bake_after_index.bake_folders(self.library, field_values.opened("exiftool"), "detector", [folder], {})
        self.assertEqual(1, WillNotStart.starts, "one start attempt, not one per photo")

    def test_the_change_is_refused_before_its_record(self):
        # A write's Fields starts ExifTool at its first command, and a save hands in its own read: that command could come
        # after the change's record and a patch's bytes, a file left `writing` that nothing reads back. ready_to_write
        # refuses the whole change first, one sentence (the trunk before L0b: the start's exception, a 500).
        before, journaled = [self.tags(photo_id) for photo_id in self.ids], self.changes("%")
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((0, self.PHOTOS), (done["changed"], done["error_count"]))
        self.assertEqual({field_values.NOT_STARTED_TO_WRITE}, {error["why"] for error in done["errors"]})
        self.assertEqual(journaled, self.changes("%"), "nothing journaled")
        self.assertEqual([], self.vl.rows("SELECT id FROM change_files"))
        self.assertEqual(before, [self.tags(photo_id) for photo_id in self.ids])


class AStalledExifTool(T.Bulk):
    def stall(self, read=False, write_at=None):
        stalled = exiftool_session.ExifToolTimeout("ExifTool did not answer within 300s (a read); its process was killed.")
        if read:
            def hook(_path, _n):
                raise stalled
            self.files.on_read = hook
        if write_at is not None:
            def writing(_path, n):
                if n == write_at:
                    raise stalled
            self.files.on_write = writing

    def order(self):
        return self.client.get("/library/api/library/ids", query_string={"kind": "all"}).get_json()["ids"]

    def test_read_does_not_retry_photo_by_photo_after_a_timeout(self):
        calls = []

        class Stalled:
            timeout = 60

            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                raise exiftool_session.ExifToolTimeout("ExifTool did not answer")
        found = field_values._read(Stalled(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(1, len(calls))
        self.assertEqual(3, len(found))
        self.assertTrue(all(isinstance(each, field_values.Unreadable) and "did not answer in 60 s" in str(each) for each in found.values()))

    def test_read_still_retries_singly_a_batch_one_bad_path_made_exiftool_refuse(self):
        calls = []

        class Refuses:
            def get_tags(self, batch, tags=None):
                calls.append(list(batch))
                if len(batch) > 1:
                    raise RuntimeError("one of them is no file")
                return [{"SourceFile": batch[0], "File:MIMEType": "image/jpeg"}]
        found = field_values._read(Refuses(), ["a.jpg", "b.jpg", "c.jpg"], ["XMP:Subject"])
        self.assertEqual(4, len(calls))
        self.assertFalse(any(isinstance(each, Exception) for each in found.values()))

    def test_a_read_that_times_out_costs_the_edit_one_timeout_and_writes_nothing(self):
        self.stall(read=True)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS, 0), (done["error_count"], done["changed"]))
        self.assertEqual(1, self.files.read_calls, "one command for the 60 photos, not one for each")
        self.assertEqual(0, self.files.writes)
        self.assertIn("did not answer", done["errors"][0]["why"])
        self.assertTrue(file_changes._one_at_a_time.acquire(blocking=False), "the lock is released")
        file_changes._one_at_a_time.release()

    def test_a_batch_cancelled_while_it_reads_its_files_stops_reading_and_writes_nothing(self):
        """#1200: the read a batch plans from (about 53 ms a photo, 4.5 minutes for 5,000) could not be stopped: Cancel or
        a hand-over took effect only when it ended. It stops at the next batch of reads."""
        def cancel_at_the_first_read(_path, _n):
            batches.current().stop.set()
        self.files.on_read = cancel_at_the_first_read
        with mock.patch.object(field_values, "READ_BATCH", 10):
            done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((0, 0), (done["changed"], self.files.writes))
        self.assertEqual(1, self.files.read_calls, "one batch of reads of the six, not all of them")
        self.assertIn("Stopped as asked before anything was written", done["errors"][0]["why"])



class AShareThatStopsAnsweringMidWrite(T.Bulk):
    """A keyword edit of a 60-photo folder on a share that stops answering part-way (#1921): a STOP, as a Ctrl+C is. The files
    written before it are done and one change, applied and undoable; the file under way and the rest are taken out, never a
    conflict (they hold what they held: ExifTool writes beside a file and renames); the edit says the share stopped
    answering; the next run writes the rest. Break: _read_now not raising _Stalled (the trunk before #1921: the file under
    way and every later one "conflict ... could not be read", undo refusing them)."""

    def stall_at(self, k, writing=False):
        """The share stops answering at the k-th file the write carries (from 1): as it reads the file just before writing
        it, or, `writing`, in the middle of its write."""
        stalled = exiftool_session.ExifToolTimeout(STALLED)
        if writing:
            def write(_path, n):
                if n == k:
                    raise stalled
            self.files.on_write = write
            return
        carried = []

        def read(path, n):
            if n == 2:   # the write's own read of the file; the first was the plan's
                carried.append(path)
                if len(carried) == k:
                    raise stalled
        self.files.on_read = read

    def change_files(self):
        return self.vl.rows("SELECT state, COUNT(*) FROM change_files GROUP BY state ORDER BY state")

    def coast(self):
        return sum(1 for photo_id in self.ids if "Trips/Coast" in self.tags(photo_id))

    def test_a_stall_in_the_second_chunk_keeps_what_was_written_as_one_change_and_the_next_run_writes_the_rest(self):
        k = batches.CHUNK + 6
        self.stall_at(k)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((k - 1, self.PHOTOS - k + 1), (done["changed"], done["error_count"]))
        self.assertIn("ExifTool stopped answering after %d of %d photos" % (k - 1, self.PHOTOS), done["errors"][0]["why"])
        self.assertEqual([("done", k - 1)], self.change_files(), "a conflict, or a file not reached left in the change")
        ((change_id, _op, status),) = self.changes("bulk tags%")
        self.assertEqual("applied", status)
        self.assertEqual((k - 1, k - 1), (self.files.writes, self.coast()))
        self.files.on_read = None
        again = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((self.PHOTOS - k + 1, k - 1, 0), (again["changed"], again["unchanged"], again["error_count"]))
        self.assertEqual(self.PHOTOS, self.coast())
        # The stopped change is undone as any other: the files it wrote, and only those, lose the keyword.
        undone = journal.undo(self.library, change_id, apply=True, fields=field_values.opened("exiftool"))
        self.assertEqual((None, k - 1), (undone.refused, undone.changed))
        self.assertEqual(self.PHOTOS - k + 1, self.coast())

    def test_a_stall_in_a_files_write_leaves_that_one_file_for_settle_and_no_conflict(self):
        k = batches.CHUNK + 6
        self.stall_at(k, writing=True)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual(k - 1, done["changed"])
        self.assertIn("1 more is settled the next time it can be read", done["errors"][0]["why"])
        self.assertEqual([("done", k - 1), ("writing", 1)], self.change_files())
        self.assertEqual(k, self.files.writes, "after the stall no write was begun: the session stays dead")
        self.files.on_write = None
        again = self.tags_edit(add=["Trips/Coast"])   # settles the one under way first, by what it holds
        self.assertEqual(["applied", "applied"], [status for _id, _op, status in self.changes("bulk tags%")])
        self.assertEqual((0, self.PHOTOS), (again["error_count"], self.coast()))
        self.assertEqual([("done", self.PHOTOS)], self.change_files())

    def test_the_file_left_under_way_waits_for_its_share_while_other_photos_are_saved(self):
        """The owner saves another photo's tags while the share is still away: the save settles the stopped change first,
        and the file it left under way is left so, not read (a deadline inside an unrelated save) and not a conflict; once
        the share answers, the next write settles it by what it holds and the change ends applied and undoable. Break:
        _finish reading it in a plain session (#1921's false conflict, one change late)."""
        k = batches.CHUNK + 6
        under_way = []

        def write(path, n):
            if n == k:
                under_way.append(paths.key(path))
                raise exiftool_session.ExifToolTimeout(STALLED)
        self.files.on_write = write
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertIn("1 more is settled the next time it can be read", done["errors"][0]["why"])
        ((change_id, _op, _status),) = self.changes("bulk tags%")
        # Two photos the stop did not reach: a save of them is unrelated to the stopped change, and leaves it undoable.
        reached = {photo_id for (photo_id,) in self.vl.rows("SELECT photo_id FROM change_files WHERE change_id = ?", change_id)}
        others = [self.path(photo_id) for photo_id in self.ids if photo_id not in reached][:2]
        bases = [page_writes.base(self.library, path, "exiftool") for path in others]   # the page read them before

        def read(path, _n):
            if paths.key(path) in under_way:
                raise exiftool_session.ExifToolTimeout(STALLED)
        real = shares.stamp_of
        self.files.on_write, self.files.on_read = None, read
        reads = self.files.reads.get(under_way[0], 0)
        with mock.patch.object(shares, "stamp_of", lambda path: shares.UNANSWERED if paths.key(path) in under_way else real(path)):
            saved = tagging.save_photo(self.library, others[0], "", ["Trips/Lakes"], None, field_values.opened("exiftool"), "",
                                       base=bases[0])
        self.assertTrue(saved.ok, saved.errors)
        self.assertEqual(reads, self.files.reads.get(under_way[0], 0), "the file on the share away was read")
        self.assertEqual([("done", k - 1), ("writing", 1)],
                         self.vl.rows("SELECT state, COUNT(*) FROM change_files WHERE change_id = ? GROUP BY state ORDER BY state",
                                      change_id))
        self.assertNotEqual("applied", self.changes("bulk tags%")[0][2])
        # The share answers again: the next write settles the file under way by what it holds (what it held: written on).
        self.files.on_read = None
        again = tagging.save_photo(self.library, others[1], "", ["Trips/Lakes"], None, field_values.opened("exiftool"), "", base=bases[1])
        self.assertTrue(again.ok, again.errors)
        self.assertEqual([(change_id, "applied")], [(each[0], each[2]) for each in self.changes("bulk tags%")])
        self.assertEqual([("done", k)],
                         self.vl.rows("SELECT state, COUNT(*) FROM change_files WHERE change_id = ? GROUP BY state ORDER BY state",
                                      change_id))
        undone = journal.undo(self.library, change_id, apply=True, fields=field_values.opened("exiftool"))
        self.assertEqual((None, k), (undone.refused, undone.changed))

    def test_a_save_whose_write_the_share_stops_says_so_after_one_deadline(self):
        """A save reports a write's errors, not its refusal: the photo under way is its error. The save passes its own
        session, kept dead for the write all the same: the read that settles the file after its write timed out answers at
        once, and the owner waits one deadline (five minutes on a share), not two. Break: the caller's session not
        wrapped in StaysDead (the trunk before #1927: the read after the write waited a second deadline)."""
        waited = []

        def write(_path, _n):
            waited.append("write")
            raise exiftool_session.ExifToolTimeout(STALLED)

        def read(_path, _n):
            if waited:
                waited.append("read")
                raise exiftool_session.ExifToolTimeout(STALLED)
        path = self.path(self.ids[0])
        base = page_writes.base(self.library, path, "exiftool")
        self.files.on_write, self.files.on_read = write, read
        saved = tagging.save_photo(self.library, path, "", ["Trips/Coast"], None, field_values.opened("exiftool"), "", base=base)
        self.assertFalse(saved.ok)
        self.assertIn("ExifTool stopped answering as it was written", str(saved.errors[0][1]))
        self.assertEqual(["write"], waited, "the commands that waited out a deadline")
        self.assertEqual([("writing", 1)], self.change_files())

    def test_the_photo_a_stall_came_before_the_write_of_is_said_not_begun(self):
        self.stall_at(2)
        two = [self.path(photo_id) for photo_id in self.ids[:2]]
        result = tagging.change_tags(self.library, two, ["Trips/Coast"], [], field_values.opened("exiftool"), stop_at_first_error=False)
        self.assertEqual(1, result.changed)
        self.assertEqual(["ExifTool stopped answering before it was written"],
                         [str(why).split(": ", 1)[1].split(",")[0] for _path, why in result.errors])

    def test_a_stall_at_the_first_file_writes_nothing_and_leaves_no_change_to_undo(self):
        self.stall_at(1)
        done = self.tags_edit(add=["Trips/Coast"])
        self.assertEqual((0, self.PHOTOS, 0), (done["changed"], done["error_count"], self.files.writes))
        self.assertIn("ExifTool stopped answering before anything was written", done["errors"][0]["why"])
        self.assertEqual([], self.change_files())
        self.assertEqual([], self.changes("bulk tags%"))
        self.assertEqual(0, self.coast())


def aged(path, minutes):
    """`path` last written `minutes` ago."""
    when = time.time() - minutes * 60
    os.utime(path, (when, when))
    return path


class AScratchFileAnInterruptedWriteLeft(T.Bulk):
    """#2180: a share that dropped mid-write left ExifTool's scratch file beside the photo (part written), and every later
    write of the photo failed "Temporary file already exists" until it was deleted by hand. The next write of the photo
    removes it, by the owner's four rules (DECISIONS 2026-10-29), and tries once more; any other scratch file stays. Break:
    _carry not calling _removed_scratch (the trunk before #2180: the first, second and fourth tests fail); the scratch file
    of a write that may be under way told as one to delete (the trunk before the Files review: the fifth fails)."""

    def setUp(self):
        super().setUp()
        self.photo, self.other = aged(self.path(self.ids[0]), 60), aged(self.path(self.ids[1]), 60)
        self.scratch = exiftool_session.scratch_of(self.photo)

    def interrupt(self, times=1):
        """The share drops in each of the next `times` writes of the photo: ExifTool leaves its scratch file, part written."""
        dropped = []

        def write(path, _n):
            if paths.key(path) == paths.key(self.photo) and len(dropped) < times:
                dropped.append(path)
                with open(exiftool_session.scratch_of(path), "wb") as handle:
                    handle.write(b"part of a photo")
                raise exiftool_session.ExifToolTimeout(STALLED)
        self.files.on_write = write

    def tag(self, *photos):
        return tagging.change_tags(self.library, list(photos), ["Trips/Coast"], [], field_values.opened("exiftool"), stop_at_first_error=False)

    def test_the_share_back_the_next_write_removes_the_scratch_file_and_writes_the_photo(self):
        self.interrupt()
        self.assertFalse(self.tag(self.photo).ok)
        self.assertTrue(os.path.exists(self.scratch))
        aged(self.scratch, 10)      # the share is back, minutes later
        with self.assertLogs(file_changes.logger, "WARNING") as logged:
            again = self.tag(self.photo)
        self.assertTrue(again.ok, again.errors)
        self.assertEqual(1, again.details.get("scratch_removed"))
        self.assertFalse(os.path.exists(self.scratch))
        self.assertIn("Trips/Coast", self.tags(self.ids[0]))
        self.assertTrue(any(self.scratch in line and "15 bytes" in line for line in logged.output), logged.output)
        self.assertEqual([("done", 1)], self.vl.rows("SELECT state, COUNT(*) FROM change_files GROUP BY state"))

    def test_only_the_scratch_file_of_the_photo_written_is_removed(self):
        open(self.scratch, "wb").close()
        mine = aged(self.scratch, 10)
        theirs = exiftool_session.scratch_of(self.other)
        with open(theirs, "wb") as handle:
            handle.write(b"another photo's")
        aged(theirs, 10)
        result = self.tag(self.photo)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual((False, True), (os.path.exists(mine), os.path.exists(theirs)))

    def leaves_it(self, result):
        self.assertFalse(result.ok)
        self.assertIn("already exists", str(result.errors[-1][1]))
        self.assertTrue(os.path.exists(self.scratch), "the scratch file was removed")
        self.assertNotIn("scratch_removed", result.details)

    def test_a_scratch_file_is_left_when_the_photo_changed_since_or_a_write_may_be_under_way_or_the_share_is_away(self):
        self.interrupt()
        self.tag(self.photo)
        aged(self.scratch, 10)
        aged(self.photo, 5)     # another program wrote the photo since
        self.leaves_it(self.tag(self.photo))
        aged(self.photo, 60)
        aged(self.scratch, 0)   # two writers: another process's write of it is under way
        self.leaves_it(self.tag(self.photo))
        aged(self.scratch, 10)
        real = shares.stamp_of
        with mock.patch.object(shares, "stamp_of",
                               lambda path: shares.UNANSWERED if paths.key(path) == paths.key(self.scratch) else real(path)):
            self.leaves_it(self.tag(self.photo))

    def test_a_write_interrupted_twice_is_written_the_third_time(self):
        self.interrupt(times=2)
        self.tag(self.photo)
        aged(self.scratch, 10)
        second = self.tag(self.photo)        # removes the first scratch file, and the share drops again
        self.assertFalse(second.ok)
        self.assertEqual(1, second.details.get("scratch_removed"))
        aged(self.scratch, 10)
        third = self.tag(self.photo)
        self.assertTrue(third.ok, third.errors)
        self.assertEqual(1, third.details.get("scratch_removed"))
        self.assertFalse(os.path.exists(self.scratch))
        self.assertIn("Trips/Coast", self.tags(self.ids[0]))

    def test_a_scratch_file_written_this_moment_is_left_and_the_owner_told_to_try_again_later(self):
        """Rule 3 (not the removal failing): the scratch file was written this moment, no handle on it, so it may be
        another write's under way (the CLI's bake, a second library holding the folder). The write fails telling the
        owner to try again in five minutes, not to delete the file, and the file is as it was."""
        with open(self.scratch, "wb") as writer:
            writer.write(b"another write's, under way")
        held = os.stat(self.scratch)
        result = self.tag(self.photo)
        after = os.stat(self.scratch)
        self.leaves_it(result)
        why = str(result.errors[-1][1])
        self.assertIn("try again after 5 minutes", why)
        self.assertNotIn("delete that file", why)
        self.assertEqual((held.st_mtime, held.st_size), (after.st_mtime, after.st_size))
        with open(self.scratch, "rb") as handle:
            self.assertEqual(b"another write's, under way", handle.read())

    @unittest.skipUnless(os.name == "nt", "a file open for writing is refused its removal only on Windows")
    def test_a_stale_scratch_file_the_share_will_not_let_go_of_is_left_and_the_owner_told_to_delete_it(self):
        """The removal itself failing: the scratch file is old enough to be removed (rule 3 passes) but a program holds
        it open, so the share refuses the removal. The write fails, the file stays with its bytes, and the owner is told
        what to do with a scratch file left (delete it), not to wait: nothing is writing it."""
        with open(self.scratch, "wb") as holder:
            holder.write(b"held by a program")
            holder.flush()
            aged(self.scratch, 10)
            result = self.tag(self.photo)
        self.assertFalse(result.ok)
        self.assertTrue(os.path.exists(self.scratch), "the scratch file was removed")
        self.assertNotIn("scratch_removed", result.details)
        why = str(result.errors[-1][1])
        self.assertIn("already exists", why)
        self.assertIn("delete that file", why)
        self.assertNotIn("try again after", why)
        with open(self.scratch, "rb") as handle:
            self.assertEqual(b"held by a program", handle.read())
        self.assertNotIn("Trips/Coast", self.tags(self.ids[0]))


if __name__ == "__main__":
    unittest.main()
