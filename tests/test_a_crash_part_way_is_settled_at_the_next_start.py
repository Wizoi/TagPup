"""A crash part-way through a write is finished by the next start (scenario D3).

The owner's server dies between a photo file being written and its change being marked done, and, in the same
minute, a change of rows dies between its commit and the rebuild of the derived rows (`derived_pending`). The next
start finishes both: the file change by what the files hold, the rows by rebuilding. Windows has meanwhile given
the dead process's id to the process that starts: a change owned by "host:pid:start" with an earlier start is not
that process's, and is settled; one owned by this very process is left to it.

Lifted: test_a_changes_owner_is_known_by_its_start (#275) and test_a_withdrawn_change_is_finished (#274). Break that
fails the kept tests: tagpup.store.file_journal.owner_alive ignoring the start time (the start read as None) fails
test_the_next_start_finishes_what_the_crash_left_also_when_windows_reused_the_process_id. There is no start-less owner
("host:pid", the old form): the live libraries hold none (0 of 3, counted 2026-10-15), and owner() refuses to name one.

A patch in place (tagpup.files.in_place.patch_jpeg, the real one, cut by test_file_journal.cut_patch) killed part-way
is settled by its bytes against the record it saved before its first byte (AnInPlacePatchCutShort). Break that fails it: _by_bytes not called, so a file torn between two ranges is read
by its fields, holds neither what the change found nor what it was to leave, and is left a half-written conflict (that
the old code also fails that test is not the proof: it fails earlier, on a write without `saving`; the mutation is).

A write whose picture its rewrite damages twice (tagpup.files.field_values.PictureChanged; the picture check of #2377), its
journal's path, the write a stand-in that raises it (APictureAWriteDamagesTwice): forward, the file is taken out of its
change and its error says why; in a settle -- the index's bake killed part-way -- or an undo, it is a conflict noted
file_journal.PICTURE_CHANGED, its row not stamped again, which the doctor counts. Fails on the old code: file_journal.finish
erasing a forward-finished bake whole (the change and its conflict, so the doctor counted 0) fails
test_a_bake_settled_at_the_next_start_keeps_its_change_and_the_conflict_the_doctor_counts; keeping every conflict of a
bake, one that may pass too, fails test_a_bake_whose_file_ends_a_conflict_that_may_pass_is_erased_with_it.
"""
import contextlib
import json
import os
import socket
import stat
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import photo_rows  # noqa: E402
from face_rows import add_face  # noqa: E402
from test_file_journal import EXIFTOOL, Crash, FilesCase, crash_at, cut_patch, field_of, keywords_of, \
    killed_before_the_save, requires_exiftool, write_outside  # noqa: E402

from tagpup import runtime  # noqa: E402
from tagpup.core import processes  # noqa: E402
from tagpup.files import bake, field_values, in_place  # noqa: E402
from tagpup.services import baking, file_changes, photos, tagging  # noqa: E402
from tagpup.store import checks, db, file_journal, journal, people, schema  # noqa: E402

HOST = socket.gethostname()
#: A process that has gone: no process has this id.
GONE = "%s:%d:1" % (HOST, 0x7FFFFFF0)
#: A photo's Date Taken, and the same an hour later (a Shift Date Taken of 60 minutes).
TAKEN, SHIFTED = "2024:07:04 10:00:00", "2024:07:04 11:00:00"
#: The copies of a Date Taken a date write sets (fields.date_taken_fields).
DATES = ("EXIF:DateTimeOriginal", "EXIF:CreateDate", "XMP:DateTimeOriginal")


def start_again(library):
    """What the next process does with the library: opens it (rows settled), reads its settings (files settled)."""
    journal._settled.clear()
    schema.forget()
    schema.ensure(library.path)
    with mock.patch.object(file_changes, "_settled", set()):
        runtime.library_settings(library)


@requires_exiftool
class ACrashAndTheNextStart(FilesCase):
    def setUp(self):
        super().setUp()
        self.a, self.b = self.make("a.jpg"), self.make("b.jpg")
        self.c = os.path.join(self.folder, "c.jpg")
        conn = db.connect(self.library.path)
        try:
            self.c_id = photo_rows.add_read(conn, self.c, {"XMP:Subject": ["Beach"]})
            self.face = add_face(conn, self.c, name="Maren Oakhollow")
            people.rebuild_photos(conn, [self.c])
            conn.commit()
        finally:
            conn.close()

    def listed(self):
        return self.rows("SELECT name FROM photo_people WHERE photo_id = ?", (self.c_id,))

    def owned_by(self, owner, change):
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (owner, change))

    def test_the_next_start_finishes_what_the_crash_left_also_when_windows_reused_the_process_id(self):
        # The server dies after writing a file and before marking it done, ...
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("file written")):
            with self.assertRaises(Crash):
                self.add([self.a, self.b])
        files = self.last_change()
        self.assertEqual(["writing", "planned"], self.states(files))
        # ... and after committing a change of rows, before its derived rows are rebuilt.
        with mock.patch.object(journal, "_reached", side_effect=crash_at("forward committed")):
            with self.assertRaises(Crash):
                journal.apply(self.library.path, "delete a face", [journal.delete(
                    "faces", (self.face,), {"name": "Maren Oakhollow"})])
        rows = self.last_change()
        self.assertEqual("derived_pending", self.status(rows))
        self.assertEqual([("Maren Oakhollow",)], self.listed(), "the photo's people are not rebuilt yet")
        # A live process's change is left to it; the process id is this one's but the start is an earlier one's.
        self.owned_by(file_journal.owner(), files)
        self.assertEqual(0, self.settle())
        earlier = processes.started(os.getpid()) - 1
        self.owned_by("%s:%d:%d" % (HOST, os.getpid(), earlier), files)
        start_again(self.library)
        self.assertEqual(("applied", ["done", "done"]), (self.status(files), self.states(files)))
        for path in (self.a, self.b):
            self.assertEqual((["Beach", "Harbour"], ["Beach", "Harbour"]), (keywords_of(path), self.indexed(path)), path)
        self.assertEqual("applied", self.status(rows))
        self.assertEqual([], self.listed())

    def test_a_running_process_that_took_the_id_is_not_the_owner_and_another_machines_is_left_to_it(self):
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("plan committed")):
            with self.assertRaises(Crash):
                self.add([self.a])
        change = self.last_change()
        running = os.getppid()
        self.owned_by("%s:%d:%d" % (HOST, running, processes.started(running)), change)
        self.assertEqual(0, self.settle(), "the live parent of this test is running and holds it")
        self.owned_by("another-machine:4242:1234567", change)
        self.assertEqual(0, self.settle())
        self.owned_by("%s:%d:%d" % (HOST, running, processes.started(running) - 1), change)
        self.assertEqual(1, self.settle(), "an earlier process with a running one's id")
        self.assertEqual("applied", self.status(change))

    def test_a_change_every_file_was_withdrawn_from_leaves_no_row_once_settled(self):
        # Its one file could not be written and was taken out; then the process stopped, leaving it planned with no files.
        # It wrote nothing: the settle erases it, as the change that is not stopped is erased (file_journal.finish).
        change, planned = file_journal.plan(self.library.path, "add to all selected", [
            {"photo_id": None, "path": self.a, "before": {"XMP:Subject": []}, "after": {"XMP:Subject": ["Harbour"]}}])
        file_journal.withdraw(self.library.path, [row.id for row in planned])
        self.owned_by(GONE, change)
        self.assertEqual([change], [c.id for c in file_journal.unfinished(self.library.path)])
        self.assertEqual(1, self.settle())
        self.assertEqual([], self.rows("SELECT id FROM changes WHERE id = ?", (change,)), "no row of a change that wrote nothing")
        self.assertEqual([], file_journal.unfinished(self.library.path))


@requires_exiftool
class AnInPlacePatchCutShort(FilesCase):
    """A keyword renamed in a photo by a patch in place (in_place.patch_jpeg: 'Beach' for 'Shore', in its XMP alone, one
    range: the own writer leaves IPTC's and EXIF's copies, segment_edits), or its Date Taken shifted (its EXIF and XMP
    copies, two ranges: the patch torn between them), the process killed at each point of the write
    (test_file_journal.cut_patch), and the next start: untouched or all old, it is written again; torn by the patch, put back
    and written again; all new, done; another program's write, a conflict never overwritten; unreadable, left `writing`
    with its record and the change let go. Another program's write is told by the ranges and the size alone: a byte of a
    range that is neither old nor new, or another size (no byte outside the ranges is read, DECISIONS 2026-11-03). A
    record the version installed before saved (v1, no size after; 0 live, counted 2026-11-05) met by this one is a
    conflict, never written over."""

    def setUp(self):
        super().setUp()
        # Dated in its EXIF and its XMP, as a photo whose Date Taken TagPup has written holds it (fields.date_taken_fields).
        self.a = self.make("a.jpg", row=False)
        dates = {field: TAKEN for field in DATES}
        write_outside(self.a, dates)
        conn = db.connect(self.library.path)
        try:
            photo_rows.add_read(conn, self.a, {"XMP:Subject": ["Beach"], "IPTC:Keywords": ["Beach"],
                                               "EXIF:XPKeywords": "Beach", **dates})
            conn.commit()
        finally:
            conn.close()
        self.original = self.held()

    def held(self):
        with open(self.a, "rb") as handle:
            return handle.read()

    def replace(self, cutting=None, step=None):
        with cutting if cutting is not None else contextlib.nullcontext(), \
                mock.patch.object(file_changes, "_reached", side_effect=crash_at(step) if step else None):
            return tagging.replace_tag(self.library, [self.a], "Beach", "Shore", field_values.opened(EXIFTOOL))

    def shift(self, cutting=None, step=None):
        with cutting if cutting is not None else contextlib.nullcontext(), \
                mock.patch.object(file_changes, "_reached", side_effect=crash_at(step) if step else None):
            return photos.shift_date_taken(self.library, [self.a], 60, field_values.opened(EXIFTOOL))

    def killed(self, cutting=None, step=None, writing=None):
        with self.assertRaises(Crash):
            (writing or self.replace)(cutting, step)
        change = self.last_change()
        self.assertEqual(["writing"], self.states(change))
        return change, self.rows("SELECT id FROM change_files WHERE change_id = ?", (change,))[0][0]

    def against_its_record(self, file_id):
        return in_place.held(self.a, *file_journal.patch_of(self.library.path, file_id))

    def assert_written_again(self, change):
        start_again(self.library)
        self.assertEqual((("applied", ["done"]), ["Shore"], ["Shore"], None),
                         ((self.status(change), self.states(change)), keywords_of(self.a), self.indexed(self.a),
                          file_journal.patch_of(self.library.path, self.rows("SELECT MAX(id) FROM change_files")[0][0])))

    def test_killed_before_its_record_was_saved_the_file_is_untouched_and_written_again(self):
        change, file_id = self.killed(killed_before_the_save())
        self.assertEqual((self.original, None), (self.held(), file_journal.patch_of(self.library.path, file_id)))
        self.assert_written_again(change)

    def test_killed_once_its_record_was_saved_every_range_is_old_and_it_is_written_again(self):
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        self.assertEqual((self.original, in_place.OLD), (self.held(), self.against_its_record(file_id)))
        self.assert_written_again(change)

    def assert_shifted_again(self, change):
        start_again(self.library)
        self.assertEqual((("applied", ["done"]), [SHIFTED] * len(DATES), None),
                         ((self.status(change), self.states(change)), [field_of(self.a, field) for field in DATES],
                          file_journal.patch_of(self.library.path, self.rows("SELECT MAX(id) FROM change_files")[0][0])))

    def test_killed_between_two_ranges_what_it_tore_is_put_back_and_written_again(self):
        change, file_id = self.killed(cut_patch(ranges=1), writing=self.shift)
        self.assertEqual(in_place.TORN, self.against_its_record(file_id))
        self.assert_shifted_again(change)

    def test_killed_after_its_last_byte_it_is_done_and_not_written_again(self):
        change, file_id = self.killed(cut_patch())
        self.assertEqual(in_place.NEW, self.against_its_record(file_id))
        patched = self.held()
        self.assert_written_again(change)
        self.assertEqual(patched, self.held(), "a file holding what it was to hold was written again")

    def test_written_by_another_program_to_another_size_it_is_a_conflict_never_overwritten(self):
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        write_outside(self.a, {"XMP:Description": "Written elsewhere"})
        self.assertEqual(in_place.NEITHER, self.against_its_record(file_id))
        start_again(self.library)
        self.assertEqual((["conflict"], ["Beach"], "Written elsewhere", None),
                         (self.states(change), keywords_of(self.a), field_of(self.a, "XMP:Description"),
                          file_journal.patch_of(self.library.path, file_id)))

    def test_unreadable_at_the_next_start_it_stays_writing_with_its_record_and_the_change_is_let_go(self):
        change, file_id = self.killed(cut_patch(ranges=1), writing=self.shift)
        torn = self.held()
        os.chmod(self.a, stat.S_IREAD)
        self.addCleanup(os.chmod, self.a, stat.S_IREAD | stat.S_IWRITE)
        start_again(self.library)
        self.assertEqual((["writing"], torn, None), (self.states(change), self.held(),
                                                     self.rows("SELECT owner FROM changes WHERE id = ?", (change,))[0][0]))
        self.assertIsNotNone(file_journal.patch_of(self.library.path, file_id), "the only way back was erased")
        self.assertNotEqual("applied", self.status(change))
        os.chmod(self.a, stat.S_IREAD | stat.S_IWRITE)
        self.assert_shifted_again(change)

    def test_a_byte_of_a_range_neither_old_nor_new_is_another_programs_a_conflict_never_overwritten(self):
        # Killed once its record was saved; then another program writes a byte where the patch was to write, the file
        # the same size: neither what the patch found nor what it was to leave, so neither put back nor written over.
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        record, before = file_journal.patch_of(self.library.path, file_id)
        at = record["ranges"][0]["at"]
        found, meant = before[0], in_place._spans(record)[0][2][0]
        stray = next(byte for byte in range(256) if byte not in (found, meant))
        with open(self.a, "r+b") as handle:
            handle.seek(at)
            handle.write(bytes([stray]))
        written = self.held()
        self.assertEqual(in_place.NEITHER, self.against_its_record(file_id))
        start_again(self.library)
        self.assertEqual((["conflict"], written, None), (self.states(change), self.held(),
                                                         file_journal.patch_of(self.library.path, file_id)))


    def test_a_record_an_earlier_tagpup_saved_is_a_conflict_never_overwritten(self):
        # The version installed before this one (record v1, which has no size after) killed once its record was saved;
        # this version starts next. The file is untouched, but its record is not one this version reads.
        change, file_id = self.killed(step=file_changes.PATCH_SAVED)
        record, _before = file_journal.patch_of(self.library.path, file_id)
        earlier = {key: value for key, value in record.items() if key != "size_after"}
        self.execute("UPDATE change_files SET patch = ? WHERE id = ?", (json.dumps(dict(earlier, v=1)), file_id))
        start_again(self.library)
        self.assertEqual((["conflict"], self.original, None), (self.states(change), self.held(),
                                                               file_journal.patch_of(self.library.path, file_id)))
        self.assertIn("earlier TagPup", self.rows("SELECT note FROM change_files WHERE id = ?", (file_id,))[0][0])

@requires_exiftool
class APictureAWriteDamagesTwice(FilesCase):
    """Each write of the photo comes back with its picture's bytes changed (field_values.PictureChanged), the new file
    discarded and the photo as it was: written once more, then the photo's error by the one failure path."""

    def setUp(self):
        super().setUp()
        self.a = self.make("a.jpg")
        self.writes = 0

    def refused(self, *_args, **_kwargs):
        self.writes += 1
        raise field_values.PictureChanged("its write changed the picture's bytes; the new file was discarded")

    def damaging(self):
        return mock.patch.object(field_values, "_write", self.refused)

    def row(self):
        return self.rows("SELECT * FROM photos WHERE path = ?", (self.a,))

    def counted(self):
        conn = db.connect(db.readonly_uri(self.library.path), uri=True)
        try:
            return checks.files_whose_picture_changed(conn).count
        finally:
            conn.close()

    def notes(self, change):
        return self.rows("SELECT state, note FROM change_files WHERE change_id = ?", (change,))

    def test_forward_it_is_taken_out_of_its_change_and_its_error_says_why(self):
        row, last = self.row(), self.last_change()
        with self.damaging():
            result = self.add([self.a])
        self.assertEqual((0, 2, 1), (result.changed, self.writes, result.details.get("pictures_refused")))
        self.assertIn(file_journal.PICTURE_CHANGED, " ".join(why for _path, why in result.errors))
        self.assertEqual((None, last, ["Beach"], row), (result.details["change"], self.last_change(), keywords_of(self.a),
                                                        self.row()), "its change, which wrote nothing, leaves no row")
        self.assertEqual(0, self.counted(), "a file taken out of its change is no conflict")

    def test_a_bake_settled_at_the_next_start_keeps_its_change_and_the_conflict_the_doctor_counts(self):
        found = bake.WRITTEN[bake.FACES_FOUND]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("plan committed")):
            with self.assertRaises(Crash):
                file_changes.write_fields(self.library, baking.INDEX_OPERATION, field_values.opened(EXIFTOOL), [self.a], [found],
                                          lambda _path, _held: file_changes.Plan(after={found: ["0"]}))
        change = self.last_change()
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, change))
        row = self.row()
        with self.damaging():
            start_again(self.library)
        self.assertEqual(2, self.writes)
        self.assertEqual(("applied", [("conflict", file_journal.PICTURE_CHANGED)], row, None, 1),
                         (self.status(change), self.notes(change), self.row(), field_of(self.a, found), self.counted()))
        self.assertEqual([], file_journal.unfinished(self.library.path))

    def test_a_bake_whose_file_ends_a_conflict_that_may_pass_is_erased_with_it(self):
        # A conflict the doctor counts no note of (NOTES): kept, it would be listed nowhere and counted by no rule, and
        # the next index writes the file anyway. Keeping every conflict of a bake (the rule before) fails this.
        found = bake.WRITTEN[bake.FACES_FOUND]
        with mock.patch.object(file_changes, "_reached", side_effect=crash_at("plan committed")):
            with self.assertRaises(Crash):
                file_changes.write_fields(self.library, baking.INDEX_OPERATION, field_values.opened(EXIFTOOL), [self.a], [found],
                                          lambda _path, _held: file_changes.Plan(after={found: ["0"]}))
        change = self.last_change()
        self.execute("UPDATE changes SET owner = ? WHERE id = ?", (GONE, change))

        def held(*_args, **_kwargs):
            raise PermissionError("the file is being used by another process")
        with mock.patch.object(field_values, "_write", held):
            start_again(self.library)
        self.assertEqual(([], [], None, 0), (self.rows("SELECT id FROM changes WHERE id = ?", (change,)),
                                              self.notes(change), field_of(self.a, found), self.counted()))
        self.assertEqual([], file_journal.unfinished(self.library.path))

    def test_an_undo_is_refused_for_it_a_conflict_the_doctor_counts(self):
        change = self.add([self.a]).details["change"]
        row = self.row()
        with self.damaging():
            result = self.undo(change)
        self.assertEqual((0, 2), (result.changed, self.writes))
        self.assertIn(file_journal.PICTURE_CHANGED, " ".join(why for _path, why in result.errors))
        self.assertEqual(([("conflict", file_journal.PICTURE_CHANGED)], ["Beach", "Harbour"], row, 1),
                         (self.notes(change), keywords_of(self.a), self.row(), self.counted()))


if __name__ == "__main__":
    unittest.main()
