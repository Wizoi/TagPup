"""Bulk edits of photo files are journaled file by file, settled after a crash, and
undone where each file still holds what the edit left (docs/ARCHITECTURE.md, phase 7.5,
"Photo files"; tagpup.services.file_changes).

Real JPEGs, written by the real ExifTool once per run and copied into a home of the test's own (tests/corpus.py);
the test is skipped only where ExifTool is not installed. Rows are seeded as the indexer
stores them (tests/photo_rows.add_read, #309), never through the recorder under test. A crash is a
BaseException raised at a step of the write (file_changes.STEPS), as tests/test_journal.py
does for the journal of rows; `settle` then finishes what it left. A patch in place is the real one
(tagpup.files.in_place.patch_jpeg), cut where it writes the photo by `cut_patch`.
"""
import contextlib
import json
import os
import re
import socket
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import corpus  # noqa: E402
import own_home  # noqa: E402
import photo_rows  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files import field_values, in_place  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, journal as journal_service, photos as photo_actions, tagging  # noqa: E402
from tagpup.store import db, file_journal  # noqa: E402

EXIFTOOL = own_home.installed_exiftool()
requires_exiftool = unittest.skipIf(EXIFTOOL is None, "ExifTool not installed")

TAKEN = "2024:07:04 10:00:00"
SHIFTED = "2024:07:04 10:30:00"


class Crash(BaseException):
    """The process stopping at a step: not an Exception, so nothing catches it."""


def crash_at(step, nth=1):
    """A stand-in for file_changes._reached that stops the `nth` time `step` is reached."""
    seen = []

    def reached(name):
        if name == step:
            seen.append(name)
            if len(seen) == nth:
                raise Crash(step)
    return reached


def keywords_of(path):
    with ExifToolSession(executable=EXIFTOOL) as et:
        found = et.get_tags([path], tags=["XMP:Subject", "IPTC:Keywords"])[0]
    subject = found.get("XMP:Subject", [])
    return sorted(subject if isinstance(subject, list) else [subject])


def field_of(path, field):
    with ExifToolSession(executable=EXIFTOOL) as et:
        return et.get_tags([path], tags=[field])[0].get(field.replace("XMP-xmpMM:", "XMP:"))


def write_outside(path, values):
    """Another program writing the file."""
    with ExifToolSession(executable=EXIFTOOL) as et:
        et.set_tags([path], tags=values, params=["-overwrite_original"])


class Torn(OSError):
    """The test stopping a patch's write (in_place._write), which runs on shares.bounded's thread: an OSError, which
    bounded hands back to the patch's thread, where cut_patch raises a Crash. A Crash raised on bounded's thread would
    end that thread alone, and escape the patch with its photo still open."""


@contextlib.contextmanager
def cut_patch(ranges=None):
    """The real patch in place (in_place.patch_jpeg, field_values.write's in-place path) cut at its write of the photo:
    `ranges` of its ranges written and flushed, then the process killed (a Crash); None, every range written and the
    process killed after the last byte. Only the first write is cut; a put back (in_place.put_back) is the real one.
    Yields the list of the ranges the cut write was given."""
    real_write, real_bounded, cut = in_place._write, in_place._bounded, []

    def write(handle, changes):
        if cut:
            return real_write(handle, changes)
        cut.extend(changes)
        real_write(handle, changes if ranges is None else changes[:ranges])
        raise Torn("killed after %s of the ranges" % ("all" if ranges is None else ranges))

    def bounded(path, call):
        try:
            return real_bounded(path, call)
        except Torn as e:
            raise Crash(str(e)) from None

    with mock.patch.object(in_place, "_write", write), mock.patch.object(in_place, "_bounded", bounded):
        yield cut


def killed_before_the_save():
    """The process killed as a patch's record is saved (file_journal.save_patch), before it is committed."""
    return mock.patch.object(file_journal, "save_patch", side_effect=Crash("before the save"))


@requires_exiftool
class FilesCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="file_journal_")
        self.library = Library(corpus.library(self.home.library("files.db")))
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)

    def make(self, name, tags=("Beach",), taken=None, caption=None, row=True, preserved=None):
        """A JPEG holding `tags`, and its row as the indexer leaves it; with `preserved`,
        the name Smart Rename keeps of it, so that renaming it writes nothing first. Written by ExifTool as another
        program writes a file (write_outside), once per run for these values, and copied (tests/corpus.py)."""
        # Every field a keyword write sets that a file without people holds (fields.keyword_fields).
        values = {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags), "EXIF:XPKeywords": ";".join(tags)}
        if taken:
            values["EXIF:DateTimeOriginal"] = taken
        if caption:
            values["XMP:Description"] = caption
        if preserved:
            values["XMP-xmpMM:PreservedFileName"] = preserved
        path = corpus.jpeg(os.path.join(self.folder, name), values, EXIFTOOL)
        if row:
            conn = db.connect(self.library.path)
            try:
                photo_rows.add_read(conn, path, values)
                conn.commit()
            finally:
                conn.close()
        return path

    def execute(self, sql, params=()):
        conn = db.connect(self.library.path)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def rows(self, sql, params=()):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def indexed(self, path):
        return sorted(json.loads(self.rows("SELECT tags FROM photos WHERE path = ?", (path,))[0][0]))

    def photo_id(self, path):
        return self.rows("SELECT id FROM photos WHERE path = ?", (path,))[0][0]

    def status(self, change_id):
        return self.rows("SELECT status FROM changes WHERE id = ?", (change_id,))[0][0]

    def states(self, change_id):
        return [state for (state,) in self.rows("SELECT state FROM change_files WHERE change_id = ? ORDER BY id",
                                                (change_id,))]

    def last_change(self):
        return self.rows("SELECT MAX(id) FROM changes")[0][0]

    def add(self, photo_paths, tags=("Harbour",)):
        return tagging.change_tags(self.library, photo_paths, list(tags), [], EXIFTOOL)

    def undo(self, change_id, apply=True):
        return journal_service.undo(self.library, change_id, apply=apply, exiftool_path=EXIFTOOL)

    def settle(self):
        return file_changes.settle(self.library, EXIFTOOL)


class Forward(FilesCase):
    def test_each_file_is_planned_written_and_recorded_with_its_row(self):
        a, b = self.make("a.jpg"), self.make("b.jpg", ["Beach", "Relay"])
        result = self.add([a, b])
        self.assertEqual((result.attempted, result.changed, result.errors), (2, 2, []))
        change = result.details["change"]
        self.assertEqual(keywords_of(a), ["Beach", "Harbour"])
        self.assertEqual(keywords_of(b), ["Beach", "Harbour", "Relay"])
        self.assertEqual(self.indexed(b), ["Beach", "Harbour", "Relay"])
        # The row takes the file's new stamp, in the transaction that marks it done.
        stat = os.stat(a)
        self.assertEqual(self.rows("SELECT mtime, size FROM photos WHERE path = ?", (a,)),
                         [(stat.st_mtime, stat.st_size)])
        self.assertEqual(("applied", ["done", "done"]), (self.status(change), self.states(change)))
        before, after = self.rows("SELECT fields_before, fields_after FROM change_files WHERE path = ?", (a,))[0]
        self.assertEqual(json.loads(before)["XMP:Subject"], ["Beach"])
        self.assertEqual(sorted(json.loads(after)["XMP:Subject"]), ["Beach", "Harbour"])
        history = journal_service.history(self.library, change)["changes"][0]
        self.assertEqual(({"done": 2}, "add to all selected"), (history["files"], history["operation"]))

    def test_a_file_holding_it_already_is_not_in_the_change(self):
        a, b = self.make("a.jpg"), self.make("b.jpg", ["Beach", "Harbour"])
        # Every field a keyword write sets.
        write_outside(b, {"EXIF:XPKeywords": "Beach;Harbour"})
        written = os.stat(b).st_mtime_ns
        result = self.add([a, b])
        self.assertEqual(result.changed, 1)
        self.assertEqual(os.stat(b).st_mtime_ns, written, "a file holding it already was written")
        self.assertEqual(len(self.states(result.details["change"])), 1)

    def test_a_person_renamed_through_the_tree_is_one_change_of_files(self):
        a = self.make("a.jpg", ["People/Rowan Thackeray", "Beach"])
        result = tagging.replace_tag(self.library, [a], "People/Rowan Thackeray", "People/Rowan Vale", EXIFTOOL)
        self.assertEqual(result.changed, 1)
        self.assertEqual(keywords_of(a), ["Beach", "People/Rowan Vale"])
        self.assertEqual(self.status(result.details["change"]), "applied")
        self.undo(result.details["change"])
        self.assertEqual(keywords_of(a), ["Beach", "People/Rowan Thackeray"])
        self.assertEqual(self.indexed(a), ["Beach", "People/Rowan Thackeray"])


class Undo(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make("a.jpg"), self.make("b.jpg", ["Beach", "Relay"])
        self.change = self.add([self.a, self.b]).details["change"]

    def test_every_file_and_its_row_are_put_back(self):
        result = self.undo(self.change)
        self.assertEqual((result.changed, result.errors), (2, []))
        self.assertEqual(keywords_of(self.a), ["Beach"])
        self.assertEqual(keywords_of(self.b), ["Beach", "Relay"])
        self.assertEqual(self.indexed(self.b), ["Beach", "Relay"])
        self.assertEqual(("undone", ["undone", "undone"]), (self.status(self.change), self.states(self.change)))
        self.assertTrue(self.undo(self.change).refused, "undone twice")

    def test_a_rehearsal_reads_every_file_and_writes_none(self):
        stamps = [os.stat(p).st_mtime_ns for p in (self.a, self.b)]
        result = self.undo(self.change, apply=False)
        self.assertIsNone(result.refused)
        self.assertEqual((result.details["rehearsal"]["rows"], result.details["rehearsal"]["exact"]), (2, True))
        self.assertEqual(stamps, [os.stat(p).st_mtime_ns for p in (self.a, self.b)])
        self.assertEqual(self.status(self.change), "applied")

    def test_a_file_changed_since_is_refused_by_name_and_the_rest_put_back(self):
        write_outside(self.b, {"XMP:Subject": ["Written Elsewhere"]})
        rehearsal = self.undo(self.change, apply=False).details["rehearsal"]
        named = "photo %d" % self.photo_id(self.b)
        self.assertFalse(rehearsal["exact"])
        self.assertIn(named, "; ".join(rehearsal["differences"]))
        self.assertNotIn(self.folder, "; ".join(rehearsal["differences"]), "a refusal names a path")
        result = self.undo(self.change)
        self.assertEqual(result.changed, 1)
        self.assertIn(named, [what for what, _why in result.errors])
        self.assertEqual(keywords_of(self.b), ["Written Elsewhere"], "a file changed since was overwritten")
        self.assertEqual(keywords_of(self.a), ["Beach"])
        self.assertEqual(self.states(self.change), ["undone", "conflict"])

    def test_the_cli_undoes_a_change_of_files(self):
        from click.testing import CliRunner
        from tagpup_cli import cli

        runner = CliRunner()
        listed = runner.invoke(cli, ["--db", self.library.path, "history"]).output
        self.assertIn("photo files: 2 done", listed)
        self.assertIn("2 photo file(s) would be put back", runner.invoke(
            cli, ["--db", self.library.path, "undo", str(self.change)]).output)
        self.assertIn("2 file(s) written back", runner.invoke(
            cli, ["--db", self.library.path, "undo", str(self.change), "--apply"]).output)
        self.assertEqual(keywords_of(self.a), ["Beach"])


class Crashes(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b, self.c = (self.make(n) for n in ("a.jpg", "b.jpg", "c.jpg"))

    def crash(self, step, nth=1, photo_paths=None):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(step, nth)):
            with self.assertRaises(Crash):
                self.add(photo_paths or [self.a, self.b, self.c])
        return self.last_change()

    def assert_finished(self, change):
        self.assertEqual(self.settle(), 1)
        for path in (self.a, self.b, self.c):
            self.assertEqual(keywords_of(path), ["Beach", "Harbour"], path)
            self.assertEqual(self.indexed(path), ["Beach", "Harbour"], path)
        self.assertEqual(("applied", ["done"] * 3), (self.status(change), self.states(change)))

    def test_after_the_plan_is_committed(self):
        change = self.crash("plan committed")
        self.assertEqual(("planned", ["planned"] * 3), (self.status(change), self.states(change)))
        self.assertEqual(keywords_of(self.a), ["Beach"], "a file was written before the plan's first step")
        self.assert_finished(change)

    def test_after_a_file_is_written_before_its_row_is(self):
        change = self.crash("file written")
        self.assertEqual(self.states(change), ["writing", "planned", "planned"])
        self.assertEqual(keywords_of(self.a), ["Beach", "Harbour"])
        self.assertEqual(self.indexed(self.a), ["Beach"], "the row was recorded before the crash")
        written = os.stat(self.a).st_mtime_ns
        self.assert_finished(change)
        self.assertEqual(os.stat(self.a).st_mtime_ns, written, "a file holding what it was to hold was written again")

    def test_marked_writing_before_exiftool_ran(self):
        change = self.crash("file writing")
        self.assertEqual((self.states(change)[0], keywords_of(self.a)), ("writing", ["Beach"]))
        self.assert_finished(change)

    def test_the_vectors_stamp_and_the_journals_fingerprint_are_one_look_at_the_file(self):
        """Just before a file is written its stamp (what its vectors are carried over the write from) and its fingerprint
        (what the journal knows it by) are taken from ONE stat: two were two looks over a share, and could describe two
        files when another program changed it between them (#1958). Break: either taken by a stat of its own. The
        looks _carry makes itself, through a helper; the stat before the read that finds the file still to write
        (_held_or_conflict) is the read's, not counted."""
        looked = []
        real = os.stat

        def stat(path, *args, **kwargs):
            if sys._getframe(2).f_code.co_name == "_carry":
                looked.append(sys._getframe(1).f_code.co_name)
            return real(path, *args, **kwargs)

        def reached(step):
            if step == "file writing":
                raise Crash(step)
        with mock.patch.object(os, "stat", stat), mock.patch.object(file_changes, "_reached", side_effect=reached):
            with self.assertRaises(Crash):
                self.add([self.a])
        self.assertEqual(1, len(looked), "the writer looked at the file before its write by %s" % looked)

    def test_in_the_middle_of_the_batch(self):
        change = self.crash("file recorded", nth=2)
        self.assertEqual(self.states(change), ["done", "done", "planned"])
        self.assert_finished(change)

    def test_a_file_changed_before_settling_is_a_conflict_not_overwritten(self):
        change = self.crash("file writing")
        write_outside(self.a, {"XMP:Subject": ["Written Elsewhere"]})
        self.settle()
        self.assertEqual(keywords_of(self.a), ["Written Elsewhere"])
        self.assertEqual(self.states(change), ["conflict", "done", "done"])
        self.assertEqual(self.status(change), "applied")

    def test_an_undo_stopped_half_way_is_finished(self):
        change = self.add([self.a, self.b, self.c]).details["change"]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file recorded", 2)):
            with self.assertRaises(Crash):
                self.undo(change)
        self.assertEqual(self.states(change), ["undone", "undone", "done"])
        self.assertEqual(self.settle(), 1)
        for path in (self.a, self.b, self.c):
            self.assertEqual((keywords_of(path), self.indexed(path)), (["Beach"], ["Beach"]), path)
        self.assertEqual(self.status(change), "undone")

    def test_a_change_a_live_process_is_writing_is_left_to_it(self):
        change = self.crash("plan committed")
        for owner in (file_journal.owner(), "another-machine:4242:1"):
            self.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, change))
            self.assertEqual(self.settle(), 0, owner)
        self.assertEqual(self.states(change), ["planned"] * 3)
        # A process that has gone -- no process has id 0x7FFFFFF0 -- leaves it to whoever
        # opens the library next.
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", ("%s:%d:1" % (socket.gethostname(), 0x7FFFFFF0),
                                                                      change))
        self.assert_finished(change)

    def test_the_first_read_of_the_librarys_settings_settles_it(self):
        from tagpup import runtime

        change = self.crash("file written")
        # As the first read of this process: the set of libraries settled is empty.
        with mock.patch.object(file_changes, "_settled", set()):
            runtime.library_settings(self.library)
        self.assertEqual(self.status(change), "applied")
        self.assertEqual(self.indexed(self.a), ["Beach", "Harbour"])


class Conflicts(FilesCase):
    def test_a_file_changed_between_the_plan_and_its_write_is_not_overwritten(self):
        a, b = self.make("a.jpg"), self.make("b.jpg")

        def elsewhere(step):
            if step == "plan committed":
                write_outside(b, {"XMP:Subject": ["Written Elsewhere"], "IPTC:Keywords": ["Written Elsewhere"]})

        with mock.patch.object(file_changes, "_reached", side_effect=elsewhere):
            result = self.add([a, b])
        self.assertEqual(result.changed, 1)
        self.assertEqual(result.details["conflicts"], ["photo %d" % self.photo_id(b)])
        self.assertEqual(keywords_of(b), ["Written Elsewhere"])
        self.assertEqual(keywords_of(a), ["Beach", "Harbour"])
        change = result.details["change"]
        self.assertEqual(("applied", ["done", "conflict"]), (self.status(change), self.states(change)))


class Renames(FilesCase):
    def setUp(self):
        super().setUp()
        # Each keeps its name already: the crashes are the rename's, not that write's.
        self.a = self.make("IMG_0001.jpg", caption="Start", preserved="IMG_0001.jpg")
        self.b = self.make("IMG_0002.jpg", caption="Finish", preserved="IMG_0002.jpg")
        # Already holding the name the first is to take: moved aside.
        self.other = self.make("Regatta - 1 - Start.jpg", row=False)
        self.ids = (self.photo_id(self.a), self.photo_id(self.b))

    def rename(self):
        return photo_actions.smart_rename(self.library, [self.a, self.b], "Regatta",
                                          "{grouping} - {index} - {caption}", EXIFTOOL)

    def names(self):
        return sorted(os.listdir(self.folder))

    def paths_of_rows(self):
        return [self.rows("SELECT path FROM photos WHERE id = ?", (i,))[0][0] for i in self.ids]

    def test_a_rename_is_undone_with_its_rows_and_the_file_moved_aside(self):
        before = self.names()
        result = self.rename()
        self.assertEqual(result.changed, 2)
        self.assertEqual(self.names(), ["Regatta - 1 - Start.jpg", "Regatta - 1 - Start_conflict_1.jpg",
                                        "Regatta - 2 - Finish.jpg"])
        self.assertEqual([os.path.basename(p) for p in self.paths_of_rows()],
                         ["Regatta - 1 - Start.jpg", "Regatta - 2 - Finish.jpg"])
        change = result.details["change"]
        self.assertEqual(self.states(change), ["done"] * 3)
        undone = self.undo(change)
        self.assertEqual((undone.changed, undone.errors), (3, []))
        self.assertEqual(self.names(), before)
        self.assertEqual(self.paths_of_rows(), [self.a, self.b])
        self.assertEqual(self.status(change), "undone")

    def test_a_rename_stopped_before_its_rows_moved_is_finished(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.rename()
        self.assertEqual(self.paths_of_rows(), [self.a, self.b])
        self.settle()
        self.assertEqual([os.path.basename(p) for p in self.paths_of_rows()],
                         ["Regatta - 1 - Start.jpg", "Regatta - 2 - Finish.jpg"])
        self.assertEqual(self.status(self.last_change()), "applied")

    def test_a_rename_stopped_before_any_file_moved_is_carried_out(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file writing")):
            with self.assertRaises(Crash):
                self.rename()
        self.assertIn("IMG_0001.jpg", self.names())
        self.settle()
        self.assertEqual(self.names(), ["Regatta - 1 - Start.jpg", "Regatta - 1 - Start_conflict_1.jpg",
                                        "Regatta - 2 - Finish.jpg"])
        self.assertEqual([os.path.basename(p) for p in self.paths_of_rows()],
                         ["Regatta - 1 - Start.jpg", "Regatta - 2 - Finish.jpg"])

    def test_an_undo_whose_old_name_is_taken_is_refused_for_that_file(self):
        change = self.rename().details["change"]
        # A new file where the second used to be.
        self.make("IMG_0002.jpg", row=False)
        result = self.undo(change)
        self.assertIn("photo %d" % self.ids[1], [what for what, _why in result.errors])
        self.assertEqual(os.path.basename(self.paths_of_rows()[1]), "Regatta - 2 - Finish.jpg")
        self.assertEqual(self.paths_of_rows()[0], self.a)


class APatchInPlace(FilesCase):
    """The journal's record of a patch in place (file_journal.save_patch): on the row being written and no other,
    erased by every mark that takes it out of `writing`, refused over PATCH_CAP before a byte is written, and an undo
    carried by the fields, as any undo is."""

    def replace(self, photo_paths):
        return tagging.replace_tag(self.library, photo_paths, "Beach", "Shore", EXIFTOOL)

    def recorded(self):
        return [i for (i,) in self.rows("SELECT id FROM change_files WHERE patch IS NOT NULL OR patch_before IS NOT NULL")]

    def test_only_the_file_being_written_holds_a_record_and_every_way_out_erases_it(self):
        a, b = self.make("a.jpg"), self.make("b.jpg")
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at(file_changes.PATCH_SAVED)):
            with self.assertRaises(Crash):
                self.replace([a, b])
        change = self.last_change()
        self.assertEqual(["writing", "planned"], self.states(change))
        first = self.rows("SELECT MIN(id) FROM change_files WHERE change_id = ?", (change,))[0][0]
        self.assertEqual([first], self.recorded())
        record, before = file_journal.patch_of(self.library.path, first)
        # The own writer patches a keyword save into the XMP alone, one range, and records the copies it left as they
        # were (DECISIONS 2026-11-03, A KEYWORD OR CAPTION SAVE WRITES THE XMP COPY ONLY; segment_edits.stale_of).
        self.assertEqual((1, ["IPTC:Keywords", "EXIF:XPKeywords"]), (len(record["ranges"]), record["stale"]),
                         "one range, the XMP segment's; IPTC's and EXIF's copies left")
        self.assertEqual({"v", "size", "size_after", "ranges", "stale"}, set(record), "a patch keeps only the bytes it changes, no hash of the"
                         " rest (DECISIONS 2026-11-03, AN IN-PLACE PATCH CHECKS ONLY THE BYTES IT CHANGED)")
        # Only the bytes that differ, from the first to the last: the keyword in the XMP packet.
        self.assertEqual(b"Beach", before, "the record does not hold what its ranges held")
        self.assertEqual(1, self.settle())
        self.assertEqual((("applied", ["done", "done"]), [], ["Shore"]), ((self.status(change), self.states(change)),
                                                                          self.recorded(), keywords_of(a)))
        with mock.patch.object(field_values, "write", wraps=field_values.write) as writes, \
                mock.patch.object(file_journal, "save_patch", wraps=file_journal.save_patch) as saved:
            result = self.undo(change)
        self.assertEqual((2, []), (result.changed, result.errors))
        self.assertEqual([["Beach"], ["Beach"]], [call.args[2]["XMP:Subject"] for call in writes.call_args_list],
                         "the undo was not carried by the fields the change found")
        self.assertEqual(2, saved.call_count, "the undo of a patched change is a patch too")
        self.assertEqual((["Beach"], ["Beach"], [], ["undone", "undone"]),
                         (keywords_of(a), self.indexed(a), self.recorded(), self.states(change)))

    def test_a_patch_larger_than_the_journal_keeps_is_a_failed_write_of_no_byte(self):
        # A JPEG's patch is at most three segments of 64 KB, with its record under PATCH_CAP (1 MiB, the record and the
        # old bytes): nothing real meets the cap, so a patch over it is not given to ExifTool's rewrite instead; it is
        # refused with its sentence, the photo as it was (main session, 2026-11-03; owner, 2026-11-12).
        a = self.make("a.jpg")
        with open(a, "rb") as handle:
            held = handle.read()
        with mock.patch.object(file_journal, "PATCH_CAP", 4):
            result = self.replace([a])
        with open(a, "rb") as handle:
            self.assertEqual(held, handle.read(), "a byte was written when the journal refused the patch")
        self.assertEqual(0, result.changed)
        self.assertIn("its metadata is too large to keep a copy of", str(result.errors))
        self.assertEqual((["Beach"], [], ["Beach"]), (keywords_of(a), self.recorded(), self.indexed(a)))


@contextlib.contextmanager
def share_away(at_the_write=True):
    """The photos' share stops answering a patch in place and stays away: from half way through the first patch's first
    range (`at_the_write`), or from the patch's first look at the photo, before its record is saved. Every later bounded
    call of in_place (a put back, a look at the bytes, the next photo's patch) is Away, as a share gone is. Yields the
    paths field_values.read_one was asked to read through ExifTool after the share went: on a share gone, each waits
    ExifTool's whole deadline (five minutes)."""
    real_write, real_bounded, away, asked = in_place._write, in_place._bounded, [], []
    real_read_one = field_values.read_one

    def write(handle, changes):
        at, new = changes[0]
        real_write(handle, [(at, new[:len(new) // 2])])
        away.append(True)
        raise in_place.Away("the photo's folder did not answer in 30 s")

    def bounded(path, call):
        if away or not at_the_write:
            away.append(True)
            raise in_place.Away("the photo's folder did not answer in 30 s")
        return real_bounded(path, call)

    def read_one(et, path, *args, **kwargs):
        if away:
            asked.append(path)
        return real_read_one(et, path, *args, **kwargs)

    with mock.patch.object(in_place, "_write", write), mock.patch.object(in_place, "_bounded", bounded), \
            mock.patch.object(field_values, "read_one", read_one):
        yield asked


class AShareThatStopsAnsweringInAPatch(FilesCase):
    """A keyword renamed in three photos on a share that stops answering in the patch of the first (#2400): the write stops
    there, as it does when ExifTool stops answering (#1921), and no photo is read through ExifTool after it, which on a
    share gone waits ExifTool's whole deadline each. The photo under way stays `writing` for a settle by its bytes once
    the share answers; the two after it are taken out, untouched. Break: _after_failure going on to the next photo (the
    trunk before #2400: the next photo's read waited its deadline before the write stopped)."""

    def rename(self, photo_paths):
        """Beach renamed Shore: the same length, so each photo's fields fit its segments and are patched in place."""
        return tagging.replace_tag(self.library, photo_paths, "Beach", "Shore", EXIFTOOL)

    def test_a_share_gone_half_way_through_the_first_patch_stops_the_write_there(self):
        a, b, c = self.make("a.jpg"), self.make("b.jpg"), self.make("c.jpg")
        untouched = {path: os.stat(path).st_mtime_ns for path in (b, c)}
        with share_away() as asked:
            result = self.rename([a, b, c])
        self.assertEqual([], asked, "a photo was read through ExifTool after its share went away")
        change = result.details["change"]
        self.assertEqual((0, ["writing"]), (result.changed, self.states(change)))
        self.assertEqual([a, b, c], result.details["not_answered"])
        self.assertIn("stopped answering as it was written", str(result.errors[0][1]))
        self.assertEqual(untouched, {path: os.stat(path).st_mtime_ns for path in (b, c)})
        # The share answers again: the torn patch is put back by its bytes and written again; the change ends applied.
        self.assertEqual(1, self.settle())
        self.assertEqual((["done"], "applied", ["Shore"]), (self.states(change), self.status(change), keywords_of(a)))
        self.assertEqual([["Beach"], ["Beach"]], [keywords_of(b), keywords_of(c)])

    def test_a_share_gone_before_the_first_patch_saved_its_record_stops_the_write_with_no_byte_written(self):
        a, b, c = self.make("a.jpg"), self.make("b.jpg"), self.make("c.jpg")
        with open(a, "rb") as handle:
            held = handle.read()
        with share_away(at_the_write=False) as asked:
            result = self.rename([a, b, c])
        self.assertEqual([], asked, "a photo was read through ExifTool after its share went away")
        with open(a, "rb") as handle:
            self.assertEqual(held, handle.read())
        self.assertEqual((0, ["writing"]), (result.changed, self.states(result.details["change"])))
        self.assertEqual(1, self.settle())
        self.assertEqual(["Shore"], keywords_of(a))


class ThePatchRecordHasOneReader(unittest.TestCase):
    """The saved bytes of a patch are a crash record, not a second undo: only the journal (file_journal), the schema
    that adds them and the doctor, by the journal's functions, name them in SQL. History, the MCP server and an undo's
    rehearsal read change_files by _FILE_COLUMNS, which does not hold them. Break: a reader selecting patch_before."""

    SQL = re.compile(r"\b(SELECT|UPDATE|INSERT|ALTER|WHERE)\b[^\n]*\bpatch(_before)?\b|SELECT \*[^\n]*change_files")
    OWNERS = {os.path.join("tagpup", "store", name) for name in ("file_journal.py", "schema.py", "checks.py")}

    def test_only_the_journal_the_schema_and_the_doctor_name_the_columns(self):
        found = set()
        for top in ("tagpup", "scripts", "tools"):
            for folder, _dirs, names in os.walk(os.path.join(WORKSPACE_DIR, top)):
                for name in names:
                    if name.endswith(".py"):
                        path = os.path.join(folder, name)
                        with open(path, encoding="utf-8") as handle:
                            if self.SQL.search(handle.read()):
                                found.add(os.path.relpath(path, WORKSPACE_DIR))
        self.assertEqual(set(), found - self.OWNERS)
        self.assertNotIn("patch", file_journal._FILE_COLUMNS)


class TimeShift(FilesCase):
    def test_a_time_shift_is_undone(self):
        a = self.make("a.jpg", taken=TAKEN)
        self.execute("UPDATE photos SET taken = NULL")
        result = photo_actions.shift_date_taken(self.library, [a], 30, EXIFTOOL)
        self.assertEqual(result.changed, 1)
        self.assertEqual(field_of(a, "EXIF:DateTimeOriginal"), SHIFTED)
        shifted = self.rows("SELECT taken FROM photos WHERE path = ?", (a,))[0][0]
        self.assertIn("10:30", shifted, "when it was taken did not follow the file")
        self.assertEqual(self.undo(result.details["change"]).changed, 1)
        self.assertEqual(field_of(a, "EXIF:DateTimeOriginal"), TAKEN)
        self.assertIn("10:00", self.rows("SELECT taken FROM photos WHERE path = ?", (a,))[0][0])
        raw = json.loads(self.rows("SELECT raw_metadata FROM photos WHERE path = ?", (a,))[0][0])
        self.assertEqual(raw["EXIF:DateTimeOriginal"], TAKEN)


if __name__ == "__main__":
    unittest.main()
