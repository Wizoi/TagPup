"""The one-off sweep of captions that repeat a photo's title (scripts/clear_duplicate_captions.py), as the owner runs it
on a library: a dry run first, then --apply over hours on a share, stopped and run again, another program writing a
file meanwhile (F3).

The title and the caption are two values (DECISIONS 2026-11-06): a photo's Description, IPTC caption and EXIF
ImageDescription holding exactly its title (XMP:Title, else IPTC:ObjectName) are copies, cleared; a caption holding
other text, XPComment, a title-only photo and a video are left as they are. Real JPEGs, written by the real ExifTool;
rows as the indexer records a read of them (photo_rows.add_read). The script did not exist before this file: each test
fails on the trunk before it for want of it.
"""
import contextlib
import hashlib
import io
import json
import os
import signal
import socket
import sys
import threading
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import clear_duplicate_captions  # noqa: E402
import corpus  # noqa: E402
import photo_rows  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, field_of, write_outside  # noqa: E402

from tagpup.core.fields import scan_reads  # noqa: E402
from tagpup.files import shares  # noqa: E402
from tagpup.services import batches, file_changes, journal as journal_service  # noqa: E402
from tagpup.store import db  # noqa: E402

TITLE = "Relay at the lake"


def stopped_at(step, nth):
    """A stand-in for file_changes._reached that is a Ctrl+C the `nth` time `step` is reached."""
    seen = []

    def reached(name):
        if name == step:
            seen.append(name)
            if len(seen) == nth:
                raise KeyboardInterrupt
    return reached


def pressed_at(step, nth):
    """A stand-in for file_changes._reached that is the person pressing Ctrl+C the `nth` time `step` is reached."""
    seen = []

    def reached(name):
        if name == step:
            seen.append(name)
            if len(seen) == nth:
                signal.raise_signal(signal.SIGINT)
    return reached


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class CaptionSweep(FilesCase):
    def made(self, name, written):
        """A JPEG another program wrote `written` into (ExifTool, once per run for these fields, copied: tests/corpus.py),
        and its row as the indexer records the read of it."""
        path = corpus.jpeg(os.path.join(self.folder, name), written, EXIFTOOL)
        self.indexed_as(path, written)
        return path

    def indexed_as(self, path, fields):
        conn = db.connect(self.library.path)
        try:
            # The fields the folder scan asks ExifTool for: not EXIF's ImageDescription nor XPComment (fields.scan_reads).
            photo_rows.add_read(conn, path, {field: value for field, value in fields.items() if scan_reads(field)})
            conn.commit()
        finally:
            conn.close()

    def repeated(self, name, title=TITLE):
        return self.made(name, {"XMP:Title": title, "IPTC:ObjectName": title, "XMP:Description": title,
                                "IPTC:Caption-Abstract": title, "EXIF:ImageDescription": title})

    def sweep(self, *apply):
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            code = clear_duplicate_captions.main(["--library", self.library.path, *apply])
        return code, said.getvalue()

    def captions_of(self, path):
        return tuple(field_of(path, field) for field in ("XMP:Description", "IPTC:Caption-Abstract", "EXIF:ImageDescription"))

    def changes(self):
        return self.rows("SELECT id, operation, status FROM changes WHERE operation = ? ORDER BY id",
                         ("clear duplicate captions",))

    def assert_cleared(self, path, title=TITLE):
        self.assertEqual((None, None, None), self.captions_of(path), path)
        self.assertEqual(title, field_of(path, "XMP:Title"), path)
        raw, captions = self.rows("SELECT raw_metadata, captions FROM photos WHERE path = ?", (path,))[0]
        # The row follows its file, in the transaction that marked it done.
        self.assertNotIn("XMP:Description", json.loads(raw))
        self.assertEqual([title], json.loads(captions))


class AShelfOfPhotos(CaptionSweep):
    """Every kind the owner's libraries hold, side by side."""

    def setUp(self):
        super().setUp()
        self.same = self.made("same.jpg", {"XMP:Title": TITLE, "IPTC:ObjectName": TITLE, "XMP:Description": TITLE,
                                           "IPTC:Caption-Abstract": TITLE, "EXIF:ImageDescription": TITLE,
                                           "EXIF:XPComment": TITLE})
        # "Description = Title + more": 8,367 photo_index photos hold a caption other than their title (2026-11-06).
        self.more = self.made("more.jpg", {"XMP:Title": "Relay", "XMP:Description": "Relay at the lake, second leg",
                                           "IPTC:Caption-Abstract": "Relay"})
        self.title_only = self.made("title only.jpg", {"XMP:Title": TITLE, "IPTC:ObjectName": TITLE})
        # A title in IPTC alone (no XMP:Title): its ObjectName is the title.
        self.iptc_titled = self.made("iptc.jpg", {"IPTC:ObjectName": "Finish line", "IPTC:Caption-Abstract": "Finish line"})
        # A Description and no title of its own: shown as its title, not a copy of one (341 in photo_index, 2026-11-06).
        self.untitled = self.made("untitled.jpg", {"XMP:Description": TITLE, "IPTC:Caption-Abstract": TITLE})
        # Its Description is the title, its IPTC caption other words: cleared, the photo shows the IPTC words after
        # (104 in photo_index, 2026-11-06).
        self.hidden = self.made("hidden.jpg", {"XMP:Title": TITLE, "XMP:Description": TITLE,
                                               "IPTC:Caption-Abstract": "Second leg, the handover"})
        # A video whose row holds the same copy: never read, never written (0 such clips in the live libraries).
        self.clip = os.path.join(self.folder, "clip.mp4")
        self.indexed_as(self.clip, {"XMP:Title": TITLE, "XMP:Description": TITLE})

    def test_a_dry_run_counts_from_the_index_and_writes_nothing(self):
        before = {path: digest(path) for path in (self.same, self.more, self.title_only, self.iptc_titled,
                                                  self.untitled, self.hidden)}
        code, said = self.sweep()
        self.assertEqual(0, code, said)
        for counted in ("photos 6", "to read 4", "XMP:Description 2", "IPTC:Caption-Abstract 3", "captions kept 2",
                        "clips left alone 1", "newly shown IPTC captions 1", "nothing was read or written",
                        "1 photo(s) will newly show their IPTC caption", "title changed by another program",
                        "Undo is refused once any swept photo is saved again", "undoing takes hours"):
            self.assertIn(counted, said)
        self.assertEqual(before, {path: digest(path) for path in before})
        self.assertEqual([], self.changes())
        self.assertNotIn(self.folder, said, "a path was printed")
        self.assertNotIn(TITLE, said, "a caption was printed")

    def test_apply_clears_each_copy_of_the_title_and_keeps_the_rest_as_one_undoable_change(self):
        untouched = digest(self.title_only)
        untitled = digest(self.untitled)
        code, said = self.sweep("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Cleared: 4 photo file(s) written", said)
        self.assertIn("Conflicts: 0", said)
        self.assertIn("1 photo(s) will newly show their IPTC caption", said)
        self.assert_cleared(self.same)
        # The IPTC words stay in the file, to be shown now that the Description repeating the title is gone.
        self.assertEqual((None, "Second leg, the handover"), self.captions_of(self.hidden)[:2])
        # XPComment, Windows' Comment, is never touched.
        self.assertEqual(TITLE, field_of(self.same, "EXIF:XPComment"))
        # The caption holding more than the title is kept; its IPTC copy of the title alone is cleared.
        self.assertEqual(("Relay at the lake, second leg", None), self.captions_of(self.more)[:2])
        self.assertEqual((None, None, None), self.captions_of(self.iptc_titled))
        self.assertEqual("Finish line", field_of(self.iptc_titled, "IPTC:ObjectName"))
        self.assertEqual(untouched, digest(self.title_only), "a photo with a title alone was written")
        self.assertEqual(untitled, digest(self.untitled), "a photo with a Description and no title of its own was written")
        [(change, _operation, status)] = self.changes()
        self.assertEqual("applied", status)
        self.assertEqual(4, self.rows("SELECT COUNT(*) FROM change_files WHERE change_id = ? AND state = 'done'",
                                      (change,))[0][0])
        self.assertIn("Recorded as change %d" % change, said)

        # Run again: the index names nothing more.
        code, said = self.sweep()
        self.assertIn("to read 0", said)

        # One undo puts the caption back as the photo shows it: its XMP copy, which the reader takes. The undo is the
        # own writer's patch, which writes a caption into the XMP alone and leaves IPTC's and EXIF's as the sweep left
        # them, cleared (DECISIONS 2026-11-03, A KEYWORD OR CAPTION SAVE WRITES THE XMP COPY ONLY; docs/domains/files.md,
        # "Known, by this design").
        undone = journal_service.undo(self.library, change, apply=True, exiftool_path=EXIFTOOL)
        self.assertTrue(undone.ok, undone.errors)
        self.assertEqual((TITLE, None, None), self.captions_of(self.same))
        self.assertEqual("Relay", field_of(self.more, "IPTC:Caption-Abstract"))

    def test_each_line_reaches_a_redirected_log_as_it_is_said(self):
        """The owner redirects an --apply's output to a log and reads it while it runs: a block-buffered stream lagged by
        thousands of files (#2623). Each line is flushed as it is said."""
        class Log(io.StringIO):
            unflushed = 0

            def write(self, text):
                Log.unflushed += 1
                return super().write(text)

            def flush(self):
                Log.unflushed = 0
                return super().flush()
        log = Log()
        with contextlib.redirect_stdout(log):
            clear_duplicate_captions.main(["--library", self.library.path])
        self.assertIn("nothing was read or written", log.getvalue())
        self.assertEqual(0, Log.unflushed, "the last line was left in the buffer")

    def test_a_file_another_program_changed_after_the_plan_is_a_conflict_left_as_it_is(self):
        def meanwhile(step):
            if step == "plan committed":
                write_outside(self.more, {"IPTC:Caption-Abstract": "Relay, rewritten in another program"})

        with mock.patch.object(file_changes, "_reached", side_effect=meanwhile):
            code, said = self.sweep("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("Cleared: 3 photo file(s) written", said)
        self.assertIn("Conflicts: 1", said)
        self.assertEqual("Relay, rewritten in another program", field_of(self.more, "IPTC:Caption-Abstract"))
        self.assert_cleared(self.same)


class OnAShareThatIsSlowToAnswer(CaptionSweep):
    """The first --apply of 2026-11-06 ran on an idle share: its first look was slow, the share was taken as away for 30 s,
    every photo looked at meanwhile was left out, and the script said "Left alone: 43754 (no caption holds the title any
    more, or unreadable)" -- the owner read "nothing to do" (#2622). The share is stood in for at its seam, as
    test_a_share_asleep does: the photos' folder is on a network drive whose looks (os.stat) hang while it is asleep."""

    def setUp(self):
        super().setUp()
        self.photos = [self.repeated("photo %d.jpg" % n) for n in range(3)]
        self.awake, self.looks = threading.Event(), []
        under = os.path.normcase(self.folder)
        shares.forget()
        self.addCleanup(shares.forget)
        self.addCleanup(self.awake.set)
        real_stat = os.stat

        def stat(path, *args, **kwargs):
            if os.path.normcase(str(path)).startswith(under):
                self.looks.append(path)
                if len(self.looks) <= self.slow_looks and not self.awake.is_set():
                    self.awake.wait(self.asleep_for)
            return real_stat(path, *args, **kwargs)
        for patcher in (mock.patch.object(os, "stat", stat),
                        mock.patch.object(shares, "on_a_network_drive",
                                          lambda path: os.path.normcase(str(path)).startswith(under)),
                        mock.patch.object(shares, "SHARE_WAIT", 0.05), mock.patch.object(shares, "AWAY_SECONDS", 1.0)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_share_slow_to_wake_is_looked_at_again_and_every_photo_is_cleared(self):
        """Its first look takes 0.3 s, past the 0.05 s a look is waited for: the share is away, and answers once that
        look ends. Fails on the code before: every photo left alone, nothing written."""
        self.asleep_for, self.slow_looks = 0.3, 1
        code, said = self.sweep("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Cleared: 3 photo file(s) written", said)
        self.assertIn("0 away", said)
        for path in self.photos:
            self.assert_cleared(path)

    def test_a_share_that_stays_away_is_said_as_away_and_to_run_again_not_nothing_to_clear(self):
        """Asleep throughout (every look hangs past its wait): one more look after the window, then each photo is left
        alone as away, and the script says so and to run it again, exit 1. Fails on the code before: "Left alone: 3 (no
        caption holds the title any more, or unreadable)", exit 0."""
        self.asleep_for, self.slow_looks = 30, 10 ** 6
        code, said = self.sweep("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("Cleared: 0 photo file(s) written", said)
        self.assertIn("Left alone: 0 with no caption holding the title any more, 0 unreadable, 3 away", said)
        self.assertIn("3 photo(s) were not reached: their network share did not answer. Run this again", said)
        self.assertNotIn(self.folder, said, "a path was printed")
        self.awake.set()
        self.assertEqual([], self.changes(), "nothing was written, nothing recorded")

    def test_a_ctrl_c_while_a_batch_waits_for_the_share_stops_it_before_anything_is_planned(self):
        """Three photos past a chunk of two are a batch: the wait for the share asks the batch whether to stop, and the
        owner pressing Ctrl+C during it stops the sweep there, nothing planned or written, said as such."""
        self.asleep_for, self.slow_looks = 30, 10 ** 6
        real, asked = shares.wait_back, []

        def wait_back(share, seconds, stop=None):
            asked.append(stop)
            signal.raise_signal(signal.SIGINT)
            return real(share, seconds, stop)
        with mock.patch.object(batches, "CHUNK", 2), mock.patch.object(shares, "wait_back", side_effect=wait_back):
            code, said = self.sweep("--apply")
        self.awake.set()
        self.assertEqual(130, code, said)
        [stop] = asked
        self.assertIsNotNone(stop, "the wait of a batch did not ask it whether to stop")
        self.assertFalse(stop())
        self.assertIn("stopped before this run planned or wrote anything", said)
        self.assertEqual([], self.changes(), "nothing was planned")
        self.assertEqual(0, batches.running_here(), "the batch's turn was given back")
        for path in self.photos:
            self.assertEqual((TITLE, TITLE, TITLE), self.captions_of(path), "a photo was written")


class StoppedPartWay(CaptionSweep):
    """Hours on a share: the owner presses Ctrl+C, or the process dies, mid-chunk; running it again finishes."""

    def setUp(self):
        super().setUp()
        self.photos = [self.repeated("photo %d.jpg" % n) for n in range(5)]
        # Chunks of two, so that the third file is in the middle of the second chunk.
        chunk = mock.patch.object(batches, "CHUNK", 2)
        chunk.start()
        self.addCleanup(chunk.stop)

    def test_ctrl_c_mid_chunk_keeps_what_was_written_and_a_second_run_finishes(self):
        with mock.patch.object(file_changes, "_reached", side_effect=stopped_at("file written", 3)):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertIn("stopped: 3 file(s) written", said)
        for path in self.photos[:3]:
            self.assert_cleared(path)
        for path in self.photos[3:]:
            self.assertEqual((TITLE, TITLE, TITLE), self.captions_of(path), "a file not reached was written")
        [(_first, _op, status)] = self.changes()
        self.assertEqual("applied", status)

        code, said = self.sweep()
        self.assertIn("to read 2", said)
        code, said = self.sweep("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Cleared: 2 photo file(s) written", said)
        for path in self.photos:
            self.assert_cleared(path)
        self.assertEqual(["applied", "applied"], [status for _id, _op, status in self.changes()])

    def test_a_ctrl_c_before_the_first_file_is_written_says_nothing_was_written_and_leaves_no_change(self):
        # #2974: that change is erased, so the stop's `change` is None as it is for a second press; it is not a second press.
        with mock.patch.object(file_changes, "_reached", side_effect=stopped_at("file writing", 1)):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertIn("stopped: no file written, no change recorded, nothing to undo", said)
        self.assertNotIn("stopped again", said)
        self.assertNotIn("change None", said)
        self.assertEqual([], self.changes())
        for path in self.photos:
            self.assertEqual((TITLE, TITLE, TITLE), self.captions_of(path), "a file was written")

    def test_a_first_ctrl_c_after_the_change_was_applied_does_not_say_nothing_was_recorded(self):
        # #2997: the press comes once the write has returned (ExifTool closing), so the stop carries no `change`: the files
        # are written and the change is applied in History, which the line must not deny.
        real = file_changes._said_removed

        def late(result, since):
            signal.raise_signal(signal.SIGINT)
            return real(result, since)

        with mock.patch.object(file_changes, "_said_removed", side_effect=late):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertEqual(["applied"], [status for _id, _op, status in self.changes()])
        self.assertNotIn("no change recorded", said)
        self.assertNotIn("nothing to undo", said)
        self.assertIn("recorded as change", said)
        for path in self.photos:
            self.assert_cleared(path)

    def test_a_ctrl_c_says_it_is_reading_back_and_a_second_says_how_many_were_written(self):
        # More than a few files written when it stops: a stop is a read back of every one of them.
        more = [self.repeated("more %d.jpg" % n) for n in range(7)]

        with mock.patch.object(file_changes, "_reached", side_effect=pressed_at("file written", 9)):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertLess(said.index("stopping: reading back the files written (8 recorded so far), press Ctrl+C again only if you must"),
                        said.index("stopped: 9 file(s) written"))
        for line in ("2 of 12 files carried out.", "4 of 12 files carried out.", "8 of 12 files carried out."):
            self.assertIn(line, said)
        self.assertNotIn("10 of 12", said)
        self.assertNotIn(self.folder, said, "a path was printed")
        self.assertNotIn(TITLE, said, "a caption was printed")
        for path in (self.photos + more)[:9]:
            self.assert_cleared(path)

    def test_a_second_ctrl_c_during_the_read_back_does_not_say_nothing_was_written(self):
        more = [self.repeated("more %d.jpg" % n) for n in range(7)]
        real, read_at_the_stop = file_changes._read_back, []

        def again(et, library, wrote, read=None):
            if not isinstance(sys.exc_info()[1], KeyboardInterrupt):
                return real(et, library, wrote, read)     # a chunk's own read back, before the first press
            # The read back after the first press: the files written since the last chunk, not all of them.
            read_at_the_stop.append(len(wrote))
            signal.raise_signal(signal.SIGINT)
            return []
        with mock.patch.object(file_changes, "_reached", side_effect=pressed_at("file written", 9)), \
                mock.patch.object(file_changes, "_read_back", side_effect=again):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertNotIn("before anything was written", said)
        self.assertIn("stopped: at least 8 file(s) written", said)
        self.assertIn("the next open of the library finishes settling them", said)
        self.assertEqual([0], read_at_the_stop, "the ninth file is under way, its chunk's eight read back already")
        # The files not reached were taken out before the read back: the next open settles the one under way, not
        # three more written as a kill leaves them.
        [(change, _op, _status)] = self.changes()
        self.assertEqual([], self.rows("SELECT id FROM change_files WHERE change_id = ? AND state = 'planned'", (change,)))
        # The script's process has ended (a process of this machine that is gone owns the change).
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", ("%s:4000000:1" % socket.gethostname(), change))
        self.assertEqual(1, file_changes.settle(self.library, EXIFTOOL))
        self.assertEqual("applied", self.changes()[0][2])
        for path in (self.photos + more)[:9]:
            self.assert_cleared(path)
        for path in (self.photos + more)[9:]:
            self.assertEqual((TITLE, TITLE, TITLE), self.captions_of(path), "a file not reached was written")

    def test_a_ctrl_c_while_exiftool_starts_is_a_message_not_a_traceback(self):
        """Opening the library first finishes a change an earlier run left half done (runtime.library_settings'
        settle_once), and that settle writes files: a Ctrl+C then is before this run wrote anything, not before anything
        was written (#2611 said the latter)."""
        with mock.patch.object(clear_duplicate_captions.runtime, "exiftool", side_effect=KeyboardInterrupt):
            code, said = self.sweep("--apply")
        self.assertEqual(130, code, said)
        self.assertIn("stopped before this run planned or wrote anything", said)
        self.assertIn("a change an earlier run left half done", said)
        self.assertNotIn("before anything was written", said)

    def test_a_process_killed_mid_chunk_is_finished_by_the_next_run(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file writing", 3)):
            with self.assertRaises(Crash):
                self.sweep("--apply")
        code, said = self.sweep("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Conflicts: 0", said)
        for path in self.photos:
            self.assert_cleared(path)
        self.assertEqual(["applied"], [status for _id, _op, status in self.changes()])


if __name__ == "__main__":
    unittest.main()
