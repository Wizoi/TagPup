"""Bulk edits of photo files are journaled file by file, settled after a crash, and
undone where each file still holds what the edit left (docs/ARCHITECTURE.md, phase 7.5,
"Photo files"; tagpup.services.file_changes).

Real JPEGs, made here in a home of the test's own, written by the real ExifTool; the
test is skipped only where ExifTool is not installed. Rows are seeded as the indexer
stores them, with SQL, never through the recorder under test. A crash is a
BaseException raised at a step of the write (file_changes.STEPS), as tests/test_journal.py
does for the journal of rows; `settle` then finishes what it left.
"""
import contextlib
import io
import json
import os
import socket
import sys
import unittest
from unittest import mock

WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_DIR)
sys.path.insert(0, os.path.join(WORKSPACE_DIR, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import own_home  # noqa: E402

from tagpup.core.library import Library  # noqa: E402
from tagpup.files.exiftool_session import ExifToolSession  # noqa: E402
from tagpup.services import file_changes, journal as journal_service, photos as photo_actions, tagging  # noqa: E402
from tagpup.store import db, file_journal, schema  # noqa: E402

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


@requires_exiftool
class FilesCase(unittest.TestCase):
    def setUp(self):
        self.home = own_home.for_test(self, prefix="file_journal_")
        self.library = Library(self.home.library("files.db"))
        schema.ensure(self.library.path)
        self.folder = os.path.join(self.home.root, "Photos")
        os.makedirs(self.folder)

    def make(self, name, tags=("Beach",), taken=None, caption=None, row=True, preserved=None):
        """A JPEG holding `tags`, and its row as the indexer leaves it; with `preserved`,
        the name Smart Rename keeps of it, so that renaming it writes nothing first."""
        from PIL import Image

        path = os.path.join(self.folder, name)
        Image.new("RGB", (16, 12), (90, 110, 130)).save(path, "JPEG")
        values = {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags)}
        if taken:
            values["EXIF:DateTimeOriginal"] = taken
        if caption:
            values["XMP:Description"] = caption
        if preserved:
            values["XMP-xmpMM:PreservedFileName"] = preserved
        write_outside(path, values)
        if row:
            raw = {"XMP:Subject": list(tags), "IPTC:Keywords": list(tags)}
            if taken:
                raw["EXIF:DateTimeOriginal"] = taken
            stat = os.stat(path)
            self.execute("INSERT INTO photos (path, mtime, size, tags, captions, raw_metadata) VALUES (?, ?, ?, ?, ?, ?)",
                         (path, stat.st_mtime, stat.st_size, json.dumps(list(tags)),
                          json.dumps([caption] if caption else []), json.dumps(raw)))
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
        for owner in (file_journal.owner(), "another-machine:4242"):
            self.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, change))
            self.assertEqual(self.settle(), 0, owner)
        self.assertEqual(self.states(change), ["planned"] * 3)
        # A process that has gone -- no process has id 0x7FFFFFF0 -- leaves it to whoever
        # opens the library next.
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", ("%s:%d" % (socket.gethostname(), 0x7FFFFFF0),
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


class TheScripts(FilesCase):
    """The two scripts that copied the whole library before they wrote: each now records
    changes of the journal instead, and each can be undone."""

    def run_script(self, module, *arguments):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            module.main(["--db", self.library.path, "--exiftool", EXIFTOOL] + list(arguments))
        return out.getvalue()

    def test_backfill_document_ids(self):
        import backfill_document_ids as script

        holds = self.make("holds.jpg")
        write_outside(holds, {"XMP-xmpMM:DocumentID": "xmp.did:held-already"})
        lacks = self.make("lacks.jpg")
        dry = self.run_script(script)
        self.assertIn("Nothing was changed", dry)
        self.assertIsNone(field_of(lacks, "XMP-xmpMM:DocumentID"))
        self.assertEqual(self.rows("SELECT COUNT(*) FROM changes")[0][0], 0)

        applied = self.run_script(script, "--apply")
        self.assertNotIn("backed up", applied)
        self.assertFalse(os.path.isdir(os.path.join(self.home.data, "backups")), "a copy of the library was taken")
        minted = field_of(lacks, "XMP-xmpMM:DocumentID")
        self.assertTrue(minted and minted.startswith("xmp.did:"))
        identities = dict(self.rows("SELECT path, document_id FROM photos"))
        self.assertEqual((identities[holds], identities[lacks]), ("xmp.did:held-already", minted))
        changes = self.rows("SELECT id, operation, status FROM changes ORDER BY id")
        self.assertEqual([(op, status) for _id, op, status in changes],
                         [("backfill_document_ids", "applied"), ("backfill_document_ids: mint", "applied")])

        for change_id, _op, _status in reversed(changes):
            self.assertFalse(self.undo(change_id).errors)
        self.assertIsNone(field_of(lacks, "XMP-xmpMM:DocumentID"))
        self.assertEqual(field_of(holds, "XMP-xmpMM:DocumentID"), "xmp.did:held-already")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM photos WHERE document_id IS NOT NULL")[0][0], 0)

    def test_relink_renamed_photos(self):
        import relink_renamed_photos as script

        dead = os.path.join(self.folder, "IMG_0421.jpg")
        self.execute("INSERT INTO photos (path, tags, captions, raw_metadata) VALUES (?, '[]', '[]', '{}')", (dead,))
        renamed = self.make("Regatta - 01.jpg", row=False)
        write_outside(renamed, {"XMP-xmpMM:PreservedFileName": "IMG_0421.CR3"})
        photo_id = self.photo_id(dead)

        self.assertIn("the undo restored every one exactly", self.run_script(script))
        self.assertEqual(self.rows("SELECT path FROM photos WHERE id = ?", (photo_id,)), [(dead,)])
        applied = self.run_script(script, "--apply")
        self.assertNotIn("backed up", applied)
        self.assertIn("Recorded as change", applied)
        self.assertEqual(self.rows("SELECT path FROM photos WHERE id = ?", (photo_id,)), [(renamed,)])

        change = self.last_change()
        self.assertEqual(self.rows("SELECT operation FROM changes WHERE id = ?", (change,)),
                         [("relink_renamed_photos",)])
        self.assertEqual(self.undo(change).changed, 1)
        self.assertEqual(self.rows("SELECT path FROM photos WHERE id = ?", (photo_id,)), [(dead,)])


if __name__ == "__main__":
    unittest.main()
