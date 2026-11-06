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
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))

import clear_duplicate_captions  # noqa: E402
import photo_rows  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, field_of, write_outside  # noqa: E402

from tagpup.core.fields import scan_reads  # noqa: E402
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


def digest(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class CaptionSweep(FilesCase):
    def made(self, name, written):
        """A JPEG another program wrote `written` into, and its row as the indexer records the read of it."""
        from PIL import Image

        path = os.path.join(self.folder, name)
        Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")
        write_outside(path, written)
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
        # A video whose row holds the same copy: never read, never written (0 such clips in the live libraries).
        self.clip = os.path.join(self.folder, "clip.mp4")
        self.indexed_as(self.clip, {"XMP:Title": TITLE, "XMP:Description": TITLE})

    def test_a_dry_run_counts_from_the_index_and_writes_nothing(self):
        before = {path: digest(path) for path in (self.same, self.more, self.title_only, self.iptc_titled)}
        code, said = self.sweep()
        self.assertEqual(0, code, said)
        for counted in ("photos 4", "to read 3", "XMP:Description 1", "IPTC:Caption-Abstract 3", "captions kept 1",
                        "clips left alone 1", "nothing was read or written"):
            self.assertIn(counted, said)
        self.assertEqual(before, {path: digest(path) for path in before})
        self.assertEqual([], self.changes())
        self.assertNotIn(self.folder, said, "a path was printed")
        self.assertNotIn(TITLE, said, "a caption was printed")

    def test_apply_clears_each_copy_of_the_title_and_keeps_the_rest_as_one_undoable_change(self):
        untouched = digest(self.title_only)
        code, said = self.sweep("--apply")
        self.assertEqual(0, code, said)
        self.assertIn("Cleared: 3 photo file(s) written", said)
        self.assertIn("Conflicts: 0", said)
        self.assert_cleared(self.same)
        # XPComment, Windows' Comment, is never touched.
        self.assertEqual(TITLE, field_of(self.same, "EXIF:XPComment"))
        # The caption holding more than the title is kept; its IPTC copy of the title alone is cleared.
        self.assertEqual(("Relay at the lake, second leg", None), self.captions_of(self.more)[:2])
        self.assertEqual((None, None, None), self.captions_of(self.iptc_titled))
        self.assertEqual("Finish line", field_of(self.iptc_titled, "IPTC:ObjectName"))
        self.assertEqual(untouched, digest(self.title_only), "a photo with a title alone was written")
        [(change, _operation, status)] = self.changes()
        self.assertEqual("applied", status)
        self.assertEqual(3, self.rows("SELECT COUNT(*) FROM change_files WHERE change_id = ? AND state = 'done'",
                                      (change,))[0][0])
        self.assertIn("Recorded as change %d" % change, said)

        # Run again: the index names nothing more.
        code, said = self.sweep()
        self.assertIn("to read 0", said)

        # One undo puts every copy back.
        undone = journal_service.undo(self.library, change, apply=True, exiftool_path=EXIFTOOL)
        self.assertTrue(undone.ok, undone.errors)
        self.assertEqual((TITLE, TITLE, TITLE), self.captions_of(self.same))
        self.assertEqual("Relay", field_of(self.more, "IPTC:Caption-Abstract"))

    def test_a_file_another_program_changed_after_the_plan_is_a_conflict_left_as_it_is(self):
        def meanwhile(step):
            if step == "plan committed":
                write_outside(self.more, {"IPTC:Caption-Abstract": "Relay, rewritten in another program"})

        with mock.patch.object(file_changes, "_reached", side_effect=meanwhile):
            code, said = self.sweep("--apply")
        self.assertEqual(1, code, said)
        self.assertIn("Cleared: 2 photo file(s) written", said)
        self.assertIn("Conflicts: 1", said)
        self.assertEqual("Relay, rewritten in another program", field_of(self.more, "IPTC:Caption-Abstract"))
        self.assert_cleared(self.same)


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
